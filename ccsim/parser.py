"""X12 837P / 835 parser -> rows for the lossless schema (claude/schema/01_lossless_edi_schema.sql).

Lossless at two levels: every segment lands in edi.segment (raw text + elements + delimiters on the
interchange), and every segment also lands in a typed table or, when it has none, in edi.misc_segment.
Keys are deterministic, <file_id>:<seg_ord>, so re-parsing a file gives identical rows.

A file is rejected (quarantined) when its envelope is broken: no ISA, a missing SE/GE/IEA trailer,
control numbers that do not match, or a segment outside a transaction set. A wrong SE01 count is kept and
flagged (edi.transaction_set.count_check_ok = false), since the content is still complete.

Speed and memory (docs/data-backend/parser-optimization.md): the file is read in chunks of normalized text, never
split whole. Rows go into a TableRows sink. Every row of a table is a dict with exactly the table's columns in DDL
order, made by that table's generated adder, so a table turns into columns with one zip; edi.segment, the bulk,
is kept as tuples (SEGMENT_FIELDS). The parser only ever changes the latest row of a table (the open claim, line,
entity, envelope, ...), so a sink may flush every other row after any chunk (TableRows.after_chunk).
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import duckdb

DDL_LOSSLESS = (Path(__file__).resolve().parent.parent / "claude/schema/01_lossless_edi_schema.sql").read_text()
TABLES = ["edi.source_file", "edi.segment", "edi.interchange", "edi.functional_group", "edi.transaction_set",
          "edi.entity", "edi.reference", "edi.date_time", "edi.amount", "edi.adjustment", "edi.contact",
          "edi.misc_segment", "c837.hl", "c837.claim", "c837.claim_code", "c837.other_payer", "c837.service_line",
          "c837.line_adjudication", "r835.payment", "r835.claim_payment", "r835.medicare_adjudication",
          "r835.service_payment", "r835.remark", "r835.provider_adjustment"]
# edi.segment rows are tuples of these. The elements column is not among them: it is computed at insert, as
# string_split(raw_text, element_separator)[2:], or, for a file whose elements hold control characters, as
# elements_json cast to VARCHAR[] (the representation always stored, including how that cast reads JSON escapes).
SEGMENT_FIELDS = ("file_id", "seg_ord", "interchange_uid", "group_uid", "txn_uid", "segment_id", "loop_id", "raw_text",
                  "byte_offset", "element_separator", "elements_json")
TEXT_CHUNK_CHARS = 1 << 20

LOOP_837_NM1 = {"41": "1000A", "40": "1000B", "85": "2010AA", "87": "2010AB", "IL": "2010BA", "PR": "2010BB",
                "QC": "2010CA", "DN": "2310A", "82": "2310B", "77": "2310C", "DQ": "2310D"}


class ParseError(ValueError):
    pass


def _table_columns() -> dict[str, list[str]]:
    con = duckdb.connect()
    con.execute(DDL_LOSSLESS)
    out = {t: [] for t in TABLES}
    for table, column in con.execute("SELECT table_schema || '.' || table_name, column_name "
                                     "FROM information_schema.columns ORDER BY table_schema, table_name, "
                                     "ordinal_position").fetchall():
        if table in out:
            out[table].append(column)
    return out


TABLE_COLUMNS = _table_columns()


def _row_adder(columns: list[str], rows: list):
    """add(**values) -> a new row dict holding every column in DDL order (None unless given), appended to rows.
    An unknown column is a TypeError."""
    source = (f"def add(*, {', '.join(c + '=None' for c in columns)}):\n"
              f"    r = {{{', '.join(f'{c!r}: {c}' for c in columns)}}}\n"
              f"    append(r)\n"
              f"    return r\n")
    namespace = {"append": rows.append}
    exec(source, namespace)                                   # noqa: S102 - code generated from DDL column names
    return namespace["add"]


class TableRows:
    """Where the parser puts rows: rows[table] is a list of row dicts (tuples for edi.segment) and add[table](...)
    makes one. after_chunk is called after each chunk of text; a streaming sink flushes there (see module doc)."""

    def __init__(self):
        self.rows = {t: [] for t in TABLES}
        self.add = {t: _row_adder(TABLE_COLUMNS[t], self.rows[t]) for t in TABLES if t != "edi.segment"}

    def after_chunk(self):
        pass


@lru_cache(maxsize=1 << 16)                                   # distinct dates in a batch: service dates, birth dates
def _d8(v):
    try:
        return datetime.strptime(v[:8], "%Y%m%d").date()
    except (ValueError, TypeError):
        return None


def _num(v):
    if v in (None, ""):
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _el(e: list, i: int):
    """Element i, None if absent or empty."""
    try:
        return e[i] or None
    except IndexError:
        return None


_BOM = b"\xef\xbb\xbf"
_LEADING_NOISE = " \t\r\n"           # before ISA (after a BOM)
_TRAILING_NOISE = " \t\r\n\x00"      # after IEA: space / NUL padding of fixed-block transfers


def _normalized_text(data: str | bytes):
    """Delivery noise that is not part of the interchange: a UTF-8 BOM or whitespace before ISA, 80-column line
    wrapping (CR/LF inside segments), and space / NUL padding after IEA. Returns the first 106 or more normalized
    characters and an iterator over the whole normalized text in chunks. Bytes must be ASCII after a BOM."""
    is_bytes = not isinstance(data, str)
    if is_bytes:
        start = len(_BOM) if data.startswith(_BOM) else 0
        if not data.isascii():
            data[start:].decode("ascii")                      # raises the UnicodeDecodeError a decode would
        leading, trailing = set(_LEADING_NOISE.encode()), set(_TRAILING_NOISE.encode())
    else:
        start, leading, trailing = 0, set("﻿" + _LEADING_NOISE), set(_TRAILING_NOISE)
    end = len(data)
    while start < end and data[start] in leading:
        start += 1
    while end > start and data[end - 1] in trailing:
        end -= 1

    def text(a, b):
        return data[a:b].decode("ascii") if is_bytes else data[a:b]

    lstripped_head = text(start, start + 106)
    remove_newlines = "\n" in lstripped_head or "\r" in lstripped_head or (
        len(lstripped_head) >= 106 and lstripped_head[105] not in "\r\n")

    def normalized(a, b):
        t = text(a, b)
        return t.replace("\r", "").replace("\n", "") if remove_newlines else t

    head, width = normalized(start, min(start + 4096, end)), 8192
    while len(head) < 106 and start + width // 2 < end:       # only many CR/LF can leave the first 4 KB short
        head, width = normalized(start, min(start + width, end)), width * 2
    return head, (normalized(a, min(a + TEXT_CHUNK_CHARS, end)) for a in range(start, end, TEXT_CHUNK_CHARS))


def iter_segment_chunks(data: str | bytes):
    """Yields the delimiters (element, component, repetition, terminator) first, then, per chunk of text, the list
    of its segments as (byte_offset, elements, raw text), offsets into the normalized text, and whether that text
    holds a control character other than a delimiter (json.dumps would escape it in an element)."""
    head, chunks = _normalized_text(data)
    if not head.startswith("ISA") or len(head) < 106:
        raise ParseError("not an X12 interchange (no ISA)")
    el, rep, comp, term = head[3], head[82], head[104], head[105]
    if len({el, comp, term}) != 3:
        raise ParseError("ISA delimiters are not distinct")
    yield el, comp, rep, term
    control = re.compile("[" + "".join(f"\\x{c:02x}" for c in range(32) if chr(c) not in (el, term)) + "]")
    strip_chars = "\r\n\x00 " if term not in " " else "\r\n\x00"
    pos, carry, last = 0, "", False
    chunks = iter(chunks)
    while not last:
        chunk = next(chunks, None)
        last = chunk is None
        text = carry + chunk if not last else carry
        pieces = text.split(term)
        carry = "" if last else pieces.pop()
        out = []
        for raw in pieces:
            seg = raw.strip(strip_chars)
            if seg:
                out.append((pos + (len(raw) - len(raw.lstrip("\r\n")) if raw[0] in "\r\n" else 0), seg.split(el), seg))
            pos += len(raw) + 1
        yield out, control.search(text) is not None


def _proc(composite, comp) -> list:
    parts = (composite or "").split(comp) + [None] * 8
    return [p or None for p in parts[:8]]


class _State:
    def __init__(self):
        self.ic = self.gp = self.tx = self.kind = self.loop = None
        self.ic_row = self.gp_row = self.tx_row = None
        self.hl20 = self.hl22 = self.hl23 = self.hl = None
        self.claim = self.line = self.other_payer = self.svd = None
        self.pay = self.clp = self.svc = None
        self.ent = None
        self.n_claim = self.n_clp = self.n_svc = self.n_hi = self.code_seq = self.cas_n = self.n_op = 0
        self.st_ord = 0
        self.hl_uid_by_hl01 = {}       # latest HL with each HL01 in the file: the HL02 parent lookup
        self.n_interchanges = self.n_transactions = 0
        self.txn_kinds = set()


def parse_file(data: str | bytes, file_id: str, tenant: str, s3: dict, ingest_run_id: str, ingested_at: datetime,
               sink: TableRows | None = None) -> dict[str, list]:
    """Parse one raw file (the bytes as received, or text) into sink (a fresh TableRows by default) and return
    sink.rows. Raises ParseError, or UnicodeDecodeError for non-ASCII bytes, if the file must be quarantined."""
    sink = sink if sink is not None else TableRows()
    try:
        _parse(data, file_id, tenant, s3, ingest_run_id, ingested_at, sink)
    except (ParseError, UnicodeDecodeError):
        raise
    except (IndexError, KeyError, ValueError, TypeError, AttributeError) as e:
        raise ParseError(f"malformed segment: {type(e).__name__}: {e}") from e
    return sink.rows


def _parse(data, file_id, tenant, s3, ingest_run_id, ingested_at, sink: TableRows):
    chunks = iter_segment_chunks(data)
    el_sep, comp, rep, term = next(chunks)
    add, add_segment = sink.add, sink.rows["edi.segment"].append
    st = _State()
    so = 0
    for segments, json_elements in chunks:
        for offset, e, raw in segments:
            sid = e[0].strip()
            u = f"{file_id}:{so}"
            interchange_uid, group_uid, txn_uid = st.ic, st.gp, st.tx
            if sid == "ISA":
                if len(e) != 17:
                    raise ParseError(f"ISA has {len(e) - 1} elements")
                st.ic = interchange_uid = u
                st.n_interchanges += 1
                st.ic_row = add["edi.interchange"](
                    interchange_uid=u, file_id=file_id, seg_ord=so, element_separator=el_sep, component_separator=comp,
                    repetition_separator=rep, segment_terminator=term, isa01_auth_qual=e[1], isa02_auth_info=e[2],
                    isa03_sec_qual=e[3], isa04_sec_info=e[4], isa05_sender_qual=e[5], isa06_sender_id=e[6].strip(),
                    isa07_receiver_qual=e[7], isa08_receiver_id=e[8].strip(), isa09_date=e[9], isa10_time=e[10],
                    isa12_version=e[12], isa13_control_number=e[13], isa14_ack_requested=e[14],
                    isa15_usage_indicator=e[15], interchange_datetime=datetime.strptime(e[9] + e[10], "%y%m%d%H%M"))
            elif sid == "IEA":
                if st.ic is None or st.gp is not None:
                    raise ParseError("IEA without ISA, or functional group not closed")
                if e[2] != st.ic_row["isa13_control_number"]:
                    raise ParseError("IEA02 does not match ISA13")
                st.ic_row.update(iea01_group_count=int(e[1]), iea02_control_number=e[2], iea_seg_ord=so)
                st.ic = None
            elif sid == "GS":
                st.gp = u
                st.gp_row = add["edi.functional_group"](
                    group_uid=u, interchange_uid=st.ic, file_id=file_id, seg_ord=so, gs01_functional_id=e[1],
                    gs02_sender_code=e[2], gs03_receiver_code=e[3], gs04_date=e[4], gs05_time=e[5],
                    gs06_control_number=e[6], gs07_agency=e[7], gs08_version=e[8])
            elif sid == "GE":
                if st.gp is None or st.tx is not None:
                    raise ParseError("GE without GS, or transaction set not closed")
                if e[2] != st.gp_row["gs06_control_number"]:
                    raise ParseError("GE02 does not match GS06")
                st.gp_row.update(ge01_txn_count=int(e[1]), ge02_control_number=e[2], ge_seg_ord=so)
                st.gp = None
            elif sid == "ST":
                st.tx, st.kind, st.st_ord, st.loop = u, e[1], so, "HEADER"
                txn_uid = u
                st.tx_row = add["edi.transaction_set"](
                    txn_uid=u, group_uid=st.gp, file_id=file_id, seg_ord=so, seg_ord_end=so, txn_type=e[1], st01=e[1],
                    st02_control_number=e[2], st03_impl_convention=_el(e, 3))
                st.n_transactions += 1
                st.txn_kinds.add(e[1])
                st.hl20 = st.hl22 = st.hl23 = st.hl = st.claim = st.line = st.other_payer = st.svd = None
                st.clp = st.svc = st.ent = None
                st.n_claim = st.n_clp = 0
                st.pay = add["r835.payment"](payment_uid=u + "/pay", txn_uid=u, file_id=file_id, seg_ord=so) \
                    if e[1] == "835" else None
            elif sid == "SE":
                if st.tx is None:
                    raise ParseError("SE without ST")
                if e[2] != st.tx_row["st02_control_number"]:
                    raise ParseError("SE02 does not match ST02")
                for obj in (st.line, st.claim, st.svc, st.clp):
                    if obj is not None:
                        obj["seg_ord_end"] = so - 1
                st.tx_row.update(seg_ord_end=so, se01_segment_count=int(e[1]), se02_control_number=e[2],
                                 count_check_ok=int(e[1]) == so - st.st_ord + 1)
                st.tx = st.kind = st.loop = None
            elif st.tx is None:
                raise ParseError(f"segment {sid} outside a transaction set")
            elif st.kind == "837":
                _seg_837(st, add, e, sid, so, u, file_id, comp)
            elif st.kind == "835":
                _seg_835(st, add, e, sid, so, u, file_id, comp)
            else:
                raise ParseError(f"unsupported transaction set {st.kind}")
            add_segment((file_id, so, interchange_uid, group_uid, txn_uid, sid, st.loop, raw, offset, el_sep,
                         json.dumps(e[1:]) if json_elements else None))
            so += 1
        sink.after_chunk()
    if st.ic is not None or st.gp is not None or st.tx is not None:
        raise ParseError("file ends inside an envelope (missing SE/GE/IEA)")
    if not st.n_transactions:
        raise ParseError("no transaction sets")
    kinds = st.txn_kinds
    add["edi.source_file"](file_id=file_id, tenant_code=tenant,
                           file_kind="837P" if kinds == {"837"} else "/".join(sorted(kinds)),
                           ingest_run_id=ingest_run_id, ingested_at=ingested_at, parse_status="PARSED",
                           segment_count=so, interchange_count=st.n_interchanges,
                           transaction_count=st.n_transactions, **s3)


# --- shared children --------------------------------------------------------------------------------
def _entity(st, add, e, so, u, file_id, owner, owner_table, loop, sid):
    r = add["edi.entity"](entity_uid=u, txn_uid=st.tx, owner_uid=owner, owner_table=owner_table, file_id=file_id,
            seg_ord=so, loop_id=loop, segment_id=sid, entity_id_code=e[1])
    if sid == "NM1":
        r.update(entity_type=_el(e, 2), name_last_or_org=_el(e, 3), name_first=_el(e, 4), name_middle=_el(e, 5),
                 name_prefix=_el(e, 6), name_suffix=_el(e, 7), id_qualifier=_el(e, 8), id_value=_el(e, 9),
                 nm110=_el(e, 10), nm111=_el(e, 11), nm112=_el(e, 12))
    else:
        r.update(name_last_or_org=_el(e, 2), id_qualifier=_el(e, 3), id_value=_el(e, 4))
    r["segment_ords"] = [so]                                  # NM1/N1 plus the N3/N4/DMG/PRV folded into it
    st.ent = r
    return r


def _child(st, add, e, sid, so, u, file_id, owner, owner_table) -> bool:
    """Segments that hang off the current entity or loop owner. Returns False if not handled."""
    ent, tx = st.ent, st.tx
    if sid in ("N3", "N4", "DMG", "PRV") and ent:
        ent["segment_ords"].append(so)
    if sid == "N3" and ent:
        ent.update(addr_line1=_el(e, 1), addr_line2=_el(e, 2))
    elif sid == "N4" and ent:
        ent.update(city=_el(e, 1), state=_el(e, 2), postal_code=_el(e, 3), country=_el(e, 4),
                   location_qualifier=_el(e, 5), location_id=_el(e, 6), country_subdivision=_el(e, 7))
    elif sid == "DMG" and ent:
        ent.update(dmg_format=_el(e, 1), dmg_birth_date=_d8(_el(e, 2)), dmg_gender=_el(e, 3))
    elif sid == "PRV" and ent:
        ent.update(prv_provider_code=_el(e, 1), prv_ref_qualifier=_el(e, 2), prv_taxonomy=_el(e, 3))
    elif sid == "REF":
        add["edi.reference"](ref_uid=u, txn_uid=tx, owner_uid=owner, owner_table=owner_table, file_id=file_id,
            seg_ord=so, loop_id=st.loop, qualifier=e[1], value=_el(e, 2), description=_el(e, 3), ref04_raw=_el(e, 4))
    elif sid in ("DTP", "DTM"):
        fmt, val = (e[2], e[3]) if sid == "DTP" else ("D8", e[2])
        start = _d8(val)
        end = _d8(val.split("-")[1]) if fmt == "RD8" and "-" in val else start
        add["edi.date_time"](dt_uid=u, txn_uid=tx, owner_uid=owner, owner_table=owner_table, file_id=file_id, seg_ord=so,
            loop_id=st.loop, segment_id=sid, qualifier=e[1], format_qualifier=fmt, raw_value=val, date_start=start,
            date_end=end, time_value=_el(e, 3) if sid == "DTM" else None)
    elif sid == "AMT":
        add["edi.amount"](amt_uid=u, txn_uid=tx, owner_uid=owner, owner_table=owner_table, file_id=file_id, seg_ord=so,
            loop_id=st.loop, qualifier=e[1], amount=_num(_el(e, 2)), raw_amount=_el(e, 2), credit_debit_flag=_el(e, 3))
    elif sid == "PER":
        add["edi.contact"](contact_uid=u, txn_uid=tx, owner_uid=owner, owner_table=owner_table, file_id=file_id,
            seg_ord=so, loop_id=st.loop, function_code=e[1], contact_name=_el(e, 2), comm1_qual=_el(e, 3),
            comm1_value=_el(e, 4), comm2_qual=_el(e, 5), comm2_value=_el(e, 6), comm3_qual=_el(e, 7),
            comm3_value=_el(e, 8))
    else:
        return False
    return True


def _cas(st, add, e, so, u, file_id, owner, owner_table, level):
    st.cas_n += 1
    for ti in range(6):
        reason = _el(e, 2 + ti * 3)
        if reason is None:
            continue
        amt, qty = _el(e, 3 + ti * 3), _el(e, 4 + ti * 3)
        add["edi.adjustment"](adj_uid=f"{u}:{ti + 1}", txn_uid=st.tx, txn_type=st.kind, owner_uid=owner,
            owner_table=owner_table, adj_level=level, file_id=file_id, seg_ord=so, loop_id=st.loop, cas_ordinal=st.cas_n,
            triplet_index=ti + 1, group_code=e[1], reason_code=reason, amount=_num(amt), quantity=_num(qty),
            raw_amount=amt, raw_quantity=qty)


def _misc(st, add, e, sid, so, u, file_id, owner, owner_table):
    add["edi.misc_segment"](misc_uid=u, txn_uid=st.tx, owner_uid=owner, owner_table=owner_table, file_id=file_id,
        seg_ord=so, loop_id=st.loop, segment_id=sid, elements=json.dumps(e[1:]))


def _close(obj, so):
    if obj is not None:
        obj["seg_ord_end"] = so - 1


# --- 837P -----------------------------------------------------------------------------------------
def _seg_837(st, add, e, sid, so, u, file_id, comp):
    tx = st.tx
    if sid == "BHT":
        st.tx_row.update(bht01_structure_code=e[1], bht02_purpose_code=e[2], bht03_reference_id=e[3],
                         bht04_date=_d8(e[4]), bht05_time=_el(e, 5), bht06_claim_or_encounter=_el(e, 6))
        return
    if sid == "HL":
        _close(st.line, so)
        _close(st.claim, so)
        st.line = st.claim = st.other_payer = st.svd = st.ent = None
        level = e[3]
        st.loop = {"20": "2000A", "22": "2000B", "23": "2000C"}.get(level, "2000")
        parent = st.hl_uid_by_hl01.get(_el(e, 2))
        st.hl_uid_by_hl01[e[1]] = u
        st.hl = add["c837.hl"](hl_uid=u, txn_uid=tx, file_id=file_id, seg_ord=so, loop_id=st.loop, hl01_id=e[1],
                    hl02_parent_id=_el(e, 2), hl03_level_code=level, hl04_child_code=_el(e, 4), parent_hl_uid=parent)
        if level == "20":
            st.hl20, st.hl22, st.hl23 = u, None, None
        elif level == "22":
            st.hl22, st.hl23 = u, None
        elif level == "23":
            st.hl23 = u
        return
    if sid == "SBR":
        f = dict(sbr01_payer_resp_seq=_el(e, 1), sbr02_relationship=_el(e, 2), sbr03_group_number=_el(e, 3),
                 sbr04_group_name=_el(e, 4), sbr05_insurance_type=_el(e, 5), sbr06=_el(e, 6), sbr07=_el(e, 7),
                 sbr08=_el(e, 8), sbr09_filing_indicator=_el(e, 9))
        if st.claim is None:
            st.hl.update(f)
        else:                                                     # 2320: other subscriber / other payer
            _close(st.line, so)
            st.line = st.svd = st.ent = None
            st.n_op += 1
            st.loop = "2320"
            st.cas_n = 0
            st.other_payer = add["c837.other_payer"](other_payer_uid=u, claim_uid=st.claim["claim_uid"], txn_uid=tx,
                                 file_id=file_id, seg_ord=so, ordinal=st.n_op, **f)
        return
    if sid == "PAT":
        st.hl.update(pat01_relationship=_el(e, 1), pat02=_el(e, 2), pat03=_el(e, 3), pat04=_el(e, 4),
                     pat05_date_format=_el(e, 5), pat06_death_date=_el(e, 6), pat07_weight_unit=_el(e, 7),
                     pat08_weight=_el(e, 8), pat09_pregnancy=_el(e, 9))
        return
    if sid == "NM1":
        code = e[1]
        if st.other_payer is not None and st.line is None:
            loop, owner, ot = ("2330B" if code == "PR" else "2330A"), st.other_payer["other_payer_uid"], "c837.other_payer"
        elif st.line is not None:
            loop, owner, ot = "2420", st.line["line_uid"], "c837.service_line"
        elif st.claim is not None:
            loop, owner, ot = LOOP_837_NM1.get(code, "2310"), st.claim["claim_uid"], "c837.claim"
        elif st.hl is not None:
            loop, owner, ot = LOOP_837_NM1.get(code, st.loop), st.hl["hl_uid"], "c837.hl"
        else:
            loop, owner, ot = LOOP_837_NM1.get(code, "1000"), tx, "edi.transaction_set"
        st.loop = loop
        _entity(st, add, e, so, u, file_id, owner, ot, loop, sid)
        return
    if sid == "CLM":
        st.n_claim += 1
        st.n_hi = st.code_seq = st.n_op = st.cas_n = 0
        c05 = (e[5].split(comp) + [None, None, None])[:3]
        st.loop, st.ent, st.other_payer, st.line = "2300", None, None, None
        st.claim = add["c837.claim"](claim_uid=u, txn_uid=tx, file_id=file_id, seg_ord=so, seg_ord_end=so,
                       claim_ord_in_txn=st.n_claim, billing_hl_uid=st.hl20, subscriber_hl_uid=st.hl22,
                       patient_hl_uid=st.hl23, clm01_patient_control_number=e[1], clm02_total_charge=_num(e[2]),
                       clm02_raw=e[2], clm03=_el(e, 3), clm04=_el(e, 4), clm05_raw=e[5], clm05_1_place_of_service=c05[0],
                       clm05_2_facility_qualifier=c05[1], clm05_3_frequency_code=c05[2], clm06_provider_signature=_el(e, 6),
                       clm07_assignment_participation=_el(e, 7), clm08_benefits_assignment=_el(e, 8),
                       clm09_release_of_info=_el(e, 9), clm10_patient_signature_source=_el(e, 10),
                       clm11_raw=_el(e, 11), clm12_special_program=_el(e, 12), clm20_delay_reason=_el(e, 20), line_count=0)
        return
    if sid == "HI":
        st.n_hi += 1
        for pos, composite in enumerate(e[1:], 1):
            if not composite:
                continue
            q, _, code = composite.partition(comp)
            st.code_seq += 1
            add["c837.claim_code"](claim_code_uid=f"{u}:{pos}", claim_uid=st.claim["claim_uid"], txn_uid=tx,
                file_id=file_id, seg_ord=so, hi_segment_ordinal=st.n_hi, element_position=pos,
                code_sequence=st.code_seq, code_list_qualifier=q, code=code.split(comp)[0], raw_composite=composite)
        return
    if sid == "OI" and st.other_payer is not None:
        st.other_payer.update(oi03_benefits_assignment=_el(e, 3), oi04_patient_signature=_el(e, 4),
                              oi06_release_of_info=_el(e, 6), oi_other_elements=json.dumps(e[1:]))
        return
    if sid == "LX":
        _close(st.line, so)
        st.loop, st.ent, st.svd, st.other_payer = "2400", None, None, None
        st.line = add["c837.service_line"](line_uid=u, claim_uid=st.claim["claim_uid"], txn_uid=tx, file_id=file_id,
                      seg_ord=so, seg_ord_end=so, lx01_line_number=int(e[1]), sv101_raw="")
        st.claim["line_count"] += 1
        return
    if sid == "SV1":
        p = _proc(e[1], comp)
        st.line.update(sv101_raw=e[1], sv101_1_qualifier=p[0], sv101_2_procedure_code=p[1], sv101_3_mod1=p[2],
                       sv101_4_mod2=p[3], sv101_5_mod3=p[4], sv101_6_mod4=p[5], sv101_7_description=p[6], sv101_8=p[7],
                       sv102_charge=_num(_el(e, 2)), sv102_raw=_el(e, 2), sv103_unit_basis=_el(e, 3),
                       sv104_units=_num(_el(e, 4)), sv104_raw=_el(e, 4), sv105_place_of_service=_el(e, 5),
                       sv106=_el(e, 6), sv107_raw=_el(e, 7), sv109_emergency=_el(e, 9),
                       sv107_dx_pointers=[int(x) for x in _el(e, 7).split(comp) if x.isdigit()] if _el(e, 7) else None)
        return
    if sid == "SVD":
        p = _proc(_el(e, 3), comp)
        st.loop, st.cas_n = "2430", 0
        st.svd = add["c837.line_adjudication"](line_adj_uid=u, line_uid=st.line["line_uid"], txn_uid=tx, file_id=file_id,
                     seg_ord=so, svd01_other_payer_id=_el(e, 1), svd02_paid=_num(_el(e, 2)), svd03_raw=_el(e, 3),
                     svd03_qualifier=p[0], svd03_procedure_code=p[1], svd03_mod1=p[2], svd03_mod2=p[3], svd03_mod3=p[4],
                     svd03_mod4=p[5], svd03_description=p[6], svd04=_el(e, 4), svd05_paid_units=_num(_el(e, 5)),
                     svd06_bundled_line_number=_el(e, 6))
        return
    if sid == "CAS":
        if st.svd is not None:
            _cas(st, add, e, so, u, file_id, st.svd["line_adj_uid"], "c837.line_adjudication", "LINE")
        elif st.other_payer is not None:
            _cas(st, add, e, so, u, file_id, st.other_payer["other_payer_uid"], "c837.other_payer", "CLAIM")
        else:
            _misc(st, add, e, sid, so, u, file_id, st.claim["claim_uid"], "c837.claim")
        return
    # owner for shared children
    if st.svd is not None:
        owner, ot = st.svd["line_adj_uid"], "c837.line_adjudication"
    elif st.line is not None:
        owner, ot = st.line["line_uid"], "c837.service_line"
    elif st.other_payer is not None:
        owner, ot = st.other_payer["other_payer_uid"], "c837.other_payer"
    elif st.claim is not None:
        owner, ot = st.claim["claim_uid"], "c837.claim"
    elif st.hl is not None:
        owner, ot = st.hl["hl_uid"], "c837.hl"
    else:
        owner, ot = tx, "edi.transaction_set"
    if sid in ("REF", "DTP", "PER") and st.ent is not None and st.loop.startswith(("1000", "2010", "2330")):
        owner, ot = st.ent["entity_uid"], "edi.entity"
    if not _child(st, add, e, sid, so, u, file_id, owner, ot):
        _misc(st, add, e, sid, so, u, file_id, owner, ot)
    if sid == "REF" and e[1] == "6R" and st.line is not None and st.svd is None:
        st.line["line_item_control_number"] = _el(e, 2)


# --- 835 ------------------------------------------------------------------------------------------
_BPR = ["bpr01_transaction_handling", "bpr02_raw", "bpr03_credit_debit", "bpr04_payment_method", "bpr05_payment_format",
        "bpr06_sender_dfi_qual", "bpr07_sender_dfi_id", "bpr08_sender_acct_qual", "bpr09_sender_account",
        "bpr10_originating_company_id", "bpr11_originating_company_supplemental", "bpr12_receiver_dfi_qual",
        "bpr13_receiver_dfi_id", "bpr14_receiver_acct_qual", "bpr15_receiver_account"]


def _seg_835(st, add, e, sid, so, u, file_id, comp):
    tx, pay = st.tx, st.pay
    if sid == "BPR":
        pay.update({k: _el(e, i) for i, k in enumerate(_BPR, 1)}, seg_ord=so, bpr02_total_paid=_num(e[2]),
                   bpr16_effective_date=_d8(_el(e, 16)), bpr17_business_function=_el(e, 17))
        return
    if sid == "TRN":
        pay.update(trn01_trace_type=e[1], trn02_check_eft_number=e[2], trn03_payer_id=_el(e, 3),
                   trn04_originating_supplemental=_el(e, 4))
        return
    if sid == "CUR":
        pay["cur_elements"] = json.dumps(e[1:])
        return
    if sid == "N1":
        st.loop = {"PR": "1000A", "PE": "1000B"}.get(e[1], "1000")
        _entity(st, add, e, so, u, file_id, pay["payment_uid"], "r835.payment", st.loop, sid)
        return
    if sid == "LX":
        _close(st.svc, so)
        _close(st.clp, so)
        st.loop, st.ent, st.svc, st.clp = "2000", None, None, None
        _misc(st, add, e, sid, so, u, file_id, pay["payment_uid"], "r835.payment")
        return
    if sid == "CLP":
        _close(st.svc, so)
        _close(st.clp, so)
        st.svc, st.ent = None, None
        st.n_clp += 1
        st.n_svc = st.cas_n = 0
        st.loop = "2100"
        st.clp = add["r835.claim_payment"](claim_payment_uid=u, payment_uid=pay["payment_uid"], txn_uid=tx,
                     file_id=file_id, seg_ord=so, seg_ord_end=so, clp_ord_in_txn=st.n_clp,
                     clp01_patient_control_number=e[1], clp02_claim_status=e[2], clp03_total_charge=_num(e[3]),
                     clp04_total_paid=_num(e[4]), clp05_patient_responsibility=_num(_el(e, 5)),
                     clp06_filing_indicator=_el(e, 6), clp07_payer_claim_control_number=_el(e, 7),
                     clp08_facility_type=_el(e, 8), clp09_frequency_code=_el(e, 9), clp10=_el(e, 10),
                     clp11_drg_code=_el(e, 11), clp12_drg_weight=_num(_el(e, 12)),
                     clp13_discharge_fraction=_num(_el(e, 13)), clp14=_el(e, 14), clp15=_el(e, 15),
                     raw_clp03=e[3], raw_clp04=e[4], raw_clp05=_el(e, 5))
        return
    if sid in ("MOA", "MIA") and st.clp is not None:
        add["r835.medicare_adjudication"](medicare_adj_uid=u, claim_payment_uid=st.clp["claim_payment_uid"], txn_uid=tx,
            file_id=file_id, seg_ord=so, segment_id=sid, elements=json.dumps(e[1:]),
            reimbursement_rate=_num(_el(e, 1)) if sid == "MOA" else None,
            hcpcs_payable_amount=_num(_el(e, 2)) if sid == "MOA" else None)
        return
    if sid == "NM1" and st.clp is not None:
        _entity(st, add, e, so, u, file_id, st.clp["claim_payment_uid"], "r835.claim_payment", "2100", sid)
        return
    if sid == "SVC":
        _close(st.svc, so)
        st.n_svc += 1
        st.cas_n = 0
        st.loop, st.ent = "2110", None
        p, p6 = _proc(e[1], comp), _proc(_el(e, 6), comp)
        st.svc = add["r835.service_payment"](service_payment_uid=u, claim_payment_uid=st.clp["claim_payment_uid"],
                     txn_uid=tx, file_id=file_id, seg_ord=so, seg_ord_end=so, svc_ord_in_claim=st.n_svc,
                     svc01_raw=e[1], svc01_1_qualifier=p[0], svc01_2_procedure_code=p[1], svc01_3_mod1=p[2],
                     svc01_4_mod2=p[3], svc01_5_mod3=p[4], svc01_6_mod4=p[5], svc01_7_description=p[6], svc01_8=p[7],
                     svc02_charge=_num(e[2]), svc03_paid=_num(_el(e, 3)), svc04_revenue_code=_el(e, 4),
                     svc05_units_paid=_num(_el(e, 5)), svc06_raw=_el(e, 6), svc06_1_qualifier=p6[0],
                     svc06_2_procedure_code=p6[1], svc06_3_mod1=p6[2], svc06_4_mod2=p6[3], svc06_5_mod3=p6[4],
                     svc06_6_mod4=p6[5], svc06_7_description=p6[6], svc07_original_units=_num(_el(e, 7)),
                     raw_svc02=e[2], raw_svc03=_el(e, 3))
        return
    if sid == "CAS":
        if st.svc is not None:
            _cas(st, add, e, so, u, file_id, st.svc["service_payment_uid"], "r835.service_payment", "LINE")
        else:
            _cas(st, add, e, so, u, file_id, st.clp["claim_payment_uid"], "r835.claim_payment", "CLAIM")
        return
    if sid == "LQ" and st.svc is not None:
        add["r835.remark"](remark_uid=u, owner_uid=st.svc["service_payment_uid"], owner_table="r835.service_payment",
            remark_level="LINE", txn_uid=tx, file_id=file_id, seg_ord=so, source_segment="LQ", source_element="LQ02",
            code_list_qualifier=e[1], remark_code=e[2])
        return
    if sid == "PLB":
        _close(st.svc, so)
        _close(st.clp, so)
        st.svc = st.clp = None
        st.loop = "SUMMARY"
        for pi in range(6):
            reason = _el(e, 3 + pi * 2)
            if reason is None:
                continue
            code, _, ref = reason.partition(comp)
            add["r835.provider_adjustment"](plb_uid=f"{u}:{pi + 1}", payment_uid=pay["payment_uid"], txn_uid=tx,
                file_id=file_id, seg_ord=so, plb01_provider_id=e[1], plb02_fiscal_period_date=_d8(e[2]),
                pair_index=pi + 1, reason_raw=reason, reason_code=code, reference_id=ref or None,
                amount=_num(_el(e, 4 + pi * 2)), raw_amount=_el(e, 4 + pi * 2))
        return
    if st.svc is not None:
        owner, ot = st.svc["service_payment_uid"], "r835.service_payment"
    elif st.clp is not None:
        owner, ot = st.clp["claim_payment_uid"], "r835.claim_payment"
    else:
        owner, ot = pay["payment_uid"], "r835.payment"
    if sid in ("REF", "PER") and st.ent is not None and st.loop in ("1000A", "1000B"):
        owner, ot = st.ent["entity_uid"], "edi.entity"
    if not _child(st, add, e, sid, so, u, file_id, owner, ot):
        _misc(st, add, e, sid, so, u, file_id, owner, ot)
    if sid == "REF" and e[1] == "6R" and st.svc is not None:
        st.svc["line_item_control_number"] = _el(e, 2)

"""edi.segment keeps only where each segment is in bronze (byte_offset, byte_length), not its text or elements.
That is lossless because bronze is immutable (Object Lock) and

  1. bronze bytes [byte_offset, byte_offset + byte_length) are the segment as received, for every segment of every
     file, delivery noise included (BOM, leading whitespace, 80-column wrapping, NUL / space padding, several
     interchanges per file), as the independent validator (ccsim.x12_validate) splits the file;
  2. every element value of every segment is still in silver: in a typed row made or filled from that segment,
     or in edi.misc_segment;
  3. for a 277CA, stronger: every segment is rebuilt from silver alone, element for element, in order.
"""
import bisect
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import pytest

from ccsim.edi_mock import Scenario, generate
from ccsim.parser import SEGMENT_FIELDS, TABLES
from ccsim.silver_staging import RawFile, SilverBatch, file_id_of
from ccsim.x12_validate import tokenize

# Segments whose elements fill the latest row of another table rather than a row of their own (see ccsim.parser).
FOLDED_INTO_LATEST = {"BHT": "edi.transaction_set", "SBR": "c837.hl", "PAT": "c837.hl", "SV1": "c837.service_line",
                      "OI": "c837.other_payer", "BPR": "r835.payment", "TRN": "r835.payment", "CUR": "r835.payment"}
BOM = b"\xef\xbb\xbf"
# Columns that point at a segment other than the row's own seg_ord.
OTHER_SEGMENT_COLUMNS = ("iea_seg_ord", "ge_seg_ord", "seg_ord_end")


def _handwritten_277ca() -> bytes:
    """What the mock sends rarely or never: a clearinghouse as information source, a date range, all three status
    composites with a code list qualifier and free text, a service line with modifiers, and two segments without
    a typed home (they go to edi.misc_segment)."""
    body = """BHT*0085*08*ACK1*20260708*0327*TH
HL*1**20*1
NM1*AY*2*CC CLEARINGHOUSE*****46*CCCH01
TRN*1*ACK1
DTP*050*D8*20260707
DTP*009*D8*20260708
HL*2*1*21*1
NM1*41*2*ACME LABS INC*****46*561234567
TRN*2*BATCH1
STC*A1:19:PR*20260708*WQ*300
QTY*90*1
QTY*AA*1
AMT*YU*100
AMT*YY*200
HL*3*2*19*1
NM1*85*2*ACME LABS INC*****XX*1705546145
TRN*1*0
HL*4*3*PT
NM1*QC*1*DOE*JANE*Q***MI*W123456789
TRN*2*PCN1
STC*A2:20:PR*20260708*WQ*100
REF*1K*ICN123
DTP*472*RD8*20260701-20260702
HL*5*3*PT
NM1*QC*1*ROE*RICHARD****MI*W987654321
TRN*2*PCN2
STC*A3:21*20260708*U*200******A7:562:DN*A8:128:85:RX*SEE THE SERVICE LINE
REF*D9*TRACE2
DTP*472*D8*20260701
SVC*HC:87086:59:91*45*****1
STC*A7:507*20260708*U*45
REF*FJ*PCN201
DTP*472*D8*20260701
NTE*ADD*NOT A 277CA SEGMENT
QTY*QA*1*UN*A QTY04 HAS NO COLUMN""".split("\n")
    segments = ["ISA*00*          *00*          *ZZ*CC             *ZZ*ACMELABS       *260708*0327*^*00501*000000001*0*P*:",
                "GS*HN*CC*ACMELABS*20260708*032731*1*X*005010X214", "ST*277*0001*005010X214", *body,
                f"SE*{len(body) + 2}*0001", "GE*1*1", "IEA*1*000000001"]
    return "".join(segment + "~\n" for segment in segments).encode()


def _noise_variants(body: bytes) -> dict[str, bytes]:
    flat = body.replace(b"\r", b"").replace(b"\n", b"")
    return {"bom": b"\xef\xbb\xbf" + body, "leading_whitespace": b" \r\n\t" + body, "nul_padding": body + b"\x00" * 77,
            "space_padding": body + b" " * 80, "two_interchanges": body + body,
            "wrapped_80": b"\n".join(flat[i:i + 80] for i in range(0, len(flat), 80)) + b"\n",
            "wrapped_80_crlf": b"\r\n".join(flat[i:i + 80] for i in range(0, len(flat), 80)) + b"\r\n"}


@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    out = tmp_path_factory.mktemp("lossless")
    truth = generate(out, Scenario.stress(days=10, volume_scale=.3, seed=3), ["acme_labs"], return_claims=False)
    files = []
    for f in truth["tenants"]["acme_labs"]["files"]:
        if f["expect"] != "rejected":
            key = f"acme_labs/raw/received_date={f['day']}/{f['kind']}/{f['name']}"
            files.append((key, (out / key).read_bytes()))
    plain = [b for _, b in files if b.startswith(b"ISA") and b"\x00" not in b and b"\n" not in b[:106]]
    for kind_marker in (b"*837*", b"*835*", b"*277*"):
        body = min((b for b in plain if kind_marker in b[:300]), key=len)
        files += [(f"noise/{name}{kind_marker.decode()}", v) for name, v in _noise_variants(body).items()]
    files.append(("handwritten/277ca", _handwritten_277ca()))
    batch = SilverBatch("r1", datetime(2026, 1, 1), triggered_by="test", notes=None, parser_version="test")
    raws = [RawFile(k, b, file_id_of(k, "v1"), dict(s3_uri=k, s3_bucket="b", s3_key=k, size_bytes=len(b), sha256=k))
            for k, b in files]
    assert all(ok for _, ok in batch.add_raw_files(raws, "acme_labs", {}))
    con = batch.finish()
    tables = {t: con.sql(f"SELECT * FROM {t}").to_arrow_table().to_pylist() for t in TABLES}
    return raws, tables


def test_segment_columns_are_location_only():
    assert "raw_text" not in SEGMENT_FIELDS and "byte_length" in SEGMENT_FIELDS


def test_bronze_bytes_at_each_segments_location_are_the_segment_as_received(sample):
    raws, tables = sample
    by_file, terminators = {}, {i["file_id"]: i["segment_terminator"] for i in tables["edi.interchange"]}
    for s in tables["edi.segment"]:
        by_file.setdefault(s["file_id"], []).append(s)
    for f in raws:
        text = f.body.removeprefix(BOM).decode("ascii")
        bom = len(f.body) - len(text)
        tokens, el, _, _ = tokenize(text)
        term = terminators[f.file_id]
        segments = sorted(by_file[f.file_id], key=lambda s: s["seg_ord"])
        assert len(segments) == len(tokens), f.key
        for s, elements in zip(segments, tokens, strict=True):
            received = f.body[s["byte_offset"]:s["byte_offset"] + s["byte_length"]].decode("ascii")
            unwrapped = received.replace("\r", "").replace("\n", "")      # an 80-column wrap may split a segment
            assert unwrapped == el.join(elements), (f.key, s["seg_ord"])
            assert received == received.strip("\r\n\x00 ") and unwrapped.startswith(s["segment_id"])
            after = f.body[s["byte_offset"] + s["byte_length"]:].lstrip(b"\r\n\x00 ")
            assert after[:1] in (term.encode(), b""), (f.key, s["seg_ord"])   # nothing but noise before the terminator
            assert s["byte_offset"] >= bom


def _values(v) -> set[str]:
    """A stored value as the element strings it can stand for."""
    if v is None or isinstance(v, bool):
        return set()
    if isinstance(v, list):
        return set().union(*map(_values, v)) if v else set()
    if isinstance(v, datetime):
        return {v.strftime("%y%m%d"), v.strftime("%H%M")}
    if isinstance(v, date):
        return {v.strftime("%Y%m%d")}
    if isinstance(v, (int, float, Decimal)):
        return {_number(str(v))}
    return {v, v.strip()}


def _number(v: str) -> str | None:
    try:
        return str(Decimal(v).normalize())
    except InvalidOperation:
        return None


def test_every_element_value_is_still_in_a_typed_table_or_misc_segment(sample):
    raws, tables = sample
    held: dict[tuple, set] = {}                                      # (file_id, seg_ord) -> values of linked rows
    latest: dict[tuple, tuple] = {}                     # (table, file_id) -> (seg_ords, rows), in seg_ord order

    def hold(file_id, seg_ord, row):
        held.setdefault((file_id, seg_ord), set()).update(*map(_values, row.values()))

    for t, rows in tables.items():
        if t in ("edi.segment", "edi.source_file"):
            continue
        for r in rows:
            hold(r["file_id"], r["seg_ord"], r)
            for c in OTHER_SEGMENT_COLUMNS:
                if r.get(c) is not None:
                    hold(r["file_id"], r[c], r)
            for so in r.get("segment_ords") or ():
                hold(r["file_id"], so, r)
        if t in FOLDED_INTO_LATEST.values():
            for r in sorted(rows, key=lambda r: r["seg_ord"]):
                ords, of_file = latest.setdefault((t, r["file_id"]), ([], []))
                ords.append(r["seg_ord"])
                of_file.append(r)
    missing = []
    for f in raws:
        tokens, _, _, _ = tokenize(f.body.removeprefix(BOM).decode("ascii"))
        for seg_ord, elements in enumerate(tokens):
            values = set(held.get((f.file_id, seg_ord), ()))
            owner_table = FOLDED_INTO_LATEST.get(elements[0])
            if (owner_table, f.file_id) in latest:                   # the latest such row at or before the segment
                ords, of_file = latest[(owner_table, f.file_id)]     # (a 277CA has no such table: its TRN is a row)
                owner = of_file[bisect.bisect_right(ords, seg_ord) - 1]
                values |= set().union(*map(_values, owner.values()))
            for position, v in enumerate(elements[1:], 1):
                if v and v not in values and v.strip() not in values and _number(v) not in values:
                    missing.append((f.key, seg_ord, f"{elements[0]}{position:02d}", v))
    assert not missing, f"{len(missing)} element values not in any table, e.g. {sorted(set(missing))[:12]}"


# --- 277CA: the file's segments, rebuilt from silver alone ---------------------------------------------------------
def _text(value) -> str:
    return "" if value is None else str(value)


def _columns(*names):
    return lambda row: [_text(row[n]) for n in names]


def _status_information(rows: list) -> list[str]:
    """STC from its a277.status rows (one per composite: STC01, STC10, STC11)."""
    composite = {r["composite_index"]: r["raw_composite"] for r in rows}
    r = rows[0]
    assert all(_columns("raw_status_date", "action_code", "raw_amount", "stc05", "stc06", "stc07", "stc08", "stc09",
                        "free_text")(other) == _columns("raw_status_date", "action_code", "raw_amount", "stc05",
                                                        "stc06", "stc07", "stc08", "stc09", "free_text")(r)
               for other in rows)
    return [composite.get(1, ""), *_columns("raw_status_date", "action_code", "raw_amount", "stc05", "stc06", "stc07",
                                             "stc08", "stc09")(r), composite.get(2, ""), composite.get(3, ""),
            _text(r["free_text"])]


# segment id -> (table whose row has the segment's seg_ord, its elements from that row or rows)
REBUILD_277CA = {
    "ISA": ("edi.interchange", lambda r: [
        r["isa01_auth_qual"], r["isa02_auth_info"], r["isa03_sec_qual"], r["isa04_sec_info"], r["isa05_sender_qual"],
        f"{r['isa06_sender_id']:<15}", r["isa07_receiver_qual"], f"{r['isa08_receiver_id']:<15}", r["isa09_date"],
        r["isa10_time"], r["repetition_separator"], r["isa12_version"], r["isa13_control_number"],
        r["isa14_ack_requested"], r["isa15_usage_indicator"], r["component_separator"]]),
    "GS": ("edi.functional_group", _columns("gs01_functional_id", "gs02_sender_code", "gs03_receiver_code", "gs04_date",
                                            "gs05_time", "gs06_control_number", "gs07_agency", "gs08_version")),
    "ST": ("edi.transaction_set", _columns("st01", "st02_control_number", "st03_impl_convention")),
    "HL": ("c837.hl", _columns("hl01_id", "hl02_parent_id", "hl03_level_code", "hl04_child_code")),
    "NM1": ("edi.entity", _columns("entity_id_code", "entity_type", "name_last_or_org", "name_first", "name_middle",
                                   "name_prefix", "name_suffix", "id_qualifier", "id_value", "nm110", "nm111", "nm112")),
    "TRN": ("a277.status_trace", _columns("trn01_trace_type", "trn02_trace_identifier", "trn03", "trn04")),
    "REF": ("edi.reference", _columns("qualifier", "value", "description", "ref04_raw")),
    "DTP": ("edi.date_time", _columns("qualifier", "format_qualifier", "raw_value")),
    "AMT": ("edi.amount", _columns("qualifier", "raw_amount", "credit_debit_flag")),
    "QTY": ("edi.quantity", _columns("qualifier", "raw_quantity", "qty03_raw")),
    "SVC": ("a277.service_line", _columns("svc01_raw", "raw_svc02", "svc03", "svc04_revenue_code", "svc05", "svc06",
                                          "raw_svc07")),
}
# segments folded into a row of another segment: (table, the column holding this segment's seg_ord, its elements)
REBUILD_277CA_TRAILERS = {
    "IEA": ("edi.interchange", "iea_seg_ord", _columns("iea01_group_count", "iea02_control_number")),
    "GE": ("edi.functional_group", "ge_seg_ord", _columns("ge01_txn_count", "ge02_control_number")),
    "SE": ("edi.transaction_set", "seg_ord_end", _columns("se01_segment_count", "se02_control_number")),
}


def _trimmed(elements: list[str]) -> list[str]:
    """Without trailing empty elements: X12 does not send them."""
    while elements and elements[-1] == "":
        elements = elements[:-1]
    return elements


def test_every_277ca_segment_is_rebuilt_from_silver_exactly(sample):
    raws, tables = sample
    acknowledgment_file_ids = {r["file_id"] for r in tables["edi.source_file"] if r["file_kind"] == "277CA"}
    assert len(acknowledgment_file_ids) > 10
    at = {t: {} for t in tables}                                     # table -> (file_id, seg_ord) -> rows
    for t, rows in tables.items():
        if t not in ("edi.segment", "edi.source_file"):
            for r in rows:
                at[t].setdefault((r["file_id"], r["seg_ord"]), []).append(r)
    for table, seg_ord_column, _ in REBUILD_277CA_TRAILERS.values():
        for r in tables[table]:
            at[table].setdefault(("trailer", r["file_id"], r[seg_ord_column]), []).append(r)
    transaction_sets = {r["txn_uid"]: r for r in tables["edi.transaction_set"]}
    spine = {}
    for s in tables["edi.segment"]:
        if s["file_id"] in acknowledgment_file_ids:
            spine.setdefault(s["file_id"], []).append(s)
    seen_segment_ids, n_segments = set(), 0
    for f in raws:
        if f.file_id not in acknowledgment_file_ids:
            continue
        tokens, _, _, _ = tokenize(f.body.removeprefix(BOM).decode("ascii"))
        segments = sorted(spine[f.file_id], key=lambda s: s["seg_ord"])
        assert len(segments) == len(tokens), f.key
        for s, received in zip(segments, tokens, strict=True):
            segment_id, where = s["segment_id"], (f.file_id, s["seg_ord"])
            if where in at["edi.misc_segment"]:                     # no typed home (or more elements than it has)
                (row,) = at["edi.misc_segment"][where]
                rebuilt = list(row["elements"])
            elif segment_id == "BHT":
                t = transaction_sets[s["txn_uid"]]
                rebuilt = [t["bht01_structure_code"], t["bht02_purpose_code"], t["bht03_reference_id"],
                           f"{t['bht04_date']:%Y%m%d}", _text(t["bht05_time"]), _text(t["bht06_claim_or_encounter"])]
            elif segment_id == "STC":
                rebuilt = _status_information(sorted(at["a277.status"][where], key=lambda r: r["composite_index"]))
            elif segment_id in REBUILD_277CA_TRAILERS:
                table, _, elements = REBUILD_277CA_TRAILERS[segment_id]
                (row,) = at[table][("trailer", *where)]
                rebuilt = elements(row)
            elif segment_id in REBUILD_277CA:
                table, elements = REBUILD_277CA[segment_id]
                (row,) = at[table][where]
                rebuilt = elements(row)
            else:
                raise AssertionError(f"{segment_id} at {where}: no row holds it")
            assert [segment_id] + _trimmed(rebuilt) == _trimmed(received), (f.key, s["seg_ord"])
            seen_segment_ids.add(segment_id)
            n_segments += 1
    assert seen_segment_ids >= set(REBUILD_277CA) | set(REBUILD_277CA_TRAILERS) | {"BHT", "STC"}, seen_segment_ids
    assert n_segments > 1000

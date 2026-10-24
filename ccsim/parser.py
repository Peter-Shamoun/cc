"""Lightweight X12 837P / 835 parser -> rows for the lossless schema (claude/schema/01_lossless_edi_schema.sql).

Covers the tables the semantic layer reads plus the full segment spine (edi.segment holds every segment,
so nothing is lost even where a segment has no typed table yet). Keys are deterministic: <file_id>:<seg_ord>.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime

TABLES = ["edi.source_file", "edi.segment", "edi.interchange", "edi.functional_group", "edi.transaction_set",
          "edi.entity", "edi.reference", "edi.date_time", "edi.amount", "edi.adjustment",
          "c837.hl", "c837.claim", "c837.claim_code", "c837.service_line",
          "r835.payment", "r835.claim_payment", "r835.service_payment", "r835.remark", "r835.provider_adjustment"]

ENTITY_LOOPS_837 = {"41": "1000A", "40": "1000B", "85": "2010AA", "87": "2010AB", "IL": "2010BA", "PR": "2010BB",
                    "QC": "2010CA", "DN": "2310A", "82": "2310B", "77": "2310C", "DQ": "2310D"}
PERSON_CODES = {"IL", "QC", "74", "QD"}           # subscriber / patient entities: PHI


class ParseError(ValueError):
    pass


def _d8(v: str) -> date | None:
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
    return e[i] if len(e) > i and e[i] != "" else None


def split_segments(text: str) -> tuple[list[tuple[int, list[str], str]], str, str, str]:
    if not text.startswith("ISA") or len(text) < 106:
        raise ParseError("not an X12 interchange (no ISA)")
    el, comp, term = text[3], text[104], text[105]
    out, pos = [], 0
    for raw in text.split(term):
        stripped = raw.strip("\r\n")
        offset = pos + (len(raw) - len(raw.lstrip("\r\n")))
        pos += len(raw) + 1
        if stripped:
            out.append((offset, stripped.split(el), stripped))
    return out, el, comp, term


def parse_file(text: str, file_id: str, tenant: str, s3: dict, ingest_run_id: str, ingested_at: datetime) -> dict:
    rows: dict[str, list[dict]] = defaultdict(list)
    segs, el_sep, comp, term = split_segments(text)
    add = lambda t, **r: rows[t].append(r) or r
    uid = lambda o: f"{file_id}:{o}"
    st = dict(ic=None, gp=None, tx=None, txn_type=None, loop=None, hl20=None, hl22=None, hl=None, claim=None,
              line=None, pay=None, clp=None, svc=None, ent=None, n_claim=0, n_clp=0, n_svc=0, n_hi=0,
              code_seq=0, cas_n=0)
    kind = None

    def close(key, end_ord, table, field="seg_ord_end"):
        r = st.get(key)
        if r is not None:
            r[field] = end_ord
        st[key] = None

    for o, e, raw in segs:
        sid = e[0]
        add("edi.segment", file_id=file_id, seg_ord=len(rows["edi.segment"]), interchange_uid=st["ic"],
            group_uid=st["gp"], txn_uid=st["tx"], segment_id=sid, loop_id=st["loop"], elements=json.dumps(e[1:]),
            raw_text=raw, byte_offset=o)
        so = len(rows["edi.segment"]) - 1
        u = uid(so)
        tx = st["tx"]
        if sid == "ISA":
            st["ic"] = u
            rows["edi.segment"][-1]["interchange_uid"] = u
            st["ic_row"] = add("edi.interchange", interchange_uid=u, file_id=file_id, seg_ord=so, element_separator=el_sep,
                               component_separator=comp, repetition_separator=_el(e, 11), segment_terminator=term,
                               isa01_auth_qual=e[1], isa02_auth_info=e[2], isa03_sec_qual=e[3], isa04_sec_info=e[4],
                               isa05_sender_qual=e[5], isa06_sender_id=e[6].strip(), isa07_receiver_qual=e[7],
                               isa08_receiver_id=e[8].strip(), isa09_date=e[9], isa10_time=e[10], isa12_version=e[12],
                               isa13_control_number=e[13], isa14_ack_requested=e[14], isa15_usage_indicator=e[15],
                               interchange_datetime=datetime.strptime(e[9] + e[10], "%y%m%d%H%M"))
        elif sid == "IEA":
            st["ic_row"].update(iea01_group_count=int(e[1]), iea02_control_number=e[2], iea_seg_ord=so)
        elif sid == "GS":
            st["gp"] = u
            st["gp_row"] = add("edi.functional_group", group_uid=u, interchange_uid=st["ic"], file_id=file_id, seg_ord=so,
                               gs01_functional_id=e[1], gs02_sender_code=e[2], gs03_receiver_code=e[3], gs04_date=e[4],
                               gs05_time=e[5], gs06_control_number=e[6], gs07_agency=e[7], gs08_version=e[8])
        elif sid == "GE":
            st["gp_row"].update(ge01_txn_count=int(e[1]), ge02_control_number=e[2], ge_seg_ord=so)
        elif sid == "ST":
            st["tx"] = tx = u
            st["txn_type"] = kind = e[1]
            st["st_ord"] = so
            st["tx_row"] = add("edi.transaction_set", txn_uid=u, group_uid=st["gp"], file_id=file_id, seg_ord=so,
                               seg_ord_end=so, txn_type=e[1], st01=e[1], st02_control_number=e[2], st03_impl_convention=_el(e, 3))
            rows["edi.segment"][-1]["txn_uid"] = u
            st["loop"] = "HEADER"
            if kind == "835":
                st["pay"] = add("r835.payment", payment_uid=u + "/pay", txn_uid=u, file_id=file_id, seg_ord=so)
        elif sid == "SE":
            for k in ("line", "claim", "svc", "clp"):
                close(k, so - 1, None)
            st["tx_row"].update(seg_ord_end=so, se01_segment_count=int(e[1]), se02_control_number=e[2],
                                count_check_ok=int(e[1]) == so - st["st_ord"] + 1)
            st["tx"], st["loop"] = None, None
        elif tx is None:
            raise ParseError(f"segment {sid} outside a transaction set")
        elif kind == "837":
            _seg_837(st, rows, add, e, sid, so, u, tx, file_id, comp)
        elif kind == "835":
            _seg_835(st, rows, add, e, sid, so, u, tx, file_id, comp)
        rows["edi.segment"][-1]["loop_id"] = st["loop"]
    if not rows["edi.transaction_set"]:
        raise ParseError("no transaction sets")
    file_kind = "837P" if kind == "837" else kind
    add("edi.source_file", file_id=file_id, tenant_code=tenant, file_kind=file_kind, ingest_run_id=ingest_run_id,
        ingested_at=ingested_at, parse_status="PARSED", segment_count=len(rows["edi.segment"]),
        interchange_count=len(rows["edi.interchange"]), transaction_count=len(rows["edi.transaction_set"]), **s3)
    return rows


def _entity(st, add, e, so, u, tx, file_id, owner, owner_table, loop, sid):
    r = add("edi.entity", entity_uid=u, txn_uid=tx, owner_uid=owner, owner_table=owner_table, file_id=file_id, seg_ord=so,
            loop_id=loop, segment_id=sid, entity_id_code=e[1])
    if sid == "NM1":
        r.update(entity_type=_el(e, 2), name_last_or_org=_el(e, 3), name_first=_el(e, 4), name_middle=_el(e, 5),
                 name_prefix=_el(e, 6), name_suffix=_el(e, 7), id_qualifier=_el(e, 8), id_value=_el(e, 9))
    else:  # N1
        r.update(name_last_or_org=_el(e, 2), id_qualifier=_el(e, 3), id_value=_el(e, 4))
    st["ent"] = r
    return r


def _child(st, rows, add, e, sid, so, u, tx, file_id, owner, owner_table):
    """Segments that attach to the current entity or loop owner: N3/N4/DMG/PRV/REF/DTP/DTM/AMT/PER."""
    ent = st["ent"]
    if sid == "N3" and ent:
        ent.update(addr_line1=_el(e, 1), addr_line2=_el(e, 2)); return True
    if sid == "N4" and ent:
        ent.update(city=_el(e, 1), state=_el(e, 2), postal_code=_el(e, 3), country=_el(e, 4)); return True
    if sid == "DMG" and ent:
        ent.update(dmg_format=_el(e, 1), dmg_birth_date=_d8(_el(e, 2)), dmg_gender=_el(e, 3)); return True
    if sid == "PRV":
        target = ent if ent else None
        if target:
            target.update(prv_provider_code=_el(e, 1), prv_ref_qualifier=_el(e, 2), prv_taxonomy=_el(e, 3))
        return True
    if sid == "REF":
        add("edi.reference", ref_uid=u, txn_uid=tx, owner_uid=owner, owner_table=owner_table, file_id=file_id,
            seg_ord=so, loop_id=st["loop"], qualifier=e[1], value=_el(e, 2), description=_el(e, 3), ref04_raw=_el(e, 4))
        return True
    if sid in ("DTP", "DTM"):
        if sid == "DTP":
            fmt, val = e[2], e[3]
        else:
            fmt, val = "D8", e[2]
        start = _d8(val)
        end = _d8(val.split("-")[1]) if fmt == "RD8" and "-" in val else start
        add("edi.date_time", dt_uid=u, txn_uid=tx, owner_uid=owner, owner_table=owner_table, file_id=file_id, seg_ord=so,
            loop_id=st["loop"], segment_id=sid, qualifier=e[1], format_qualifier=fmt, raw_value=val, date_start=start,
            date_end=end, time_value=_el(e, 3) if sid == "DTM" else None)
        return True
    if sid == "AMT":
        add("edi.amount", amt_uid=u, txn_uid=tx, owner_uid=owner, owner_table=owner_table, file_id=file_id, seg_ord=so,
            loop_id=st["loop"], qualifier=e[1], amount=_num(_el(e, 2)), raw_amount=_el(e, 2), credit_debit_flag=_el(e, 3))
        return True
    if sid == "PER":
        return True                                      # kept in edi.segment; edi.contact not populated yet
    return False


def _proc(composite: str, comp: str) -> list:
    parts = composite.split(comp) + [None] * 8
    return [p or None for p in parts[:8]]


def _seg_837(st, rows, add, e, sid, so, u, tx, file_id, comp):
    if sid == "BHT":
        st["tx_row"].update(bht01_structure_code=e[1], bht02_purpose_code=e[2], bht03_reference_id=e[3],
                            bht04_date=_d8(e[4]), bht05_time=_el(e, 5), bht06_claim_or_encounter=_el(e, 6))
    elif sid == "HL":
        for k in ("line", "claim"):
            if st[k] is not None:
                st[k]["seg_ord_end"] = so - 1
                st[k] = None
        level = e[3]
        st["loop"] = {"20": "2000A", "22": "2000B", "23": "2000C"}.get(level, "2000")
        parent = next((h["hl_uid"] for h in reversed(rows["c837.hl"]) if h["hl01_id"] == _el(e, 2)), None)
        st["hl"] = add("c837.hl", hl_uid=u, txn_uid=tx, file_id=file_id, seg_ord=so, loop_id=st["loop"], hl01_id=e[1],
                       hl02_parent_id=_el(e, 2), hl03_level_code=level, hl04_child_code=_el(e, 4), parent_hl_uid=parent)
        st["ent"] = None
        if level == "20":
            st["hl20"] = u
        elif level == "22":
            st["hl22"] = u
    elif sid == "SBR":
        st["hl"].update(sbr01_payer_resp_seq=_el(e, 1), sbr02_relationship=_el(e, 2), sbr03_group_number=_el(e, 3),
                        sbr04_group_name=_el(e, 4), sbr05_insurance_type=_el(e, 5), sbr06=_el(e, 6), sbr07=_el(e, 7),
                        sbr08=_el(e, 8), sbr09_filing_indicator=_el(e, 9))
    elif sid == "NM1":
        loop = ENTITY_LOOPS_837.get(e[1], st["loop"])
        if st["claim"] is not None and loop.startswith("2010"):
            loop = "2330"                                    # other-payer names inside the claim
        st["loop"] = loop
        if st["line"] is not None:
            owner, ot = st["line"]["line_uid"], "c837.service_line"
        elif st["claim"] is not None:
            owner, ot = st["claim"]["claim_uid"], "c837.claim"
        elif st["hl"] is not None:
            owner, ot = st["hl"]["hl_uid"], "c837.hl"
        else:
            owner, ot = tx, "edi.transaction_set"
        _entity(st, add, e, so, u, tx, file_id, owner, ot, loop, sid)
    elif sid == "CLM":
        st["n_claim"] += 1
        st["n_hi"] = st["code_seq"] = 0
        c05 = e[5].split(comp) + [None, None, None]
        st["loop"] = "2300"
        st["ent"] = None
        st["claim"] = add("c837.claim", claim_uid=u, txn_uid=tx, file_id=file_id, seg_ord=so, seg_ord_end=so,
                          claim_ord_in_txn=st["n_claim"], billing_hl_uid=st["hl20"], subscriber_hl_uid=st["hl22"],
                          clm01_patient_control_number=e[1], clm02_total_charge=_num(e[2]), clm02_raw=e[2],
                          clm03=_el(e, 3), clm04=_el(e, 4), clm05_raw=e[5], clm05_1_place_of_service=c05[0],
                          clm05_2_facility_qualifier=c05[1], clm05_3_frequency_code=c05[2], clm06_provider_signature=_el(e, 6),
                          clm07_assignment_participation=_el(e, 7), clm08_benefits_assignment=_el(e, 8),
                          clm09_release_of_info=_el(e, 9), clm10_patient_signature_source=_el(e, 10), line_count=0)
    elif sid == "HI":
        st["n_hi"] += 1
        for pos, composite in enumerate(e[1:], 1):
            if not composite:
                continue
            q, _, code = composite.partition(comp)
            st["code_seq"] += 1
            add("c837.claim_code", claim_code_uid=f"{u}:{pos}", claim_uid=st["claim"]["claim_uid"], txn_uid=tx,
                file_id=file_id, seg_ord=so, hi_segment_ordinal=st["n_hi"], element_position=pos,
                code_sequence=st["code_seq"], code_list_qualifier=q, code=code.split(comp)[0], raw_composite=composite)
    elif sid == "LX":
        if st["line"] is not None:
            st["line"]["seg_ord_end"] = so - 1
        st["loop"] = "2400"
        st["ent"] = None
        st["line"] = add("c837.service_line", line_uid=u, claim_uid=st["claim"]["claim_uid"], txn_uid=tx, file_id=file_id,
                         seg_ord=so, seg_ord_end=so, lx01_line_number=int(e[1]), sv101_raw="")
        st["claim"]["line_count"] += 1
    elif sid == "SV1":
        p = _proc(e[1], comp)
        st["line"].update(sv101_raw=e[1], sv101_1_qualifier=p[0], sv101_2_procedure_code=p[1], sv101_3_mod1=p[2],
                          sv101_4_mod2=p[3], sv101_5_mod3=p[4], sv101_6_mod4=p[5], sv101_7_description=p[6],
                          sv102_charge=_num(_el(e, 2)), sv102_raw=_el(e, 2), sv103_unit_basis=_el(e, 3),
                          sv104_units=_num(_el(e, 4)), sv104_raw=_el(e, 4), sv105_place_of_service=_el(e, 5),
                          sv107_raw=_el(e, 7), sv109_emergency=_el(e, 9))
    else:
        if st["line"] is not None and sid not in ("NM1",):
            owner, ot = st["line"]["line_uid"], "c837.service_line"
        elif st["claim"] is not None:
            owner, ot = st["claim"]["claim_uid"], "c837.claim"
        else:
            owner, ot = (st["hl"]["hl_uid"], "c837.hl") if st["hl"] else (tx, "edi.transaction_set")
        if sid in ("DTP", "REF", "AMT") and st["ent"] is not None and st["loop"] in ("2010AA", "2010BA", "2010BB"):
            owner, ot = st["ent"]["entity_uid"], "edi.entity"
        _child(st, rows, add, e, sid, so, u, tx, file_id, owner, ot)
        if sid == "REF" and e[1] == "6R" and st["line"] is not None:
            st["line"]["line_item_control_number"] = _el(e, 2)


def _seg_835(st, rows, add, e, sid, so, u, tx, file_id, comp):
    pay = st["pay"]
    if sid == "BPR":
        keys = ["bpr01_transaction_handling", "bpr02_raw", "bpr03_credit_debit", "bpr04_payment_method",
                "bpr05_payment_format", "bpr06_sender_dfi_qual", "bpr07_sender_dfi_id", "bpr08_sender_acct_qual",
                "bpr09_sender_account", "bpr10_originating_company_id", "bpr11_originating_company_supplemental",
                "bpr12_receiver_dfi_qual", "bpr13_receiver_dfi_id", "bpr14_receiver_acct_qual", "bpr15_receiver_account"]
        pay.update({k: _el(e, i) for i, k in enumerate(keys, 1)}, seg_ord=so, bpr02_total_paid=_num(e[2]),
                   bpr16_effective_date=_d8(_el(e, 16)))
    elif sid == "TRN":
        pay.update(trn01_trace_type=e[1], trn02_check_eft_number=e[2], trn03_payer_id=_el(e, 3),
                   trn04_originating_supplemental=_el(e, 4))
    elif sid == "N1":
        st["loop"] = {"PR": "1000A", "PE": "1000B"}.get(e[1], "1000")
        _entity(st, add, e, so, u, tx, file_id, pay["payment_uid"], "r835.payment", st["loop"], sid)
    elif sid == "LX":
        st["loop"], st["ent"] = "2000", None
    elif sid == "CLP":
        for k in ("svc", "clp"):
            if st[k] is not None:
                st[k]["seg_ord_end"] = so - 1
                st[k] = None
        st["n_clp"] += 1
        st["n_svc"] = st["cas_n"] = 0
        st["loop"], st["ent"] = "2100", None
        st["clp"] = add("r835.claim_payment", claim_payment_uid=u, payment_uid=pay["payment_uid"], txn_uid=tx,
                        file_id=file_id, seg_ord=so, seg_ord_end=so, clp_ord_in_txn=st["n_clp"],
                        clp01_patient_control_number=e[1], clp02_claim_status=e[2], clp03_total_charge=_num(e[3]),
                        clp04_total_paid=_num(e[4]), clp05_patient_responsibility=_num(_el(e, 5)),
                        clp06_filing_indicator=_el(e, 6), clp07_payer_claim_control_number=_el(e, 7),
                        clp08_facility_type=_el(e, 8), clp09_frequency_code=_el(e, 9), raw_clp03=e[3], raw_clp04=e[4],
                        raw_clp05=_el(e, 5))
    elif sid == "NM1" and st["clp"] is not None:
        _entity(st, add, e, so, u, tx, file_id, st["clp"]["claim_payment_uid"], "r835.claim_payment", "2100", sid)
    elif sid == "SVC":
        if st["svc"] is not None:
            st["svc"]["seg_ord_end"] = so - 1
        st["n_svc"] += 1
        st["cas_n"] = 0
        st["loop"], st["ent"] = "2110", None
        p, p6 = _proc(e[1], comp), _proc(_el(e, 6) or "", comp)
        st["svc"] = add("r835.service_payment", service_payment_uid=u, claim_payment_uid=st["clp"]["claim_payment_uid"],
                        txn_uid=tx, file_id=file_id, seg_ord=so, seg_ord_end=so, svc_ord_in_claim=st["n_svc"],
                        svc01_raw=e[1], svc01_1_qualifier=p[0], svc01_2_procedure_code=p[1], svc01_3_mod1=p[2],
                        svc01_4_mod2=p[3], svc01_5_mod3=p[4], svc01_6_mod4=p[5], svc01_7_description=p[6],
                        svc02_charge=_num(e[2]), svc03_paid=_num(_el(e, 3)), svc04_revenue_code=_el(e, 4),
                        svc05_units_paid=_num(_el(e, 5)), svc06_raw=_el(e, 6), svc06_1_qualifier=p6[0],
                        svc06_2_procedure_code=p6[1], svc06_3_mod1=p6[2], svc06_4_mod2=p6[3], svc06_5_mod3=p6[4],
                        svc06_6_mod4=p6[5], svc06_7_description=p6[6], svc07_original_units=_num(_el(e, 7)),
                        raw_svc02=e[2], raw_svc03=_el(e, 3))
    elif sid == "CAS":
        line = st["svc"] is not None
        owner = st["svc"]["service_payment_uid"] if line else st["clp"]["claim_payment_uid"]
        st["cas_n"] += 1
        for ti in range(6):
            reason = _el(e, 2 + ti * 3)
            if reason is None:
                continue
            amt, qty = _el(e, 3 + ti * 3), _el(e, 4 + ti * 3)
            add("edi.adjustment", adj_uid=f"{u}:{ti + 1}", txn_uid=tx, txn_type="835", owner_uid=owner,
                owner_table="r835.service_payment" if line else "r835.claim_payment", adj_level="LINE" if line else "CLAIM",
                file_id=file_id, seg_ord=so, loop_id=st["loop"], cas_ordinal=st["cas_n"], triplet_index=ti + 1,
                group_code=e[1], reason_code=reason, amount=_num(amt), quantity=_num(qty), raw_amount=amt, raw_quantity=qty)
    elif sid == "LQ":
        owner = st["svc"]["service_payment_uid"]
        add("r835.remark", remark_uid=u, owner_uid=owner, owner_table="r835.service_payment", remark_level="LINE", txn_uid=tx,
            file_id=file_id, seg_ord=so, source_segment="LQ", source_element="LQ02", code_list_qualifier=e[1],
            remark_code=e[2])
    elif sid == "PLB":
        for k in ("svc", "clp"):
            if st[k] is not None:
                st[k]["seg_ord_end"] = so - 1
                st[k] = None
        st["loop"] = "SUMMARY"
        for pi in range(6):
            reason = _el(e, 3 + pi * 2)
            if reason is None:
                continue
            code, _, ref = reason.partition(comp)
            add("r835.provider_adjustment", plb_uid=f"{u}:{pi + 1}", payment_uid=pay["payment_uid"], txn_uid=tx,
                file_id=file_id, seg_ord=so, plb01_provider_id=e[1], plb02_fiscal_period_date=_d8(e[2]), pair_index=pi + 1,
                reason_raw=reason, reason_code=code, reference_id=ref or None, amount=_num(_el(e, 4 + pi * 2)),
                raw_amount=_el(e, 4 + pi * 2))
    else:
        if st["svc"] is not None:
            owner, ot = st["svc"]["service_payment_uid"], "r835.service_payment"
        elif st["clp"] is not None:
            owner, ot = st["clp"]["claim_payment_uid"], "r835.claim_payment"
        else:
            owner, ot = pay["payment_uid"], "r835.payment"
        if sid == "REF" and st["ent"] is not None and st["loop"] in ("1000A", "1000B"):
            owner, ot = st["ent"]["entity_uid"], "edi.entity"
        _child(st, rows, add, e, sid, so, u, tx, file_id, owner, ot)
        if sid == "REF" and e[1] == "6R" and st["svc"] is not None:
            st["svc"]["line_item_control_number"] = _el(e, 2)

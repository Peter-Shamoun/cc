"""edi.segment keeps only where each segment is in bronze (byte_offset, byte_length), not its text or elements.
That is lossless because bronze is immutable (Object Lock) and

  1. bronze bytes [byte_offset, byte_offset + byte_length) are the segment as received, for every segment of every
     file, delivery noise included (BOM, leading whitespace, 80-column wrapping, NUL / space padding, several
     interchanges per file), as the independent validator (ccsim.x12_validate) splits the file;
  2. every element value of every segment is still in silver: in a typed row made or filled from that segment,
     or in edi.misc_segment.
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
    for kind_marker in (b"*837*", b"*835*"):
        body = min((b for b in plain if kind_marker in b[:300]), key=len)
        files += [(f"noise/{name}{kind_marker.decode()}", v) for name, v in _noise_variants(body).items()]
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
            if owner_table:                                          # the latest such row at or before the segment
                ords, of_file = latest[(owner_table, f.file_id)]
                owner = of_file[bisect.bisect_right(ords, seg_ord) - 1]
                values |= set().union(*map(_values, owner.values()))
            for position, v in enumerate(elements[1:], 1):
                if v and v not in values and v.strip() not in values and _number(v) not in values:
                    missing.append((f.key, seg_ord, f"{elements[0]}{position:02d}", v))
    assert not missing, f"{len(missing)} element values not in any table, e.g. {sorted(set(missing))[:12]}"

"""CSV downloads must not carry spreadsheet formulas (OWASP CSV injection). Synthetic values only."""
import csv
import io
import time

from api.store import Download, defuse_cell


def test_defuse_cell():
    for s in ("=1+1", "+1", "-1+2", "@SUM(A1)", "\t=1", "\r=1"):
        assert defuse_cell(s) == "'" + s
    assert defuse_cell(-5) == -5 and defuse_cell(2.5) == 2.5
    assert defuse_cell("CO-97") == "CO-97"


def test_download_csv_is_defused():
    d = Download(id="f_x", owner=None, name="result.csv", columns=["payer", "=x"],
                 rows=[['=HYPERLINK("https://x.example")', -3], ["Aetna", 4]], expires_at=time.time() + 60)
    rows = list(csv.reader(io.StringIO(d.csv_bytes().decode())))
    assert rows[0] == ["payer", "'=x"]
    assert rows[1] == ["'=HYPERLINK(\"https://x.example\")", "-3"]
    assert rows[2] == ["Aetna", "4"]

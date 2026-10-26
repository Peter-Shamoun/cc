"""API CSV names match the web client's csvFilename (shared vectors), and the header can't be split."""
import datetime
import json
import pathlib
import re

from api.app import _safe_filename
from api.store import csv_filename
from api.tests.conftest import LABS, ask

VECTORS = json.loads((pathlib.Path(__file__).parents[2] / "web/src/lib/csvFilename.vectors.json").read_text())


def test_matches_client_vectors():
    for v in VECTORS:
        when = datetime.datetime.fromisoformat(v["date"].replace("Z", "+00:00")).timestamp()
        assert csv_filename(v["title"], when) == v["name"], v


def test_safe_filename():
    assert _safe_filename('a"b\r\nSet-Cookie: x.csv') == "a_b_Set-Cookie_x.csv"
    assert _safe_filename("../../etc/passwd") == "etc_passwd.csv"
    assert _safe_filename("") == "download.csv"


def test_download_header_uses_client_style_name(client):
    _, evs = ask(client, "list all claims")
    table = next(e["part"] for e in evs if e["type"] == "part" and e["part"]["type"] == "table")
    r = client.get(f"/api/download/{table['csvFileId']}", headers=LABS)
    assert r.status_code == 200
    assert re.fullmatch(r'attachment; filename="claimclarity_result_\d{4}-\d{2}-\d{2}\.csv"',
                        r.headers["content-disposition"])

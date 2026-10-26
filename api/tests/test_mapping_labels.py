"""QA-P5: billing acronyms stay uppercase in column labels and chart titles."""
from api.mapping import chart_for, columns_for, humanize, lower_label


def test_humanize_keeps_acronyms():
    assert humanize("carc_code") == "CARC code"
    assert humanize("payer_name") == "Payer name"
    assert humanize("npi") == "NPI"
    assert humanize("bcbs_share") == "BCBS share"


def test_lower_label_keeps_acronyms():
    assert lower_label("CARC code") == "CARC code"
    assert lower_label("Payer name") == "payer name"


def test_chart_title_keeps_acronyms():
    rows = [["CO-45", 10], ["CO-97", 8], ["PR-1", 5]]
    cols = columns_for(["carc_code", "claims"], rows)
    assert cols[0]["label"] == "CARC code"
    chart = chart_for(cols, rows)
    assert chart is not None
    assert chart["title"] == "Claims by CARC code"
    assert "CARC code" in chart["alt"]

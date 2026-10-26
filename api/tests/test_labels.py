"""QA-P5: humanised labels keep revenue-cycle acronyms upper-case, in columns and chart titles."""
from api.mapping import chart_for, columns_for, humanize, lower_label


def test_humanize():
    assert humanize("carc_code") == "CARC code"
    assert humanize("payer_name") == "Payer name"
    assert humanize("bcbs_share") == "BCBS share"
    assert humanize("cpt") == "CPT"
    assert humanize("denial_rate") == "Denial rate"
    assert humanize("cardiology_visits") == "Cardiology visits"  # 'car' prefixes are not acronyms


def test_lower_label_keeps_acronyms():
    assert lower_label("CARC code") == "CARC code"
    assert lower_label("Service month") == "service month"


def test_chart_title_keeps_acronyms():
    rows = [["CO-97", 84], ["CO-45", 72], ["PR-1", 64]]
    cols = columns_for(["carc_code", "claims"], rows)
    assert [c["label"] for c in cols] == ["CARC code", "Claims"]
    chart = chart_for(cols, rows)
    assert chart["title"] == "Claims by CARC code"
    assert chart["alt"].startswith("Bar chart of claims by CARC code")
    assert chart["spec"]["encoding"]["x"]["title"] == "CARC code"

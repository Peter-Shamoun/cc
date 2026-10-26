"""Ratio/rate/share/pct columns and fractional values never get currency, even when their name names money."""
from decimal import Decimal

import pytest

from api.mapping import column_type, columns_for


@pytest.mark.parametrize("name", ["payment_to_charge_ratio", "paid_rate", "billed_share", "charge_pct",
                                  "payment_percent", "pct_paid"])
def test_ratio_named_fraction_is_percent(name):
    assert column_type(name, [0.1746, 0.2267, None]) == "percent"


def test_ratio_in_percentage_points_is_a_plain_number():
    # The mock run: ``ROUND(paid / billed * 100, 2) AS payment_to_charge_pct`` rendered as $17.46.
    names = ["payer_class", "total_billed_charges", "total_paid", "payment_to_charge_pct"]
    rows = [["blues", Decimal("221351.26"), Decimal("38637.66"), Decimal("17.46")],
            ["commercial", Decimal("314401.49"), Decimal("71279.57"), Decimal("22.67")]]
    cols = columns_for(names, rows)
    assert [c["type"] for c in cols] == ["string", "currency", "currency", "number"]
    assert "format" not in cols[3]
    assert column_type("paid_ratio", [17, 22]) == "integer"


def test_fractional_values_under_a_money_name_are_not_currency():
    assert column_type("paid_over_billed", [0.17, 0.23]) == "number"
    assert column_type("total_paid", [Decimal("0.00"), Decimal("1.00")]) == "currency"  # whole dollars stay money
    assert column_type("total_paid", [38637.66, 0.5]) == "currency"


def test_ratio_word_must_be_a_whole_name_word():
    assert column_type("generated_revenue", [1200.5]) == "currency"  # "generated" contains "rate"
    assert column_type("denial_rate", [0.08]) == "percent"

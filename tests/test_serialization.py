from datetime import date
from decimal import Decimal
import json

import pytest

from app.models import (
    ForecastSummary,
    SavingsFundingMode,
    SavingsGoalStage,
    SavingsPlan,
)
from app.serialization import decimal_to_json_string, dumps_json, to_json_ready


def test_decimal_json_strings_preserve_two_places_and_normalize_negative_zero():
    assert decimal_to_json_string(Decimal("215.00")) == "215.00"
    assert decimal_to_json_string(Decimal("0.00")) == "0.00"
    assert decimal_to_json_string(Decimal("-0.00")) == "0.00"
    assert decimal_to_json_string(Decimal("999999999999.995")) == "1000000000000.00"


def test_nested_dataclasses_serialize_money_dates_and_enums_as_json_values():
    plan = SavingsPlan(
        deadline_priority_enabled=True,
        goals=[
            SavingsGoalStage(
                name="Goal",
                target_amount=Decimal("2400.00"),
                start_date=date(2026, 7, 17),
                target_date=date(2026, 8, 11),
                funding_mode=SavingsFundingMode.DEADLINE_PRIORITY,
            )
        ],
    )

    payload = to_json_ready({"plan": plan, "amounts": [Decimal("215.00")]})

    assert payload["plan"]["goals"][0]["target_amount"] == "2400.00"
    assert payload["plan"]["goals"][0]["start_date"] == "2026-07-17"
    assert payload["plan"]["goals"][0]["funding_mode"] == "deadline_priority"
    assert payload["amounts"] == ["215.00"]


def test_forecast_output_serializes_as_fixed_point_json_strings():
    forecast = ForecastSummary(
        forecast_start_date=date(2026, 1, 1),
        forecast_end_date=date(2026, 1, 15),
        debt_free_date=None,
        savings_goal_date=date(2026, 1, 15),
        starting_debt=Decimal("1500.00"),
        total_interest_paid=Decimal("25.50"),
        total_minimum_payments=Decimal("100.00"),
        total_snowball_payments=Decimal("500.00"),
        ending_savings=Decimal("2400.00"),
        remaining_debt=Decimal("900.00"),
        completed=False,
    )

    payload = json.loads(dumps_json(forecast, sort_keys=True))

    assert payload["forecast_start_date"] == "2026-01-01"
    assert payload["starting_debt"] == "1500.00"
    assert payload["total_interest_paid"] == "25.50"
    assert payload["ending_savings"] == "2400.00"


def test_json_money_round_trips_through_decimal_parser():
    payload = json.loads(dumps_json({"amount": Decimal("568.50")}))

    assert Decimal(payload["amount"]) == Decimal("568.50")


def test_unsupported_json_serialization_type_raises_clear_error():
    with pytest.raises(TypeError, match="Unsupported JSON serialization type"):
        to_json_ready(object())

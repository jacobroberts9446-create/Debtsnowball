import csv
import json
import sqlite3
from contextlib import closing
from copy import deepcopy
from datetime import date
from decimal import Decimal

import pytest
from openpyxl import load_workbook

from app.budget_engine import BudgetEngine
from app.calendar_engine import CalendarEngine
from app.config import Config
from app.forecast_engine import ForecastEngine
from app.history import (
    PlanHistoryService,
    build_warnings,
    config_fingerprint,
    explain_forecast_period,
    forecast_fingerprint,
    normalized_config_snapshot,
)
from app.excel_writer import ExcelWriter
from app.models import (
    ActualEntryType,
    AllocationReasonCode,
    DataWarningSeverity,
)
from app.money import money


def short_plan(config):
    periods = CalendarEngine(config.settings).generate(date(2026, 8, 28))
    summaries = BudgetEngine(config).build_plan(periods)
    forecast = ForecastEngine(config, max_years=1).forecast()
    return summaries, forecast


def test_configuration_fingerprint_is_stable_and_money_normalized():
    first = {
        "budget": {
            "paycheck": 215,
            "starting_savings": Decimal("-0.00"),
            "first_paycheck": "2026-07-17",
        }
    }
    second = {
        "budget": {
            "first_paycheck": "2026-07-17",
            "starting_savings": "0.00",
            "paycheck": "215.00",
        }
    }
    changed = deepcopy(second)
    changed["budget"]["paycheck"] = "216.00"

    assert normalized_config_snapshot(first)["budget"]["paycheck"] == "215.00"
    assert config_fingerprint(first) == config_fingerprint(second)
    assert config_fingerprint(second) != config_fingerprint(changed)


def test_create_plan_versions_prevent_duplicates_force_and_restore(tmp_path):
    service = PlanHistoryService(tmp_path / "history.sqlite")
    config = Config().load("config.json")

    plan = service.create_plan("Current Plan", config, description="Live config")
    versions = service.list_plan_versions(plan.id)
    assert versions[0].version_number == 1
    assert versions[0].active is True

    duplicate = service.save_plan_version(plan.id, config)
    assert duplicate.id == versions[0].id
    assert len(service.list_plan_versions(plan.id)) == 1

    forced = service.save_plan_version(plan.id, config, force=True, change_note="Forced")
    assert forced.version_number == 2

    changed_config = deepcopy(config)
    changed_config.settings.paycheck = Decimal("2300.00")
    changed = service.save_plan_version(plan.id, changed_config, change_note="Changed")
    assert changed.version_number == 3
    assert service.get_plan_version(versions[0].id).config_snapshot == versions[0].config_snapshot

    restored = service.restore_plan_version(versions[0].id)
    assert restored.version_number == 4
    service.archive_plan(plan.id)
    assert service.list_plans() == []
    assert service.list_plans(include_archived=True)[0].archived is True


def test_save_forecast_snapshot_periods_debt_and_savings_rows_are_exact(tmp_path):
    service = PlanHistoryService(tmp_path / "history.sqlite")
    config = Config().load("config.json")
    summaries, forecast = short_plan(config)
    plan = service.create_plan("Forecast Plan", config)
    version = service.list_plan_versions(plan.id)[0]

    snapshot = service.generate_and_save_forecast(
        version.id,
        forecast,
        starting_savings=config.settings.starting_savings,
        pay_period_summaries=summaries,
    )

    assert snapshot.forecast_fingerprint == forecast_fingerprint(forecast)
    assert snapshot.total_projected_interest == forecast.total_interest_paid
    with closing(sqlite3.connect(tmp_path / "history.sqlite")) as conn:
        period = conn.execute(
            """
            SELECT income, savings_deposit, snowball_payment
            FROM forecast_periods
            WHERE forecast_snapshot_id = ?
            ORDER BY sequence_number
            LIMIT 1
            """,
            (snapshot.id,),
        ).fetchone()
        debt = conn.execute("SELECT ending_balance FROM debt_snapshots").fetchone()[0]
        savings = conn.execute("SELECT ending_savings FROM savings_snapshots").fetchone()[0]

    assert period == (223400, 21500, 21500)
    assert isinstance(period[0], int)
    assert money(Decimal(debt) / Decimal("100")) == forecast.periods[0].total_debt_balance
    assert money(Decimal(savings) / Decimal("100")) == forecast.periods[0].savings_balance


def test_forecast_fingerprint_is_deterministic_for_regenerated_forecast(tmp_path):
    config = Config().load("config.json")
    first = ForecastEngine(config, max_years=1).forecast()
    second = ForecastEngine(config, max_years=1).forecast()

    assert forecast_fingerprint(first) == forecast_fingerprint(second)


def test_actual_entries_reverse_and_compare_without_moralizing(tmp_path):
    service = PlanHistoryService(tmp_path / "history.sqlite")
    config = Config().load("config.json")
    summaries, forecast = short_plan(config)
    plan = service.create_plan("Actual Plan", config)
    version = service.list_plan_versions(plan.id)[0]
    service.generate_and_save_forecast(
        version.id,
        forecast,
        starting_savings=config.settings.starting_savings,
        pay_period_summaries=summaries,
    )

    income = service.add_actual_entry(
        plan.id,
        date(2026, 7, 17),
        ActualEntryType.INCOME_RECEIVED,
        Decimal("2234.00"),
    )
    service.add_actual_entry(
        plan.id,
        date(2026, 7, 17),
        ActualEntryType.DEBT_PAYMENT,
        Decimal("1253.00"),
    )
    reversal = service.reverse_actual_entry(income.id)
    comparison = service.compare_forecast_to_actual(plan.id)

    assert reversal.amount == Decimal("-2234.00")
    assert reversal.corrected_entry_id == income.id
    assert comparison.status in {
        "Ahead of plan",
        "On track",
        "Needs review",
        "Insufficient actual data",
    }
    assert "bad" not in comparison.status.casefold()
    assert "failed" not in comparison.status.casefold()


def test_plan_comparison_reports_tradeoffs_and_first_difference(tmp_path):
    service = PlanHistoryService(tmp_path / "history.sqlite")
    config = Config().load("config.json")
    plan = service.create_plan("Compare Plan", config)
    version_one = service.list_plan_versions(plan.id)[0]
    summaries_one, forecast_one = short_plan(config)
    service.generate_and_save_forecast(
        version_one.id,
        forecast_one,
        starting_savings=config.settings.starting_savings,
        pay_period_summaries=summaries_one,
    )

    changed_config = deepcopy(config)
    changed_config.settings.paycheck = Decimal("2500.00")
    version_two = service.save_plan_version(plan.id, changed_config)
    summaries_two, forecast_two = short_plan(changed_config)
    service.generate_and_save_forecast(
        version_two.id,
        forecast_two,
        starting_savings=changed_config.settings.starting_savings,
        pay_period_summaries=summaries_two,
    )

    comparison = service.compare_plan_versions(version_one.id, version_two.id)

    assert comparison.later_version == 2
    assert comparison.first_different_period == date(2026, 7, 17)
    assert comparison.interest_difference <= Decimal("0.00")
    assert "better" not in comparison.explanation.casefold()


def test_export_import_json_and_csv_preserve_money_strings_and_duplicate_protection(tmp_path):
    service = PlanHistoryService(tmp_path / "source.sqlite")
    config = Config().load("config.json")
    summaries, forecast = short_plan(config)
    plan = service.create_plan("Export Plan", config)
    version = service.list_plan_versions(plan.id)[0]
    snapshot = service.generate_and_save_forecast(
        version.id,
        forecast,
        starting_savings=config.settings.starting_savings,
        pay_period_summaries=summaries,
    )

    json_path = service.export_plan_json(plan.id, tmp_path / "plan.json")
    csv_path = service.export_forecast_periods_csv(snapshot.id, tmp_path / "periods.csv")
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    with csv_path.open(encoding="utf-8") as csv_file:
        csv_rows = list(csv.DictReader(csv_file))

    assert payload["versions"][0]["config_snapshot"].count("2400.00") > 0
    assert csv_rows[0]["savings"] == "215.00"

    imported_service = PlanHistoryService(tmp_path / "target.sqlite")
    imported = imported_service.import_plan_json(json_path)
    assert imported.name == "Export Plan"
    with pytest.raises(ValueError, match="already exists"):
        imported_service.import_plan_json(json_path)

    bad_path = tmp_path / "bad.json"
    bad_path.write_text('{"format_version": 999}', encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported"):
        imported_service.import_plan_json(bad_path, new_name="Bad")
    assert len(imported_service.list_plans()) == 1


def test_optional_history_excel_report_uses_requested_history_sheets(tmp_path):
    service = PlanHistoryService(tmp_path / "history.sqlite")
    config = Config().load("config.json")
    summaries, forecast = short_plan(config)
    plan = service.create_plan("Workbook Plan", config)
    version = service.list_plan_versions(plan.id)[0]
    service.generate_and_save_forecast(
        version.id,
        forecast,
        starting_savings=config.settings.starting_savings,
        pay_period_summaries=summaries,
    )
    service.add_actual_entry(
        plan.id,
        date(2026, 7, 17),
        ActualEntryType.INCOME_RECEIVED,
        Decimal("2234.00"),
    )
    actual = service.compare_forecast_to_actual(plan.id)

    path = ExcelWriter(tmp_path / "history.xlsx").write_history_report(
        plan,
        service.list_plan_versions(plan.id),
        actual_comparison=actual,
    )

    workbook = load_workbook(path)
    assert workbook.sheetnames == ["Plan History", "Forecast vs Actual"]
    assert workbook["Forecast vs Actual"]["B3"].value == 8936
    assert workbook["Forecast vs Actual"]["C3"].value == 2234
    assert isinstance(workbook["Forecast vs Actual"]["B3"].value, int | float)
    workbook.close()


def test_explanations_and_warnings_are_structured_plain_language():
    config = Config().load("config.json")
    config.savings_plan.deadline_priority_enabled = True
    forecast = ForecastEngine(config, max_years=1).forecast()
    first_period = forecast.periods[0]

    explanation = explain_forecast_period(first_period)
    warnings = build_warnings(
        forecast,
        high_apr_debts=[("Card", Decimal("35.00"))],
    )

    assert AllocationReasonCode.DEADLINE_SAVINGS_REDIRECTION in explanation.reason_codes
    assert "$215.00" in explanation.explanation
    assert all("irresponsible" not in warning.message.casefold() for warning in warnings)
    assert any(warning.severity == DataWarningSeverity.REVIEW for warning in warnings)


def test_live_and_enabled_deadline_regressions_remain_unchanged():
    config = Config().load("config.json")
    summaries, _ = short_plan(config)
    assert summaries[0].savings_contribution == Decimal("215.00")
    assert summaries[0].snowball_payment == Decimal("215.00")
    assert summaries[0].personal_expense_reduction == Decimal("0.00")
    assert summaries[1].savings_contribution == Decimal("568.50")
    assert summaries[1].snowball_payment == Decimal("568.50")
    assert summaries[1].personal_expense_reduction == Decimal("0.00")

    enabled = Config().load("config.json")
    enabled.savings_plan.deadline_priority_enabled = True
    enabled_summaries, _ = short_plan(enabled)
    assert enabled_summaries[0].savings_contribution == Decimal("430.00")
    assert enabled_summaries[0].snowball_reduction == Decimal("215.00")
    assert enabled_summaries[0].personal_expense_reduction == Decimal("0.00")
    assert enabled_summaries[1].savings_contribution == Decimal("470.00")
    assert enabled_summaries[1].snowball_reduction == Decimal("0.00")
    assert enabled_summaries[2].planned_withdrawal_amount == Decimal("2400.00")
    assert enabled_summaries[2].snowball_payment == Decimal("215.00")

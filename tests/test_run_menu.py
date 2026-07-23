"""Tests for the interactive run.py menu."""

from argparse import Namespace
import re
from types import SimpleNamespace

import run

ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def output_text(output: list[str]) -> str:
    """Join captured console output for readable substring assertions."""
    return "\n".join(output)


def strip_ansi(text: str) -> str:
    """Remove ANSI escapes from captured output."""
    return ANSI_RE.sub("", text)


class FakeStream:
    """Small stream fake for ANSI auto-detection tests."""

    def __init__(self, is_tty: bool) -> None:
        self.is_tty = is_tty

    def isatty(self) -> bool:
        return self.is_tty


class FakePlanHistoryService:
    """Small fake service for interactive menu tests."""

    def __init__(self, plans=None) -> None:
        self.plans = plans or []
        self.created = []
        self.saved_versions = []
        self.versions_by_plan = {}
        self.comparison = SimpleNamespace(explanation="Comparison explanation")
        self.restored_version = SimpleNamespace(id=12, plan_id=4, version_number=3)
        self.compare_error = None
        self.restore_error = None

    def list_plans(self):
        return self.plans

    def get_plan(self, plan_id):
        for plan in self.plans:
            if plan.id == plan_id:
                return plan
        raise ValueError(f"plan {plan_id} was not found.")

    def create_plan(
        self,
        name,
        config,
        *,
        description="",
        change_note="Initial version",
        source="manual",
    ):
        plan = SimpleNamespace(
            id=len(self.plans) + 1,
            name=name,
            description=description,
            change_note=change_note,
            source=source,
            config=config,
        )
        self.plans.append(plan)
        self.created.append(plan)
        return plan

    def save_plan_version(
        self,
        plan_id,
        config,
        *,
        change_note="",
        source="manual",
        force=False,
    ):
        version = SimpleNamespace(
            version_number=len(self.saved_versions) + 2,
            id=len(self.saved_versions) + 20,
            plan_id=plan_id,
            config=config,
            change_note=change_note,
            source=source,
            force=force,
        )
        self.saved_versions.append(version)
        return version

    def list_plan_versions(self, plan_id):
        versions = self.versions_by_plan.get(plan_id)
        if versions is None:
            raise ValueError(f"plan {plan_id} was not found.")
        return versions

    def compare_plan_versions(self, from_version, to_version):
        if self.compare_error is not None:
            raise self.compare_error
        self.comparison.from_version = from_version
        self.comparison.to_version = to_version
        return self.comparison

    def restore_plan_version(self, version_id):
        if self.restore_error is not None:
            raise self.restore_error
        self.restored_version.source_version_id = version_id
        return self.restored_version


class FakePreferences:
    """In-memory recent-plan preferences for menu tests."""

    def __init__(self, recent_plans=None) -> None:
        self.recent_plans = recent_plans or []
        self.marked = []

    def list_existing_recent_plans(self, existing_plans):
        existing_by_id = {plan.id: plan.name for plan in existing_plans}
        self.recent_plans = [
            SimpleNamespace(id=plan.id, name=existing_by_id[plan.id])
            for plan in self.recent_plans
            if plan.id in existing_by_id
        ]
        return self.recent_plans

    def mark_recent(self, plan_id, plan_name):
        self.marked.append((plan_id, plan_name))
        self.recent_plans = [
            SimpleNamespace(id=plan_id, name=plan_name),
            *[plan for plan in self.recent_plans if plan.id != plan_id],
        ][:5]


def test_style_text_adds_ansi_and_reset_when_color_enabled() -> None:
    """Explicitly enabled styles wrap text with ANSI and reset."""
    styled = run.success_text("Success: Done", enable_color=True)

    assert styled.startswith("\033[32m")
    assert styled.endswith(run.ANSI_RESET)
    assert strip_ansi(styled) == "Success: Done"


def test_style_text_returns_plain_text_when_color_disabled() -> None:
    """Disabled color returns script-friendly plain text."""
    assert run.error_text("Error: Nope", enable_color=False) == "Error: Nope"


def test_no_color_takes_precedence_over_force_on(monkeypatch) -> None:
    """NO_COLOR disables color even when DEBTSNOWBALL_COLOR is forced on."""
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("DEBTSNOWBALL_COLOR", "1")

    assert not run.ansi_color_enabled(
        FakeStream(True),
        {"NO_COLOR": "1", "DEBTSNOWBALL_COLOR": "1"},
    )


def test_debtsnowball_color_force_on(monkeypatch) -> None:
    """DEBTSNOWBALL_COLOR=1 forces ANSI output on."""
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("DEBTSNOWBALL_COLOR", "1")

    assert run.warning_text("Warning: Careful").startswith("\033[33m")


def test_debtsnowball_color_force_off(monkeypatch) -> None:
    """DEBTSNOWBALL_COLOR=0 forces ANSI output off."""
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("DEBTSNOWBALL_COLOR", "0")

    assert run.success_text("Success: Done") == "Success: Done"


def test_color_disabled_for_non_tty_output() -> None:
    """Non-TTY output disables color automatically."""
    enabled = run.ansi_color_enabled(FakeStream(False), {"TERM": "xterm-256color"})

    assert not enabled


def test_color_disabled_for_unsupported_terminal() -> None:
    """Unsupported terminal declarations disable automatic color."""
    enabled = run.ansi_color_enabled(FakeStream(True), {"TERM": "dumb"})

    assert not enabled


def test_format_helpers_style_success_warning_error_and_headings(monkeypatch) -> None:
    """Formatting helpers use the centralized styling layer."""
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("DEBTSNOWBALL_COLOR", "1")
    output = []

    run.print_success("Saved.", output.append)
    run.print_warning("Review.", output.append)
    run.print_error("Failed.", output.append)
    run.print_menu_title("Menu", output.append)

    text = output_text(output)
    assert "\033[32mSuccess: Saved.\033[0m" in text
    assert "\033[33mWarning: Review.\033[0m" in text
    assert "\033[31mError: Failed.\033[0m" in text
    assert "\033[96mMenu\033[0m" in text
    assert strip_ansi(text).splitlines() == [
        "Success: Saved.",
        "Warning: Review.",
        "Error: Failed.",
        "",
        "Menu",
        "----",
    ]


def test_no_ansi_leakage_when_output_is_captured(monkeypatch) -> None:
    """Captured test output remains plain text unless color is forced."""
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("DEBTSNOWBALL_COLOR", raising=False)
    output = []

    run.print_success("Saved.", output.append)
    run.print_warning("Review.", output.append)
    run.print_error("Failed.", output.append)
    run.print_menu_title("Menu", output.append)

    text = output_text(output)
    assert "\033[" not in text
    assert "Success: Saved." in text
    assert "Warning: Review." in text
    assert "Error: Failed." in text
    assert "Menu" in text


def test_menu_generate_budget_plan_runs_existing_workflow_once() -> None:
    """Choosing option 1 runs the default budget workflow and exits."""
    calls = []
    output = []

    run.run_main_menu(
        generate_budget_plan_func=lambda: calls.append("generated"),
        input_func=lambda _prompt: "1",
        output_func=output.append,
    )

    assert calls == ["generated"]
    assert "DebtSnowball v1.1.0" in output
    assert "1. Generate Budget Plan" in output
    assert "2. Saved Plans" in output
    assert "3. History" in output
    assert "4. Help" in output
    assert "5. Exit" in output


def test_menu_help_explains_options_and_returns_to_menu() -> None:
    """Choosing help prints all option descriptions before accepting another choice."""
    prompts = []
    choices = iter(["4", "", "5"])
    output = []

    run.run_main_menu(
        generate_budget_plan_func=lambda: None,
        input_func=lambda prompt: prompts.append(prompt) or next(choices),
        output_func=output.append,
    )

    assert output.count("DebtSnowball v1.1.0") == 2
    assert "Generate Budget Plan: creates the budget plan and Excel workbook." in output
    assert "Saved Plans: lists saved plans or saves the current plan." in output
    assert "History: views, compares, or restores saved plan versions." in output
    assert "Help: explains the menu options." in output
    assert "Exit: closes DebtSnowball without generating a plan." in output
    assert "Press Enter to return to the main menu..." in prompts
    assert "Success: Goodbye." in output


def test_menu_saved_plans_submenu_back_returns_to_main_menu() -> None:
    """The saved-plans submenu can return to the main menu."""
    choices = iter(["2", "4", "5"])
    output = []
    service = FakePlanHistoryService()
    preferences = FakePreferences()

    run.run_main_menu(
        generate_budget_plan_func=lambda: None,
        plan_history_service_factory=lambda: service,
        preferences_factory=lambda: preferences,
        input_func=lambda _prompt: next(choices),
        output_func=output.append,
    )

    assert output.count("DebtSnowball v1.1.0") == 2
    assert "Saved Plans" in output
    assert "1. Recent Plans" in output
    assert "2. List All Saved Plans" in output
    assert "3. Save Current Plan" in output
    assert "4. Back" in output
    assert "Success: Goodbye." in output


def test_saved_plans_recent_empty_waits_and_returns_to_submenu() -> None:
    """Recent Plans shows a friendly warning when no recents exist."""
    prompts = []
    choices = iter(["2", "1", "", "4", "5"])
    output = []
    service = FakePlanHistoryService()
    preferences = FakePreferences()

    run.run_main_menu(
        generate_budget_plan_func=lambda: None,
        plan_history_service_factory=lambda: service,
        preferences_factory=lambda: preferences,
        input_func=lambda prompt: prompts.append(prompt) or next(choices),
        output_func=output.append,
    )

    assert "Warning: No recent plans found." in output
    assert output.count("Saved Plans") == 2
    assert "Press Enter to return to the main menu..." in prompts


def test_saved_plans_recent_populated_uses_table() -> None:
    """Recent Plans displays most-recent-first plan IDs and names."""
    choices = iter(["2", "1", "", "4", "5"])
    output = []
    service = FakePlanHistoryService(
        [
            SimpleNamespace(id=1, name="Older Plan", description=""),
            SimpleNamespace(id=2, name="Current Plan", description=""),
        ]
    )
    preferences = FakePreferences(
        [
            SimpleNamespace(id=2, name="Current Plan"),
            SimpleNamespace(id=1, name="Older Plan"),
        ]
    )

    run.run_main_menu(
        generate_budget_plan_func=lambda: None,
        plan_history_service_factory=lambda: service,
        preferences_factory=lambda: preferences,
        input_func=lambda _prompt: next(choices),
        output_func=output.append,
    )

    text = output_text(output)
    assert "ID | Name" in text
    assert "2  | Current Plan" in text
    assert "1  | Older Plan" in text


def test_saved_plans_recent_removes_stale_plans() -> None:
    """Stale recent plans are removed and not displayed."""
    choices = iter(["2", "1", "", "4", "5"])
    output = []
    service = FakePlanHistoryService(
        [SimpleNamespace(id=2, name="Current Plan", description="")]
    )
    preferences = FakePreferences(
        [
            SimpleNamespace(id=99, name="Gone"),
            SimpleNamespace(id=2, name="Old Name"),
        ]
    )

    run.run_main_menu(
        generate_budget_plan_func=lambda: None,
        plan_history_service_factory=lambda: service,
        preferences_factory=lambda: preferences,
        input_func=lambda _prompt: next(choices),
        output_func=output.append,
    )

    text = output_text(output)
    assert "99" not in text
    assert "Gone" not in text
    assert "2  | Current Plan" in text
    assert [(plan.id, plan.name) for plan in preferences.recent_plans] == [
        (2, "Current Plan")
    ]


def test_saved_plans_list_empty_waits_and_returns_to_submenu() -> None:
    """Listing with no saved plans shows a friendly empty state."""
    prompts = []
    choices = iter(["2", "2", "", "4", "5"])
    output = []
    service = FakePlanHistoryService()
    preferences = FakePreferences()

    run.run_main_menu(
        generate_budget_plan_func=lambda: None,
        plan_history_service_factory=lambda: service,
        preferences_factory=lambda: preferences,
        input_func=lambda prompt: prompts.append(prompt) or next(choices),
        output_func=output.append,
    )

    assert "Warning: No saved plans found." in output
    assert output.count("Saved Plans") == 2
    assert "Press Enter to return to the main menu..." in prompts


def test_saved_plans_list_populated_waits_and_returns_to_submenu() -> None:
    """Listing saved plans displays ID, name, and optional description."""
    prompts = []
    choices = iter(["2", "2", "", "4", "5"])
    output = []
    service = FakePlanHistoryService(
        [
            SimpleNamespace(id=7, name="Current Plan", description="Live config"),
            SimpleNamespace(id=8, name="No Description", description=""),
        ]
    )
    preferences = FakePreferences()

    run.run_main_menu(
        generate_budget_plan_func=lambda: None,
        plan_history_service_factory=lambda: service,
        preferences_factory=lambda: preferences,
        input_func=lambda prompt: prompts.append(prompt) or next(choices),
        output_func=output.append,
    )

    text = output_text(output)
    assert "ID | Name           | Description" in text
    assert "7  | Current Plan   | Live config" in text
    assert "8  | No Description |" in text
    assert "Press Enter to return to the main menu..." in prompts


def test_saved_plans_save_new_plan() -> None:
    """Saving a new plan prompts for details and creates it through the service."""
    prompts = []
    choices = iter(["2", "3", "New Plan", "A useful plan", "", "4", "5"])
    output = []
    service = FakePlanHistoryService()
    preferences = FakePreferences()
    config = SimpleNamespace(name="config")

    run.run_main_menu(
        generate_budget_plan_func=lambda: None,
        plan_history_service_factory=lambda: service,
        preferences_factory=lambda: preferences,
        config_loader=lambda: config,
        input_func=lambda prompt: prompts.append(prompt) or next(choices),
        output_func=output.append,
    )

    assert service.created[0].name == "New Plan"
    assert service.created[0].description == "A useful plan"
    assert service.created[0].config is config
    assert preferences.marked == [(1, "New Plan")]
    assert "Success: Created plan 1: New Plan" in output
    assert "Plan name: " in prompts
    assert "Description (optional): " in prompts


def test_saved_plans_save_rejects_blank_name() -> None:
    """A blank plan name does not call the service save methods."""
    choices = iter(["2", "3", "   ", "", "4", "5"])
    output = []
    service = FakePlanHistoryService()
    preferences = FakePreferences()

    run.run_main_menu(
        generate_budget_plan_func=lambda: None,
        plan_history_service_factory=lambda: service,
        preferences_factory=lambda: preferences,
        config_loader=lambda: SimpleNamespace(name="config"),
        input_func=lambda _prompt: next(choices),
        output_func=output.append,
    )

    assert service.created == []
    assert service.saved_versions == []
    assert preferences.marked == []
    assert "Warning: Plan name cannot be blank." in output


def test_saved_plans_save_existing_plan_when_confirmed() -> None:
    """An existing plan saves a new version only when confirmed."""
    choices = iter(["2", "3", "Current Plan", "", "yes", "", "4", "5"])
    output = []
    service = FakePlanHistoryService(
        [SimpleNamespace(id=4, name="Current Plan", description="Live")]
    )
    preferences = FakePreferences()
    config = SimpleNamespace(name="config")

    run.run_main_menu(
        generate_budget_plan_func=lambda: None,
        plan_history_service_factory=lambda: service,
        preferences_factory=lambda: preferences,
        config_loader=lambda: config,
        input_func=lambda _prompt: next(choices),
        output_func=output.append,
    )

    assert service.created == []
    assert service.saved_versions[0].plan_id == 4
    assert service.saved_versions[0].force is True
    assert service.saved_versions[0].config is config
    assert preferences.marked == [(4, "Current Plan")]
    assert "Success: Saved version 2 for Current Plan" in output


def test_saved_plans_save_existing_plan_when_declined() -> None:
    """Declining the existing-plan prompt cancels without saving."""
    choices = iter(["2", "3", "Current Plan", "", "n", "", "4", "5"])
    output = []
    service = FakePlanHistoryService(
        [SimpleNamespace(id=4, name="Current Plan", description="Live")]
    )
    preferences = FakePreferences()

    run.run_main_menu(
        generate_budget_plan_func=lambda: None,
        plan_history_service_factory=lambda: service,
        preferences_factory=lambda: preferences,
        config_loader=lambda: SimpleNamespace(name="config"),
        input_func=lambda _prompt: next(choices),
        output_func=output.append,
    )

    assert service.created == []
    assert service.saved_versions == []
    assert preferences.marked == []
    assert "Warning: Save cancelled." in output


def test_menu_history_submenu_back_returns_to_main_menu() -> None:
    """The history submenu can return to the main menu."""
    choices = iter(["3", "4", "5"])
    output = []
    service = FakePlanHistoryService()
    preferences = FakePreferences()

    run.run_main_menu(
        generate_budget_plan_func=lambda: None,
        plan_history_service_factory=lambda: service,
        preferences_factory=lambda: preferences,
        input_func=lambda _prompt: next(choices),
        output_func=output.append,
    )

    assert output.count("DebtSnowball v1.1.0") == 2
    assert "History" in output
    assert "1. View Plan History" in output
    assert "2. Compare Versions" in output
    assert "3. Restore Version" in output
    assert "4. Back" in output
    assert "Success: Goodbye." in output


def test_history_view_rejects_invalid_plan_id() -> None:
    """View history validates that plan IDs are positive integers."""
    choices = iter(["3", "1", "abc", "", "4", "5"])
    output = []
    service = FakePlanHistoryService()
    preferences = FakePreferences()

    run.run_main_menu(
        generate_budget_plan_func=lambda: None,
        plan_history_service_factory=lambda: service,
        preferences_factory=lambda: preferences,
        input_func=lambda _prompt: next(choices),
        output_func=output.append,
    )

    assert "Warning: Please enter a positive whole number." in output


def test_history_view_populated_versions() -> None:
    """View history displays version ID, number, date, and note."""
    prompts = []
    choices = iter(["3", "1", "7", "", "4", "5"])
    output = []
    service = FakePlanHistoryService([SimpleNamespace(id=7, name="Plan 7", description="")])
    preferences = FakePreferences()
    service.versions_by_plan[7] = [
        SimpleNamespace(
            id=21,
            version_number=1,
            created_at="2026-07-22T01:00:00+00:00",
            change_note="Initial",
            active=False,
        ),
        SimpleNamespace(
            id=22,
            version_number=2,
            created_at="2026-07-22T02:00:00+00:00",
            change_note="Updated",
            active=True,
        ),
    ]

    run.run_main_menu(
        generate_budget_plan_func=lambda: None,
        plan_history_service_factory=lambda: service,
        preferences_factory=lambda: preferences,
        input_func=lambda prompt: prompts.append(prompt) or next(choices),
        output_func=output.append,
    )

    text = output_text(output)
    assert "ID | Version | Created                   | Status | Note" in text
    assert "21 | v1      | 2026-07-22T01:00:00+00:00 |        | Initial" in text
    assert "22 | v2      | 2026-07-22T02:00:00+00:00 | active | Updated" in text
    assert preferences.marked == [(7, "Plan 7")]
    assert "Press Enter to return to the main menu..." in prompts


def test_history_view_empty_and_missing_history() -> None:
    """View history handles empty version lists and missing plans gracefully."""
    choices = iter(["3", "1", "7", "", "1", "8", "", "4", "5"])
    output = []
    service = FakePlanHistoryService([SimpleNamespace(id=7, name="Plan 7", description="")])
    service.versions_by_plan[7] = []
    preferences = FakePreferences()

    run.run_main_menu(
        generate_budget_plan_func=lambda: None,
        plan_history_service_factory=lambda: service,
        preferences_factory=lambda: preferences,
        input_func=lambda _prompt: next(choices),
        output_func=output.append,
    )

    assert "Warning: No saved versions found for that plan." in output
    assert "Error: plan 8 was not found." in output


def test_history_compare_versions() -> None:
    """Compare Versions displays the existing comparison explanation."""
    choices = iter(["3", "2", "10", "11", "", "4", "5"])
    output = []
    service = FakePlanHistoryService()
    preferences = FakePreferences()

    run.run_main_menu(
        generate_budget_plan_func=lambda: None,
        plan_history_service_factory=lambda: service,
        preferences_factory=lambda: preferences,
        input_func=lambda _prompt: next(choices),
        output_func=output.append,
    )

    assert service.comparison.from_version == 10
    assert service.comparison.to_version == 11
    assert "Comparison explanation" in output


def test_history_compare_invalid_input_and_errors() -> None:
    """Compare Versions validates IDs and displays service errors."""
    choices = iter(["3", "2", "0", "", "2", "10", "11", "", "4", "5"])
    output = []
    service = FakePlanHistoryService()
    service.compare_error = ValueError("version was not found.")
    preferences = FakePreferences()

    run.run_main_menu(
        generate_budget_plan_func=lambda: None,
        plan_history_service_factory=lambda: service,
        preferences_factory=lambda: preferences,
        input_func=lambda _prompt: next(choices),
        output_func=output.append,
    )

    assert "Warning: Please enter a positive whole number." in output
    assert "Error: version was not found." in output


def test_history_restore_confirmed() -> None:
    """Restore Version asks for confirmation and displays the new version."""
    choices = iter(["3", "3", "8", "y", "", "4", "5"])
    output = []
    service = FakePlanHistoryService([SimpleNamespace(id=4, name="Current Plan", description="")])
    preferences = FakePreferences()

    run.run_main_menu(
        generate_budget_plan_func=lambda: None,
        plan_history_service_factory=lambda: service,
        preferences_factory=lambda: preferences,
        input_func=lambda _prompt: next(choices),
        output_func=output.append,
    )

    assert service.restored_version.source_version_id == 8
    assert preferences.marked == [(4, "Current Plan")]
    assert "Success: Restored as version 3 (version ID 12)." in output


def test_history_restore_declined() -> None:
    """Restore Version cancels cleanly when the user declines."""
    choices = iter(["3", "3", "8", "no", "", "4", "5"])
    output = []
    service = FakePlanHistoryService()
    preferences = FakePreferences()

    run.run_main_menu(
        generate_budget_plan_func=lambda: None,
        plan_history_service_factory=lambda: service,
        preferences_factory=lambda: preferences,
        input_func=lambda _prompt: next(choices),
        output_func=output.append,
    )

    assert not hasattr(service.restored_version, "source_version_id")
    assert preferences.marked == []
    assert "Warning: Restore cancelled." in output


def test_history_restore_invalid_input_and_errors() -> None:
    """Restore Version validates IDs and displays service errors."""
    choices = iter(["3", "3", "-1", "", "3", "8", "yes", "", "4", "5"])
    output = []
    service = FakePlanHistoryService()
    service.restore_error = ValueError("version 8 was not found.")
    preferences = FakePreferences()

    run.run_main_menu(
        generate_budget_plan_func=lambda: None,
        plan_history_service_factory=lambda: service,
        preferences_factory=lambda: preferences,
        input_func=lambda _prompt: next(choices),
        output_func=output.append,
    )

    assert "Warning: Please enter a positive whole number." in output
    assert "Error: version 8 was not found." in output


def test_menu_invalid_input_returns_to_menu() -> None:
    """Invalid input displays a friendly message and loops back to the menu."""
    choices = iter(["not a choice", "5"])
    output = []

    run.run_main_menu(
        generate_budget_plan_func=lambda: None,
        input_func=lambda _prompt: next(choices),
        output_func=output.append,
    )

    assert output.count("DebtSnowball v1.1.0") == 2
    assert "Warning: Please choose one of: 1, 2, 3, 4, 5." in output
    assert "Success: Goodbye." in output


def test_main_preserves_existing_argparse_command_behavior(monkeypatch) -> None:
    """Supplying an argparse command bypasses the interactive menu."""
    calls = []

    monkeypatch.setattr(run, "run_cli", lambda args: calls.append(args.command))
    monkeypatch.setattr(
        run,
        "run_main_menu",
        lambda: calls.append("menu"),
    )

    run.main(["plan", "list"])

    assert calls == ["plan"]


def test_run_cli_accepts_parsed_namespace_for_existing_commands(monkeypatch) -> None:
    """The existing CLI dispatcher still accepts parsed argparse namespaces."""
    calls = []

    class FakeService:
        pass

    monkeypatch.setattr(run, "PlanHistoryService", FakeService)
    monkeypatch.setattr(
        run,
        "run_plan_command",
        lambda _service, args: calls.append(("plan", args.plan_command)),
    )

    run.run_cli(Namespace(command="plan", plan_command="list"))

    assert calls == [("plan", "list")]

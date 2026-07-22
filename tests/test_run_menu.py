"""Tests for the interactive run.py menu."""

from argparse import Namespace
from types import SimpleNamespace

import run


class FakePlanHistoryService:
    """Small fake service for interactive menu tests."""

    def __init__(self, plans=None) -> None:
        self.plans = plans or []
        self.created = []
        self.saved_versions = []

    def list_plans(self):
        return self.plans

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
            plan_id=plan_id,
            config=config,
            change_note=change_note,
            source=source,
            force=force,
        )
        self.saved_versions.append(version)
        return version


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
    assert "Saved Plans: opens saved plan tools when they are available." in output
    assert "History: opens plan history tools when they are available." in output
    assert "Help: explains the menu options." in output
    assert "Exit: closes DebtSnowball without generating a plan." in output
    assert "Press Enter to return to the main menu..." in prompts
    assert "Goodbye." in output


def test_menu_saved_plans_submenu_back_returns_to_main_menu() -> None:
    """The saved-plans submenu can return to the main menu."""
    choices = iter(["2", "3", "5"])
    output = []
    service = FakePlanHistoryService()

    run.run_main_menu(
        generate_budget_plan_func=lambda: None,
        plan_history_service_factory=lambda: service,
        input_func=lambda _prompt: next(choices),
        output_func=output.append,
    )

    assert output.count("DebtSnowball v1.1.0") == 2
    assert "Saved Plans" in output
    assert "1. List Saved Plans" in output
    assert "2. Save Current Plan" in output
    assert "3. Back" in output
    assert "Goodbye." in output


def test_saved_plans_list_empty_waits_and_returns_to_submenu() -> None:
    """Listing with no saved plans shows a friendly empty state."""
    prompts = []
    choices = iter(["2", "1", "", "3", "5"])
    output = []
    service = FakePlanHistoryService()

    run.run_main_menu(
        generate_budget_plan_func=lambda: None,
        plan_history_service_factory=lambda: service,
        input_func=lambda prompt: prompts.append(prompt) or next(choices),
        output_func=output.append,
    )

    assert "No saved plans found." in output
    assert output.count("Saved Plans") == 2
    assert "Press Enter to return to the main menu..." in prompts


def test_saved_plans_list_populated_waits_and_returns_to_submenu() -> None:
    """Listing saved plans displays ID, name, and optional description."""
    prompts = []
    choices = iter(["2", "1", "", "3", "5"])
    output = []
    service = FakePlanHistoryService(
        [
            SimpleNamespace(id=7, name="Current Plan", description="Live config"),
            SimpleNamespace(id=8, name="No Description", description=""),
        ]
    )

    run.run_main_menu(
        generate_budget_plan_func=lambda: None,
        plan_history_service_factory=lambda: service,
        input_func=lambda prompt: prompts.append(prompt) or next(choices),
        output_func=output.append,
    )

    assert "7: Current Plan" in output
    assert "   Live config" in output
    assert "8: No Description" in output
    assert "Press Enter to return to the main menu..." in prompts


def test_saved_plans_save_new_plan() -> None:
    """Saving a new plan prompts for details and creates it through the service."""
    prompts = []
    choices = iter(["2", "2", "New Plan", "A useful plan", "", "3", "5"])
    output = []
    service = FakePlanHistoryService()
    config = SimpleNamespace(name="config")

    run.run_main_menu(
        generate_budget_plan_func=lambda: None,
        plan_history_service_factory=lambda: service,
        config_loader=lambda: config,
        input_func=lambda prompt: prompts.append(prompt) or next(choices),
        output_func=output.append,
    )

    assert service.created[0].name == "New Plan"
    assert service.created[0].description == "A useful plan"
    assert service.created[0].config is config
    assert "Created plan 1: New Plan" in output
    assert "Plan name: " in prompts
    assert "Description (optional): " in prompts


def test_saved_plans_save_rejects_blank_name() -> None:
    """A blank plan name does not call the service save methods."""
    choices = iter(["2", "2", "   ", "", "3", "5"])
    output = []
    service = FakePlanHistoryService()

    run.run_main_menu(
        generate_budget_plan_func=lambda: None,
        plan_history_service_factory=lambda: service,
        config_loader=lambda: SimpleNamespace(name="config"),
        input_func=lambda _prompt: next(choices),
        output_func=output.append,
    )

    assert service.created == []
    assert service.saved_versions == []
    assert "Plan name cannot be blank." in output


def test_saved_plans_save_existing_plan_when_confirmed() -> None:
    """An existing plan saves a new version only when confirmed."""
    choices = iter(["2", "2", "Current Plan", "", "yes", "", "3", "5"])
    output = []
    service = FakePlanHistoryService(
        [SimpleNamespace(id=4, name="Current Plan", description="Live")]
    )
    config = SimpleNamespace(name="config")

    run.run_main_menu(
        generate_budget_plan_func=lambda: None,
        plan_history_service_factory=lambda: service,
        config_loader=lambda: config,
        input_func=lambda _prompt: next(choices),
        output_func=output.append,
    )

    assert service.created == []
    assert service.saved_versions[0].plan_id == 4
    assert service.saved_versions[0].force is True
    assert service.saved_versions[0].config is config
    assert "Saved version 2 for Current Plan" in output


def test_saved_plans_save_existing_plan_when_declined() -> None:
    """Declining the existing-plan prompt cancels without saving."""
    choices = iter(["2", "2", "Current Plan", "", "n", "", "3", "5"])
    output = []
    service = FakePlanHistoryService(
        [SimpleNamespace(id=4, name="Current Plan", description="Live")]
    )

    run.run_main_menu(
        generate_budget_plan_func=lambda: None,
        plan_history_service_factory=lambda: service,
        config_loader=lambda: SimpleNamespace(name="config"),
        input_func=lambda _prompt: next(choices),
        output_func=output.append,
    )

    assert service.created == []
    assert service.saved_versions == []
    assert "Save cancelled." in output


def test_menu_history_placeholder_returns_to_menu() -> None:
    """The history placeholder waits before redisplaying the menu."""
    prompts = []
    choices = iter(["3", "", "5"])
    output = []

    run.run_main_menu(
        generate_budget_plan_func=lambda: None,
        input_func=lambda prompt: prompts.append(prompt) or next(choices),
        output_func=output.append,
    )

    assert output.count("DebtSnowball v1.1.0") == 2
    assert "History tools are coming in a future v1.1 update." in output
    assert "Press Enter to return to the main menu..." in prompts
    assert "Goodbye." in output


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
    assert "Please choose one of: 1, 2, 3, 4, 5." in output
    assert "Goodbye." in output


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

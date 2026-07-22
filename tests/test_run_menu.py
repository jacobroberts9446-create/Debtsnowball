"""Tests for the interactive run.py menu."""

from argparse import Namespace

import run


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


def test_menu_saved_plans_placeholder_returns_to_menu() -> None:
    """The saved-plans placeholder waits before redisplaying the menu."""
    prompts = []
    choices = iter(["2", "", "5"])
    output = []

    run.run_main_menu(
        generate_budget_plan_func=lambda: None,
        input_func=lambda prompt: prompts.append(prompt) or next(choices),
        output_func=output.append,
    )

    assert output.count("DebtSnowball v1.1.0") == 2
    assert "Saved Plans is coming in a future v1.1 update." in output
    assert "Press Enter to return to the main menu..." in prompts
    assert "Goodbye." in output


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

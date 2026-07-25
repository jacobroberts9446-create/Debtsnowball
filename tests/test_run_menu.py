"""Tests for the interactive run.py menu."""

from argparse import Namespace
import re
from types import SimpleNamespace

from app import cli
from app import console
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
        self.missing_get_plan_ids = set()
        self.restore_calls = []
        self.renamed = []
        self.deleted = []
        self.archived = []
        self.generated_saves = []

    def list_plans(self):
        return self.plans

    def get_plan(self, plan_id):
        if plan_id in self.missing_get_plan_ids:
            raise ValueError(f"plan {plan_id} was not found.")
        for plan in self.plans:
            if plan.id == plan_id:
                return plan
        raise ValueError(f"plan {plan_id} was not found.")

    def get_plan_version(self, version_id):
        for versions in self.versions_by_plan.values():
            for version in versions:
                if version.id == version_id:
                    return version
        raise ValueError(f"plan version {version_id} was not found.")

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
        self.restore_calls.append(version_id)
        self.restored_version.source_version_id = version_id
        return self.restored_version

    def plan_summary(self, version_id):
        return f"Summary for version {version_id}"

    def rename_plan(self, plan_id, name):
        plan = self.get_plan(plan_id)
        renamed = SimpleNamespace(**{**vars(plan), "name": name})
        self.plans = [renamed if item.id == plan_id else item for item in self.plans]
        self.renamed.append((plan_id, name))
        return renamed

    def save_generated_plan(self, **kwargs):
        plan_id = kwargs.get("plan_id")
        if plan_id is None:
            plan = SimpleNamespace(
                id=len(self.plans) + 1,
                name=kwargs["name"],
                description=kwargs.get("description", ""),
                updated_at="2026-07-24T20:15:00+00:00",
                current_version_id=len(self.generated_saves) + 100,
            )
            self.plans.append(plan)
            version_number = 1
        else:
            plan = self.get_plan(plan_id)
            version_number = len(self.versions_by_plan.get(plan_id, [])) + 1
        version = SimpleNamespace(
            id=len(self.generated_saves) + 100,
            plan_id=plan.id,
            version_number=version_number,
            created_at="2026-07-24T20:15:00+00:00",
        )
        self.versions_by_plan.setdefault(plan.id, []).append(version)
        self.generated_saves.append(kwargs)
        return SimpleNamespace(plan=plan, version=version, snapshot=SimpleNamespace(id=1))

    def archive_plan(self, plan_id):
        self.archived.append(plan_id)

    def delete_plan_permanently(self, plan_id, *, confirmation_name, export_path=None):
        self.deleted.append((plan_id, confirmation_name, export_path))
        self.plans = [plan for plan in self.plans if plan.id != plan_id]


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

    def remove_recent(self, plan_id):
        self.recent_plans = [plan for plan in self.recent_plans if plan.id != plan_id]


def minimal_cli_dependencies(**overrides):
    """Build lightweight CLI dependencies for dispatcher tests."""
    values = {
        "load_current_config": lambda: SimpleNamespace(name="config"),
        "save_current_plan": lambda *_args, **_kwargs: (
            "plan",
            SimpleNamespace(id=1, name="Plan"),
        ),
        "print_plan_comparison": lambda _comparison: None,
        "write_history_report": lambda _service, _plan_id, _path: None,
        "service_factory": FakePlanHistoryService,
    }
    values.update(overrides)
    return cli.CliDependencies(**values)


def test_style_text_adds_ansi_and_reset_when_color_enabled() -> None:
    """Explicitly enabled styles wrap text with ANSI and reset."""
    styled = console.success_text("Success: Done", enable_color=True)

    assert styled.startswith("\033[32m")
    assert styled.endswith(console.ANSI_RESET)
    assert strip_ansi(styled) == "Success: Done"


def test_style_text_returns_plain_text_when_color_disabled() -> None:
    """Disabled color returns script-friendly plain text."""
    assert console.error_text("Error: Nope", enable_color=False) == "Error: Nope"


def test_no_color_takes_precedence_over_force_on(monkeypatch) -> None:
    """NO_COLOR disables color even when DEBTSNOWBALL_COLOR is forced on."""
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("DEBTSNOWBALL_COLOR", "1")

    assert not console.ansi_color_enabled(
        FakeStream(True),
        {"NO_COLOR": "1", "DEBTSNOWBALL_COLOR": "1"},
    )


def test_debtsnowball_color_force_on(monkeypatch) -> None:
    """DEBTSNOWBALL_COLOR=1 forces ANSI output on."""
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("DEBTSNOWBALL_COLOR", "1")

    assert console.warning_text("Warning: Careful").startswith("\033[33m")


def test_debtsnowball_color_force_off(monkeypatch) -> None:
    """DEBTSNOWBALL_COLOR=0 forces ANSI output off."""
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("DEBTSNOWBALL_COLOR", "0")

    assert console.success_text("Success: Done") == "Success: Done"


def test_color_disabled_for_non_tty_output() -> None:
    """Non-TTY output disables color automatically."""
    enabled = console.ansi_color_enabled(FakeStream(False), {"TERM": "xterm-256color"})

    assert not enabled


def test_color_disabled_for_unsupported_terminal() -> None:
    """Unsupported terminal declarations disable automatic color."""
    enabled = console.ansi_color_enabled(FakeStream(True), {"TERM": "dumb"})

    assert not enabled


def test_format_helpers_style_success_warning_error_and_headings(monkeypatch) -> None:
    """Formatting helpers use the centralized styling layer."""
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("DEBTSNOWBALL_COLOR", "1")
    output = []

    console.print_success("Saved.", output.append)
    console.print_warning("Review.", output.append)
    console.print_error("Failed.", output.append)
    console.print_menu_title("Menu", output.append)

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

    console.print_success("Saved.", output.append)
    console.print_warning("Review.", output.append)
    console.print_error("Failed.", output.append)
    console.print_menu_title("Menu", output.append)

    text = output_text(output)
    assert "\033[" not in text
    assert "Success: Saved." in text
    assert "Warning: Review." in text
    assert "Error: Failed." in text
    assert "Menu" in text


def test_format_percentage_handles_ratios_and_whole_percentages() -> None:
    """Percentage formatting preserves whole percentages and expands ratios."""
    assert console.format_percentage("0.5") == "50%"
    assert console.format_percentage("1") == "100%"
    assert console.format_percentage("0") == "0%"
    assert console.format_percentage("0.325") == "32.5%"
    assert console.format_percentage("50") == "50%"
    assert console.format_percentage("12.5") == "12.5%"


def test_main_menu_no_longer_shows_config_generation() -> None:
    """The interactive menu keeps guided plan setup as the normal workflow."""
    output = []
    choices = iter(["4"])

    run.run_main_menu(
        generate_budget_plan_func=lambda: None,
        input_func=lambda _prompt: next(choices),
        output_func=output.append,
    )

    assert "1. Create New Plan" in output
    assert "2. Saved Plans" in output
    assert "3. Help" in output
    assert "4. Exit" in output
    assert "Generate Plan From Config" not in output
    assert "5. Help" not in output
    assert "6. Exit" not in output


def test_menu_create_new_plan_runs_debt_entry_workflow() -> None:
    """Choosing Create New Plan runs debt entry without saving anything yet."""
    calls = []
    choices = iter(["1", "4"])
    output = []
    generated = object()

    run.run_main_menu(
        generate_budget_plan_func=lambda: None,
        plan_setup_func=lambda **_kwargs: calls.append("plan-setup") or generated,
        results_viewer_func=lambda result, **_kwargs: calls.append(("viewer", result)),
        input_func=lambda _prompt: next(choices),
        output_func=output.append,
    )

    assert calls == ["plan-setup", ("viewer", generated)]
    assert "Returned from plan results." in output
    assert "Success: Goodbye." in output


def test_menu_help_explains_options_and_returns_to_menu() -> None:
    """Choosing help prints all option descriptions before accepting another choice."""
    prompts = []
    choices = iter(["3", "", "4"])
    output = []

    run.run_main_menu(
        generate_budget_plan_func=lambda: None,
        input_func=lambda prompt: prompts.append(prompt) or next(choices),
        output_func=output.append,
    )

    assert output.count("DebtSnowball v1.2.0-dev") == 2
    assert "Create New Plan: starts the guided interactive setup workflow." in output
    assert "Generate Plan From Config" not in output
    assert "Saved Plans: opens separate scenarios or people's saved plans." in output
    assert (
        "Saved Plan History: select a plan in Saved Plans to view prior versions."
        in output
    )
    assert not any(line.startswith("History:") for line in output)
    assert "Help: explains the menu options." in output
    assert "Exit: closes DebtSnowball without generating a plan." in output
    assert "Press Enter to continue..." in prompts
    assert "Success: Goodbye." in output


def test_menu_invalid_input_returns_to_menu() -> None:
    """Invalid input displays a friendly message and loops back to the menu."""
    choices = iter(["not a choice", "4"])
    output = []

    run.run_main_menu(
        generate_budget_plan_func=lambda: None,
        input_func=lambda _prompt: next(choices),
        output_func=output.append,
    )

    assert output.count("DebtSnowball v1.2.0-dev") == 2
    assert "Warning: Please choose one of: 1, 2, 3, 4." in output
    assert "Success: Goodbye." in output


def test_main_preserves_existing_argparse_command_behavior(monkeypatch) -> None:
    """Supplying an argparse command bypasses the interactive menu."""
    calls = []

    monkeypatch.setattr(
        cli,
        "run_cli",
        lambda args, dependencies=None: calls.append(args.command),
    )

    cli.main(
        ["plan", "list"],
        interactive_runner=lambda: calls.append("menu"),
        dependencies=minimal_cli_dependencies(),
    )

    assert calls == ["plan"]


def test_run_cli_accepts_parsed_namespace_for_existing_commands(monkeypatch) -> None:
    """The existing CLI dispatcher still accepts parsed argparse namespaces."""
    calls = []

    class FakeService:
        pass

    monkeypatch.setattr(
        cli,
        "run_plan_command",
        lambda _service, args, _dependencies: calls.append(
            ("plan", args.plan_command)
        ),
    )

    cli.run_cli(
        Namespace(command="plan", plan_command="list"),
        dependencies=minimal_cli_dependencies(service_factory=FakeService),
    )

    assert calls == [("plan", "list")]


"""Interactive save workflow for generated in-memory plans."""

import sqlite3
from dataclasses import dataclass
from typing import Any

from app.console import OutputFunc, print_error, print_warning
from app.database import DatabaseMigrationError
from app.history import PlanHistoryService
from app.menu import InputFunc, MenuOption, display_menu, wait_for_enter
from app.plan_generation import setup_to_engine_config
from app.workflows.plan_presenter import format_saved_datetime

EXPECTED_SAVE_ERRORS = (ValueError, OSError, sqlite3.Error, DatabaseMigrationError)


@dataclass
class PlanSaveState:
    """Tracks whether a generated plan has been saved during this session."""

    plan_id: int | None = None
    plan_name: str | None = None
    version_id: int | None = None
    version_number: int | None = None
    saved_at: str | None = None

    @property
    def saved(self) -> bool:
        """Return whether this generated plan has been saved."""
        return self.plan_id is not None and self.version_id is not None


@dataclass(frozen=True)
class PlanSaveResult:
    """Result of saving a generated plan."""

    plan: Any
    version: Any
    snapshot: Any | None
    created_new_plan: bool
    created_new_version: bool


def run_save_plan_workflow(
    generated_plan,
    state: PlanSaveState,
    service_factory=PlanHistoryService,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> None:
    """Save a generated plan and update session save state on success."""
    try:
        service = service_factory()
        result = (
            prompt_saved_plan_action(generated_plan, state, service, input_func, output_func)
            if state.saved
            else save_as_new_plan(generated_plan, state, service, input_func, output_func)
        )
    except EXPECTED_SAVE_ERRORS as exc:
        print_error(str(exc), output_func)
        wait_for_enter(input_func)
        return

    if result is None:
        return

    mark_saved(state, result)
    print_save_success(result, output_func)
    wait_for_enter(input_func)


def prompt_saved_plan_action(
    generated_plan,
    state: PlanSaveState,
    service: PlanHistoryService,
    input_func: InputFunc,
    output_func: OutputFunc,
) -> PlanSaveResult | None:
    """Prompt for how to save a plan already saved in this session."""
    while True:
        display_menu(
            "Save Plan",
            [
                MenuOption("1", "Save New Version", lambda: True),
                MenuOption("2", "Save As New Plan", lambda: True),
                MenuOption("3", "Cancel", lambda: True),
            ],
            output_func,
        )
        choice = input_func("Choose an option: ").strip()
        if choice == "1":
            return save_existing_plan_version(
                generated_plan,
                state.plan_id,
                service,
                previous_version_id=state.version_id,
            )
        if choice == "2":
            return save_as_new_plan(generated_plan, state, service, input_func, output_func)
        if choice == "3":
            print_warning("Save cancelled.", output_func)
            return None
        print_warning("Please choose one of: 1, 2, 3.", output_func)


def save_as_new_plan(
    generated_plan,
    state: PlanSaveState,
    service: PlanHistoryService,
    input_func: InputFunc,
    output_func: OutputFunc,
) -> PlanSaveResult | None:
    """Prompt for a plan name and save as a new plan or existing version."""
    default_name = state.plan_name or getattr(generated_plan, "plan_name", "New Plan")
    name = prompt_plan_name(default_name, input_func, output_func)
    existing = [plan for plan in service.list_plans() if plan.name == name]
    if existing:
        confirm = input_func(
            f"A plan named '{name}' already exists. Save a new version? [y/N]: "
        ).strip()
        if confirm.casefold() not in {"y", "yes"}:
            print_warning("Save cancelled.", output_func)
            return None
        return save_existing_plan_version(generated_plan, existing[0].id, service)

    result = service.save_generated_plan(
        name=name,
        config=setup_to_engine_config(generated_plan.setup),
        forecast=generated_plan.forecast,
        starting_savings=generated_plan.setup.current_savings,
        starting_debts=generated_plan.setup.debts,
        change_note="Saved from interactive results viewer",
        source="interactive",
    )
    return PlanSaveResult(
        plan=result.plan,
        version=result.version,
        snapshot=result.snapshot,
        created_new_plan=result.created_new_plan,
        created_new_version=result.created_new_version,
    )


def save_existing_plan_version(
    generated_plan,
    plan_id: int | None,
    service: PlanHistoryService,
    *,
    previous_version_id: int | None = None,
) -> PlanSaveResult:
    """Save the generated plan as a version of an existing plan."""
    if plan_id is None:
        raise ValueError("A saved plan is required before saving a new version.")
    result = service.save_generated_plan(
        name="",
        plan_id=plan_id,
        config=setup_to_engine_config(generated_plan.setup),
        forecast=generated_plan.forecast,
        starting_savings=generated_plan.setup.current_savings,
        starting_debts=generated_plan.setup.debts,
        change_note="Saved from interactive results viewer",
        source="interactive",
    )
    return PlanSaveResult(
        plan=result.plan,
        version=result.version,
        snapshot=result.snapshot,
        created_new_plan=False,
        created_new_version=(
            result.created_new_version and result.version.id != previous_version_id
        ),
    )


def prompt_plan_name(
    default_name: str,
    input_func: InputFunc,
    output_func: OutputFunc,
) -> str:
    """Prompt for a nonblank save name, preserving the default on blank input."""
    while True:
        raw_name = input_func(f"Plan name [{default_name}]: ").strip()
        name = raw_name or default_name
        if name.strip():
            return name.strip()
        print_warning("Plan name cannot be blank.", output_func)


def mark_saved(state: PlanSaveState, result: PlanSaveResult) -> None:
    """Update the session state after a successful save."""
    state.plan_id = result.plan.id
    state.plan_name = result.plan.name
    state.version_id = result.version.id
    state.version_number = result.version.version_number
    state.saved_at = result.version.created_at


def print_save_success(
    result: PlanSaveResult,
    output_func: OutputFunc = print,
) -> None:
    """Print the save success screen."""
    output_func("")
    output_func("Plan saved successfully.")
    output_func("")
    output_func("Plan:")
    output_func(result.plan.name)
    output_func("")
    output_func("Version:")
    output_func(str(result.version.version_number))
    output_func("")
    output_func("Saved:")
    output_func(format_saved_datetime(result.version.created_at))

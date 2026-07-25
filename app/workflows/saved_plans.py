"""Saved-plan interactive workflow actions."""

from collections.abc import Callable
from typing import Any

from app.bill_input import collect_bills
from app.budget_setup import review_budget_setup
from app.config import Config
from app.console import print_error, print_section_header, print_success, print_warning
from app.history import PlanHistoryService
from app.menu import (
    InputFunc,
    MenuOption,
    OutputFunc,
    display_menu,
    run_menu,
    wait_for_enter,
)
from app.preferences import RecentPlanPreferences
from app.plan_generation import generate_plan_from_setup
from app.plan_setup import collect_setup_debts
from app.savings_setup import prompt_savings_strategy
from app.workflows.plan_history import run_selected_plan_history_menu
from app.workflows.plan_presenter import (
    display_plan_name,
    format_saved_datetime,
    version_count_label,
)
from app.workflows.plan_progress import run_plan_progress_menu
from app.workflows.workbook_export import (
    build_workbook_outputs,
    config_from_plan_version,
    generate_saved_plan_workbook_action,
    latest_plan_version,
)

SelectedPlanHistoryAction = Callable[
    [PlanHistoryService, RecentPlanPreferences, Any, InputFunc, OutputFunc],
    bool,
]
SetupFromConfig = Callable[[str, Config], Any]
SetupFromGeneratedPlan = Callable[[Any], Config]


def run_saved_plans_menu(
    plan_history_service_factory: Callable[[], PlanHistoryService] = PlanHistoryService,
    preferences_factory: Callable[[], RecentPlanPreferences] = RecentPlanPreferences,
    config_loader: Callable[[], Config] | None = None,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
    selected_plan_history_action: SelectedPlanHistoryAction | None = None,
    setup_from_config_func: SetupFromConfig | None = None,
    setup_from_generated_plan_func: SetupFromGeneratedPlan | None = None,
) -> bool:
    """Open saved-plan tools and return to the main menu when finished."""
    config_loader = config_loader or _missing_config_loader
    try:
        service = plan_history_service_factory()
        preferences = preferences_factory()
    except (FileNotFoundError, ValueError) as exc:
        print_error(str(exc), output_func)
        wait_for_enter(input_func)
        return False

    run_saved_plan_selection_loop(
        service,
        preferences,
        config_loader,
        input_func=input_func,
        output_func=output_func,
        selected_plan_history_action=selected_plan_history_action,
        setup_from_config_func=setup_from_config_func,
        setup_from_generated_plan_func=setup_from_generated_plan_func,
    )
    return False


def run_saved_plan_selection_loop(
    service: PlanHistoryService,
    preferences: RecentPlanPreferences,
    config_loader: Callable[[], Config],
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
    selected_plan_history_action: SelectedPlanHistoryAction | None = None,
    setup_from_config_func: SetupFromConfig | None = None,
    setup_from_generated_plan_func: SetupFromGeneratedPlan | None = None,
) -> None:
    """Show saved plans as scenarios or people and open the selected plan."""
    while True:
        output_func("")
        print_section_header("Saved Plans", output_func)
        try:
            plans = service.list_plans()
        except ValueError as exc:
            print_error(str(exc), output_func)
            wait_for_enter(input_func)
            return

        if not plans:
            output_func("No saved plans yet.")
            output_func("Create a new plan and save it to see it here.")
            wait_for_enter(input_func)
            return

        for index, plan in enumerate(plans, start=1):
            output_func(f"{index}. {display_plan_name(plan)}")
            output_func(f"   Updated: {format_saved_datetime(plan_updated_at(plan))}")
            output_func(f"   Versions: {version_count_label(service, plan)}")
            if plan.description:
                output_func(f"   Description: {plan.description}")
            output_func("")

        back_key = str(len(plans) + 1)
        output_func(f"{back_key}. Back")
        output_func("")
        choice = input_func("Choose an option: ").strip()
        if choice == back_key:
            return

        try:
            selected_index = int(choice)
        except ValueError:
            print_warning(f"Please choose one of: {valid_choice_label(len(plans))}.", output_func)
            continue

        if not 1 <= selected_index <= len(plans):
            print_warning(f"Please choose one of: {valid_choice_label(len(plans))}.", output_func)
            continue

        plan = plans[selected_index - 1]
        try:
            current_plan = service.get_plan(plan.id)
        except (FileNotFoundError, ValueError) as exc:
            print_error(str(exc), output_func)
            wait_for_enter(input_func)
            continue
        preferences.mark_recent(current_plan.id, current_plan.name)
        run_selected_plan_menu(
            service,
            preferences,
            config_loader,
            current_plan,
            input_func=input_func,
            output_func=output_func,
            selected_plan_history_action=selected_plan_history_action,
            setup_from_config_func=setup_from_config_func,
            setup_from_generated_plan_func=setup_from_generated_plan_func,
        )


def list_saved_plans_action(
    service: PlanHistoryService,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> bool:
    """List saved plans, then return to the saved-plans submenu."""
    output_func("")
    try:
        plans = service.list_plans()
    except ValueError as exc:
        print_error(str(exc), output_func)
    else:
        if not plans:
            output_func("No saved plans yet.")
            output_func("Create a new plan and save it to see it here.")
        else:
            for index, plan in enumerate(plans, start=1):
                output_func(f"{index}. {display_plan_name(plan)}")
                output_func(f"   Updated: {format_saved_datetime(plan_updated_at(plan))}")
                output_func(f"   Versions: {version_count_label(service, plan)}")
                if plan.description:
                    output_func(f"   Description: {plan.description}")
                output_func("")

    wait_for_enter(input_func)
    return False


def list_recent_plans_action(
    service: PlanHistoryService,
    preferences: RecentPlanPreferences,
    config_loader: Callable[[], Config],
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
    selected_plan_history_action: SelectedPlanHistoryAction | None = None,
    setup_from_config_func: SetupFromConfig | None = None,
    setup_from_generated_plan_func: SetupFromGeneratedPlan | None = None,
) -> bool:
    """Show actionable recently used plans."""
    while True:
        try:
            plans = service.list_plans()
            recent_plans = preferences.list_existing_recent_plans(plans)
        except (OSError, ValueError) as exc:
            print_error(str(exc), output_func)
            wait_for_enter(input_func)
            return False
        if not recent_plans:
            output_func("")
            output_func("No saved plans yet.")
            output_func("Create a new plan and save it to see it here.")
            wait_for_enter(input_func)
            return False

        options = [
            MenuOption(
                str(index),
                display_plan_name(plan),
                lambda plan=plan: open_recent_plan_action(
                    service,
                    preferences,
                    config_loader,
                    plan.id,
                    input_func,
                    output_func,
                    selected_plan_history_action,
                    setup_from_config_func,
                    setup_from_generated_plan_func,
                ),
            )
            for index, plan in enumerate(recent_plans, start=1)
        ]
        back_key = str(len(options) + 1)
        options.append(MenuOption(back_key, "Back", lambda: True))
        display_menu("Recent Plans", options, output_func)

        option_map = {option.key: option for option in options}
        choice = input_func("Choose an option: ").strip()
        option = option_map.get(choice)
        if option is None:
            print_warning(f"Please choose one of: {', '.join(option_map)}.", output_func)
            continue
        if option.key == back_key:
            return False
        option.action()

    return False


def open_recent_plan_action(
    service: PlanHistoryService,
    preferences: RecentPlanPreferences,
    config_loader: Callable[[], Config],
    plan_id: int,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
    selected_plan_history_action: SelectedPlanHistoryAction | None = None,
    setup_from_config_func: SetupFromConfig | None = None,
    setup_from_generated_plan_func: SetupFromGeneratedPlan | None = None,
) -> bool:
    """Open the selected recent plan or remove it if stale."""
    try:
        plan = service.get_plan(plan_id)
    except (FileNotFoundError, ValueError):
        preferences.remove_recent(plan_id)
        print_warning("That recent plan no longer exists and was removed.", output_func)
        wait_for_enter(input_func)
    else:
        preferences.mark_recent(plan.id, plan.name)
        run_selected_plan_menu(
            service,
            preferences,
            config_loader,
            plan,
            input_func=input_func,
            output_func=output_func,
            selected_plan_history_action=selected_plan_history_action,
            setup_from_config_func=setup_from_config_func,
            setup_from_generated_plan_func=setup_from_generated_plan_func,
        )

    return False


def run_selected_plan_menu(
    service: PlanHistoryService,
    preferences: RecentPlanPreferences,
    config_loader: Callable[[], Config],
    plan,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
    selected_plan_history_action: SelectedPlanHistoryAction | None = None,
    setup_from_config_func: SetupFromConfig | None = None,
    setup_from_generated_plan_func: SetupFromGeneratedPlan | None = None,
) -> None:
    """Open details and implemented actions for one selected plan."""
    selected_plan_history_action = (
        selected_plan_history_action or run_selected_plan_history_menu
    )
    options = [
        MenuOption(
            "1",
            "View Latest Plan Summary",
            lambda: view_latest_plan_summary_action(
                service,
                plan,
                input_func,
                output_func,
            ),
        ),
        MenuOption(
            "2",
            "Generate Excel Workbook",
            lambda: generate_saved_plan_workbook_action(
                service,
                plan,
                input_func,
                output_func,
            ),
        ),
        MenuOption(
            "3",
            "Create New Version",
            lambda: create_saved_plan_version_action(
                service,
                preferences,
                plan,
                input_func,
                output_func,
                setup_from_config_func,
                setup_from_generated_plan_func,
            ),
        ),
        MenuOption(
            "4",
            "History",
            lambda: selected_plan_history_action(
                service,
                preferences,
                plan,
                input_func,
                output_func,
            ),
        ),
        MenuOption(
            "5",
            "Rename Plan",
            lambda: rename_saved_plan_action(
                service,
                preferences,
                plan,
                input_func,
                output_func,
            ),
        ),
        MenuOption(
            "6",
            "Duplicate Plan",
            lambda: duplicate_saved_plan_action(
                service,
                preferences,
                plan,
                input_func,
                output_func,
            ),
        ),
        MenuOption(
            "7",
            "Delete Plan",
            lambda: delete_saved_plan_action(
                service,
                preferences,
                plan,
                input_func,
                output_func,
            ),
        ),
        MenuOption(
            "8",
            "Track Progress",
            lambda: run_plan_progress_menu(
                service,
                plan,
                input_func,
                output_func,
            ),
        ),
        MenuOption("9", "Back", lambda: True),
    ]

    run_menu(
        title=f"Plan: {display_plan_name(plan)}",
        options=options,
        input_func=input_func,
        output_func=output_func,
        title_renderer=lambda _title, output: render_plan_details(
            service,
            plan,
            output,
        ),
    )


def render_plan_details(
    service: PlanHistoryService,
    plan,
    output_func: OutputFunc = print,
) -> None:
    """Render details for one saved plan."""
    print_section_header(f"Plan: {display_plan_name(plan)}", output_func)
    if plan.description:
        output_func(f"Description: {plan.description}")
    output_func(f"Last updated: {format_saved_datetime(plan_updated_at(plan))}")
    output_func(f"Versions: {version_count_label(service, plan)}")
    output_func("")


def view_latest_plan_summary_action(
    service: PlanHistoryService,
    plan,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> bool:
    """Display the latest stored forecast summary for a saved plan."""
    output_func("")
    try:
        version = latest_plan_version(service, plan)
        output_func(service.plan_summary(version.id))
    except (FileNotFoundError, ValueError) as exc:
        print_error(str(exc), output_func)
    wait_for_enter(input_func)
    return False


def create_saved_plan_version_action(
    service: PlanHistoryService,
    preferences: RecentPlanPreferences,
    plan,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
    setup_from_config_func: SetupFromConfig | None = None,
    setup_from_generated_plan_func: SetupFromGeneratedPlan | None = None,
) -> bool:
    """Edit the latest saved plan assumptions and save a new version."""
    output_func("")
    if setup_from_config_func is None or setup_from_generated_plan_func is None:
        print_error("saved plan version editing is not configured.", output_func)
        wait_for_enter(input_func)
        return False
    try:
        config = config_from_plan_version(latest_plan_version(service, plan))
        setup = setup_from_config_func(display_plan_name(plan), config)
        generated_plan = review_budget_setup(
            setup,
            input_func,
            output_func,
            collect_setup_debts,
            collect_bills,
            prompt_savings_strategy,
            generate_plan_from_setup,
        )
        if generated_plan is None:
            print_warning("New version cancelled.", output_func)
            wait_for_enter(input_func)
            return False
        config = setup_from_generated_plan_func(generated_plan)
        result = service.save_generated_plan(
            name=plan.name,
            plan_id=plan.id,
            config=config,
            forecast=generated_plan.forecast,
            starting_savings=generated_plan.setup.current_savings,
            starting_debts=generated_plan.setup.debts,
            change_note="Saved from selected plan details",
            source="interactive",
            force=True,
        )
    except (FileNotFoundError, ValueError, RuntimeError, OSError) as exc:
        print_error(str(exc), output_func)
    else:
        preferences.mark_recent(result.plan.id, result.plan.name)
        print_success(
            f"Saved version {result.version.version_number} for {result.plan.name}.",
            output_func,
        )
        wait_for_enter(input_func)
        return True
    wait_for_enter(input_func)
    return False


def rename_saved_plan_action(
    service: PlanHistoryService,
    preferences: RecentPlanPreferences,
    plan,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> bool:
    """Rename the selected plan without changing version history."""
    output_func("")
    new_name = input_func(f"New plan name [{display_plan_name(plan)}]: ").strip()
    if not new_name:
        print_warning("Plan name cannot be blank.", output_func)
        wait_for_enter(input_func)
        return False

    try:
        renamed = service.rename_plan(plan.id, new_name)
    except (FileNotFoundError, ValueError) as exc:
        print_error(str(exc), output_func)
        wait_for_enter(input_func)
        return False

    preferences.mark_recent(renamed.id, renamed.name)
    print_success(f"Renamed plan to {renamed.name}.", output_func)
    wait_for_enter(input_func)
    return True


def duplicate_saved_plan_action(
    service: PlanHistoryService,
    preferences: RecentPlanPreferences,
    plan,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> bool:
    """Duplicate the latest selected plan version as a new saved plan."""
    output_func("")
    new_name = input_func("New duplicate plan name: ").strip()
    if not new_name:
        print_warning("Plan name cannot be blank.", output_func)
        wait_for_enter(input_func)
        return False
    try:
        if any(existing.name == new_name for existing in service.list_plans()):
            print_warning("A saved plan with that name already exists.", output_func)
            wait_for_enter(input_func)
            return False
        config = config_from_plan_version(latest_plan_version(service, plan))
        summaries, forecast, _scenario_comparison, _target_result = build_workbook_outputs(
            config,
        )
        result = service.save_generated_plan(
            name=new_name,
            config=config,
            forecast=forecast,
            starting_savings=config.settings.starting_savings,
            starting_debts=config.debts,
            description=plan.description,
            change_note=f"Duplicated from {display_plan_name(plan)}",
            source="interactive",
            pay_period_summaries=summaries,
            force=True,
        )
    except (FileNotFoundError, ValueError, RuntimeError, OSError) as exc:
        print_error(str(exc), output_func)
    else:
        preferences.mark_recent(result.plan.id, result.plan.name)
        print_success(
            f"Duplicated plan as {result.plan.name} with version 1.",
            output_func,
        )
        wait_for_enter(input_func)
        return True
    wait_for_enter(input_func)
    return False


def delete_saved_plan_action(
    service: PlanHistoryService,
    preferences: RecentPlanPreferences,
    plan,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> bool:
    """Permanently delete a selected saved plan after explicit confirmation."""
    output_func("")
    output_func(f"Plan: {display_plan_name(plan)}")
    output_func(f"Versions: {version_count_label(service, plan)}")
    confirm = input_func("Type DELETE to permanently remove this plan: ").strip()
    if confirm != "DELETE":
        print_warning("Delete cancelled.", output_func)
        wait_for_enter(input_func)
        return False

    try:
        service.archive_plan(plan.id)
        service.delete_plan_permanently(plan.id, confirmation_name=plan.name)
    except (FileNotFoundError, ValueError, RuntimeError, OSError) as exc:
        print_error(str(exc), output_func)
        wait_for_enter(input_func)
        return False

    preferences.remove_recent(plan.id)
    print_success(f"Deleted plan {display_plan_name(plan)}.", output_func)
    wait_for_enter(input_func)
    return True


def save_selected_plan_version_action(
    service: PlanHistoryService,
    preferences: RecentPlanPreferences,
    config_loader: Callable[[], Config],
    plan,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> bool:
    """Save a new version for a selected recent plan after confirmation."""
    output_func("")
    note = input_func("Version note (optional): ").strip()
    confirm = input_func(
        f"Save a new version for '{plan.name}'? [y/N]: "
    ).strip()
    if confirm.casefold() not in {"y", "yes"}:
        print_warning("Save cancelled.", output_func)
        wait_for_enter(input_func)
        return False

    try:
        current_plan = service.get_plan(plan.id)
        result = save_current_plan(
            service,
            config_loader(),
            name=current_plan.name,
            change_note=note or "Saved from recent plan menu",
            source="interactive",
            force=True,
        )
    except (FileNotFoundError, ValueError) as exc:
        print_error(str(exc), output_func)
    else:
        version = result[1]
        preferences.mark_recent(current_plan.id, current_plan.name)
        print_success(
            f"Saved version {version.version_number} for {current_plan.name}",
            output_func,
        )

    wait_for_enter(input_func)
    return False


def save_current_plan_action(
    service: PlanHistoryService,
    preferences: RecentPlanPreferences,
    config_loader: Callable[[], Config],
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> bool:
    """Prompt for saved-plan details and save through PlanHistoryService."""
    output_func("")
    name = input_func("Plan name: ").strip()
    if not name:
        print_warning("Plan name cannot be blank.", output_func)
        wait_for_enter(input_func)
        return False

    description = input_func("Description (optional): ").strip()
    try:
        config = config_loader()
        existing = [plan for plan in service.list_plans() if plan.name == name]
    except (FileNotFoundError, ValueError) as exc:
        print_error(str(exc), output_func)
        wait_for_enter(input_func)
        return False

    if existing:
        confirm = input_func(
            f"A plan named '{name}' already exists. Save a new version? [y/N]: "
        ).strip()
        if confirm.casefold() not in {"y", "yes"}:
            print_warning("Save cancelled.", output_func)
            wait_for_enter(input_func)
            return False

    try:
        result = save_current_plan(
            service,
            config,
            name=name,
            description=description,
            change_note="Saved from interactive menu",
            source="interactive",
            force=bool(existing),
        )
    except (FileNotFoundError, ValueError) as exc:
        print_error(str(exc), output_func)
    else:
        if result[0] == "created":
            preferences.mark_recent(result[1].id, result[1].name)
            print_success(f"Created plan {result[1].id}: {result[1].name}", output_func)
        else:
            preferences.mark_recent(existing[0].id, existing[0].name)
            print_success(
                f"Saved version {result[1].version_number} for {name}",
                output_func,
            )

    wait_for_enter(input_func)
    return False


def plan_updated_at(plan) -> str:
    """Return the best available saved-plan update timestamp."""
    return getattr(plan, "updated_at", "") or getattr(plan, "created_at", "")


def valid_choice_label(plan_count: int) -> str:
    """Return a readable list of valid saved-plan menu choices."""
    return ", ".join(str(index) for index in range(1, plan_count + 2))


def save_current_plan(
    service: PlanHistoryService,
    config: Config,
    *,
    name: str,
    description: str = "",
    change_note: str = "Saved from CLI",
    source: str = "cli",
    force: bool = False,
):
    """Create a plan or save a new version using the existing service behavior."""
    existing = [plan for plan in service.list_plans() if plan.name == name]
    if existing:
        version = service.save_plan_version(
            existing[0].id,
            config,
            change_note=change_note,
            source=source,
            force=force,
        )
        return "version", version

    plan = service.create_plan(
        name,
        config,
        description=description,
        change_note=change_note,
        source=source,
    )
    return "created", plan


def _missing_config_loader() -> Config:
    raise ValueError("saved plan config loading is not configured.")

"""Interactive plan-history and version-management workflows."""

from collections.abc import Callable
from typing import Any

from app.console import print_error, print_success, print_table, print_warning
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
from app.workflows.plan_presenter import (
    display_plan_name,
    format_saved_datetime,
    version_count_label,
)

__all__ = [
    "print_plan_comparison",
    "run_history_menu",
    "run_selected_plan_history_menu",
    "view_selected_plan_history_action",
]


def run_selected_plan_history_menu(
    service: PlanHistoryService,
    preferences: RecentPlanPreferences,
    plan: Any,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> bool:
    """Open numbered History actions for one selected saved plan."""
    try:
        current_plan = service.get_plan(plan.id)
    except (FileNotFoundError, ValueError) as exc:
        print_error(str(exc), output_func)
        wait_for_enter(input_func)
        return False

    preferences.mark_recent(current_plan.id, current_plan.name)
    options = [
        MenuOption(
            "1",
            "View Versions",
            lambda: view_selected_plan_history_action(
                service,
                preferences,
                current_plan,
                input_func,
                output_func,
            ),
        ),
        MenuOption(
            "2",
            "Compare Versions",
            lambda: compare_selected_plan_versions_action(
                service,
                current_plan,
                input_func,
                output_func,
            ),
        ),
        MenuOption(
            "3",
            "Restore Version",
            lambda: restore_selected_plan_version_by_choice_action(
                service,
                preferences,
                current_plan,
                input_func,
                output_func,
            ),
        ),
        MenuOption("4", "Back", lambda: True),
    ]
    run_menu(
        title=f"History - {display_plan_name(current_plan)}",
        options=options,
        input_func=input_func,
        output_func=output_func,
    )
    return False


def view_selected_plan_history_action(
    service: PlanHistoryService,
    preferences: RecentPlanPreferences,
    plan: Any,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> bool:
    """Display history for a selected plan and allow version summary viewing."""
    output_func("")
    try:
        versions = display_plan_history(service, preferences, plan, output_func)
        if versions:
            select_plan_version_summary(service, versions, input_func, output_func)
    except (FileNotFoundError, ValueError) as exc:
        print_error(str(exc), output_func)
    wait_for_enter(input_func)
    return False


def compare_selected_plan_versions_action(
    service: PlanHistoryService,
    plan: Any,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> bool:
    """Compare two numbered versions belonging to the selected plan."""
    output_func("")
    try:
        versions = service.list_plan_versions(plan.id)
    except (FileNotFoundError, ValueError) as exc:
        print_error(str(exc), output_func)
        wait_for_enter(input_func)
        return False

    if not versions:
        print_warning("No saved versions found for that plan.", output_func)
        wait_for_enter(input_func)
        return False

    earlier = select_version_by_number(
        versions,
        "Select Starting Version",
        input_func,
        output_func,
    )
    if earlier is None:
        print_warning("Comparison cancelled.", output_func)
        wait_for_enter(input_func)
        return False

    later = select_version_by_number(
        versions,
        "Select Ending Version",
        input_func,
        output_func,
    )
    if later is None:
        print_warning("Comparison cancelled.", output_func)
        wait_for_enter(input_func)
        return False

    try:
        comparison = service.compare_plan_versions(earlier.id, later.id)
    except (FileNotFoundError, ValueError) as exc:
        print_error(str(exc), output_func)
    else:
        print_plan_comparison(comparison, output_func)

    wait_for_enter(input_func)
    return False


def restore_selected_plan_version_by_choice_action(
    service: PlanHistoryService,
    preferences: RecentPlanPreferences,
    plan: Any,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> bool:
    """Restore a numbered version belonging to the selected plan."""
    output_func("")
    try:
        versions = service.list_plan_versions(plan.id)
    except (FileNotFoundError, ValueError) as exc:
        print_error(str(exc), output_func)
        wait_for_enter(input_func)
        return False

    if not versions:
        print_warning("No saved versions found for that plan.", output_func)
        wait_for_enter(input_func)
        return False

    selected = select_version_by_number(
        versions,
        "Select Version To Restore",
        input_func,
        output_func,
    )
    if selected is None:
        print_warning("Restore cancelled.", output_func)
        wait_for_enter(input_func)
        return False

    confirm = input_func(
        f"Restore version {selected.version_number} as a new version? [y/N]: "
    )
    if confirm.strip().casefold() not in {"y", "yes"}:
        print_warning("Restore cancelled.", output_func)
        wait_for_enter(input_func)
        return False

    try:
        restored = service.restore_plan_version(selected.id)
    except (FileNotFoundError, ValueError) as exc:
        print_error(str(exc), output_func)
    else:
        preferences.mark_recent(plan.id, plan.name)
        print_success(f"Restored as version {restored.version_number}.", output_func)

    wait_for_enter(input_func)
    return False


def select_version_by_number(
    versions: list[Any],
    title: str,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> Any | None:
    """Select a version through numbered choices, or return None for Back."""
    options = [
        MenuOption(
            str(index),
            f"Version {version.version_number}",
            lambda version=version: version,
        )
        for index, version in enumerate(versions, start=1)
    ]
    back_key = str(len(options) + 1)
    options.append(MenuOption(back_key, "Back", lambda: None))
    display_menu(title, options, output_func)
    option_map = {option.key: option for option in options}
    while True:
        choice = input_func("Choose an option: ").strip()
        option = option_map.get(choice)
        if option is not None:
            return option.action()
        print_warning(f"Please choose one of: {', '.join(option_map)}.", output_func)


def restore_selected_plan_version_action(
    service: PlanHistoryService,
    preferences: RecentPlanPreferences,
    plan: Any,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> bool:
    """Restore a version that belongs to the selected plan."""
    output_func("")
    version_id = prompt_positive_int("Version ID: ", input_func, output_func)
    if version_id is None:
        wait_for_enter(input_func)
        return False

    try:
        source_version = service.get_plan_version(version_id)
    except (FileNotFoundError, ValueError) as exc:
        print_error(str(exc), output_func)
        wait_for_enter(input_func)
        return False

    if source_version.plan_id != plan.id:
        print_warning("That version does not belong to the selected plan.", output_func)
        wait_for_enter(input_func)
        return False

    return restore_version_by_id_action(
        service,
        preferences,
        version_id,
        input_func,
        output_func,
        plan=plan,
    )


def run_history_menu(
    plan_history_service_factory: Callable[[], PlanHistoryService] = PlanHistoryService,
    preferences_factory: Callable[[], RecentPlanPreferences] = RecentPlanPreferences,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> bool:
    """Open plan history tools and return to the calling menu when finished."""
    try:
        service = plan_history_service_factory()
        preferences = preferences_factory()
    except (FileNotFoundError, ValueError) as exc:
        print_error(str(exc), output_func)
        wait_for_enter(input_func)
        return False

    options = [
        MenuOption(
            "1",
            "View Plan History",
            lambda: view_plan_history_action(
                service,
                preferences,
                input_func,
                output_func,
            ),
        ),
        MenuOption(
            "2",
            "Compare Versions",
            lambda: compare_versions_action(service, input_func, output_func),
        ),
        MenuOption(
            "3",
            "Restore Version",
            lambda: restore_version_action(
                service,
                preferences,
                input_func,
                output_func,
            ),
        ),
        MenuOption("4", "Back", lambda: True),
    ]

    run_menu(
        title="History",
        options=options,
        input_func=input_func,
        output_func=output_func,
    )
    return False


def view_plan_history_action(
    service: PlanHistoryService,
    preferences: RecentPlanPreferences,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> bool:
    """Let the user select a saved plan and display its versions."""
    output_func("")
    try:
        plan = select_saved_plan_for_history(service, input_func, output_func)
    except (FileNotFoundError, ValueError) as exc:
        print_error(str(exc), output_func)
        wait_for_enter(input_func)
        return False

    if plan is not None:
        try:
            display_plan_history(service, preferences, plan, output_func)
        except (FileNotFoundError, ValueError) as exc:
            print_error(str(exc), output_func)

    wait_for_enter(input_func)
    return False


def select_saved_plan_for_history(
    service: PlanHistoryService,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> Any | None:
    """Display saved plans and return the selected plan, or None for Back."""
    plans = service.list_plans()
    if not plans:
        output_func("No saved plans found.")
        return None

    rows = []
    for index, plan in enumerate(plans, start=1):
        rows.append(
            [
                str(index),
                plan.name,
                getattr(plan, "updated_at", "") or getattr(plan, "created_at", ""),
                version_count_label(service, plan),
            ]
        )
    print_table(["#", "Plan", "Updated", "Versions"], rows, output_func)

    back_key = str(len(plans) + 1)
    options = [
        *[
            MenuOption(str(index), plan.name, lambda plan=plan: plan)
            for index, plan in enumerate(plans, start=1)
        ],
        MenuOption(back_key, "Back", lambda: None),
    ]
    display_menu("Select Plan", options, output_func)
    option_map = {option.key: option for option in options}
    while True:
        choice = input_func("Choose an option: ").strip()
        option = option_map.get(choice)
        if option is not None:
            return option.action()
        print_warning(f"Please choose one of: {', '.join(option_map)}.", output_func)


def compare_versions_action(
    service: PlanHistoryService,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> bool:
    """Prompt for two version IDs and display their existing comparison."""
    output_func("")
    from_version = prompt_positive_int("Starting version ID: ", input_func, output_func)
    if from_version is None:
        wait_for_enter(input_func)
        return False

    to_version = prompt_positive_int("Ending version ID: ", input_func, output_func)
    if to_version is None:
        wait_for_enter(input_func)
        return False

    try:
        comparison = service.compare_plan_versions(from_version, to_version)
    except (FileNotFoundError, ValueError) as exc:
        print_error(str(exc), output_func)
    else:
        print_plan_comparison(comparison, output_func)

    wait_for_enter(input_func)
    return False


def restore_version_action(
    service: PlanHistoryService,
    preferences: RecentPlanPreferences,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> bool:
    """Prompt for a version ID and restore it after confirmation."""
    output_func("")
    version_id = prompt_positive_int("Version ID: ", input_func, output_func)
    if version_id is None:
        wait_for_enter(input_func)
        return False

    return restore_version_by_id_action(
        service,
        preferences,
        version_id,
        input_func,
        output_func,
    )


def display_plan_history(
    service: PlanHistoryService,
    preferences: RecentPlanPreferences,
    plan: Any,
    output_func: OutputFunc = print,
) -> list[Any]:
    """Display versions for an already-selected plan."""
    versions = service.list_plan_versions(plan.id)
    preferences.mark_recent(plan.id, plan.name)
    output_func(f"Plan: {display_plan_name(plan)}")
    print_plan_versions(versions, output_func)
    return versions


def select_plan_version_summary(
    service: PlanHistoryService,
    versions: list[Any],
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> None:
    """Allow the user to select a displayed version and view its summary."""
    options = [
        MenuOption(
            str(index),
            f"View Version {version.version_number}",
            lambda version=version: version,
        )
        for index, version in enumerate(versions, start=1)
    ]
    back_key = str(len(options) + 1)
    options.append(MenuOption(back_key, "Back", lambda: None))
    display_menu("Select Version", options, output_func)
    option_map = {option.key: option for option in options}
    while True:
        choice = input_func("Choose an option: ").strip()
        option = option_map.get(choice)
        if option is None:
            print_warning(f"Please choose one of: {', '.join(option_map)}.", output_func)
            continue
        version = option.action()
        if version is None:
            return
        try:
            output_func("")
            output_func(service.plan_summary(version.id))
        except (FileNotFoundError, ValueError) as exc:
            print_error(str(exc), output_func)
        return


def restore_version_by_id_action(
    service: PlanHistoryService,
    preferences: RecentPlanPreferences,
    version_id: int,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
    *,
    plan: Any | None = None,
) -> bool:
    """Restore one version by ID after confirmation."""
    confirm = input_func(f"Restore version {version_id} as a new version? [y/N]: ")
    if confirm.strip().casefold() not in {"y", "yes"}:
        print_warning("Restore cancelled.", output_func)
        wait_for_enter(input_func)
        return False

    try:
        version = service.restore_plan_version(version_id)
        restored_plan = plan or service.get_plan(version.plan_id)
    except (FileNotFoundError, ValueError) as exc:
        print_error(str(exc), output_func)
    else:
        preferences.mark_recent(restored_plan.id, restored_plan.name)
        print_success(
            f"Restored as version {version.version_number} "
            f"(version ID {version.id}).",
            output_func,
        )

    wait_for_enter(input_func)
    return False


def prompt_positive_int(
    prompt: str,
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> int | None:
    """Prompt for a positive integer and return None for invalid input."""
    raw_value = input_func(prompt).strip()
    try:
        value = int(raw_value)
    except ValueError:
        print_warning("Please enter a positive whole number.", output_func)
        return None

    if value <= 0:
        print_warning("Please enter a positive whole number.", output_func)
        return None

    return value


def print_plan_versions(
    versions: list[Any],
    output_func: OutputFunc = print,
) -> None:
    """Print saved plan versions in a clear, compact format."""
    if not versions:
        print_warning("No saved versions found for that plan.", output_func)
        return

    print_table(
        ["Version", "Saved", "Status", "Note"],
        [
            [
                f"Version {version.version_number}",
                format_saved_datetime(version.created_at),
                "active" if version.active else "",
                version.change_note or "",
            ]
            for version in versions
        ],
        output_func,
    )


def print_plan_comparison(
    comparison: Any,
    output_func: OutputFunc = print,
) -> None:
    """Print the existing plan-comparison result."""
    output_func(comparison.explanation)

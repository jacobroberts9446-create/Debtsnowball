"""Command-line interface construction and command routing."""

import argparse
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.history import PlanHistoryService
from app.models import ActualEntryType
from app.workflows.workbook_export import write_history_report


@dataclass(frozen=True)
class CliDependencies:
    """Workflow callbacks used by CLI command routing."""

    load_current_config: Callable[[], Any]
    save_current_plan: Callable[..., tuple[str, Any]]
    print_plan_comparison: Callable[[Any], None]
    write_history_report: Callable[[PlanHistoryService, int, str], None]
    service_factory: Callable[[], PlanHistoryService] = PlanHistoryService
    output_func: Callable[[str], None] = print


def main(
    argv: list[str] | None = None,
    *,
    interactive_runner: Callable[[], None] | None = None,
    dependencies: CliDependencies | None = None,
) -> int:
    """Run either the argparse CLI or the interactive menu."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command:
        return run_cli(args, dependencies=dependencies)

    if interactive_runner is None:
        from run import run_main_menu

        interactive_runner = run_main_menu
    interactive_runner()
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Create optional history CLI commands while preserving default behavior."""
    parser = argparse.ArgumentParser(description="DebtSnowball")
    subparsers = parser.add_subparsers(dest="command")

    plan = subparsers.add_parser("plan", help="Manage saved local plans")
    plan_sub = plan.add_subparsers(dest="plan_command", required=True)
    plan_save = plan_sub.add_parser("save", help="Save the current config as a plan")
    plan_save.add_argument("--name", required=True)
    plan_save.add_argument("--description", default="")
    plan_save.add_argument("--note", default="Saved from CLI")
    plan_save.add_argument("--force", action="store_true")

    plan_sub.add_parser("list", help="List saved plans")
    plan_history = plan_sub.add_parser("history", help="List versions for a plan")
    plan_history.add_argument("--plan-id", type=int, required=True)
    plan_compare = plan_sub.add_parser("compare", help="Compare two saved versions")
    plan_compare.add_argument("--from-version", type=int, required=True)
    plan_compare.add_argument("--to-version", type=int, required=True)
    plan_restore = plan_sub.add_parser("restore", help="Restore a version as a new version")
    plan_restore.add_argument("--version", type=int, required=True)
    plan_delete = plan_sub.add_parser("delete", help="Permanently delete an archived plan")
    plan_delete.add_argument("--plan-id", type=int, required=True)
    plan_delete.add_argument("--confirm-name", required=True)
    plan_delete.add_argument("--export-path")

    actual = subparsers.add_parser("actual", help="Record actual local activity")
    actual_sub = actual.add_subparsers(dest="actual_command", required=True)
    actual_add = actual_sub.add_parser("add", help="Add an actual entry")
    actual_add.add_argument("--plan-id", type=int, required=True)
    actual_add.add_argument("--date", required=True)
    actual_add.add_argument(
        "--type",
        choices=[entry.value for entry in ActualEntryType],
        required=True,
    )
    actual_add.add_argument("--amount", required=True)
    actual_add.add_argument(
        "--category",
        default="",
        help="Bill or debt name when required",
    )
    actual_add.add_argument("--description", default="")
    actual_sub.add_parser(
        "summary",
        help="Summarize actuals against the latest forecast",
    ).add_argument(
        "--plan-id",
        type=int,
        required=True,
    )
    actual_balance = actual_sub.add_parser(
        "balance",
        help="Record an actual balance observation",
    )
    actual_balance.add_argument("--plan-id", type=int, required=True)
    actual_balance.add_argument("--date", required=True)
    actual_balance.add_argument(
        "--type",
        choices=[
            ActualEntryType.DEBT_BALANCE_OBSERVATION.value,
            ActualEntryType.SAVINGS_BALANCE_OBSERVATION.value,
        ],
        required=True,
    )
    actual_balance.add_argument("--balance", required=True)
    actual_balance.add_argument("--debt")
    actual_balance.add_argument("--note", default="")

    export_cmd = subparsers.add_parser("export", help="Export a saved plan")
    export_cmd.add_argument("--plan-id", type=int, required=True)
    export_cmd.add_argument("--path", required=True)
    export_cmd.add_argument("--format", choices=["json", "csv"], default="json")

    import_cmd = subparsers.add_parser("import", help="Import a saved plan")
    import_cmd.add_argument("--path", required=True)
    import_cmd.add_argument("--name")

    report_cmd = subparsers.add_parser(
        "history-report",
        help="Create a saved history workbook",
    )
    report_cmd.add_argument("--plan-id", type=int, required=True)
    report_cmd.add_argument("--path", required=True)
    return parser


def run_cli(args: argparse.Namespace, dependencies: CliDependencies | None = None) -> int:
    """Run optional CLI commands with plain errors for normal user mistakes."""
    dependencies = dependencies or _default_dependencies()
    output = dependencies.output_func
    try:
        service = dependencies.service_factory()
        if args.command == "plan":
            run_plan_command(service, args, dependencies)
        elif args.command == "actual":
            run_actual_command(service, args, output)
        elif args.command == "export":
            if args.format == "csv":
                paths = service.export_csv_bundle(args.plan_id, args.path)
                output(f"Exported {len(paths)} CSV files to {args.path}")
            else:
                path = service.export_plan_json(args.plan_id, args.path)
                output(f"Exported plan to {path}")
        elif args.command == "import":
            plan = service.import_plan_json(args.path, new_name=args.name)
            output(f"Imported plan {plan.id}: {plan.name}")
        elif args.command == "history-report":
            dependencies.write_history_report(service, args.plan_id, args.path)
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        output(f"Error: {exc}")
        return 1
    return 0


def run_plan_command(
    service: PlanHistoryService,
    args: argparse.Namespace,
    dependencies: CliDependencies,
) -> None:
    """Run plan-history CLI commands."""
    output = dependencies.output_func
    if args.plan_command == "save":
        result = dependencies.save_current_plan(
            service,
            dependencies.load_current_config(),
            name=args.name,
            description=args.description,
            change_note=args.note,
            source="cli",
            force=args.force,
        )
        if result[0] == "version":
            version = result[1]
            output(f"Saved version {version.version_number} for {args.name}")
        else:
            plan = result[1]
            output(f"Created plan {plan.id}: {plan.name}")
    elif args.plan_command == "list":
        for plan in service.list_plans():
            output(f"{plan.id}: {plan.name}")
    elif args.plan_command == "history":
        for version in service.list_plan_versions(args.plan_id):
            active = " active" if version.active else ""
            output(f"v{version.version_number}: {version.change_note}{active}")
    elif args.plan_command == "compare":
        dependencies.print_plan_comparison(
            service.compare_plan_versions(args.from_version, args.to_version)
        )
    elif args.plan_command == "restore":
        version = service.restore_plan_version(args.version)
        output(f"Restored as version {version.version_number}")
    elif args.plan_command == "delete":
        service.delete_plan_permanently(
            args.plan_id,
            confirmation_name=args.confirm_name,
            export_path=args.export_path,
        )
        output(f"Deleted archived plan {args.plan_id}")


def run_actual_command(
    service: PlanHistoryService,
    args: argparse.Namespace,
    output: Callable[[str], None] = print,
) -> None:
    """Run actual-entry CLI commands."""
    if args.actual_command == "add":
        entry_type = ActualEntryType(args.type)
        entry = service.add_actual_entry(
            args.plan_id,
            date.fromisoformat(args.date),
            entry_type,
            args.amount,
            category=args.category,
            description=args.description,
            source="cli",
            debt_identifier=(
                args.category
                if entry_type == ActualEntryType.DEBT_PAYMENT
                else None
            ),
        )
        output(f"Added actual entry {entry.id}")
    elif args.actual_command == "summary":
        summary = service.compare_forecast_to_actual(args.plan_id)
        output(summary.status)
    elif args.actual_command == "balance":
        observation = service.add_balance_observation(
            args.plan_id,
            date.fromisoformat(args.date),
            ActualEntryType(args.type),
            args.balance,
            debt_identifier=args.debt,
            note=args.note,
            source="cli",
        )
        output(f"Added balance observation {observation.id}")


def _default_dependencies() -> CliDependencies:
    """Build default CLI dependencies without importing run.py at module import time."""
    from app.workflows.saved_plans import save_current_plan
    from app.workflows.plan_history import print_plan_comparison
    from run import load_current_config

    return CliDependencies(
        load_current_config=load_current_config,
        save_current_plan=save_current_plan,
        print_plan_comparison=print_plan_comparison,
        write_history_report=write_history_report,
    )

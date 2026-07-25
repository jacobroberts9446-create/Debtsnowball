"""Shared presentation helpers for saved plans and plan history."""

from datetime import datetime

from app.history import PlanHistoryService


def version_count_label(service: PlanHistoryService, plan) -> str:
    """Return a displayable version count when available."""
    try:
        return str(len(service.list_plan_versions(plan.id)))
    except (FileNotFoundError, ValueError):
        return "Unknown"


def display_plan_name(plan) -> str:
    """Return the best user-facing saved-plan name available."""
    name = getattr(plan, "name", "") or ""
    return name.strip() or "Untitled Plan"


def format_saved_datetime(value: str | None) -> str:
    """Format saved-plan timestamps for console display."""
    if not value:
        return "Not available"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    return f"{parsed:%b} {parsed.day}, {parsed:%Y at %I:%M %p}".replace(" 0", " ")

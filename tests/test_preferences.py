"""Tests for local interactive-console preferences."""

import json
from types import SimpleNamespace

from app.preferences import MAX_RECENT_PLANS, RecentPlanPreferences


def test_recent_preferences_empty_when_file_missing(tmp_path) -> None:
    """Missing preferences start with an empty recent-plan list."""
    preferences = RecentPlanPreferences(tmp_path / "preferences.json")

    assert preferences.list_recent_plans() == []


def test_recent_preferences_adds_recent_plan(tmp_path) -> None:
    """Adding a recent plan stores its ID and name."""
    path = tmp_path / "preferences.json"
    preferences = RecentPlanPreferences(path)

    preferences.mark_recent(1, "Current Plan")

    assert preferences.list_recent_plans()[0].id == 1
    assert preferences.list_recent_plans()[0].name == "Current Plan"
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "recent_plans": [{"id": 1, "name": "Current Plan"}]
    }


def test_recent_preferences_moves_existing_plan_to_top(tmp_path) -> None:
    """Reusing a recent plan moves it to the top instead of duplicating it."""
    preferences = RecentPlanPreferences(tmp_path / "preferences.json")

    preferences.mark_recent(1, "First")
    preferences.mark_recent(2, "Second")
    preferences.mark_recent(1, "First Updated")

    recent = preferences.list_recent_plans()
    assert [(plan.id, plan.name) for plan in recent] == [
        (1, "First Updated"),
        (2, "Second"),
    ]


def test_recent_preferences_keeps_at_most_five_entries(tmp_path) -> None:
    """Recent plans are capped at five entries."""
    preferences = RecentPlanPreferences(tmp_path / "preferences.json")

    for plan_id in range(1, 8):
        preferences.mark_recent(plan_id, f"Plan {plan_id}")

    recent = preferences.list_recent_plans()
    assert len(recent) == MAX_RECENT_PLANS
    assert [plan.id for plan in recent] == [7, 6, 5, 4, 3]


def test_recent_preferences_does_not_store_duplicates_from_file(tmp_path) -> None:
    """Duplicate IDs in a file are ignored after the first valid entry."""
    path = tmp_path / "preferences.json"
    path.write_text(
        json.dumps(
            {
                "recent_plans": [
                    {"id": 1, "name": "First"},
                    {"id": 1, "name": "Duplicate"},
                    {"id": 2, "name": "Second"},
                ]
            }
        ),
        encoding="utf-8",
    )
    preferences = RecentPlanPreferences(path)

    assert [(plan.id, plan.name) for plan in preferences.list_recent_plans()] == [
        (1, "First"),
        (2, "Second"),
    ]


def test_recent_preferences_removes_stale_plans(tmp_path) -> None:
    """Recent plans no longer in saved-plan listings are removed."""
    preferences = RecentPlanPreferences(tmp_path / "preferences.json")
    preferences.mark_recent(1, "Stale")
    preferences.mark_recent(2, "Current")

    recent = preferences.list_existing_recent_plans(
        [SimpleNamespace(id=2, name="Current Renamed")]
    )

    assert [(plan.id, plan.name) for plan in recent] == [(2, "Current Renamed")]
    assert [
        (plan.id, plan.name) for plan in preferences.list_recent_plans()
    ] == [(2, "Current Renamed")]


def test_recent_preferences_handles_corrupt_file_gracefully(tmp_path) -> None:
    """Corrupt preference files fall back to an empty recent list."""
    path = tmp_path / "preferences.json"
    path.write_text("{not json", encoding="utf-8")
    preferences = RecentPlanPreferences(path)

    assert preferences.list_recent_plans() == []


def test_recent_preferences_ignores_invalid_entries(tmp_path) -> None:
    """Preference validation keeps only valid positive IDs and names."""
    path = tmp_path / "preferences.json"
    path.write_text(
        json.dumps(
            {
                "recent_plans": [
                    {"id": True, "name": "Boolean"},
                    {"id": -1, "name": "Negative"},
                    {"id": 2, "name": ""},
                    {"id": 3, "name": "Valid"},
                    "bad",
                ]
            }
        ),
        encoding="utf-8",
    )
    preferences = RecentPlanPreferences(path)

    assert [(plan.id, plan.name) for plan in preferences.list_recent_plans()] == [
        (3, "Valid")
    ]

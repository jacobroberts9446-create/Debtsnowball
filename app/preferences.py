"""Local application preferences for interactive console conveniences."""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.paths import default_preferences_path


MAX_RECENT_PLANS = 5


@dataclass(frozen=True)
class RecentPlan:
    """A recently used saved plan reference."""

    id: int
    name: str


class RecentPlanPreferences:
    """JSON-backed preferences for recently used saved plans."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = default_preferences_path() if path is None else Path(path)

    def list_recent_plans(self) -> list[RecentPlan]:
        """Return validated recent plans, most recent first."""
        return self._load()

    def list_existing_recent_plans(self, existing_plans: list[Any]) -> list[RecentPlan]:
        """Return recent plans that still exist and remove stale entries."""
        existing_by_id = {int(plan.id): str(plan.name) for plan in existing_plans}
        current = self._load()
        filtered = [
            RecentPlan(plan.id, existing_by_id[plan.id])
            for plan in current
            if plan.id in existing_by_id
        ]
        if filtered != current:
            self._save(filtered)
        return filtered

    def mark_recent(self, plan_id: int, plan_name: str) -> None:
        """Move a plan to the top of the recent-plan list."""
        plan = RecentPlan(int(plan_id), str(plan_name).strip())
        if plan.id <= 0 or not plan.name:
            return

        existing = [item for item in self._load() if item.id != plan.id]
        self._save([plan, *existing][:MAX_RECENT_PLANS])

    def remove_recent(self, plan_id: int) -> None:
        """Remove one plan from the recent-plan list."""
        self._save([plan for plan in self._load() if plan.id != int(plan_id)])

    def _load(self) -> list[RecentPlan]:
        """Load and validate recent-plan preferences."""
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []

        raw_plans = payload.get("recent_plans") if isinstance(payload, dict) else None
        if not isinstance(raw_plans, list):
            return []

        recent_plans: list[RecentPlan] = []
        seen_ids: set[int] = set()
        for item in raw_plans:
            if not isinstance(item, dict):
                continue
            plan_id = item.get("id")
            plan_name = item.get("name")
            if isinstance(plan_id, bool) or not isinstance(plan_id, int):
                continue
            if plan_id <= 0 or not isinstance(plan_name, str) or not plan_name.strip():
                continue
            if plan_id in seen_ids:
                continue
            seen_ids.add(plan_id)
            recent_plans.append(RecentPlan(plan_id, plan_name.strip()))
            if len(recent_plans) == MAX_RECENT_PLANS:
                break

        return recent_plans

    def _save(self, recent_plans: list[RecentPlan]) -> None:
        """Persist preferences with atomic file replacement."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_name(f"{self.path.name}.tmp")
        payload = {
            "recent_plans": [
                {"id": plan.id, "name": plan.name}
                for plan in recent_plans[:MAX_RECENT_PLANS]
            ]
        }
        temp_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(temp_path, self.path)

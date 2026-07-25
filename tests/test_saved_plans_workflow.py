"""Tests for interactive saved-plan workflows."""

from types import SimpleNamespace

from app.workflows import saved_plans


def output_text(output: list[str]) -> str:
    """Join captured console output for readable substring assertions."""
    return "\n".join(output)


class FakePlanHistoryService:
    """Small fake service for saved-plan workflow tests."""

    def __init__(self, plans=None) -> None:
        self.plans = plans or []
        self.versions_by_plan = {}
        self.renamed = []
        self.deleted = []
        self.archived = []
        self.created = []
        self.generated_saves = []
        self.saved_versions = []
        self.missing_get_plan_ids = set()
        self.list_plans_error = None
        self.rename_error = None
        self.delete_error = None
        self.save_generated_error = None
        self.save_version_error = None
        self.create_plan_error = None

    def list_plans(self):
        if self.list_plans_error is not None:
            raise self.list_plans_error
        return self.plans

    def get_plan(self, plan_id):
        if plan_id in self.missing_get_plan_ids:
            raise ValueError(f"plan {plan_id} was not found.")
        for plan in self.plans:
            if plan.id == plan_id:
                return plan
        raise ValueError(f"plan {plan_id} was not found.")

    def create_plan(
        self,
        name,
        config,
        *,
        description="",
        change_note="Initial version",
        source="manual",
    ):
        if self.create_plan_error is not None:
            raise self.create_plan_error
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
        if self.save_version_error is not None:
            raise self.save_version_error
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

    def get_plan_version(self, version_id):
        for versions in self.versions_by_plan.values():
            for version in versions:
                if version.id == version_id:
                    return version
        raise ValueError(f"plan version {version_id} was not found.")

    def plan_summary(self, version_id):
        return f"Summary for version {version_id}"

    def rename_plan(self, plan_id, name):
        if self.rename_error is not None:
            raise self.rename_error
        plan = self.get_plan(plan_id)
        renamed = SimpleNamespace(**{**vars(plan), "name": name})
        self.plans = [renamed if item.id == plan_id else item for item in self.plans]
        self.renamed.append((plan_id, name))
        return renamed

    def save_generated_plan(self, **kwargs):
        if self.save_generated_error is not None:
            raise self.save_generated_error
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
        if self.delete_error is not None:
            raise self.delete_error
        self.deleted.append((plan_id, confirmation_name, export_path))
        self.plans = [plan for plan in self.plans if plan.id != plan_id]


class FakePreferences:
    """In-memory recent-plan preferences for saved-plan workflow tests."""

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


def plan_stub(**overrides):
    """Build a saved-plan test stub."""
    values = {
        "id": 4,
        "name": "Current Plan",
        "description": "",
        "updated_at": "",
        "current_version_id": 21,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def config_stub():
    """Build a minimal config-like object for saved-plan workflow tests."""
    return SimpleNamespace(
        settings=SimpleNamespace(starting_savings=0),
        debts=[],
        bills=[],
    )


def test_saved_plans_empty_state_returns_to_saved_menu() -> None:
    """Saved Plans explains the empty state without exposing raw IDs."""
    choices = iter([""])
    output = []
    service = FakePlanHistoryService()
    preferences = FakePreferences()

    saved_plans.run_saved_plans_menu(
        plan_history_service_factory=lambda: service,
        preferences_factory=lambda: preferences,
        input_func=lambda _prompt: next(choices),
        output_func=output.append,
    )

    assert "Saved Plans" in output
    assert "No saved plans yet." in output
    assert "Create a new plan and save it to see it here." in output
    assert "Recent Plans" not in output


def test_saved_plans_list_populated_without_raw_ids() -> None:
    """Saved Plans shows names, update dates, and version counts without IDs."""
    choices = iter(["3"])
    output = []
    service = FakePlanHistoryService(
        [
            SimpleNamespace(
                id=7,
                name="Aggressive Plan",
                description="Fast payoff",
                updated_at="2026-07-24T20:15:00+00:00",
            ),
            SimpleNamespace(
                id=8,
                name="Mom's Debt Plan",
                description="",
                updated_at="2026-07-20T09:00:00+00:00",
            ),
        ]
    )
    service.versions_by_plan[7] = [SimpleNamespace(id=21), SimpleNamespace(id=22)]
    service.versions_by_plan[8] = [SimpleNamespace(id=23)]
    preferences = FakePreferences()

    saved_plans.run_saved_plans_menu(
        plan_history_service_factory=lambda: service,
        preferences_factory=lambda: preferences,
        input_func=lambda _prompt: next(choices),
        output_func=output.append,
    )

    text = output_text(output)
    assert "1. Aggressive Plan" in text
    assert "Updated: Jul 24, 2026 at 8:15 PM" in text
    assert "Versions: 2" in text
    assert "Description: Fast payoff" in text
    assert "2. Mom's Debt Plan" in text
    assert "Versions: 1" in text
    assert "ID" not in text
    assert "plan ID" not in text


def test_saved_plan_selection_opens_plan_details_actions() -> None:
    """Selecting a saved plan exposes implemented plan-management actions."""
    choices = iter(["1", "8", "2"])
    output = []
    service = FakePlanHistoryService(
        [SimpleNamespace(id=4, name="Current Plan", description="", updated_at="")]
    )
    preferences = FakePreferences()

    saved_plans.run_saved_plans_menu(
        plan_history_service_factory=lambda: service,
        preferences_factory=lambda: preferences,
        input_func=lambda _prompt: next(choices),
        output_func=output.append,
    )

    text = output_text(output)
    assert "Plan: Current Plan" in text
    assert "1. View Latest Plan Summary" in text
    assert "2. Generate Excel Workbook" in text
    assert "3. Create New Version" in text
    assert "4. History" in text
    assert "5. Rename Plan" in text
    assert "6. Duplicate Plan" in text
    assert "7. Delete Plan" in text
    assert "8. Back" in text
    assert preferences.marked[0] == (4, "Current Plan")


def test_saved_plan_history_action_is_dispatched_for_selected_plan(monkeypatch) -> None:
    """Selected-plan history is delegated without asking for the plan ID again."""
    prompts = []
    choices = iter(["1", "4", "", "8", "2"])
    output = []
    calls = []
    service = FakePlanHistoryService(
        [SimpleNamespace(id=7, name="Plan 7", description="", updated_at="")]
    )
    preferences = FakePreferences()

    def fake_history_action(service_arg, preferences_arg, plan, input_func, output_func):
        calls.append((service_arg, preferences_arg, plan.id))
        output_func("History for selected plan")
        input_func("Press Enter to continue...")
        return False

    monkeypatch.setattr(
        saved_plans,
        "run_selected_plan_history_menu",
        fake_history_action,
    )
    saved_plans.run_saved_plans_menu(
        plan_history_service_factory=lambda: service,
        preferences_factory=lambda: preferences,
        input_func=lambda prompt: prompts.append(prompt) or next(choices),
        output_func=output.append,
    )

    assert calls == [(service, preferences, 7)]
    assert "History for selected plan" in output
    assert "Plan ID: " not in prompts


def test_saved_plan_invalid_selection_returns_to_saved_plans() -> None:
    """Invalid saved-plan choices use a friendly warning."""
    choices = iter(["bad", "2"])
    output = []
    service = FakePlanHistoryService(
        [SimpleNamespace(id=4, name="Current Plan", description="", updated_at="")]
    )
    preferences = FakePreferences()

    saved_plans.run_saved_plans_menu(
        plan_history_service_factory=lambda: service,
        preferences_factory=lambda: preferences,
        input_func=lambda _prompt: next(choices),
        output_func=output.append,
    )

    assert "Warning: Please choose one of: 1, 2." in output


def test_saved_plan_view_latest_summary() -> None:
    """Plan details can show the latest saved summary."""
    choices = iter(["1", "1", "", "8", "2"])
    output = []
    service = FakePlanHistoryService(
        [
            SimpleNamespace(
                id=4,
                name="Current Plan",
                description="",
                updated_at="",
                current_version_id=21,
            )
        ]
    )
    service.versions_by_plan[4] = [SimpleNamespace(id=21, version_number=1)]

    saved_plans.run_saved_plans_menu(
        plan_history_service_factory=lambda: service,
        preferences_factory=FakePreferences,
        input_func=lambda _prompt: next(choices),
        output_func=output.append,
    )

    assert "Summary for version 21" in output


def test_saved_plan_rename_and_blank_rejection() -> None:
    """Plan renaming rejects blanks and updates the plan through the service."""
    choices = iter(["1", "5", "   ", "", "5", "Renamed Plan", "", "2"])
    output = []
    service = FakePlanHistoryService(
        [SimpleNamespace(id=4, name="Current Plan", description="", updated_at="")]
    )
    preferences = FakePreferences()

    saved_plans.run_saved_plans_menu(
        plan_history_service_factory=lambda: service,
        preferences_factory=lambda: preferences,
        input_func=lambda _prompt: next(choices),
        output_func=output.append,
    )

    assert "Warning: Plan name cannot be blank." in output
    assert service.renamed == [(4, "Renamed Plan")]
    assert preferences.marked[-1] == (4, "Renamed Plan")
    assert "Success: Renamed plan to Renamed Plan." in output


def test_saved_plan_duplicate_and_duplicate_name_rejection(monkeypatch) -> None:
    """Plan duplication creates a new version-1 plan and rejects duplicate names."""
    choices = iter(["1", "6", "Current Plan", "", "6", "Copy Plan", "", "3"])
    output = []
    service = FakePlanHistoryService(
        [
            SimpleNamespace(id=4, name="Current Plan", description="", updated_at=""),
        ]
    )
    service.versions_by_plan[4] = [SimpleNamespace(id=21, version_number=1)]
    config = SimpleNamespace(
        settings=SimpleNamespace(starting_savings=0),
        debts=[],
    )
    monkeypatch.setattr(
        saved_plans,
        "config_from_plan_version",
        lambda _version: config,
    )
    monkeypatch.setattr(
        saved_plans,
        "build_workbook_outputs",
        lambda _config: (["summary"], "forecast", None, None),
    )

    saved_plans.run_saved_plans_menu(
        plan_history_service_factory=lambda: service,
        preferences_factory=FakePreferences,
        input_func=lambda _prompt: next(choices),
        output_func=output.append,
    )

    assert "Warning: A saved plan with that name already exists." in output
    assert service.generated_saves[-1]["name"] == "Copy Plan"
    assert service.generated_saves[-1]["force"] is True
    assert "Success: Duplicated plan as Copy Plan with version 1." in output


def test_saved_plan_delete_confirmation_and_cancellation() -> None:
    """Plan deletion requires typing DELETE and removes only after confirmation."""
    choices = iter(["1", "7", "no", "", "7", "DELETE", "", "2"])
    output = []
    service = FakePlanHistoryService(
        [SimpleNamespace(id=4, name="Current Plan", description="", updated_at="")]
    )
    service.versions_by_plan[4] = [SimpleNamespace(id=21, version_number=1)]
    preferences = FakePreferences([SimpleNamespace(id=4, name="Current Plan")])

    saved_plans.run_saved_plans_menu(
        plan_history_service_factory=lambda: service,
        preferences_factory=lambda: preferences,
        input_func=lambda _prompt: next(choices),
        output_func=output.append,
    )

    assert "Warning: Delete cancelled." in output
    assert service.archived == [4]
    assert service.deleted == [(4, "Current Plan", None)]
    assert preferences.recent_plans == []
    assert "Success: Deleted plan Current Plan." in output


def test_saved_plan_create_new_version(monkeypatch) -> None:
    """Create New Version reuses the review flow and saves the generated result."""
    choices = iter(["1", "3", "", "2"])
    output = []
    config = SimpleNamespace(settings=SimpleNamespace(), debts=[], bills=[])
    generated = SimpleNamespace(
        forecast="forecast",
        setup=SimpleNamespace(current_savings=0, debts=[]),
    )
    service = FakePlanHistoryService(
        [
            SimpleNamespace(
                id=4,
                name="Current Plan",
                description="",
                updated_at="",
                current_version_id=21,
            )
        ]
    )
    service.versions_by_plan[4] = [SimpleNamespace(id=21, version_number=1)]
    monkeypatch.setattr(saved_plans, "config_from_plan_version", lambda _version: config)
    monkeypatch.setattr(saved_plans, "review_budget_setup", lambda *_args: generated)

    saved_plans.run_saved_plans_menu(
        plan_history_service_factory=lambda: service,
        preferences_factory=FakePreferences,
        input_func=lambda _prompt: next(choices),
        output_func=output.append,
        setup_from_config_func=lambda _name, _config: object(),
        setup_from_generated_plan_func=lambda _plan: config,
    )

    assert service.generated_saves[-1]["plan_id"] == 4
    assert service.generated_saves[-1]["force"] is True
    assert "Success: Saved version 2 for Current Plan." in output


def test_service_factory_error_is_handled() -> None:
    """Saved Plans handles startup service errors without raising."""
    choices = iter([""])
    output = []

    result = saved_plans.run_saved_plans_menu(
        plan_history_service_factory=lambda: (_ for _ in ()).throw(ValueError("bad db")),
        input_func=lambda _prompt: next(choices),
        output_func=output.append,
    )

    assert result is False
    assert "Error: bad db" in output


def test_saved_plan_list_error_is_handled() -> None:
    """Saved Plans reports plan listing failures and returns."""
    choices = iter([""])
    output = []
    service = FakePlanHistoryService()
    service.list_plans_error = ValueError("list failed")

    saved_plans.run_saved_plans_menu(
        plan_history_service_factory=lambda: service,
        preferences_factory=FakePreferences,
        input_func=lambda _prompt: next(choices),
        output_func=output.append,
    )

    assert "Error: list failed" in output


def test_out_of_range_saved_plan_selection_warns() -> None:
    """Numeric choices outside the saved-plan range stay in the selection loop."""
    choices = iter(["9", "2"])
    output = []
    service = FakePlanHistoryService([plan_stub()])

    saved_plans.run_saved_plans_menu(
        plan_history_service_factory=lambda: service,
        preferences_factory=FakePreferences,
        input_func=lambda _prompt: next(choices),
        output_func=output.append,
    )

    assert "Warning: Please choose one of: 1, 2." in output


def test_selected_plan_disappears_between_listing_and_opening() -> None:
    """A stale plan selected from the list is handled without crashing."""
    choices = iter(["1", "", "2"])
    output = []
    service = FakePlanHistoryService([plan_stub()])
    service.missing_get_plan_ids.add(4)

    saved_plans.run_saved_plans_menu(
        plan_history_service_factory=lambda: service,
        preferences_factory=FakePreferences,
        input_func=lambda _prompt: next(choices),
        output_func=output.append,
    )

    assert "Error: plan 4 was not found." in output


def test_list_saved_plans_action_handles_empty_and_error_states() -> None:
    """The compatibility list action covers empty and error displays."""
    empty_output = []
    saved_plans.list_saved_plans_action(
        FakePlanHistoryService(),
        input_func=lambda _prompt: "",
        output_func=empty_output.append,
    )

    error_output = []
    service = FakePlanHistoryService()
    service.list_plans_error = ValueError("cannot list")
    saved_plans.list_saved_plans_action(
        service,
        input_func=lambda _prompt: "",
        output_func=error_output.append,
    )

    assert "No saved plans yet." in empty_output
    assert "Error: cannot list" in error_output


def test_list_saved_plans_action_displays_populated_plans() -> None:
    """The compatibility list action displays saved plans with metadata."""
    output = []
    service = FakePlanHistoryService(
        [plan_stub(name="Plan With Description", description="Useful note")]
    )
    service.versions_by_plan[4] = [SimpleNamespace(id=21), SimpleNamespace(id=22)]

    saved_plans.list_saved_plans_action(
        service,
        input_func=lambda _prompt: "",
        output_func=output.append,
    )

    text = output_text(output)
    assert "1. Plan With Description" in text
    assert "Versions: 2" in text
    assert "Description: Useful note" in text


def test_recent_plans_empty_invalid_stale_and_valid_navigation() -> None:
    """Recent Plans handles empty, invalid, stale, and valid selections."""
    service = FakePlanHistoryService([plan_stub(id=4, name="Current Plan")])

    empty_output = []
    saved_plans.list_recent_plans_action(
        service,
        FakePreferences(),
        lambda: config_stub(),
        input_func=lambda _prompt: "",
        output_func=empty_output.append,
    )

    stale_preferences = FakePreferences([SimpleNamespace(id=99, name="Gone")])
    stale_output = []
    saved_plans.open_recent_plan_action(
        service,
        stale_preferences,
        lambda: config_stub(),
        99,
        input_func=lambda _prompt: "",
        output_func=stale_output.append,
    )

    valid_preferences = FakePreferences([SimpleNamespace(id=4, name="Current Plan")])
    choices = iter(["bad", "1", "8", "2"])
    valid_output = []
    saved_plans.list_recent_plans_action(
        service,
        valid_preferences,
        lambda: config_stub(),
        input_func=lambda _prompt: next(choices),
        output_func=valid_output.append,
    )

    assert "No saved plans yet." in empty_output
    assert stale_preferences.recent_plans == []
    assert "Warning: That recent plan no longer exists and was removed." in stale_output
    assert "Warning: Please choose one of: 1, 2." in valid_output
    assert valid_preferences.marked[-1] == (4, "Current Plan")


def test_recent_plan_list_error_is_handled() -> None:
    """Recent Plans reports preference/listing failures without raising."""
    output = []
    service = FakePlanHistoryService([plan_stub()])
    service.list_plans_error = ValueError("recent list failed")

    saved_plans.list_recent_plans_action(
        service,
        FakePreferences([SimpleNamespace(id=4, name="Current Plan")]),
        lambda: config_stub(),
        input_func=lambda _prompt: "",
        output_func=output.append,
    )

    assert "Error: recent list failed" in output


def test_plan_details_include_description_and_workbook_delegates(monkeypatch) -> None:
    """Plan details render descriptions and delegate workbook generation."""
    choices = iter(["2", "8"])
    output = []
    calls = []
    plan = plan_stub(description="Helpful description")
    service = FakePlanHistoryService([plan])
    service.versions_by_plan[4] = [SimpleNamespace(id=21, version_number=1)]

    def fake_workbook_action(service_arg, plan_arg, input_func, output_func):
        calls.append((service_arg, plan_arg.id))
        output_func("Workbook delegated")
        return False

    monkeypatch.setattr(
        saved_plans,
        "generate_saved_plan_workbook_action",
        fake_workbook_action,
    )

    saved_plans.run_selected_plan_menu(
        service,
        FakePreferences(),
        lambda: config_stub(),
        plan,
        input_func=lambda _prompt: next(choices),
        output_func=output.append,
    )

    text = output_text(output)
    assert "Description: Helpful description" in text
    assert "Workbook delegated" in output
    assert calls == [(service, 4)]


def test_view_latest_plan_summary_handles_missing_version() -> None:
    """Latest summary errors are displayed and return to the plan menu."""
    output = []
    plan = plan_stub(current_version_id=999)
    service = FakePlanHistoryService([plan])

    saved_plans.view_latest_plan_summary_action(
        service,
        plan,
        input_func=lambda _prompt: "",
        output_func=output.append,
    )

    assert "Error: plan version 999 was not found." in output


def test_create_version_missing_callbacks_and_cancelled_review(monkeypatch) -> None:
    """Create Version handles unconfigured and user-cancelled edit flows."""
    plan = plan_stub()
    service = FakePlanHistoryService([plan])
    service.versions_by_plan[4] = [SimpleNamespace(id=21, version_number=1)]

    missing_output = []
    missing_result = saved_plans.create_saved_plan_version_action(
        service,
        FakePreferences(),
        plan,
        input_func=lambda _prompt: "",
        output_func=missing_output.append,
    )

    cancelled_output = []
    monkeypatch.setattr(saved_plans, "config_from_plan_version", lambda _version: config_stub())
    monkeypatch.setattr(saved_plans, "review_budget_setup", lambda *_args: None)
    cancelled_result = saved_plans.create_saved_plan_version_action(
        service,
        FakePreferences(),
        plan,
        input_func=lambda _prompt: "",
        output_func=cancelled_output.append,
        setup_from_config_func=lambda _name, _config: object(),
        setup_from_generated_plan_func=lambda _plan: config_stub(),
    )

    assert missing_result is False
    assert "Error: saved plan version editing is not configured." in missing_output
    assert cancelled_result is False
    assert "Warning: New version cancelled." in cancelled_output


def test_create_version_handles_config_generation_and_save_failures(monkeypatch) -> None:
    """Create Version reports config, generation, and persistence errors."""
    plan = plan_stub()
    service = FakePlanHistoryService([plan])
    service.versions_by_plan[4] = [SimpleNamespace(id=21, version_number=1)]

    config_output = []
    monkeypatch.setattr(
        saved_plans,
        "config_from_plan_version",
        lambda _version: (_ for _ in ()).throw(ValueError("bad config")),
    )
    saved_plans.create_saved_plan_version_action(
        service,
        FakePreferences(),
        plan,
        input_func=lambda _prompt: "",
        output_func=config_output.append,
        setup_from_config_func=lambda _name, _config: object(),
        setup_from_generated_plan_func=lambda _plan: config_stub(),
    )

    generation_output = []
    monkeypatch.setattr(saved_plans, "config_from_plan_version", lambda _version: config_stub())
    monkeypatch.setattr(
        saved_plans,
        "review_budget_setup",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("generation failed")),
    )
    saved_plans.create_saved_plan_version_action(
        service,
        FakePreferences(),
        plan,
        input_func=lambda _prompt: "",
        output_func=generation_output.append,
        setup_from_config_func=lambda _name, _config: object(),
        setup_from_generated_plan_func=lambda _plan: config_stub(),
    )

    save_output = []
    generated = SimpleNamespace(
        forecast="forecast",
        setup=SimpleNamespace(current_savings=0, debts=[]),
    )
    monkeypatch.setattr(saved_plans, "review_budget_setup", lambda *_args: generated)
    service.save_generated_error = OSError("save failed")
    saved_plans.create_saved_plan_version_action(
        service,
        FakePreferences(),
        plan,
        input_func=lambda _prompt: "",
        output_func=save_output.append,
        setup_from_config_func=lambda _name, _config: object(),
        setup_from_generated_plan_func=lambda _plan: config_stub(),
    )

    assert "Error: bad config" in config_output
    assert "Error: generation failed" in generation_output
    assert "Error: save failed" in save_output


def test_rename_service_failure_is_handled() -> None:
    """Rename failures are displayed without marking the plan recent."""
    output = []
    preferences = FakePreferences()
    service = FakePlanHistoryService([plan_stub()])
    service.rename_error = ValueError("rename failed")

    result = saved_plans.rename_saved_plan_action(
        service,
        preferences,
        plan_stub(),
        input_func=lambda _prompt: "New Name",
        output_func=output.append,
    )

    assert result is False
    assert preferences.marked == []
    assert "Error: rename failed" in output


def test_duplicate_empty_and_failure_paths(monkeypatch) -> None:
    """Duplicate handles blank names and source/generation/save failures."""
    plan = plan_stub()
    service = FakePlanHistoryService([plan])
    service.versions_by_plan[4] = [SimpleNamespace(id=21, version_number=1)]

    blank_output = []
    blank_result = saved_plans.duplicate_saved_plan_action(
        service,
        FakePreferences(),
        plan,
        input_func=lambda _prompt: " ",
        output_func=blank_output.append,
    )

    config_output = []
    monkeypatch.setattr(
        saved_plans,
        "config_from_plan_version",
        lambda _version: (_ for _ in ()).throw(ValueError("source config failed")),
    )
    saved_plans.duplicate_saved_plan_action(
        service,
        FakePreferences(),
        plan,
        input_func=lambda _prompt: "Copy",
        output_func=config_output.append,
    )

    generation_output = []
    monkeypatch.setattr(saved_plans, "config_from_plan_version", lambda _version: config_stub())
    monkeypatch.setattr(
        saved_plans,
        "build_workbook_outputs",
        lambda _config: (_ for _ in ()).throw(RuntimeError("generation failed")),
    )
    saved_plans.duplicate_saved_plan_action(
        service,
        FakePreferences(),
        plan,
        input_func=lambda _prompt: "Copy",
        output_func=generation_output.append,
    )

    save_output = []
    monkeypatch.setattr(
        saved_plans,
        "build_workbook_outputs",
        lambda _config: (["summary"], "forecast", None, None),
    )
    service.save_generated_error = OSError("save failed")
    saved_plans.duplicate_saved_plan_action(
        service,
        FakePreferences(),
        plan,
        input_func=lambda _prompt: "Copy",
        output_func=save_output.append,
    )

    assert blank_result is False
    assert "Warning: Plan name cannot be blank." in blank_output
    assert "Error: source config failed" in config_output
    assert "Error: generation failed" in generation_output
    assert "Error: save failed" in save_output


def test_delete_empty_confirmation_and_failure() -> None:
    """Delete treats empty confirmation as cancellation and reports service errors."""
    plan = plan_stub()
    service = FakePlanHistoryService([plan])
    service.versions_by_plan[4] = [SimpleNamespace(id=21)]

    empty_output = []
    empty_result = saved_plans.delete_saved_plan_action(
        service,
        FakePreferences(),
        plan,
        input_func=lambda _prompt: "",
        output_func=empty_output.append,
    )

    failure_output = []
    service.delete_error = OSError("delete failed")
    failure_result = saved_plans.delete_saved_plan_action(
        service,
        FakePreferences(),
        plan,
        input_func=lambda _prompt: "DELETE",
        output_func=failure_output.append,
    )

    assert empty_result is False
    assert "Warning: Delete cancelled." in empty_output
    assert failure_result is False
    assert "Error: delete failed" in failure_output


def test_save_selected_plan_version_decline_success_and_failure() -> None:
    """Compatibility save-version action handles decline, success, and errors."""
    plan = plan_stub()
    service = FakePlanHistoryService([plan])

    decline_output = []
    decline_choices = iter(["Note", "no", ""])
    saved_plans.save_selected_plan_version_action(
        service,
        FakePreferences(),
        lambda: config_stub(),
        plan,
        input_func=lambda _prompt: next(decline_choices),
        output_func=decline_output.append,
    )

    success_output = []
    preferences = FakePreferences()
    success_choices = iter(["", "yes", ""])
    saved_plans.save_selected_plan_version_action(
        service,
        preferences,
        lambda: config_stub(),
        plan,
        input_func=lambda _prompt: next(success_choices),
        output_func=success_output.append,
    )

    failure_output = []
    service.save_version_error = ValueError("version failed")
    failure_choices = iter(["", "y", ""])
    saved_plans.save_selected_plan_version_action(
        service,
        FakePreferences(),
        lambda: config_stub(),
        plan,
        input_func=lambda _prompt: next(failure_choices),
        output_func=failure_output.append,
    )

    assert "Warning: Save cancelled." in decline_output
    assert "Success: Saved version 2 for Current Plan" in success_output
    assert preferences.marked[-1] == (4, "Current Plan")
    assert "Error: version failed" in failure_output


def test_save_current_plan_action_new_existing_declined_and_errors() -> None:
    """Compatibility save-current action handles creation, versioning, and errors."""
    service = FakePlanHistoryService()
    preferences = FakePreferences()
    create_choices = iter(["New Plan", "", ""])
    saved_plans.save_current_plan_action(
        service,
        preferences,
        lambda: config_stub(),
        input_func=lambda _prompt: next(create_choices),
        output_func=[].append,
    )

    decline_output = []
    decline_choices = iter(["New Plan", "", "no", ""])
    saved_plans.save_current_plan_action(
        service,
        preferences,
        lambda: config_stub(),
        input_func=lambda _prompt: next(decline_choices),
        output_func=decline_output.append,
    )

    version_output = []
    version_choices = iter(["New Plan", "description", "yes", ""])
    saved_plans.save_current_plan_action(
        service,
        preferences,
        lambda: config_stub(),
        input_func=lambda _prompt: next(version_choices),
        output_func=version_output.append,
    )

    blank_output = []
    saved_plans.save_current_plan_action(
        service,
        preferences,
        lambda: config_stub(),
        input_func=lambda _prompt: "",
        output_func=blank_output.append,
    )

    config_output = []
    config_choices = iter(["Other Plan", "", ""])
    saved_plans.save_current_plan_action(
        service,
        preferences,
        lambda: (_ for _ in ()).throw(ValueError("config failed")),
        input_func=lambda _prompt: next(config_choices),
        output_func=config_output.append,
    )

    persistence_output = []
    service.create_plan_error = ValueError("create failed")
    persistence_choices = iter(["Other Plan", "", ""])
    saved_plans.save_current_plan_action(
        service,
        preferences,
        lambda: config_stub(),
        input_func=lambda _prompt: next(persistence_choices),
        output_func=persistence_output.append,
    )

    assert service.created[0].name == "New Plan"
    assert "Warning: Save cancelled." in decline_output
    assert "Success: Saved version 2 for New Plan" in version_output
    assert "Warning: Plan name cannot be blank." in blank_output
    assert "Error: config failed" in config_output
    assert "Error: create failed" in persistence_output


def test_save_current_plan_helper_creates_and_versions() -> None:
    """The shared save helper creates a plan or saves a version by name."""
    service = FakePlanHistoryService()
    created = saved_plans.save_current_plan(
        service,
        config_stub(),
        name="Plan",
        description="description",
    )
    version = saved_plans.save_current_plan(
        service,
        config_stub(),
        name="Plan",
        force=True,
    )

    assert created[0] == "created"
    assert created[1].description == "description"
    assert version[0] == "version"
    assert service.saved_versions[-1].force is True


def test_formatting_fallbacks_and_missing_history_action() -> None:
    """Small presentation and fallback helpers remain deterministic."""
    output = []

    result = saved_plans._missing_history_action(
        FakePlanHistoryService(),
        FakePreferences(),
        plan_stub(),
        input_func=lambda _prompt: "",
        output_func=output.append,
    )

    assert result is False
    assert saved_plans.display_plan_name(SimpleNamespace(name="   ")) == "Untitled Plan"
    assert saved_plans.version_count_label(FakePlanHistoryService(), plan_stub()) == "Unknown"
    assert saved_plans.format_saved_datetime("not-a-date") == "not-a-date"
    assert "Error: saved plan history is not configured." in output


def test_missing_config_loader_raises_clear_error() -> None:
    """Missing config loader reports the expected configuration error."""
    try:
        saved_plans._missing_config_loader()
    except ValueError as exc:
        assert str(exc) == "saved plan config loading is not configured."
    else:
        raise AssertionError("expected ValueError")

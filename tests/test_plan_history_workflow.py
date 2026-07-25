"""Tests for interactive plan-history workflows."""

from types import SimpleNamespace

import pytest

from app.workflows import plan_history


def plan_stub(plan_id: int = 4, name: str = "Household Plan"):
    """Return a small saved-plan test value."""
    return SimpleNamespace(
        id=plan_id,
        name=name,
        description="",
        created_at="2026-07-20T10:00:00+00:00",
        updated_at="2026-07-24T20:15:00+00:00",
    )


def version_stub(
    version_id: int = 21,
    plan_id: int = 4,
    number: int = 1,
    *,
    active: bool = True,
):
    """Return a small saved-version test value."""
    return SimpleNamespace(
        id=version_id,
        plan_id=plan_id,
        version_number=number,
        created_at="2026-07-22T01:00:00+00:00",
        change_note="Initial",
        active=active,
    )


class FakeHistoryService:
    """In-memory service fake for History workflow behavior."""

    def __init__(self, plans=None) -> None:
        self.plans = plans or []
        self.versions_by_plan = {}
        self.comparison = SimpleNamespace(explanation="Comparison explanation")
        self.restored_version = SimpleNamespace(id=31, plan_id=4, version_number=3)
        self.list_plans_error = None
        self.list_versions_error = None
        self.version_error = None
        self.summary_error = None
        self.compare_error = None
        self.restore_error = None
        self.get_plan_error = None
        self.compare_calls = []
        self.restore_calls = []
        self.summary_calls = []

    def list_plans(self):
        if self.list_plans_error is not None:
            raise self.list_plans_error
        return self.plans

    def get_plan(self, plan_id):
        if self.get_plan_error is not None:
            raise self.get_plan_error
        for plan in self.plans:
            if plan.id == plan_id:
                return plan
        raise ValueError(f"plan {plan_id} was not found.")

    def list_plan_versions(self, plan_id):
        if self.list_versions_error is not None:
            raise self.list_versions_error
        return self.versions_by_plan.get(plan_id, [])

    def get_plan_version(self, version_id):
        if self.version_error is not None:
            raise self.version_error
        for versions in self.versions_by_plan.values():
            for version in versions:
                if version.id == version_id:
                    return version
        raise ValueError(f"plan version {version_id} was not found.")

    def plan_summary(self, version_id):
        if self.summary_error is not None:
            raise self.summary_error
        self.summary_calls.append(version_id)
        return f"Summary for version {version_id}"

    def compare_plan_versions(self, from_version, to_version):
        if self.compare_error is not None:
            raise self.compare_error
        self.compare_calls.append((from_version, to_version))
        return self.comparison

    def restore_plan_version(self, version_id):
        if self.restore_error is not None:
            raise self.restore_error
        self.restore_calls.append(version_id)
        return self.restored_version


class FakePreferences:
    """Record recent-plan changes without touching local preferences."""

    def __init__(self) -> None:
        self.marked = []

    def mark_recent(self, plan_id, plan_name):
        self.marked.append((plan_id, plan_name))


def test_selected_plan_history_menu_opens_and_backs_without_raw_ids() -> None:
    """A selected plan opens the complete numbered History submenu."""
    plan = plan_stub()
    service = FakeHistoryService([plan])
    preferences = FakePreferences()
    prompts = []
    output = []

    result = plan_history.run_selected_plan_history_menu(
        service,
        preferences,
        plan,
        input_func=lambda prompt: prompts.append(prompt) or "4",
        output_func=output.append,
    )

    assert result is False
    assert "History - Household Plan" in output
    assert "1. View Versions" in output
    assert "2. Compare Versions" in output
    assert "3. Restore Version" in output
    assert "4. Back" in output
    assert preferences.marked == [(4, "Household Plan")]
    assert not any("ID" in prompt for prompt in prompts)


def test_selected_plan_history_menu_routes_view_and_returns() -> None:
    """View Versions returns to History before Back returns to the plan menu."""
    plan = plan_stub()
    service = FakeHistoryService([plan])
    service.versions_by_plan[plan.id] = [
        version_stub(active=False),
        version_stub(22, number=2),
    ]
    preferences = FakePreferences()
    prompts = []
    output = []
    choices = iter(["1", "3", "", "4"])

    plan_history.run_selected_plan_history_menu(
        service,
        preferences,
        plan,
        input_func=lambda prompt: prompts.append(prompt) or next(choices),
        output_func=output.append,
    )

    text = "\n".join(output)
    assert output.count("History - Household Plan") == 2
    assert "View Version 1" in text
    assert "View Version 2" in text
    assert not any("ID" in prompt for prompt in prompts)


def test_selected_plan_history_menu_compares_numbered_versions() -> None:
    """Compare Versions validates numbered choices and returns to History."""
    plan = plan_stub()
    service = FakeHistoryService([plan])
    service.versions_by_plan[plan.id] = [
        version_stub(active=False),
        version_stub(22, number=2),
    ]
    prompts = []
    output = []
    choices = iter(["2", "invalid", "1", "2", "", "4"])

    plan_history.run_selected_plan_history_menu(
        service,
        FakePreferences(),
        plan,
        input_func=lambda prompt: prompts.append(prompt) or next(choices),
        output_func=output.append,
    )

    assert service.compare_calls == [(21, 22)]
    assert "Comparison explanation" in output
    assert "Warning: Please choose one of: 1, 2, 3." in output
    assert output.count("History - Household Plan") == 2
    assert not any("ID" in prompt for prompt in prompts)


def test_selected_plan_compare_cancellation_and_failure() -> None:
    """Comparison cancellation and service failures remain in History."""
    plan = plan_stub()
    service = FakeHistoryService([plan])
    service.versions_by_plan[plan.id] = [
        version_stub(active=False),
        version_stub(22, number=2),
    ]
    cancelled_output = []
    cancelled_choices = iter(["3", ""])
    plan_history.compare_selected_plan_versions_action(
        service,
        plan,
        input_func=lambda _prompt: next(cancelled_choices),
        output_func=cancelled_output.append,
    )

    later_cancelled_output = []
    later_cancelled_choices = iter(["1", "3", ""])
    plan_history.compare_selected_plan_versions_action(
        service,
        plan,
        input_func=lambda _prompt: next(later_cancelled_choices),
        output_func=later_cancelled_output.append,
    )

    service.compare_error = ValueError("comparison unavailable")
    failed_output = []
    failed_choices = iter(["1", "2", ""])
    plan_history.compare_selected_plan_versions_action(
        service,
        plan,
        input_func=lambda _prompt: next(failed_choices),
        output_func=failed_output.append,
    )

    assert "Warning: Comparison cancelled." in cancelled_output
    assert "Warning: Comparison cancelled." in later_cancelled_output
    assert "Error: comparison unavailable" in failed_output


def test_selected_plan_history_menu_restores_numbered_version() -> None:
    """Restore confirms by version number and returns to the History submenu."""
    plan = plan_stub()
    service = FakeHistoryService([plan])
    service.versions_by_plan[plan.id] = [version_stub()]
    preferences = FakePreferences()
    prompts = []
    output = []
    choices = iter(["3", "1", "yes", "", "4"])

    plan_history.run_selected_plan_history_menu(
        service,
        preferences,
        plan,
        input_func=lambda prompt: prompts.append(prompt) or next(choices),
        output_func=output.append,
    )

    assert service.restore_calls == [21]
    assert "Success: Restored as version 3." in output
    assert output.count("History - Household Plan") == 2
    assert any(
        prompt == "Restore version 1 as a new version? [y/N]: "
        for prompt in prompts
    )
    assert not any("ID" in prompt for prompt in prompts)


def test_selected_plan_restore_cancellation_and_failure() -> None:
    """Restore handles Back, declined confirmation, and service failure."""
    plan = plan_stub()
    service = FakeHistoryService([plan])
    service.versions_by_plan[plan.id] = [version_stub()]
    preferences = FakePreferences()

    back_output = []
    back_choices = iter(["2", ""])
    plan_history.restore_selected_plan_version_by_choice_action(
        service,
        preferences,
        plan,
        input_func=lambda _prompt: next(back_choices),
        output_func=back_output.append,
    )

    declined_output = []
    declined_choices = iter(["1", "no", ""])
    plan_history.restore_selected_plan_version_by_choice_action(
        service,
        preferences,
        plan,
        input_func=lambda _prompt: next(declined_choices),
        output_func=declined_output.append,
    )

    service.restore_error = ValueError("restore unavailable")
    failed_output = []
    failed_choices = iter(["1", "yes", ""])
    plan_history.restore_selected_plan_version_by_choice_action(
        service,
        preferences,
        plan,
        input_func=lambda _prompt: next(failed_choices),
        output_func=failed_output.append,
    )

    assert "Warning: Restore cancelled." in back_output
    assert "Warning: Restore cancelled." in declined_output
    assert "Error: restore unavailable" in failed_output


def test_selected_plan_history_handles_missing_plan_and_versions() -> None:
    """Missing plans and empty or unavailable histories fail safely."""
    plan = plan_stub()
    service = FakeHistoryService([plan])
    service.get_plan_error = ValueError("plan unavailable")
    missing_output = []
    plan_history.run_selected_plan_history_menu(
        service,
        FakePreferences(),
        plan,
        input_func=lambda _prompt: "",
        output_func=missing_output.append,
    )

    service.get_plan_error = None
    no_versions_output = []
    no_versions_choices = iter([""])
    plan_history.compare_selected_plan_versions_action(
        service,
        plan,
        input_func=lambda _prompt: next(no_versions_choices),
        output_func=no_versions_output.append,
    )

    service.list_versions_error = ValueError("history unavailable")
    failed_output = []
    failed_choices = iter([""])
    plan_history.restore_selected_plan_version_by_choice_action(
        service,
        FakePreferences(),
        plan,
        input_func=lambda _prompt: next(failed_choices),
        output_func=failed_output.append,
    )

    assert "Error: plan unavailable" in missing_output
    assert "Warning: No saved versions found for that plan." in no_versions_output
    assert "Error: history unavailable" in failed_output

    compare_failed_output = []
    compare_failed_choices = iter([""])
    plan_history.compare_selected_plan_versions_action(
        service,
        plan,
        input_func=lambda _prompt: next(compare_failed_choices),
        output_func=compare_failed_output.append,
    )

    service.list_versions_error = None
    restore_empty_output = []
    restore_empty_choices = iter([""])
    plan_history.restore_selected_plan_version_by_choice_action(
        service,
        FakePreferences(),
        plan,
        input_func=lambda _prompt: next(restore_empty_choices),
        output_func=restore_empty_output.append,
    )

    assert "Error: history unavailable" in compare_failed_output
    assert "Warning: No saved versions found for that plan." in restore_empty_output


def test_selected_plan_history_displays_versions_without_raw_ids() -> None:
    """Selected-plan History shows numbered versions and their summary."""
    plan = plan_stub()
    versions = [
        version_stub(active=False),
        version_stub(22, number=2),
    ]
    service = FakeHistoryService([plan])
    service.versions_by_plan[plan.id] = versions
    preferences = FakePreferences()
    prompts = []
    output = []
    choices = iter(["invalid", "2", ""])

    result = plan_history.view_selected_plan_history_action(
        service,
        preferences,
        plan,
        input_func=lambda prompt: prompts.append(prompt) or next(choices),
        output_func=output.append,
    )

    text = "\n".join(output)
    assert result is False
    assert "Plan: Household Plan" in text
    assert "Version 1" in text
    assert "Version 2" in text
    assert "Jul 22, 2026 at 1:00 AM" in text
    assert "Summary for version 22" in text
    assert "Warning: Please choose one of: 1, 2, 3." in text
    assert "Version ID" not in text
    assert not any("Version ID" in prompt for prompt in prompts)
    assert service.summary_calls == [22]
    assert preferences.marked == [(4, "Household Plan")]


def test_selected_plan_history_handles_empty_and_failed_history() -> None:
    """Empty histories and expected service failures return without traceback."""
    plan = plan_stub()
    service = FakeHistoryService([plan])
    preferences = FakePreferences()
    empty_output = []

    plan_history.view_selected_plan_history_action(
        service,
        preferences,
        plan,
        input_func=lambda _prompt: "",
        output_func=empty_output.append,
    )

    service.list_versions_error = ValueError("history unavailable")
    failed_output = []
    plan_history.view_selected_plan_history_action(
        service,
        preferences,
        plan,
        input_func=lambda _prompt: "",
        output_func=failed_output.append,
    )

    assert "Warning: No saved versions found for that plan." in empty_output
    assert "Error: history unavailable" in failed_output


def test_version_summary_can_return_or_handle_summary_failure() -> None:
    """Version selection supports Back and reports expected summary failures."""
    version = version_stub()
    service = FakeHistoryService()
    back_output = []

    plan_history.select_plan_version_summary(
        service,
        [version],
        input_func=lambda _prompt: "2",
        output_func=back_output.append,
    )

    service.summary_error = ValueError("summary unavailable")
    failed_output = []
    plan_history.select_plan_version_summary(
        service,
        [version],
        input_func=lambda _prompt: "1",
        output_func=failed_output.append,
    )

    assert service.summary_calls == []
    assert "Error: summary unavailable" in failed_output


def test_history_menu_back_and_factory_failure() -> None:
    """Compatibility History menu returns cleanly and handles setup failures."""
    output = []
    result = plan_history.run_history_menu(
        plan_history_service_factory=FakeHistoryService,
        preferences_factory=FakePreferences,
        input_func=lambda _prompt: "4",
        output_func=output.append,
    )
    failed_output = []
    failed = plan_history.run_history_menu(
        plan_history_service_factory=lambda: (_ for _ in ()).throw(
            ValueError("database unavailable")
        ),
        preferences_factory=FakePreferences,
        input_func=lambda _prompt: "",
        output_func=failed_output.append,
    )

    assert result is False
    assert failed is False
    assert "History" in output
    assert "1. View Plan History" in output
    assert "4. Back" in output
    assert "Error: database unavailable" in failed_output


def test_select_saved_plan_handles_empty_invalid_selection_and_back() -> None:
    """Plan selection reports empty state and validates numbered choices."""
    output = []
    assert (
        plan_history.select_saved_plan_for_history(
            FakeHistoryService(),
            output_func=output.append,
        )
        is None
    )

    plan = plan_stub()
    service = FakeHistoryService([plan])
    service.versions_by_plan[plan.id] = [version_stub()]
    choices = iter(["invalid", "0", "1"])
    prompts = []
    selected_output = []
    selected = plan_history.select_saved_plan_for_history(
        service,
        input_func=lambda prompt: prompts.append(prompt) or next(choices),
        output_func=selected_output.append,
    )
    back = plan_history.select_saved_plan_for_history(
        service,
        input_func=lambda _prompt: "2",
        output_func=lambda _message: None,
    )

    assert "No saved plans found." in output
    assert selected is plan
    assert back is None
    assert selected_output.count("Warning: Please choose one of: 1, 2.") == 2
    assert not any("Plan ID" in prompt for prompt in prompts)


def test_view_plan_history_handles_selection_and_service_errors() -> None:
    """Compatibility view displays selected history and expected failures."""
    plan = plan_stub()
    service = FakeHistoryService([plan])
    service.versions_by_plan[plan.id] = [version_stub()]
    preferences = FakePreferences()
    output = []
    choices = iter(["1", ""])

    plan_history.view_plan_history_action(
        service,
        preferences,
        input_func=lambda _prompt: next(choices),
        output_func=output.append,
    )

    service.list_plans_error = ValueError("plans unavailable")
    failed_output = []
    plan_history.view_plan_history_action(
        service,
        preferences,
        input_func=lambda _prompt: "",
        output_func=failed_output.append,
    )

    service.list_plans_error = None
    service.list_versions_error = ValueError("history unavailable")
    history_output = []
    history_choices = iter(["1", ""])
    plan_history.view_plan_history_action(
        service,
        preferences,
        input_func=lambda _prompt: next(history_choices),
        output_func=history_output.append,
    )

    assert "Plan: Household Plan" in output
    assert preferences.marked == [(4, "Household Plan")]
    assert "Error: plans unavailable" in failed_output
    assert "Error: history unavailable" in history_output


@pytest.mark.parametrize("raw_value", ["bad", "0", "-2"])
def test_positive_integer_prompt_rejects_invalid_values(raw_value: str) -> None:
    """History integer validation rejects non-positive and nonnumeric input."""
    output = []

    assert (
        plan_history.prompt_positive_int(
            "Version ID: ",
            input_func=lambda _prompt: raw_value,
            output_func=output.append,
        )
        is None
    )
    assert output == ["Warning: Please enter a positive whole number."]


def test_positive_integer_prompt_accepts_valid_value() -> None:
    """History integer validation returns a positive whole number."""
    assert (
        plan_history.prompt_positive_int(
            "Version ID: ",
            input_func=lambda _prompt: " 12 ",
        )
        == 12
    )


def test_compare_versions_success_and_failure() -> None:
    """Comparison displays service output and reports expected failures."""
    service = FakeHistoryService()
    output = []
    choices = iter(["1", "2", ""])
    plan_history.compare_versions_action(
        service,
        input_func=lambda _prompt: next(choices),
        output_func=output.append,
    )

    service.compare_error = ValueError("comparison unavailable")
    failed_output = []
    failed_choices = iter(["1", "2", ""])
    plan_history.compare_versions_action(
        service,
        input_func=lambda _prompt: next(failed_choices),
        output_func=failed_output.append,
    )

    assert service.compare_calls == [(1, 2)]
    assert "Comparison explanation" in output
    assert "Error: comparison unavailable" in failed_output


def test_compare_versions_rejects_invalid_start_and_end() -> None:
    """Comparison stops after either invalid version entry."""
    service = FakeHistoryService()
    start_output = []
    start_choices = iter(["bad", ""])
    plan_history.compare_versions_action(
        service,
        input_func=lambda _prompt: next(start_choices),
        output_func=start_output.append,
    )
    end_output = []
    end_choices = iter(["1", "bad", ""])
    plan_history.compare_versions_action(
        service,
        input_func=lambda _prompt: next(end_choices),
        output_func=end_output.append,
    )

    assert service.compare_calls == []
    assert "Warning: Please enter a positive whole number." in start_output
    assert "Warning: Please enter a positive whole number." in end_output


def test_restore_version_decline_success_and_failure() -> None:
    """Restore supports cancellation, success, and expected service errors."""
    plan = plan_stub()
    service = FakeHistoryService([plan])
    preferences = FakePreferences()
    declined_output = []
    declined_choices = iter(["no", ""])
    plan_history.restore_version_by_id_action(
        service,
        preferences,
        21,
        input_func=lambda _prompt: next(declined_choices),
        output_func=declined_output.append,
    )

    success_output = []
    success_choices = iter(["yes", ""])
    plan_history.restore_version_by_id_action(
        service,
        preferences,
        21,
        input_func=lambda _prompt: next(success_choices),
        output_func=success_output.append,
    )

    service.restore_error = ValueError("restore unavailable")
    failed_output = []
    failed_choices = iter(["y", ""])
    plan_history.restore_version_by_id_action(
        service,
        preferences,
        22,
        input_func=lambda _prompt: next(failed_choices),
        output_func=failed_output.append,
        plan=plan,
    )

    assert "Warning: Restore cancelled." in declined_output
    assert service.restore_calls == [21]
    assert preferences.marked == [(4, "Household Plan")]
    assert "Success: Restored as version 3 (version ID 31)." in success_output
    assert "Error: restore unavailable" in failed_output


def test_restore_version_action_validates_version_id() -> None:
    """Compatibility restore action validates before asking for confirmation."""
    service = FakeHistoryService([plan_stub()])
    preferences = FakePreferences()
    invalid_output = []
    invalid_choices = iter(["bad", ""])
    plan_history.restore_version_action(
        service,
        preferences,
        input_func=lambda _prompt: next(invalid_choices),
        output_func=invalid_output.append,
    )

    success_output = []
    success_choices = iter(["21", "yes", ""])
    plan_history.restore_version_action(
        service,
        preferences,
        input_func=lambda _prompt: next(success_choices),
        output_func=success_output.append,
    )

    assert "Warning: Please enter a positive whole number." in invalid_output
    assert service.restore_calls == [21]
    assert "Success: Restored as version 3 (version ID 31)." in success_output


def test_selected_restore_rejects_missing_or_foreign_version() -> None:
    """Selected-plan restore cannot restore a missing or another plan's version."""
    plan = plan_stub()
    service = FakeHistoryService([plan])
    preferences = FakePreferences()
    invalid_output = []
    invalid_choices = iter(["bad", ""])
    plan_history.restore_selected_plan_version_action(
        service,
        preferences,
        plan,
        input_func=lambda _prompt: next(invalid_choices),
        output_func=invalid_output.append,
    )

    service.version_error = ValueError("version unavailable")
    missing_output = []
    missing_choices = iter(["21", ""])
    plan_history.restore_selected_plan_version_action(
        service,
        preferences,
        plan,
        input_func=lambda _prompt: next(missing_choices),
        output_func=missing_output.append,
    )

    service.version_error = None
    service.versions_by_plan[9] = [version_stub(22, plan_id=9)]
    foreign_output = []
    foreign_choices = iter(["22", ""])
    plan_history.restore_selected_plan_version_action(
        service,
        preferences,
        plan,
        input_func=lambda _prompt: next(foreign_choices),
        output_func=foreign_output.append,
    )

    assert "Warning: Please enter a positive whole number." in invalid_output
    assert "Error: version unavailable" in missing_output
    assert "Warning: That version does not belong to the selected plan." in foreign_output
    assert service.restore_calls == []


def test_selected_restore_success_uses_selected_plan_context() -> None:
    """Selected-plan restore validates ownership and refreshes recent order."""
    plan = plan_stub()
    service = FakeHistoryService([plan])
    service.versions_by_plan[plan.id] = [version_stub()]
    preferences = FakePreferences()
    output = []
    choices = iter(["21", "yes", ""])

    plan_history.restore_selected_plan_version_action(
        service,
        preferences,
        plan,
        input_func=lambda _prompt: next(choices),
        output_func=output.append,
    )

    assert service.restore_calls == [21]
    assert preferences.marked == [(4, "Household Plan")]
    assert "Success: Restored as version 3 (version ID 31)." in output


def test_print_plan_versions_and_comparison_presenters() -> None:
    """History presenters retain empty, tabular, and comparison output."""
    output = []
    plan_history.print_plan_versions([], output.append)
    plan_history.print_plan_versions([version_stub()], output.append)
    plan_history.print_plan_comparison(
        SimpleNamespace(explanation="Same assumptions"),
        output.append,
    )

    text = "\n".join(output)
    assert "Warning: No saved versions found for that plan." in text
    assert "Version 1" in text
    assert "active" in text
    assert "Initial" in text
    assert "Same assumptions" in text

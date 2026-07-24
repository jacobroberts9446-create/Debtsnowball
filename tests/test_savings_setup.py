"""Tests for interactive savings strategy selection."""

from decimal import Decimal

from app import savings_setup


def run_strategy(choices: list[str]):
    """Run savings strategy selection with canned input."""
    output = []
    inputs = iter(choices)
    result = savings_setup.prompt_savings_strategy(
        input_func=lambda _prompt: next(inputs),
        output_func=output.append,
    )
    return result, output


def test_emergency_first_strategy() -> None:
    """Emergency-first returns a stable internal value."""
    result, _output = run_strategy(["1"])

    assert result.strategy == savings_setup.SavingsStrategy.EMERGENCY_FIRST
    assert result.savings_percent == Decimal("100")
    assert result.snowball_percent == Decimal("0")


def test_split_strategy() -> None:
    """Split returns the existing 50/50 strategy."""
    result, _output = run_strategy(["2"])

    assert result == savings_setup.default_savings_strategy()


def test_maximum_snowball_strategy() -> None:
    """Maximum snowball returns zero savings allocation."""
    result, _output = run_strategy(["3"])

    assert result.strategy == savings_setup.SavingsStrategy.SNOWBALL
    assert result.savings_percent == Decimal("0")
    assert result.snowball_percent == Decimal("100")


def test_custom_strategy() -> None:
    """Custom strategy accepts matching percentages totaling 100."""
    result, _output = run_strategy(["4", "25", "75"])

    assert result.strategy == savings_setup.SavingsStrategy.CUSTOM
    assert result.savings_percent == Decimal("25")
    assert result.snowball_percent == Decimal("75")


def test_custom_invalid_percentages_reprompt() -> None:
    """Custom strategy validates each percentage range."""
    result, output = run_strategy(["4", "-1", "25", "101", "75"])

    assert result.savings_percent == Decimal("25")
    assert result.snowball_percent == Decimal("75")
    assert "Warning: Percent to savings must be between 0 and 100." in output
    assert "Warning: Percent to snowball must be between 0 and 100." in output


def test_custom_percentages_must_total_100() -> None:
    """Custom percentages must total exactly 100."""
    result, output = run_strategy(["4", "20", "70", "30", "70"])

    assert result.savings_percent == Decimal("30")
    assert result.snowball_percent == Decimal("70")
    assert "Warning: Savings and snowball percentages must total 100." in output


def test_invalid_strategy_choice_reprompts() -> None:
    """Invalid strategy menu choices show a friendly warning."""
    result, output = run_strategy(["bad", "2"])

    assert result.strategy == savings_setup.SavingsStrategy.SPLIT
    assert "Warning: Please choose one of: 1, 2, 3, 4." in output


def test_engine_percentage_mapping() -> None:
    """Strategies map to the existing engine savings percentage seam."""
    assert savings_setup.savings_percentage_for_engine(
        savings_setup.SavingsStrategySelection(
            savings_setup.SavingsStrategy.EMERGENCY_FIRST,
            Decimal("100"),
            Decimal("0"),
        )
    ) == Decimal("1")
    assert savings_setup.savings_percentage_for_engine(
        savings_setup.default_savings_strategy()
    ) is None
    assert savings_setup.savings_percentage_for_engine(
        savings_setup.SavingsStrategySelection(
            savings_setup.SavingsStrategy.SNOWBALL,
            Decimal("0"),
            Decimal("100"),
        )
    ) == Decimal("0")
    assert savings_setup.savings_percentage_for_engine(
        savings_setup.SavingsStrategySelection(
            savings_setup.SavingsStrategy.CUSTOM,
            Decimal("25"),
            Decimal("75"),
        )
    ) == Decimal("0.25")

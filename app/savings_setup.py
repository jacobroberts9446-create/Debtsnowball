"""Interactive savings-strategy selection for plan setup."""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from app.console import OutputFunc, print_warning
from app.debt_input import prompt_validated
from app.menu import InputFunc, MenuOption, display_menu
from app.money import to_decimal


class SavingsStrategy(StrEnum):
    """Stable internal savings strategy values."""

    EMERGENCY_FIRST = "emergency_first"
    SPLIT = "split"
    SNOWBALL = "snowball"
    CUSTOM = "custom"


@dataclass(frozen=True)
class SavingsStrategySelection:
    """Selected savings allocation strategy."""

    strategy: SavingsStrategy
    savings_percent: Decimal | None = None
    snowball_percent: Decimal | None = None


def default_savings_strategy() -> SavingsStrategySelection:
    """Return the existing 50/50 behavior as the default setup strategy."""
    return SavingsStrategySelection(
        SavingsStrategy.SPLIT,
        savings_percent=Decimal("50"),
        snowball_percent=Decimal("50"),
    )


def prompt_savings_strategy(
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> SavingsStrategySelection:
    """Prompt for a savings allocation strategy."""
    options = [
        MenuOption("1", "Build Emergency Fund First", lambda: True),
        MenuOption("2", "Split Between Savings and Snowball", lambda: True),
        MenuOption("3", "Maximum Snowball", lambda: True),
        MenuOption("4", "Custom", lambda: True),
    ]
    while True:
        display_menu("Savings Strategy", options, output_func)
        choice = input_func("Choose an option: ").strip()
        if choice == "1":
            return SavingsStrategySelection(
                SavingsStrategy.EMERGENCY_FIRST,
                savings_percent=Decimal("100"),
                snowball_percent=Decimal("0"),
            )
        if choice == "2":
            return default_savings_strategy()
        if choice == "3":
            return SavingsStrategySelection(
                SavingsStrategy.SNOWBALL,
                savings_percent=Decimal("0"),
                snowball_percent=Decimal("100"),
            )
        if choice == "4":
            return prompt_custom_savings_strategy(input_func, output_func)
        print_warning("Please choose one of: 1, 2, 3, 4.", output_func)


def prompt_custom_savings_strategy(
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> SavingsStrategySelection:
    """Prompt for custom savings and snowball percentages."""
    while True:
        savings_percent = prompt_validated(
            "Percent to savings: ",
            parse_percentage,
            "Percent to savings must be between 0 and 100.",
            input_func,
            output_func,
        )
        snowball_percent = prompt_validated(
            "Percent to snowball: ",
            parse_percentage,
            "Percent to snowball must be between 0 and 100.",
            input_func,
            output_func,
        )
        if savings_percent + snowball_percent == Decimal("100"):
            return SavingsStrategySelection(
                SavingsStrategy.CUSTOM,
                savings_percent=savings_percent,
                snowball_percent=snowball_percent,
            )
        print_warning("Savings and snowball percentages must total 100.", output_func)


def parse_percentage(value: str) -> Decimal:
    """Parse a percentage between 0 and 100."""
    normalized = value.strip().removesuffix("%").strip()
    parsed = to_decimal(normalized)
    if not Decimal("0") <= parsed <= Decimal("100"):
        raise ValueError("percentage must be between 0 and 100.")
    return parsed


def strategy_label(selection: SavingsStrategySelection | None) -> str:
    """Return a display label for a selected strategy."""
    if selection is None:
        return "Not available."
    return {
        SavingsStrategy.EMERGENCY_FIRST: "Build Emergency Fund First",
        SavingsStrategy.SPLIT: "Split Between Savings and Snowball",
        SavingsStrategy.SNOWBALL: "Maximum Snowball",
        SavingsStrategy.CUSTOM: "Custom",
    }[selection.strategy]


def savings_percentage_for_engine(
    selection: SavingsStrategySelection,
) -> Decimal | None:
    """Map a setup strategy to the existing engine savings-percentage seam."""
    if selection.strategy == SavingsStrategy.SPLIT:
        return None
    if selection.savings_percent is None:
        return None
    return selection.savings_percent / Decimal("100")

"""Reusable interactive console menu framework."""

from collections.abc import Callable
from dataclasses import dataclass

from app.console import OutputFunc, print_menu_title, print_warning

InputFunc = Callable[[str], str]
MenuAction = Callable[[], bool]
MenuTitleRenderer = Callable[[str, OutputFunc], None]


@dataclass(frozen=True)
class MenuOption:
    """One selectable interactive menu option."""

    key: str
    label: str
    action: MenuAction


def run_menu(
    title: str,
    options: list[MenuOption],
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
    title_renderer: MenuTitleRenderer | None = None,
) -> None:
    """Display a menu until an action requests exit."""
    option_map = {option.key: option for option in options}
    while True:
        display_menu(title, options, output_func, title_renderer)

        choice = input_func("Choose an option: ").strip()
        option = option_map.get(choice)

        if option is not None:
            should_exit = option.action()
            if should_exit:
                return
            continue

        print_warning(f"Please choose one of: {', '.join(option_map)}.", output_func)


def display_menu(
    title: str,
    options: list[MenuOption],
    output_func: OutputFunc = print,
    title_renderer: MenuTitleRenderer | None = None,
) -> None:
    """Print a numbered interactive menu."""
    output_func("")
    if title_renderer is None:
        print_menu_title(title, output_func)
    else:
        title_renderer(title, output_func)

    key_width = max(len(option.key) for option in options)
    for option in options:
        output_func(f"{option.key.rjust(key_width)}. {option.label}")
    output_func("")


def wait_for_enter(
    input_func: InputFunc = input,
    prompt: str = "Press Enter to continue...",
) -> None:
    """Wait for the user to press Enter."""
    input_func(prompt)

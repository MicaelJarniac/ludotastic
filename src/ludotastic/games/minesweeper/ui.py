"""Minesweeper in NiceGUI."""

__all__: tuple[str, ...] = ("render",)

from collections.abc import Callable
from typing import Final

from nicegui import ui

from ludotastic.engine.render import RenderContext
from ludotastic.games.minesweeper.game import (
    CELLS,
    COLS,
    MINES,
    Action,
    MinesweeperState,
    Move,
    adjacent_mines,
)

COVERED: Final = (
    "bg-slate-300 dark:bg-slate-700 hover:bg-slate-200 dark:hover:bg-slate-600"
)
REVEALED: Final = "bg-slate-100 dark:bg-slate-800"
FLAGGED: Final = "bg-rose-300 dark:bg-rose-700"
HIT_MINE: Final = "bg-red-600 text-white"
MINE: Final = "bg-slate-400 dark:bg-slate-600"
FOUND_MINE: Final = "bg-emerald-300 dark:bg-emerald-700"
WRONG_FLAG: Final = "bg-amber-200 dark:bg-amber-800 line-through"
# Quasar fades disabled buttons; a revealed cell should stay crisp.
CRISP: Final = "!opacity-100 disabled:!cursor-default"
# Quasar sizes buttons by their content; force small square cells instead.
CELL_SIZE: Final = "!p-0 !w-7 !h-7 !min-h-0 sm:!w-8 sm:!h-8"


def status_text(state: MinesweeperState) -> str:
    """The line above the grid: the flag count, or the result."""
    if state.game_over:
        return "Game over — you hit a mine!"
    if state.won:
        return "You win! Every safe cell is cleared."
    return f"Flags: {sum(state.flagged)}/{MINES}"


def cell_text(state: MinesweeperState, cell: int) -> str:
    """What a cell shows: its count, a flag, a mine, or nothing."""
    if state.game_over and cell in state.mines and not state.flagged[cell]:
        return "*"
    if state.revealed[cell]:
        count = adjacent_mines(state.mines, cell)
        return str(count) if count else ""
    if state.flagged[cell]:
        return "F"
    return ""


def cell_classes(state: MinesweeperState, cell: int) -> str:
    """The Tailwind classes that colour a cell.

    Once a mine is hit, the field is shown: the mine that went off, the other
    mines, the flags that were right and the flags that were wrong.
    """
    if state.game_over:
        if cell == state.exploded:
            return HIT_MINE
        if cell in state.mines:
            return FOUND_MINE if state.flagged[cell] else MINE
        if state.flagged[cell]:
            return WRONG_FLAG
    if state.revealed[cell]:
        return REVEALED
    if state.flagged[cell]:
        return FLAGGED
    return COVERED


def is_disabled(state: MinesweeperState, cell: int, *, clickable: bool) -> bool:
    """Whether a cell's button is disabled; flagged cells stay live to unflag."""
    return not clickable or state.revealed[cell]


def reveal(state: MinesweeperState, cell: int, submit: Callable[[Move], None]) -> None:
    """Reveal `cell` unless it is already revealed or flagged."""
    if not state.revealed[cell] and not state.flagged[cell]:
        submit(Move(action=Action.REVEAL, cell=cell))


def flag(state: MinesweeperState, cell: int, submit: Callable[[Move], None]) -> None:
    """Toggle the flag on `cell` unless it is revealed."""
    if not state.revealed[cell]:
        submit(Move(action=Action.FLAG, cell=cell))


def render(ctx: RenderContext) -> None:
    """Draw the status line, the reveal/flag switch and the grid of cells.

    Right-click flags a cell. Touch screens have no right-click, so the switch
    makes a tap flag instead of reveal.
    """
    state = ctx.match.state
    assert isinstance(state, MinesweeperState)  # noqa: S101
    seat = 0
    clickable = ctx.can_act(seat)
    storage = ui.context.client.storage

    def submit(move: Move) -> None:
        ctx.submit(seat, move)

    def tap(cell: int) -> None:
        (flag if storage.get("minesweeper-flag") else reveal)(state, cell, submit)

    with ui.column().classes("items-center gap-3"):
        ui.label(status_text(state)).classes("text-lg font-semibold").mark("status")
        if clickable:
            ui.switch("Tap to flag").bind_value(storage, "minesweeper-flag").props(
                "dense"
            ).mark("flag-mode")
        with ui.grid(columns=COLS).classes(
            "gap-1 p-2 rounded-2xl bg-slate-200 dark:bg-slate-900 shadow-md"
        ):
            for cell in range(CELLS):
                disabled = is_disabled(state, cell, clickable=clickable)
                (
                    ui.button(
                        cell_text(state, cell),
                        on_click=lambda _, cell=cell: tap(cell),
                        color=None,
                    )
                    .props("flat bordered" + (" disable" if disabled else ""))
                    .classes(
                        f"{CELL_SIZE} rounded-md text-sm font-bold {CRISP} "
                        f"{cell_classes(state, cell)}"
                    )
                    .mark(f"cell-{cell}")
                    # `.prevent` keeps the browser's own menu from opening.
                    .on(
                        "contextmenu.prevent",
                        lambda _e, cell=cell: flag(state, cell, submit),
                    )
                )

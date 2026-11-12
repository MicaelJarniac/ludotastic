"""Connect Four in NiceGUI."""

__all__: tuple[str, ...] = ("render",)

from typing import Final

from nicegui import ui

from ludotastic.engine.render import RenderContext
from ludotastic.games.connect_four.game import (
    COLS,
    DISCS,
    ConnectFourState,
    Drop,
    is_full,
    winner,
    winning_cells,
)

CELL_STYLES: Final = {
    "R": "bg-rose-500",
    "Y": "bg-amber-400",
    None: "bg-slate-100 dark:bg-slate-900",
}


def render(ctx: RenderContext) -> None:
    """Draw the 6x7 board with clickable column drop buttons."""
    state = ctx.match.state
    assert isinstance(state, ConnectFourState)  # noqa: S101
    seat = DISCS.index(state.next_disc)
    over = winner(state.board) is not None or is_full(state.board)
    clickable = not over and ctx.can_act(seat)
    highlight = winning_cells(state.board)

    with ui.column().classes("items-center gap-3"):
        with ui.grid(columns=COLS).classes("gap-2 px-3"):
            for col in range(COLS):
                open_col = clickable and state.board[0][col] is None
                (
                    ui.button(
                        str(col),
                        on_click=lambda _, col=col: ctx.submit(seat, Drop(column=col)),
                        color=None,
                    )
                    .props("flat dense" + ("" if open_col else " disable"))
                    .classes(
                        "w-10 h-9 sm:w-12 sm:h-10 rounded-lg font-bold "
                        "bg-slate-200 text-slate-800 hover:bg-blue-200 "
                        "dark:bg-slate-700 dark:text-slate-100 dark:hover:bg-blue-800"
                    )
                    .mark(f"col-{col}")
                )

        with ui.grid(columns=COLS).classes(
            "gap-2 p-3 rounded-2xl bg-blue-700 dark:bg-blue-900 shadow-md"
        ):
            for r, row in enumerate(state.board):
                for c, cell in enumerate(row):
                    ring = " ring-4 ring-white" if (r, c) in highlight else ""
                    ui.element("div").classes(
                        "w-10 h-10 sm:w-12 sm:h-12 rounded-full "
                        f"{CELL_STYLES[cell]}{ring}"
                    ).mark(f"cell-{r}-{c}")

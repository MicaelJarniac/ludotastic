"""Minesweeper rules."""

__all__: tuple[str, ...] = (
    "CELLS",
    "COLS",
    "MINES",
    "ROWS",
    "Action",
    "Cell",
    "Minesweeper",
    "MinesweeperState",
    "Move",
    "adjacent_mines",
    "neighbours",
    "place_mines",
)

import random
from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Final, override

from annotated_types import Interval, Len
from pydantic import BaseModel, ConfigDict

from ludotastic.engine.game import ActionMode, Game, GameInfo, Outcome
from ludotastic.types import Seat

if TYPE_CHECKING:
    from ludotastic.engine.render import Renderer

COLS: Final = 9
"""Number of columns in the grid."""
ROWS: Final = 13
"""Number of rows in the grid."""
MINES: Final = 20
"""Mines hidden under the grid."""
CELLS: Final = COLS * ROWS
"""Total number of cells on the grid."""

type Cell = Annotated[int, Interval(ge=0, le=CELLS - 1)]
"""A cell's number: row `cell // COLS`, column `cell % COLS`."""

type Grid = Annotated[tuple[bool, ...], Len(CELLS, CELLS)]
"""One flag per cell, by cell number."""

_ALL_COVERED: Final[tuple[bool, ...]] = (False,) * CELLS


class Action(StrEnum):
    """What a move does to a cell."""

    REVEAL = "reveal"
    FLAG = "flag"


class Move(BaseModel):
    """Reveal a cell, or toggle the flag on it."""

    model_config = ConfigDict(frozen=True, use_attribute_docstrings=True)

    action: Action
    """Whether to reveal the cell or toggle its flag."""
    # Spelled out rather than an alias, so the model gets the bounds inline,
    # not behind a `$ref`.
    cell: Annotated[int, Interval(ge=0, le=CELLS - 1)]
    """The cell's number: row `cell // COLS`, column `cell % COLS`."""


class MinesweeperState(BaseModel):
    """The mines, the cells revealed and flagged, and the mine hit, if any."""

    model_config = ConfigDict(frozen=True)

    seed: int = 0
    """Where the mines go; they are placed on the first reveal."""
    mines: frozenset[Cell] = frozenset()
    """The mines' cell numbers; empty until the first reveal."""
    revealed: Grid = _ALL_COVERED
    """Which cells are revealed, by cell number."""
    flagged: Grid = _ALL_COVERED
    """Which cells are flagged, by cell number."""
    exploded: Cell | None = None
    """The mine that was revealed, ending the game."""

    @property
    def game_over(self) -> bool:
        """Whether a mine has been revealed."""
        return self.exploded is not None

    @property
    def won(self) -> bool:
        """Whether every safe cell is revealed.

        Examples:
            >>> MinesweeperState().won
            False
        """
        if not self.mines or self.game_over:
            return False
        return all(
            shown for cell, shown in enumerate(self.revealed) if cell not in self.mines
        )


def _col_row(cell: int) -> tuple[int, int]:
    """Split a cell number into `(column, row)`."""
    return cell % COLS, cell // COLS


def neighbours(cell: int) -> tuple[int, ...]:
    """The up-to-8 cell numbers around `cell`.

    Examples:
        >>> neighbours(0)
        (1, 9, 10)
        >>> neighbours(116)
        (106, 107, 115)
    """
    col, row = _col_row(cell)
    found: list[int] = []
    for d_row in (-1, 0, 1):
        for d_col in (-1, 0, 1):
            if d_row == 0 and d_col == 0:
                continue
            n_row, n_col = row + d_row, col + d_col
            if 0 <= n_row < ROWS and 0 <= n_col < COLS:
                found.append(n_row * COLS + n_col)
    return tuple(found)


def adjacent_mines(mines: frozenset[int], cell: int) -> int:
    """How many mines touch `cell`.

    Examples:
        >>> adjacent_mines(frozenset({0}), 1)
        1
        >>> adjacent_mines(frozenset({0}), 116)
        0
    """
    return sum(neighbour in mines for neighbour in neighbours(cell))


def place_mines(seed: int, first: int) -> frozenset[int]:
    """Scatter the mines away from the first cell revealed and its neighbours.

    Examples:
        >>> mines = place_mines(0, 40)
        >>> len(mines), 40 in mines, any(n in mines for n in neighbours(40))
        (20, False, False)
    """
    keep_clear = {first, *neighbours(first)}
    free = [cell for cell in range(CELLS) if cell not in keep_clear]
    return frozenset(random.Random(seed).sample(free, MINES))


def _flood_reveal(state: MinesweeperState, start: int) -> tuple[bool, ...]:
    """Reveal `start`, opening every connected run of empty cells."""
    revealed = list(state.revealed)
    pending = [start]
    while pending:
        cell = pending.pop()
        if revealed[cell] or state.flagged[cell] or cell in state.mines:
            continue
        revealed[cell] = True
        if not adjacent_mines(state.mines, cell):
            pending.extend(neighbours(cell))
    return tuple(revealed)


def _reveal(state: MinesweeperState, cell: int) -> MinesweeperState:
    """Reveal `cell`, ending the game in a loss when it holds a mine."""
    if state.game_over or state.revealed[cell] or state.flagged[cell]:
        return state
    if not state.mines:
        state = state.model_copy(update={"mines": place_mines(state.seed, cell)})
    if cell in state.mines:
        revealed = tuple(
            index == cell or was for index, was in enumerate(state.revealed)
        )
        return state.model_copy(update={"revealed": revealed, "exploded": cell})
    return state.model_copy(update={"revealed": _flood_reveal(state, cell)})


def _toggle_flag(state: MinesweeperState, cell: int) -> MinesweeperState:
    """Flag `cell` if it is not flagged, or unflag it."""
    if state.game_over or state.revealed[cell]:
        return state
    flagged = tuple(
        not was if index == cell else was for index, was in enumerate(state.flagged)
    )
    return state.model_copy(update={"flagged": flagged})


class Minesweeper(Game[MinesweeperState, Move]):
    """Clear the field without revealing a mine."""

    info = GameInfo(
        id="minesweeper",
        name="Minesweeper",
        description="Reveal cells and clear the field without hitting a mine.",
        min_players=1,
        max_players=1,
        action_modes=(ActionMode.CHOICE, ActionMode.STRUCTURED, ActionMode.TOOLS),
        tags=("classic", "puzzle", "single-player"),
    )
    rules = (
        f"Minesweeper is played on a {COLS} by {ROWS} grid of cells with {MINES} "
        "mines hidden under some of them. On your turn, reveal one cell or toggle "
        "the flag on one cell. Your first reveal is always safe, and opens an area "
        "with no mines touching it. Revealing a cell that holds a mine ends the "
        "game in a loss. A revealed cell shows how many mines touch it, from 0 to "
        "8; a cell with no touching mines opens every connected empty cell. Flag "
        "cells you believe hold mines. You win by revealing every cell without a "
        "mine."
    )
    state_type = MinesweeperState
    action_type = Move

    @override
    def initial_state(self, rng: random.Random) -> MinesweeperState:
        """Cover every cell; the mines wait for the first reveal."""
        return MinesweeperState(seed=rng.getrandbits(32))

    @override
    def to_move(self, state: MinesweeperState) -> Sequence[Seat]:
        """The sole seat acts until the field is cleared or a mine is hit."""
        return () if self.outcome(state) is not None else (0,)

    @override
    def legal_actions(self, state: MinesweeperState, player: Seat) -> Sequence[Move]:
        """Reveal or flag any cell that is not revealed."""
        del player
        if self.outcome(state) is not None:
            return ()
        moves: list[Move] = []
        for cell in range(CELLS):
            if state.revealed[cell]:
                continue
            if not state.flagged[cell]:
                moves.append(Move(action=Action.REVEAL, cell=cell))
            moves.append(Move(action=Action.FLAG, cell=cell))
        return tuple(moves)

    @override
    def apply(
        self,
        state: MinesweeperState,
        actions: Mapping[int, Move],
        rng: random.Random,
    ) -> MinesweeperState:
        """Apply the seat's move; the game is single-player."""
        del rng
        for move in actions.values():
            state = (
                _reveal(state, move.cell)
                if move.action is Action.REVEAL
                else _toggle_flag(state, move.cell)
            )
        return state

    @override
    def outcome(self, state: MinesweeperState) -> Outcome | None:
        """A loss when a mine is revealed; a win when every safe cell is."""
        if state.game_over:
            return Outcome(scores=(0.0,), reason="You hit a mine — game over.")
        if state.won:
            return Outcome.win(1, 0, "All safe cells revealed — you win!")
        return None

    @override
    def out_of_turns(self, state: MinesweeperState) -> Outcome:
        """The field was not cleared before the turn limit."""
        del state
        return Outcome(scores=(0.0,), reason="Turn limit reached — field not cleared.")

    @override
    def public_state(self, state: MinesweeperState) -> MinesweeperState:
        """Hide the mines, and the seed that places them, until the game ends."""
        if self.outcome(state) is not None:
            return state
        return state.model_copy(update={"seed": 0, "mines": frozenset()})

    @override
    def observe(self, state: MinesweeperState, player: Seat) -> str:
        """The grid as the player sees it; mines stay hidden."""
        del player
        lines = ["    " + " ".join(str(col) for col in range(COLS))]
        for row in range(ROWS):
            marks: list[str] = []
            for col in range(COLS):
                cell = row * COLS + col
                if state.revealed[cell]:
                    count = adjacent_mines(state.mines, cell)
                    marks.append(str(count) if count else ".")
                elif state.flagged[cell]:
                    marks.append("F")
                else:
                    marks.append("?")
            lines.append(f"{row:>2}  " + " ".join(marks))
        status = (
            "Game over — you hit a mine."
            if state.game_over
            else f"Flags: {sum(state.flagged)}/{MINES}."
        )
        return "\n".join(
            [
                f"Minesweeper — {COLS}x{ROWS}, {MINES} mines. Clear every safe cell.",
                *lines,
                status,
            ]
        )

    @override
    def describe_action(self, action: Move) -> str:
        """Name the move.

        Examples:
            >>> Minesweeper().describe_action(Move(action=Action.REVEAL, cell=0))
            'Reveal cell 0.'
            >>> Minesweeper().describe_action(Move(action=Action.FLAG, cell=40))
            'Toggle the flag on cell 40.'
        """
        if action.action is Action.REVEAL:
            return f"Reveal cell {action.cell}."
        return f"Toggle the flag on cell {action.cell}."

    @override
    def describe_option(
        self, state: MinesweeperState, player: Seat, action: Move
    ) -> str:
        """Name the move and say where the cell is.

        Examples:
            >>> Minesweeper().describe_option(
            ...     MinesweeperState(), 0, Move(action=Action.REVEAL, cell=0)
            ... )
            'Reveal cell 0 (row 0, column 0).'
        """
        del state, player
        col, row = _col_row(action.cell)
        where = f"row {row}, column {col}"
        if action.action is Action.REVEAL:
            return f"Reveal cell {action.cell} ({where})."
        return f"Toggle the flag on cell {action.cell} ({where})."

    @override
    def action_key(self, action: Move) -> str:
        """A unique key such as `reveal-3` or `flag-3`.

        Examples:
            >>> Minesweeper().action_key(Move(action=Action.FLAG, cell=7))
            'flag-7'
        """
        return f"{action.action}-{action.cell}"

    @override
    def player_label(self, player: Seat) -> str:
        """The lone seat."""
        del player
        return "Player"

    @override
    def renderer(self) -> Renderer:
        """An interactive grid of cells."""
        from ludotastic.games.minesweeper.ui import render  # noqa: PLC0415

        return render

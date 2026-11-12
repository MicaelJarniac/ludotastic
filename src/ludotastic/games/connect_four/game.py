"""Connect Four rules, image snapshot and bots."""

__all__: tuple[str, ...] = (
    "COLS",
    "COLUMN_ORDER",
    "CONNECT",
    "DISCS",
    "EMPTY",
    "LINES",
    "ROWS",
    "ConnectFour",
    "ConnectFourState",
    "Disc",
    "Drop",
    "GreedyBot",
    "MinimaxBot",
    "draw_board",
    "evaluate",
    "landing_row",
    "render_board",
    "winner",
    "winning_cells",
)

import random
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Annotated, Final, Literal, override

from annotated_types import Interval
from pydantic import BaseModel, ConfigDict

from ludotastic.engine.drawing import Canvas
from ludotastic.engine.game import ActionMode, BotFactory, Game, GameInfo, Outcome
from ludotastic.engine.players import Decision, Player, PlayerKind, TurnContext
from ludotastic.types import Seat

if TYPE_CHECKING:
    from ludotastic.engine.render import Renderer

type Disc = Literal["R", "Y"]
type Cell = Disc | None
type Row = tuple[Cell, Cell, Cell, Cell, Cell, Cell, Cell]
type Board = tuple[Row, Row, Row, Row, Row, Row]
type Coord = tuple[int, int]
type Line = tuple[Coord, Coord, Coord, Coord]

type ColumnIndex = Annotated[int, Interval(ge=0, le=6)]
"""A column number from 0 (leftmost) to 6 (rightmost)."""

ROWS: Final = 6
COLS: Final = 7
CONNECT: Final = 4
DISCS: Final[tuple[Disc, Disc]] = ("R", "Y")
DISC_NAMES: Final[dict[Disc, str]] = {"R": "Red", "Y": "Yellow"}
COLUMN_ORDER: Final = (3, 2, 4, 1, 5, 0, 6)
"""Columns ordered from center outward to improve alpha-beta pruning."""

EMPTY_ROW: Final[Row] = (None, None, None, None, None, None, None)
EMPTY: Final[Board] = (
    EMPTY_ROW,
    EMPTY_ROW,
    EMPTY_ROW,
    EMPTY_ROW,
    EMPTY_ROW,
    EMPTY_ROW,
)

WIN_SCORE: Final = 10_000
CENTER_BONUS: Final = 3
WINDOW_SCORES: Final[dict[int, int]] = {2: 5, 3: 50, 4: WIN_SCORE}


def _all_lines() -> tuple[Line, ...]:
    directions = ((0, 1), (1, 0), (1, 1), (-1, 1))
    lines: list[Line] = []
    for r in range(ROWS):
        for c in range(COLS):
            for dr, dc in directions:
                end_r = r + (CONNECT - 1) * dr
                end_c = c + (CONNECT - 1) * dc
                if 0 <= end_r < ROWS and 0 <= end_c < COLS:
                    lines.append(
                        (
                            (r, c),
                            (r + dr, c + dc),
                            (r + 2 * dr, c + 2 * dc),
                            (r + 3 * dr, c + 3 * dc),
                        )
                    )
    return tuple(lines)


LINES: Final[tuple[Line, ...]] = _all_lines()
"""Every four-in-a-row line on the 6x7 board."""


class ConnectFourState(BaseModel):
    """The 6x7 board, read top to bottom and left to right."""

    model_config = ConfigDict(frozen=True)

    board: Board = EMPTY
    """Six rows (0 is top, 5 is bottom) of seven columns (0 to 6)."""

    @property
    def next_disc(self) -> Disc:
        """Whose turn it is; R (Red) always starts."""
        reds = sum(cell == "R" for row in self.board for cell in row)
        yellows = sum(cell == "Y" for row in self.board for cell in row)
        return "R" if reds == yellows else "Y"


class Drop(BaseModel):
    """Drop your disc into a column that is not full."""

    model_config = ConfigDict(frozen=True, use_attribute_docstrings=True)

    # Spelled out rather than `ColumnIndex`, so the model gets the bounds inline,
    # not behind a `$ref`.
    column: Annotated[int, Interval(ge=0, le=6)]
    """The column to drop a disc into, from 0 (leftmost) to 6 (rightmost)."""


def landing_row(board: Board, column: ColumnIndex) -> int | None:
    """Return the lowest empty row in `column`, or `None` if the column is full.

    Examples:
        >>> landing_row(EMPTY, 3)
        5
    """
    for row in range(ROWS - 1, -1, -1):
        if board[row][column] is None:
            return row
    return None


def _place_on_row(board: Board, row: int, column: int, disc: Disc) -> Board:
    new_row = (*board[row][:column], disc, *board[row][column + 1 :])
    return (*board[:row], new_row, *board[row + 1 :])  # type: ignore[ty:invalid-return-type]


def _drop(board: Board, column: int, disc: Disc) -> Board:
    row = landing_row(board, column)
    assert row is not None  # noqa: S101
    return _place_on_row(board, row, column, disc)


def winner(board: Board) -> Disc | None:
    """The disc with four in a row, if any.

    Examples:
        >>> rows = [list(r) for r in EMPTY]
        >>> for c in range(4):
        ...     rows[5][c] = "R"
        >>> winner(tuple(tuple(r) for r in rows))
        'R'
    """
    for line in LINES:
        first = board[line[0][0]][line[0][1]]
        if first is not None and all(board[r][c] == first for r, c in line[1:]):
            return first
    return None


def winning_cells(board: Board) -> set[Coord]:
    """All coordinates belonging to a four-in-a-row line."""
    disc = winner(board)
    if disc is None:
        return set()
    return {
        coord
        for line in LINES
        if all(board[r][c] == disc for r, c in line)
        for coord in line
    }


def is_full(board: Board) -> bool:
    """Whether every column on the board is full."""
    return all(cell is not None for cell in board[0])


def render_board(board: Board) -> str:
    """Draw the board in text, with `.` for empty cells and column numbers on top.

    Examples:
        >>> print(render_board(EMPTY).splitlines()[0])
          0 1 2 3 4 5 6
    """
    header = "  " + " ".join(str(c) for c in range(COLS))
    rows = [
        f"{r} " + " ".join(cell or "." for cell in row) for r, row in enumerate(board)
    ]
    return "\n".join([header, *rows])


PICTURE_WIDTH: Final = 560
"""Width of the board picture, in pixels."""
PICTURE_HEIGHT: Final = 500
"""Height of the board picture, in pixels."""
HEADER_HEIGHT: Final = 52
CELL_SIZE: Final = 72
BOARD_X: Final = 28
BOARD_Y: Final = 54
DISC_RADIUS: Final = 28
BOARD_COLOR: Final = (29, 78, 216)
HOLE_COLOR: Final = (241, 245, 249)
DISC_COLORS: Final[dict[Disc, tuple[int, int, int]]] = {
    "R": (225, 29, 72),
    "Y": (234, 179, 8),
}


def draw_board(board: Board) -> bytes:
    """Draw the board as a PNG, the way it looks in the playground.

    Examples:
        >>> draw_board(EMPTY)[1:4]
        b'PNG'
    """
    canvas = Canvas(PICTURE_WIDTH, PICTURE_HEIGHT)
    canvas.draw.rounded_rectangle(
        (20, HEADER_HEIGHT, PICTURE_WIDTH - 20, PICTURE_HEIGHT - 12),
        radius=18,
        fill=BOARD_COLOR,
    )
    for col in range(COLS):
        cx = BOARD_X + (col + 0.5) * CELL_SIZE
        canvas.text((cx, HEADER_HEIGHT / 2), str(col), size=28, fill="black")
    for r, row in enumerate(board):
        cy = BOARD_Y + (r + 0.5) * CELL_SIZE
        for c, cell in enumerate(row):
            cx = BOARD_X + (c + 0.5) * CELL_SIZE
            fill = HOLE_COLOR if cell is None else DISC_COLORS[cell]
            canvas.draw.ellipse(
                (
                    cx - DISC_RADIUS,
                    cy - DISC_RADIUS,
                    cx + DISC_RADIUS,
                    cy + DISC_RADIUS,
                ),
                fill=fill,
            )
    return canvas.png()


class ConnectFour(Game[ConnectFourState, Drop]):
    """Two players drop discs into a 6x7 grid; four in a row wins."""

    info = GameInfo(
        id="connect-four",
        name="Connect Four",
        description="Drop discs into a 6x7 grid; first to connect four wins.",
        min_players=2,
        max_players=2,
        action_modes=(ActionMode.CHOICE, ActionMode.STRUCTURED, ActionMode.TOOLS),
        tags=("classic", "turn-based", "strategy"),
    )
    rules = (
        "Two players, Red (R) and Yellow (Y), take turns dropping one disc into "
        "any non-full column of a 6-row by 7-column vertical grid (columns 0 to 6). "
        "R goes first. A disc falls to the lowest empty row in the chosen column. "
        "The first player to connect four of their discs horizontally, vertically "
        "or diagonally wins. If all 42 cells fill up with no line of four, the "
        "game is a draw."
    )
    state_type = ConnectFourState
    action_type = Drop

    @override
    def initial_state(self, rng: random.Random) -> ConnectFourState:
        """Start with an empty 6x7 board."""
        del rng
        return ConnectFourState()

    @override
    def to_move(self, state: ConnectFourState) -> Sequence[Seat]:
        """R (seat 0) and Y (seat 1) alternate until the match ends."""
        return (
            () if self.outcome(state) is not None else (DISCS.index(state.next_disc),)
        )

    @override
    def legal_actions(self, state: ConnectFourState, player: Seat) -> Sequence[Drop]:
        """Any column whose top cell is empty, on your turn."""
        if self.outcome(state) is not None or DISCS[player] != state.next_disc:
            return ()
        return tuple(
            Drop(column=col) for col in range(COLS) if state.board[0][col] is None
        )

    @override
    def apply(
        self,
        state: ConnectFourState,
        actions: Mapping[int, Drop],
        rng: random.Random,
    ) -> ConnectFourState:
        """Drop the active player's disc into the chosen column."""
        del rng
        board = state.board
        for player, action in actions.items():
            board = _drop(board, action.column, DISCS[player])
        return ConnectFourState(board=board)

    @override
    def outcome(self, state: ConnectFourState) -> Outcome | None:
        """Four in a row wins; a full board is a draw."""
        if (disc := winner(state.board)) is not None:
            return Outcome.win(2, DISCS.index(disc), f"{disc} connected four")
        if is_full(state.board):
            return Outcome.draw(2, "The board is full")
        return None

    @override
    def observe(self, state: ConnectFourState, player: Seat) -> str:
        """The board, who is who, and the open columns with landing rows."""
        disc = DISCS[player]
        other = DISCS[1 - player]
        available = ", ".join(
            f"{col} (row {row})"
            for col in range(COLS)
            if (row := landing_row(state.board, col)) is not None
        )
        return (
            f"You are {disc} ({DISC_NAMES[disc]}); your opponent is "
            f"{other} ({DISC_NAMES[other]}). It is {state.next_disc}'s turn.\n"
            "Board (rows 0 top to 5 bottom; '.' is empty):\n"
            f"{render_board(state.board)}\n"
            f"Open columns: {available or 'none'}."
        )

    @override
    def snapshot(self, state: ConnectFourState, player: Seat) -> bytes:
        """The board as a PNG picture with column numbers on top."""
        del player
        return draw_board(state.board)

    @override
    def brief(self, state: ConnectFourState, player: Seat) -> str:
        """Who is who and whose turn it is; the picture shows the board."""
        disc = DISCS[player]
        other = DISCS[1 - player]
        return (
            f"You are {disc} ({DISC_NAMES[disc]}); your opponent is "
            f"{other} ({DISC_NAMES[other]}). It is {state.next_disc}'s turn. "
            "The picture shows the 6x7 board with column numbers 0 to 6 on top."
        )

    @override
    def describe_action(self, action: Drop) -> str:
        """Name the column.

        Examples:
            >>> ConnectFour().describe_action(Drop(column=3))
            'Drop your disc in column 3 (center).'
        """
        side = " (center)" if action.column == COLS // 2 else ""
        return f"Drop your disc in column {action.column}{side}."

    @override
    def describe_option(
        self, state: ConnectFourState, player: Seat, action: Drop
    ) -> str:
        """Name the column and the row where the disc will land.

        Examples:
            >>> ConnectFour().describe_option(ConnectFourState(), 0, Drop(column=3))
            'Drop your disc in column 3 (center, lands on row 5).'
        """
        del player
        row = landing_row(state.board, action.column)
        side = "center, " if action.column == COLS // 2 else ""
        return f"Drop your disc in column {action.column} ({side}lands on row {row})."

    @override
    def action_key(self, action: Drop) -> str:
        """The column number.

        Examples:
            >>> ConnectFour().action_key(Drop(column=3))
            '3'
        """
        return str(action.column)

    @override
    def player_label(self, player: Seat) -> str:
        """`R` or `Y`."""
        return DISCS[player]

    @override
    def bots(self) -> Mapping[str, BotFactory]:
        """Greedy tactical bot and depth-limited alpha-beta Minimax bot."""
        return {"greedy": GreedyBot, "minimax": MinimaxBot}

    @override
    def renderer(self) -> Renderer:
        """An interactive 6x7 grid with column drop buttons."""
        from ludotastic.games.connect_four.ui import render  # noqa: PLC0415

        return render


class GreedyBot(Player):
    """Wins immediately if possible, blocks immediate threats, else favors center."""

    kind = PlayerKind.BOT

    def __init__(self, name: str = "greedy") -> None:
        """Name the player."""
        super().__init__(name)

    @override
    async def act(self, ctx: TurnContext) -> Decision:
        """Pick an immediate win, block an immediate threat, or prefer center."""
        state = ctx.state
        assert isinstance(state, ConnectFourState)  # noqa: S101
        disc = DISCS[ctx.player]
        opponent = DISCS[1 - ctx.player]
        actions = [a for a in ctx.legal_actions if isinstance(a, Drop)]

        winning = [
            a for a in actions if winner(_drop(state.board, a.column, disc)) == disc
        ]
        if winning:
            return Decision(action=ctx.rng.choice(winning), details={"reason": "win"})

        blocking = [
            a
            for a in actions
            if winner(_drop(state.board, a.column, opponent)) == opponent
        ]
        if blocking:
            return Decision(
                action=ctx.rng.choice(blocking), details={"reason": "block"}
            )

        best_dist = min(abs(a.column - COLS // 2) for a in actions)
        centered = [a for a in actions if abs(a.column - COLS // 2) == best_dist]
        return Decision(action=ctx.rng.choice(centered), details={"reason": "center"})


def evaluate(board: Board, disc: Disc) -> int:
    """Heuristic board evaluation from `disc`'s perspective."""
    opponent: Disc = "Y" if disc == "R" else "R"
    center_col = COLS // 2
    score = 0
    for r in range(ROWS):
        cell = board[r][center_col]
        if cell == disc:
            score += CENTER_BONUS
        elif cell == opponent:
            score -= CENTER_BONUS
    for line in LINES:
        own = 0
        opp = 0
        for r, c in line:
            cell = board[r][c]
            if cell == disc:
                own += 1
            elif cell == opponent:
                opp += 1
        if opp == 0:
            score += WINDOW_SCORES.get(own, 0)
        elif own == 0:
            score -= WINDOW_SCORES.get(opp, 0)
    return score


def _negamax(
    board: Board,
    depth: int,
    alpha: int,
    beta: int,
    disc: Disc,
) -> int:
    if winner(board) is not None:
        return -(WIN_SCORE + depth)
    if is_full(board):
        return 0
    if depth == 0:
        return evaluate(board, disc)

    other: Disc = "Y" if disc == "R" else "R"
    best = -(WIN_SCORE * 2)
    for col in COLUMN_ORDER:
        if (row := landing_row(board, col)) is None:
            continue
        child = _place_on_row(board, row, col, disc)
        value = -_negamax(child, depth - 1, -beta, -alpha, other)
        best = max(best, value)
        alpha = max(alpha, value)
        if alpha >= beta:
            break
    return best


class MinimaxBot(Player):
    """Alpha-beta negamax search with positional and window heuristics."""

    kind = PlayerKind.BOT

    def __init__(self, name: str = "minimax", *, depth: int = 4) -> None:
        """Configure the bot name and search depth in plies."""
        super().__init__(name)
        self.depth = depth

    @override
    async def act(self, ctx: TurnContext) -> Decision:
        """Pick a column with the highest alpha-beta minimax evaluation."""
        state = ctx.state
        assert isinstance(state, ConnectFourState)  # noqa: S101
        disc = DISCS[ctx.player]
        other: Disc = "Y" if disc == "R" else "R"
        legal = {a.column: a for a in ctx.legal_actions if isinstance(a, Drop)}
        alpha = -(WIN_SCORE * 2)
        beta = WIN_SCORE * 2
        best_score = -(WIN_SCORE * 2)
        best_actions: list[Drop] = []

        for col in COLUMN_ORDER:
            action = legal.get(col)
            if action is None:
                continue
            child = _drop(state.board, col, disc)
            score = -_negamax(child, self.depth - 1, -beta, -alpha, other)
            if score > best_score:
                best_score = score
                best_actions = [action]
            elif score == best_score:
                best_actions.append(action)
            alpha = max(alpha, best_score)

        return Decision(
            action=ctx.rng.choice(best_actions),
            details={"score": best_score, "depth": self.depth},
        )

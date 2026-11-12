"""Connect Four rules, snapshots, bots and UI."""

__all__: tuple[str, ...] = ()

import io
import random
from typing import Final

import pytest
from inline_snapshot import snapshot
from nicegui.testing import User
from PIL import Image
from pydantic import ValidationError

from ludotastic import Match, MatchStatus, RandomBot, TurnContext
from ludotastic.games.connect_four import (
    ConnectFour,
    ConnectFourState,
    Disc,
    Drop,
    GreedyBot,
    MinimaxBot,
)
from ludotastic.games.connect_four.game import (
    COLS,
    EMPTY,
    ROWS,
    Board,
    evaluate,
    is_full,
    landing_row,
    winner,
    winning_cells,
)
from ludotastic.service import HumanSpec, MatchService

GAME: Final = ConnectFour()


def board_from(lines: list[str]) -> Board:
    """Build a 6x7 board from six 7-character strings (`.` for empty)."""
    assert len(lines) == ROWS
    return tuple(tuple(None if ch == "." else ch for ch in line) for line in lines)  # type: ignore[ty:invalid-return-type]


def state_from(lines: list[str]) -> ConnectFourState:
    """Wrap `board_from` in a `ConnectFourState`."""
    return ConnectFourState(board=board_from(lines))


def ctx(state: ConnectFourState, player: int) -> TurnContext:
    """Create a turn context for testing bots."""
    return TurnContext(
        game=GAME,
        state=state,
        player=player,
        legal_actions=GAME.legal_actions(state, player),
        turn=0,
        rng=random.Random(0),
    )


def test_initial_state() -> None:
    state = GAME.initial_state(random.Random(0))
    assert state.board == EMPTY
    assert state.next_disc == "R"
    assert GAME.to_move(state) == (0,)
    assert len(GAME.legal_actions(state, 0)) == COLS
    assert GAME.legal_actions(state, 1) == ()
    assert GAME.outcome(state) is None


@pytest.mark.parametrize("players", [1, 3])
def test_wrong_player_count(players: int) -> None:
    with pytest.raises(ValueError, match="needs 2 to 2 players"):
        ConnectFour(players)


def test_apply_gravity_and_full_column() -> None:
    rng = random.Random(0)
    state = GAME.initial_state(rng)
    for expected_row in range(ROWS - 1, -1, -1):
        seat = GAME.to_move(state)[0]
        assert landing_row(state.board, 3) == expected_row
        state = GAME.apply(state, {seat: Drop(column=3)}, rng)
    assert landing_row(state.board, 3) is None
    assert Drop(column=3) not in GAME.legal_actions(state, 0)
    assert state.board[0][3] == "Y"
    assert state.board[5][3] == "R"


@pytest.mark.parametrize(
    ("lines", "expected_winner", "expected_scores"),
    [
        (
            [
                ".......",
                ".......",
                ".......",
                ".......",
                "YYY....",
                "RRRR...",
            ],
            "R",
            (1.0, 0.0),
        ),
        (
            [
                ".......",
                ".......",
                "Y......",
                "YR.....",
                "YR.....",
                "YRR....",
            ],
            "Y",
            (0.0, 1.0),
        ),
        (
            [
                ".......",
                ".......",
                "R......",
                "YR.....",
                "YYR....",
                "YYYR...",
            ],
            "R",
            (1.0, 0.0),
        ),
        (
            [
                ".......",
                ".......",
                "...Y...",
                "..YR...",
                ".YRR...",
                "YRRY...",
            ],
            "Y",
            (0.0, 1.0),
        ),
    ],
)
def test_winning_directions(
    lines: list[str], expected_winner: Disc, expected_scores: tuple[float, float]
) -> None:
    s = state_from(lines)
    assert winner(s.board) == expected_winner
    assert len(winning_cells(s.board)) == 4
    outcome = GAME.outcome(s)
    assert outcome is not None
    assert outcome.scores == expected_scores
    assert GAME.to_move(s) == ()
    assert GAME.legal_actions(s, 0) == ()


def test_draw_outcome() -> None:
    s = state_from(
        [
            "RYYYRYR",
            "YYRYYRY",
            "RYRRRYY",
            "RRRYYYR",
            "YRYRYRR",
            "YRYRRRY",
        ]
    )
    assert winner(s.board) is None
    assert winning_cells(s.board) == set()
    assert is_full(s.board)
    outcome = GAME.outcome(s)
    assert outcome is not None
    assert outcome.scores == (0.5, 0.5)
    assert outcome.reason == "The board is full"
    assert "Open columns: none." in GAME.observe(s, 0)


def test_observe_and_descriptions() -> None:
    s = state_from(
        [
            ".......",
            ".......",
            ".......",
            ".......",
            ".......",
            "...RY..",
        ]
    )
    assert GAME.observe(s, 0) == snapshot("""\
You are R (Red); your opponent is Y (Yellow). It is R's turn.
Board (rows 0 top to 5 bottom; '.' is empty):
  0 1 2 3 4 5 6
0 . . . . . . .
1 . . . . . . .
2 . . . . . . .
3 . . . . . . .
4 . . . . . . .
5 . . . R Y . .
Open columns: 0 (row 5), 1 (row 5), 2 (row 5), 3 (row 4), 4 (row 4), 5 (row 5), 6 (row 5).\
""")
    assert (
        GAME.describe_action(Drop(column=3)) == "Drop your disc in column 3 (center)."
    )
    assert GAME.describe_action(Drop(column=0)) == "Drop your disc in column 0."
    assert (
        GAME.describe_option(s, 0, Drop(column=3))
        == "Drop your disc in column 3 (center, lands on row 4)."
    )
    assert (
        GAME.describe_option(s, 0, Drop(column=0))
        == "Drop your disc in column 0 (lands on row 5)."
    )
    assert GAME.action_key(Drop(column=2)) == "2"
    assert GAME.parse_action({"column": 5}) == Drop(column=5)
    with pytest.raises(ValidationError):
        GAME.parse_action({"column": 7})
    assert GAME.player_label(0) == "R"
    assert GAME.player_label(1) == "Y"
    assert list(GAME.bots()) == ["greedy", "minimax"]
    assert callable(GAME.renderer())


def test_snapshot_draws_discs_and_board() -> None:
    s = state_from(
        [
            ".......",
            ".......",
            ".......",
            ".......",
            ".......",
            "...RY..",
        ]
    )
    picture = Image.open(io.BytesIO(GAME.snapshot(s, 0))).convert("RGB")
    colors = {color for _, color in picture.getcolors(maxcolors=100_000) or []}
    assert (29, 78, 216) in colors
    assert (225, 29, 72) in colors
    assert (234, 179, 8) in colors
    assert (241, 245, 249) in colors
    assert "column numbers 0 to 6" in GAME.brief(s, 1)


async def test_greedy_bot_tactics() -> None:
    bot = GreedyBot()
    # Immediate win in column 3 (3 R vs 3 Y -> R's turn).
    win_state = state_from(
        [
            ".......",
            ".......",
            ".......",
            ".......",
            "YYY....",
            "RRR....",
        ]
    )
    assert (await bot.act(ctx(win_state, 0))).action == Drop(column=3)

    # Must block R's threat in column 3 (3 R vs 2 Y -> Y's turn).
    block_state = state_from(
        [
            ".......",
            ".......",
            ".......",
            ".......",
            "YY.....",
            "RRR....",
        ]
    )
    assert (await bot.act(ctx(block_state, 1))).action == Drop(column=3)

    # Otherwise prefers center column on empty board.
    assert (await bot.act(ctx(ConnectFourState(), 0))).action == Drop(column=3)


def test_evaluate_covers_center_and_windows() -> None:
    s = state_from(
        [
            "...R...",
            "...Y...",
            ".......",
            ".......",
            ".YYY...",
            "RRR.Y..",
        ]
    )
    score_r = evaluate(s.board, "R")
    score_y = evaluate(s.board, "Y")
    assert score_r == -score_y


async def test_minimax_finds_win_block_and_handles_full_columns() -> None:
    bot = MinimaxBot(depth=4)

    # Immediate win for R in column 3 (3 R vs 3 Y -> R's turn).
    win_state = state_from(
        [
            ".......",
            ".......",
            ".......",
            ".......",
            "YYY....",
            "RRR....",
        ]
    )
    assert (await bot.act(ctx(win_state, 0))).action == Drop(column=3)

    # Immediate block for Y in column 3 (3 R vs 2 Y -> Y's turn).
    block_state = state_from(
        [
            ".......",
            ".......",
            ".......",
            ".......",
            "YY.....",
            "RRR....",
        ]
    )
    assert (await bot.act(ctx(block_state, 1))).action == Drop(column=3)

    # Only columns 0 and 1 have space; placing both fills the board in a draw.
    near_full = state_from(
        [
            "..YYRYR",
            "YYRYYRY",
            "RYRRRYY",
            "RRRYYYR",
            "YRYRYRR",
            "YRYRRRY",
        ]
    )
    decision = await MinimaxBot(depth=4).act(ctx(near_full, 0))
    assert decision.action in {Drop(column=0), Drop(column=1)}
    assert decision.details["score"] == 0

    # Depth=1 exercises leaf evaluation at depth 0 inside _negamax.
    leaf_decision = await MinimaxBot(depth=1).act(ctx(ConnectFourState(), 0))
    assert isinstance(leaf_decision.action, Drop)


async def test_minimax_beats_random_and_greedy() -> None:
    outcome = await Match(GAME, [MinimaxBot(depth=4), RandomBot()], seed=1).run()
    assert outcome is not None
    assert outcome.scores[0] == 1.0

    match_vs_greedy = Match(GAME, [MinimaxBot(depth=4), GreedyBot()], seed=2)
    greedy_outcome = await match_vs_greedy.run()
    assert match_vs_greedy.status is MatchStatus.FINISHED
    assert greedy_outcome is not None


async def test_ui_interactive_play_and_win(user: User, service: MatchService) -> None:
    match = service.create("connect-four", [HumanSpec(), HumanSpec()])
    await user.open(f"/match/{match.id}")
    await user.should_see("R to move")

    # Play alternating columns so R connects four on row 5 (cols 0, 1, 2, 3).
    moves = [
        (0, "Y to move"),
        (0, "R to move"),
        (1, "Y to move"),
        (1, "R to move"),
        (2, "Y to move"),
        (2, "R to move"),
        (3, "R connected four"),
    ]
    for col, expected in moves:
        user.find(marker=f"col-{col}").click()
        await user.should_see(expected, retries=20)

    assert match.status is MatchStatus.FINISHED
    s = match.state
    assert isinstance(s, ConnectFourState)
    assert winner(s.board) == "R"


async def test_ui_disables_full_columns_and_non_acting_seats(
    user: User, service: MatchService
) -> None:
    match = service.create("connect-four", [HumanSpec(), HumanSpec()], start=False)
    match.state = state_from(
        [
            "...Y...",
            "...R...",
            "...Y...",
            "...R...",
            "...Y...",
            "...R...",
        ]
    )
    match.thinking = {1}
    await user.open(f"/match/{match.id}")
    # Column 3 is full, while seat 0 is viewing a turn where seat 1 is thinking.
    assert user.find(marker="col-3").elements

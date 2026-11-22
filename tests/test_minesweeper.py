"""Minesweeper rules and UI."""

__all__: tuple[str, ...] = ()

import asyncio
import random
from typing import Final

import pytest
from nicegui.testing import User
from pydantic import ValidationError

from ludotastic import Match, MatchStatus, RandomBot
from ludotastic.games.minesweeper import (
    CELLS,
    COLS,
    MINES,
    Action,
    Minesweeper,
    MinesweeperState,
    Move,
)
from ludotastic.games.minesweeper.game import Mine, adjacent_mines, neighbours
from ludotastic.games.minesweeper.ui import (
    COVERED,
    FLAGGED,
    HIT_MINE,
    REVEALED,
    cell_classes,
    cell_text,
    flag,
    is_disabled,
    reveal,
    status_text,
)
from ludotastic.service import HumanSpec, MatchService

GAME: Final = Minesweeper()


def mines_at(*cells: int) -> frozenset[Mine]:
    """Turn cell numbers into mine positions."""
    return frozenset((cell % COLS, cell // COLS) for cell in cells)


def make_state(
    *,
    mines: frozenset[Mine] | None = None,
    revealed: set[int] | None = None,
    flagged: set[int] | None = None,
    game_over: bool = False,
) -> MinesweeperState:
    """Build a full state with the given mines, revealed and flagged cells."""
    shown = revealed or set()
    marks = flagged or set()
    return MinesweeperState(
        mines=mines if mines is not None else frozenset(),
        revealed=tuple(cell in shown for cell in range(CELLS)),
        flagged=tuple(cell in marks for cell in range(CELLS)),
        game_over=game_over,
    )


def move(action: Action, cell: int) -> Move:
    """Build a move."""
    return Move(action=action, cell=cell)


def test_initial_state() -> None:
    start = GAME.initial_state(random.Random(0))
    assert len(start.mines) == MINES
    assert len(start.revealed) == CELLS
    assert not any(start.revealed)
    assert not any(start.flagged)
    assert start.game_over is False
    assert GAME.to_move(start) == (0,)
    assert GAME.outcome(start) is None


def test_initial_state_is_seeded() -> None:
    assert GAME.initial_state(random.Random(0)).mines == (
        GAME.initial_state(random.Random(0)).mines
    )


def test_wrong_player_count() -> None:
    with pytest.raises(ValueError, match="needs 1 to 1 players"):
        Minesweeper(2)


def test_legal_actions_at_start() -> None:
    start = GAME.initial_state(random.Random(0))
    moves = GAME.legal_actions(start, 0)
    assert len(moves) == CELLS * 2
    assert move(Action.REVEAL, 0) in moves
    assert move(Action.FLAG, 0) in moves


def test_legal_actions_skip_revealed_and_flagged() -> None:
    moves = set(GAME.legal_actions(make_state(revealed={0}, flagged={1}), 0))
    assert move(Action.REVEAL, 0) not in moves
    assert move(Action.FLAG, 0) not in moves
    assert move(Action.REVEAL, 1) not in moves
    assert move(Action.FLAG, 1) in moves
    assert move(Action.REVEAL, 2) in moves


def test_no_actions_once_over() -> None:
    over = make_state(game_over=True)
    assert GAME.legal_actions(over, 0) == ()
    assert GAME.to_move(over) == ()


def test_reveal_mine_loses() -> None:
    after = GAME.apply(
        make_state(mines=mines_at(0)), {0: move(Action.REVEAL, 0)}, random.Random(0)
    )
    assert after.game_over is True
    assert after.revealed[0] is True
    outcome = GAME.outcome(after)
    assert outcome is not None
    assert outcome.scores == (0.0,)
    assert outcome.reason == "You hit a mine — game over."


def test_reveal_numbered_cell_reveals_only_it() -> None:
    after = GAME.apply(
        make_state(mines=mines_at(1)), {0: move(Action.REVEAL, 0)}, random.Random(0)
    )
    assert after.revealed[0] is True
    assert sum(after.revealed) == 1
    assert GAME.outcome(after) is None


def test_reveal_empty_floods_to_a_win() -> None:
    after = GAME.apply(
        make_state(mines=mines_at(CELLS - 1)),
        {0: move(Action.REVEAL, 0)},
        random.Random(0),
    )
    assert after.revealed[0] is True
    assert after.revealed[CELLS - 1] is False
    assert sum(after.revealed) == CELLS - 1
    assert after.won is True
    outcome = GAME.outcome(after)
    assert outcome is not None
    assert outcome.scores == (1.0,)
    assert outcome.reason == "All safe cells revealed — you win!"


def test_reveal_is_noop_when_flagged_revealed_or_over() -> None:
    flagged = make_state(flagged={0})
    assert GAME.apply(flagged, {0: move(Action.REVEAL, 0)}, random.Random(0)) == flagged
    revealed = make_state(revealed={0})
    assert (
        GAME.apply(revealed, {0: move(Action.REVEAL, 0)}, random.Random(0)) == revealed
    )
    over = make_state(game_over=True)
    assert GAME.apply(over, {0: move(Action.REVEAL, 1)}, random.Random(0)) == over


def test_flag_and_unflag() -> None:
    flagged = GAME.apply(make_state(), {0: move(Action.FLAG, 5)}, random.Random(0))
    assert flagged.flagged[5] is True
    unflagged = GAME.apply(flagged, {0: move(Action.FLAG, 5)}, random.Random(0))
    assert unflagged.flagged[5] is False


def test_flag_is_noop_when_revealed_or_over() -> None:
    revealed = make_state(revealed={0})
    assert GAME.apply(revealed, {0: move(Action.FLAG, 0)}, random.Random(0)) == revealed
    over = make_state(game_over=True)
    assert GAME.apply(over, {0: move(Action.FLAG, 1)}, random.Random(0)) == over


def test_out_of_turns_is_a_loss() -> None:
    outcome = GAME.out_of_turns(make_state())
    assert outcome.scores == (0.0,)
    assert "Turn limit" in outcome.reason


def test_won_needs_a_full_board() -> None:
    assert MinesweeperState().won is False


def test_observe_hides_mines_and_shows_counts() -> None:
    text = GAME.observe(make_state(mines=mines_at(0), revealed={1}, flagged={2}), 0)
    lines = text.splitlines()
    assert lines[0].startswith("Minesweeper")
    assert lines[-1] == "Flags: 1/30."
    row_zero = lines[2]
    assert "1" in row_zero
    assert "F" in row_zero
    assert row_zero.split()[1] == "?"


def test_observe_after_loss() -> None:
    text = GAME.observe(make_state(mines=mines_at(0), revealed={0}, game_over=True), 0)
    assert text.endswith("Game over — you hit a mine.")


def test_descriptions_keys_and_parse() -> None:
    state = make_state()
    assert GAME.describe_action(move(Action.REVEAL, 0)) == "Reveal cell 0."
    assert GAME.describe_action(move(Action.FLAG, 40)) == "Toggle the flag on cell 40."
    assert (
        GAME.describe_option(state, 0, move(Action.REVEAL, 0))
        == "Reveal cell 0 (row 0, column 0)."
    )
    assert (
        GAME.describe_option(state, 0, move(Action.FLAG, 40))
        == "Toggle the flag on cell 40 (row 4, column 4)."
    )
    assert GAME.action_key(move(Action.REVEAL, 3)) == "reveal-3"
    assert GAME.action_key(move(Action.FLAG, 3)) == "flag-3"
    assert GAME.parse_action({"action": "reveal", "cell": 3}) == move(Action.REVEAL, 3)
    assert GAME.parse_action({"action": "flag", "cell": 3}) == move(Action.FLAG, 3)
    with pytest.raises(ValidationError):
        GAME.parse_action({"action": "reveal", "cell": CELLS})
    assert GAME.player_label(0) == "Player"
    assert callable(GAME.renderer())


def test_helpers() -> None:
    assert neighbours(0) == (1, 9, 10)
    assert neighbours(CELLS - 1) == (106, 107, 115)
    assert adjacent_mines(mines_at(0), 1) == 1
    assert adjacent_mines(mines_at(0), CELLS - 1) == 0


def test_choice_options_fit_the_decision_model() -> None:
    """The decision model accepts at most 255 options."""
    start = GAME.initial_state(random.Random(0))
    assert len(GAME.legal_actions(start, 0)) <= 255


async def test_random_bot_finishes() -> None:
    match = Match(GAME, [RandomBot()], seed=0, max_turns=400)
    outcome = await match.run()
    assert match.status is MatchStatus.FINISHED
    assert outcome is not None


def test_status_text() -> None:
    assert status_text(make_state()) == "Flags: 0/30"
    assert status_text(make_state(flagged={0, 1})) == "Flags: 2/30"
    assert status_text(make_state(game_over=True)) == "Game over — you hit a mine!"
    won = make_state(mines=mines_at(CELLS - 1), revealed=set(range(CELLS - 1)))
    assert won.won is True
    assert status_text(won) == "You win! Every safe cell is cleared."


def test_cell_text() -> None:
    state = make_state(mines=mines_at(0), revealed={1}, flagged={2})
    assert cell_text(state, 0) == ""
    assert cell_text(state, 1) == "1"
    assert cell_text(state, 2) == "F"
    assert cell_text(state, 3) == ""
    assert cell_text(make_state(mines=mines_at(0), game_over=True), 0) == "*"
    assert cell_text(make_state(revealed={0}), 0) == ""


def test_cell_classes() -> None:
    state = make_state(mines=mines_at(0), revealed={1}, flagged={2})
    assert cell_classes(state, 0) == COVERED
    assert cell_classes(state, 1) == REVEALED
    assert cell_classes(state, 2) == FLAGGED
    assert cell_classes(make_state(mines=mines_at(0), game_over=True), 0) == HIT_MINE


def test_is_disabled() -> None:
    state = make_state(revealed={0})
    assert is_disabled(state, 0, clickable=True) is True
    assert is_disabled(state, 1, clickable=True) is False
    assert is_disabled(state, 1, clickable=False) is True


def test_reveal_and_flag_submit() -> None:
    sent: list[Move] = []
    state = make_state(revealed={0}, flagged={1})
    reveal(state, 0, sent.append)
    reveal(state, 1, sent.append)
    reveal(state, 2, sent.append)
    flag(state, 0, sent.append)
    flag(state, 2, sent.append)
    assert sent == [move(Action.REVEAL, 2), move(Action.FLAG, 2)]


async def test_ui_reveals_a_safe_cell(user: User, service: MatchService) -> None:
    match = service.create("minesweeper", [HumanSpec()], seed=0)
    await user.open(f"/match/{match.id}")
    await user.should_see("Flags: 0/30")
    state = match.state
    assert isinstance(state, MinesweeperState)
    safe = next(
        cell for cell in range(CELLS) if (cell % COLS, cell // COLS) not in state.mines
    )
    user.find(marker=f"cell-{safe}").click()
    for _ in range(100):
        current = match.state
        assert isinstance(current, MinesweeperState)
        if current.revealed[safe]:
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("the cell was never revealed")


async def test_ui_flags_and_unflags_a_cell(user: User, service: MatchService) -> None:
    match = service.create("minesweeper", [HumanSpec()], seed=0)
    await user.open(f"/match/{match.id}")
    await user.should_see("Flags: 0/30")
    user.find(marker="cell-0").trigger("contextmenu")
    await user.should_see("Flags: 1/30", retries=20)
    user.find(marker="cell-0").trigger("contextmenu")
    await user.should_see("Flags: 0/30", retries=20)


async def test_ui_hitting_a_mine_shows_the_banner(
    user: User, service: MatchService
) -> None:
    match = service.create("minesweeper", [HumanSpec()], seed=0)
    await user.open(f"/match/{match.id}")
    state = match.state
    assert isinstance(state, MinesweeperState)
    mine = next(
        cell for cell in range(CELLS) if (cell % COLS, cell // COLS) in state.mines
    )
    user.find(marker=f"cell-{mine}").click()
    await user.should_see("Game over — you hit a mine!", retries=20)
    await user.should_see(marker=f"cell-{mine}", content="*", retries=20)


async def test_ui_shows_the_win_banner(user: User, service: MatchService) -> None:
    match = service.create("minesweeper", [HumanSpec()], start=False)
    match.state = make_state(mines=mines_at(CELLS - 1), revealed=set(range(CELLS - 1)))
    await user.open(f"/match/{match.id}")
    await user.should_see("You win! Every safe cell is cleared.")

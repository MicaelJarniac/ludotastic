"""Minesweeper rules and UI."""

__all__: tuple[str, ...] = ()

import asyncio
import random
from typing import Final

import httpx2
import pytest
from fastapi import FastAPI
from nicegui.testing import User
from pydantic import ValidationError

from ludotastic import Match, MatchStatus, RandomBot
from ludotastic.api import mount_api
from ludotastic.games.minesweeper import (
    CELLS,
    MINES,
    Action,
    Minesweeper,
    MinesweeperState,
    Move,
)
from ludotastic.games.minesweeper.game import adjacent_mines, neighbours, place_mines
from ludotastic.games.minesweeper.ui import (
    COVERED,
    FLAGGED,
    FOUND_MINE,
    HIT_MINE,
    MINE,
    REVEALED,
    WRONG_FLAG,
    cell_classes,
    cell_text,
    flag,
    is_disabled,
    reveal,
    status_text,
)
from ludotastic.service import BotSpec, HumanSpec, MatchService, replay

GAME: Final = Minesweeper()


def make_state(
    *,
    mines: set[int] | None = None,
    revealed: set[int] | None = None,
    flagged: set[int] | None = None,
    exploded: int | None = None,
) -> MinesweeperState:
    """Build a full state with the given mines, revealed and flagged cells."""
    shown = revealed or set()
    marks = flagged or set()
    return MinesweeperState(
        mines=frozenset(mines or ()),
        revealed=tuple(cell in shown for cell in range(CELLS)),
        flagged=tuple(cell in marks for cell in range(CELLS)),
        exploded=exploded,
    )


def move(action: Action, cell: int) -> Move:
    """Build a move."""
    return Move(action=action, cell=cell)


def apply(state: MinesweeperState, action: Action, cell: int) -> MinesweeperState:
    """Play one move."""
    return GAME.apply(state, {0: move(action, cell)}, random.Random(0))


def test_initial_state() -> None:
    start = GAME.initial_state(random.Random(0))
    assert start.mines == frozenset()
    assert len(start.revealed) == CELLS
    assert not any(start.revealed)
    assert not any(start.flagged)
    assert start.game_over is False
    assert GAME.to_move(start) == (0,)
    assert GAME.outcome(start) is None


def test_initial_state_is_seeded() -> None:
    assert GAME.initial_state(random.Random(0)) == GAME.initial_state(random.Random(0))
    assert GAME.initial_state(random.Random(0)) != GAME.initial_state(random.Random(1))


def test_state_needs_a_full_grid() -> None:
    with pytest.raises(ValidationError):
        MinesweeperState(revealed=(False,))


@pytest.mark.parametrize("first", [0, 40, CELLS - 1])
def test_first_reveal_is_safe_and_opens_an_area(first: int) -> None:
    for seed in range(20):
        after = apply(MinesweeperState(seed=seed), Action.REVEAL, first)
        assert len(after.mines) == MINES
        assert after.game_over is False
        assert first not in after.mines
        assert not any(n in after.mines for n in neighbours(first))
        assert sum(after.revealed) > 1


def test_mines_follow_the_seed() -> None:
    assert place_mines(3, 40) == place_mines(3, 40)
    assert place_mines(3, 40) != place_mines(4, 40)


def test_flagging_first_does_not_place_mines() -> None:
    assert apply(MinesweeperState(), Action.FLAG, 0).mines == frozenset()


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
    state = make_state(mines={50}, revealed={0}, flagged={1})
    moves = set(GAME.legal_actions(state, 0))
    assert move(Action.REVEAL, 0) not in moves
    assert move(Action.FLAG, 0) not in moves
    assert move(Action.REVEAL, 1) not in moves
    assert move(Action.FLAG, 1) in moves
    assert move(Action.REVEAL, 2) in moves


def test_no_actions_once_over() -> None:
    over = make_state(mines={0}, revealed={0}, exploded=0)
    assert GAME.legal_actions(over, 0) == ()
    assert GAME.to_move(over) == ()


def test_reveal_mine_loses() -> None:
    after = apply(make_state(mines={0}), Action.REVEAL, 0)
    assert after.game_over is True
    assert after.exploded == 0
    assert after.revealed[0] is True
    assert after.won is False
    outcome = GAME.outcome(after)
    assert outcome is not None
    assert outcome.scores == (0.0,)
    assert outcome.reason == "You hit a mine — game over."


def test_reveal_numbered_cell_reveals_only_it() -> None:
    after = apply(make_state(mines={1}), Action.REVEAL, 0)
    assert after.revealed[0] is True
    assert sum(after.revealed) == 1
    assert GAME.outcome(after) is None


def test_reveal_empty_floods_to_a_win() -> None:
    after = apply(make_state(mines={CELLS - 1}), Action.REVEAL, 0)
    assert after.revealed[0] is True
    assert after.revealed[CELLS - 1] is False
    assert sum(after.revealed) == CELLS - 1
    assert after.won is True
    outcome = GAME.outcome(after)
    assert outcome is not None
    assert outcome.scores == (1.0,)
    assert outcome.reason == "All safe cells revealed — you win!"


def test_flood_stops_at_flags() -> None:
    after = apply(make_state(mines={CELLS - 1}, flagged={1}), Action.REVEAL, 0)
    assert after.revealed[1] is False
    assert after.won is False


def test_reveal_is_noop_when_flagged_revealed_or_over() -> None:
    flagged = make_state(mines={50}, flagged={0})
    assert apply(flagged, Action.REVEAL, 0) == flagged
    revealed = make_state(mines={50}, revealed={0})
    assert apply(revealed, Action.REVEAL, 0) == revealed
    over = make_state(mines={0}, revealed={0}, exploded=0)
    assert apply(over, Action.REVEAL, 1) == over


def test_flag_and_unflag() -> None:
    flagged = apply(make_state(), Action.FLAG, 5)
    assert flagged.flagged[5] is True
    unflagged = apply(flagged, Action.FLAG, 5)
    assert unflagged.flagged[5] is False


def test_flag_is_noop_when_revealed_or_over() -> None:
    revealed = make_state(mines={50}, revealed={0})
    assert apply(revealed, Action.FLAG, 0) == revealed
    over = make_state(mines={0}, revealed={0}, exploded=0)
    assert apply(over, Action.FLAG, 1) == over


def test_out_of_turns_is_a_loss() -> None:
    outcome = GAME.out_of_turns(make_state())
    assert outcome.scores == (0.0,)
    assert "Turn limit" in outcome.reason


def test_won_needs_mines_and_every_safe_cell() -> None:
    assert MinesweeperState().won is False
    assert make_state(mines={0}, revealed={1}).won is False


def test_public_state_hides_the_mines_until_the_end() -> None:
    playing = apply(MinesweeperState(seed=7), Action.REVEAL, 40)
    hidden = GAME.public_state(playing)
    assert hidden.mines == frozenset()
    assert hidden.seed == 0
    assert hidden.revealed == playing.revealed
    over = make_state(mines={0}, revealed={0}, exploded=0)
    assert GAME.public_state(over) == over


def test_observe_hides_mines_and_shows_counts() -> None:
    text = GAME.observe(make_state(mines={0}, revealed={1}, flagged={2}), 0)
    lines = text.splitlines()
    assert lines[0].startswith("Minesweeper")
    assert lines[-1] == f"Flags: 1/{MINES}."
    row_zero = lines[2]
    assert "1" in row_zero
    assert "F" in row_zero
    assert row_zero.split()[1] == "?"


def test_observe_after_loss() -> None:
    text = GAME.observe(make_state(mines={0}, revealed={0}, exploded=0), 0)
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
    assert adjacent_mines(frozenset({0}), 1) == 1
    assert adjacent_mines(frozenset({0}), CELLS - 1) == 0


def test_choice_options_fit_the_decision_model() -> None:
    """The decision model accepts at most 255 options."""
    start = GAME.initial_state(random.Random(0))
    assert len(GAME.legal_actions(start, 0)) <= 255


async def test_random_bot_finishes() -> None:
    match = Match(GAME, [RandomBot()], seed=0, max_turns=400)
    outcome = await match.run()
    assert match.status is MatchStatus.FINISHED
    assert outcome is not None


async def test_replay_places_the_same_mines(service: MatchService) -> None:
    match = service.create("minesweeper", [BotSpec()], seed=4)
    await match.start()
    record = service.store.get(match.id)
    assert record is not None
    assert replay(service.games, record).state == match.state


async def test_api_hides_the_mines(service: MatchService) -> None:
    app = FastAPI()
    mount_api(app, service)
    match = service.create("minesweeper", [HumanSpec()], seed=0)
    await wait_until(match, lambda _: 0 in match.thinking)
    match.submit(0, move(Action.REVEAL, 40))
    await wait_until(match, lambda s: s.revealed[40])
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app), base_url="http://test"
    ) as client:
        view = (await client.get(f"/api/matches/{match.id}")).json()
    assert view["state"]["revealed"][40] is True
    assert view["state"]["mines"] == []
    assert view["state"]["seed"] == 0
    match.abort()


def test_status_text() -> None:
    assert status_text(make_state()) == f"Flags: 0/{MINES}"
    assert status_text(make_state(flagged={0, 1})) == f"Flags: 2/{MINES}"
    over = make_state(mines={0}, revealed={0}, exploded=0)
    assert status_text(over) == "Game over — you hit a mine!"
    won = make_state(mines={CELLS - 1}, revealed=set(range(CELLS - 1)))
    assert won.won is True
    assert status_text(won) == "You win! Every safe cell is cleared."


def test_cell_text() -> None:
    state = make_state(mines={0}, revealed={1}, flagged={2})
    assert cell_text(state, 0) == ""
    assert cell_text(state, 1) == "1"
    assert cell_text(state, 2) == "F"
    assert cell_text(state, 3) == ""
    over = make_state(mines={0, 5}, revealed={0}, flagged={5}, exploded=0)
    assert cell_text(over, 0) == "*"
    assert cell_text(over, 5) == "F"
    assert cell_text(make_state(revealed={0}), 0) == ""


def test_cell_classes() -> None:
    state = make_state(mines={0}, revealed={1}, flagged={2})
    assert cell_classes(state, 0) == COVERED
    assert cell_classes(state, 1) == REVEALED
    assert cell_classes(state, 2) == FLAGGED
    over = make_state(mines={0, 5, 7}, revealed={0, 20}, flagged={5, 30}, exploded=0)
    assert cell_classes(over, 0) == HIT_MINE
    assert cell_classes(over, 5) == FOUND_MINE
    assert cell_classes(over, 7) == MINE
    assert cell_classes(over, 30) == WRONG_FLAG
    assert cell_classes(over, 20) == REVEALED
    assert cell_classes(over, 40) == COVERED


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


async def wait_until(match: Match, check: object) -> MinesweeperState:
    """Wait for the match state to pass `check`."""
    assert callable(check)
    for _ in range(100):
        state = match.state
        assert isinstance(state, MinesweeperState)
        if check(state):
            return state
        await asyncio.sleep(0.01)
    pytest.fail("the state never changed")


async def test_ui_first_click_opens_the_field(
    user: User, service: MatchService
) -> None:
    match = service.create("minesweeper", [HumanSpec()], seed=0)
    await user.open(f"/match/{match.id}")
    await user.should_see(f"Flags: 0/{MINES}")
    user.find(marker="cell-40").click()
    state = await wait_until(match, lambda s: s.revealed[40])
    assert not state.game_over


async def test_ui_flags_and_unflags_a_cell(user: User, service: MatchService) -> None:
    match = service.create("minesweeper", [HumanSpec()], seed=0)
    await user.open(f"/match/{match.id}")
    await user.should_see(f"Flags: 0/{MINES}")
    user.find(marker="cell-0").trigger("contextmenu.prevent")
    await user.should_see(f"Flags: 1/{MINES}", retries=20)
    user.find(marker="cell-0").trigger("contextmenu.prevent")
    await user.should_see(f"Flags: 0/{MINES}", retries=20)


async def test_ui_flag_mode_makes_a_tap_flag(user: User, service: MatchService) -> None:
    match = service.create("minesweeper", [HumanSpec()], seed=0)
    await user.open(f"/match/{match.id}")
    user.find(marker="flag-mode").click()
    user.find(marker="cell-3").click()
    state = await wait_until(match, lambda s: s.flagged[3])
    assert not state.revealed[3]


async def test_ui_hitting_a_mine_shows_the_banner(
    user: User, service: MatchService
) -> None:
    match = service.create("minesweeper", [HumanSpec()], start=False)
    match.state = make_state(mines={0, 60}, revealed={40})
    match.start()
    await user.open(f"/match/{match.id}")
    user.find(marker="cell-0").click()
    await user.should_see("Game over — you hit a mine!", retries=20)
    await user.should_see(marker="cell-0", content="*", retries=20)
    await user.should_see(marker="banner", content="You hit a mine — game over.")
    await user.should_not_see("Draw")


async def test_ui_shows_the_win_banner(user: User, service: MatchService) -> None:
    match = service.create("minesweeper", [HumanSpec()], start=False)
    match.state = make_state(mines={CELLS - 1}, revealed=set(range(CELLS - 1)))
    await user.open(f"/match/{match.id}")
    await user.should_see("You win! Every safe cell is cleared.")

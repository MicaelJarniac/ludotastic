"""Live timing: every seat acts whenever it is ready."""

__all__: tuple[str, ...] = ()

import asyncio
import random
from collections.abc import Hashable, Mapping, Sequence
from typing import override

import pytest
from pydantic import BaseModel

from ludotastic import (
    Decision,
    Game,
    GameInfo,
    HumanPlayer,
    Match,
    MatchStatus,
    Outcome,
    Player,
    PlayerKind,
    Timing,
    TurnContext,
)
from ludotastic.service import replay
from ludotastic.storage import MatchRecord


class Board(BaseModel):
    """Two players write numbers; the round ends after a while."""

    clock: float = 0.0
    numbers: tuple[int, ...] = (0, 0)
    limit: float = 0.3
    target: int = 3


class Write(BaseModel):
    """Write a number in your own slot."""

    number: int


class Scribble(Game[Board, Write]):
    """Two seats write numbers at their own pace, until one reaches the target."""

    info = GameInfo(
        id="scribble",
        name="Scribble",
        description="Write!",
        min_players=2,
        max_players=2,
        timing=Timing.LIVE,
        tick_seconds=0.01,
    )
    state_type = Board
    action_type = Write

    @override
    def initial_state(self, rng: random.Random) -> Board:
        return Board()

    @override
    def to_move(self, state: Board) -> Sequence[int]:
        return (0, 1)

    @override
    def legal_actions(self, state: Board, player: int) -> Sequence[Write]:
        return (Write(number=state.numbers[player] + 1),)

    @override
    def is_legal(self, state: Board, player: int, action: Write) -> bool:
        return action.number > state.numbers[player]

    @override
    def apply(
        self, state: Board, actions: Mapping[int, Write], rng: random.Random
    ) -> Board:
        numbers = tuple(
            actions[i].number if i in actions else n
            for i, n in enumerate(state.numbers)
        )
        return state.model_copy(update={"numbers": numbers})

    @override
    def clock(self, state: Board, elapsed: float) -> Board:
        return state.model_copy(update={"clock": elapsed})

    @override
    def outcome(self, state: Board) -> Outcome | None:
        if max(state.numbers) >= state.target:
            top = max(state.numbers)
            scores = tuple(1.0 if n == top else 0.0 for n in state.numbers)
            return Outcome(scores=scores, reason="Target reached")
        if state.clock >= state.limit:
            return Outcome.draw(2, "Time is up")
        return None

    @override
    def observe(self, state: Board, player: int) -> str:
        return f"{state.numbers}"


class Counter(Player):
    """Writes the next number, as fast as it is asked."""

    kind = PlayerKind.BOT

    def __init__(self, name: str = "counter", *, delay: float = 0.0) -> None:
        super().__init__(name)
        self.delay = delay
        self.asked = 0

    @override
    async def act(self, ctx: TurnContext) -> Decision:
        self.asked += 1
        await asyncio.sleep(self.delay)
        return Decision(action=ctx.legal_actions[0], details={"asked": self.asked})


class Idle(Player):
    """Answers once, then never changes its mind."""

    kind = PlayerKind.BOT

    def __init__(self) -> None:
        super().__init__("idle")
        self.asked = 0

    @override
    async def act(self, ctx: TurnContext) -> Decision:
        self.asked += 1
        if self.asked > 1:
            await asyncio.sleep(10)
        return Decision(action=Write(number=1))


async def test_fast_seats_are_not_held_back_by_slow_ones() -> None:
    fast, slow = Counter("fast"), Counter("slow", delay=0.5)
    match = Match(Scribble(), [fast, slow])
    outcome = await match.run()
    assert outcome is not None
    assert outcome.scores == (1.0, 0.0)
    assert [m.seat for m in match.history] == [0, 0, 0]
    times = [m.at or 0.0 for m in match.history]
    assert all(m.at is not None for m in match.history)
    assert times == sorted(times)
    assert match.thinking == set()


async def test_seats_are_asked_again_only_when_their_view_changes() -> None:
    idle = Idle()
    match = Match(Scribble(), [idle, Counter("slow", delay=0.05)])
    outcome = await match.run()
    assert outcome is not None
    # Asked at the start, and again once its move changed the board; that
    # second answer never comes, so the other seat's moves ask nothing more.
    assert idle.asked == 2


async def test_min_interval_spaces_requests_out() -> None:
    patient = Counter("patient")
    patient.min_interval = 0.2
    game = Scribble()
    match = Match(game, [patient, Idle()])
    await match.run()
    times = [m.at or 0.0 for m in match.history if m.seat == 0]
    assert len(times) == 2  # At 0 and around 0.2, then time runs out at 0.3.
    assert times[1] - times[0] >= 0.19


async def test_time_running_out_ends_the_match() -> None:
    match = Match(Scribble(), [Idle(), Idle()])
    outcome = await match.run()
    assert outcome is not None
    assert outcome.reason == "Time is up"
    assert match.state.clock >= 0.3  # type: ignore[ty:unresolved-attribute]


class Liar(Player):
    """Writes a number that does not go up."""

    kind = PlayerKind.BOT

    @override
    async def act(self, ctx: TurnContext) -> Decision:
        return Decision(action=Write(number=0))


class Crasher(Player):
    """Raises."""

    kind = PlayerKind.BOT

    @override
    async def act(self, ctx: TurnContext) -> Decision:
        msg = "boom"
        raise RuntimeError(msg)


async def test_illegal_actions_and_crashes_forfeit() -> None:
    outcome = await Match(Scribble(), [Idle(), Liar("liar")]).run()
    assert outcome is not None
    assert outcome.scores == (1.0, 0.0)
    assert "illegal" in outcome.reason
    outcome = await Match(Scribble(), [Crasher("c"), Idle()]).run()
    assert outcome is not None
    assert outcome.scores == (0.0, 1.0)
    assert "boom" in outcome.reason


class Late(Player):
    """Answers after the game has moved past its answer."""

    kind = PlayerKind.BOT

    @override
    async def act(self, ctx: TurnContext) -> Decision:
        await asyncio.sleep(0.05)
        return Decision(action=Write(number=1))


class Overtaken(Player):
    """Answers with a number somebody else has already written for it."""

    kind = PlayerKind.BOT

    @override
    async def act(self, ctx: TurnContext) -> Decision:
        await asyncio.sleep(0.05)
        return Decision(action=Write(number=1))


async def test_answers_the_game_moved_past_are_dropped() -> None:
    match = Match(Scribble(), [Overtaken("o"), Idle()])
    task = match.start()
    await asyncio.sleep(0.01)
    match.state = match.state.model_copy(update={"numbers": (2, 0)})
    await asyncio.sleep(0.1)
    assert [m.seat for m in match.history] == [1]
    match.abort()
    await task


async def test_people_act_the_moment_they_submit() -> None:
    human = HumanPlayer()
    match = Match(Scribble(), [human, Idle()])
    match.state = match.state.model_copy(update={"limit": 10.0})
    task = match.start()
    await asyncio.sleep(0.01)
    assert 0 in match.thinking
    with pytest.raises(ValueError, match="Illegal"):
        match.submit(0, Write(number=0))
    for number in (1, 2, 3):
        match.submit(0, Write(number=number))
    outcome = await task
    assert outcome is not None
    assert outcome.scores == (1.0, 0.0)
    human_moves = [m for m in match.history if m.seat == 0]
    assert [m.action["number"] for m in human_moves] == [1, 2, 3]
    assert all(m.seconds == 0.0 for m in human_moves)


async def test_max_turns_in_live_games() -> None:
    match = Match(Scribble(), [Counter(), Counter()], max_turns=1)
    outcome = await match.run()
    assert outcome is not None
    assert outcome.reason == "Turn limit reached"


class Handover(Scribble):
    """The first seat acts until the second writes anything."""

    @override
    def to_move(self, state: Board) -> Sequence[int]:
        return (0, 1) if state.numbers[1] == 0 else (1,)


async def test_seats_that_stop_acting_are_dropped() -> None:
    human = HumanPlayer()
    match = Match(Handover(), [Late("late"), human])
    task = match.start()
    await asyncio.sleep(0.01)
    assert match.thinking == {0, 1}
    match.submit(1, Write(number=1))
    await asyncio.sleep(0.01)
    assert match.thinking == {1}
    match.submit(1, Write(number=3))
    outcome = await task
    assert outcome is not None
    assert outcome.scores == (0.0, 1.0)
    assert [m.seat for m in match.history] == [1, 1]


async def test_aborting_a_live_match_cancels_every_seat() -> None:
    match = Match(Scribble(), [Late("a"), HumanPlayer()])
    task = match.start()
    await asyncio.sleep(0.01)
    match.abort("bye")
    assert await task is None
    assert match.status is MatchStatus.ABORTED


async def test_live_matches_replay_with_their_clock() -> None:
    from ludotastic.engine.registry import GameRegistry  # noqa: PLC0415

    match = Match(Scribble(), [Counter("fast"), Counter("slow", delay=0.5)])
    await match.run()
    record = MatchRecord.from_match(match)
    games = GameRegistry(group="ludotastic.tests.none")
    games.register(Scribble)
    replayed = replay(games, record)
    assert replayed.state.numbers == match.state.numbers  # type: ignore[ty:unresolved-attribute]
    assert replayed.state.clock > 0  # type: ignore[ty:unresolved-attribute]


async def test_min_interval_holds_a_seat_back() -> None:
    eager = Counter("eager")
    eager.min_interval = 0.1
    match = Match(Scribble(), [eager, Idle()])
    match.state = match.state.model_copy(update={"limit": 0.05})
    await match.run()
    # Its first move changes its view at once, but it may not ask again yet.
    assert eager.asked == 1


def test_live_game_defaults() -> None:
    game = Scribble()
    state = Board()
    assert Game.clock(game, state, 1.0) is state
    assert game.view_key(state, 0) == game.observe(state, 0)
    with pytest.raises(NotImplementedError, match="rank"):
        game.rank_options(state, 0)
    with pytest.raises(NotImplementedError, match="rank"):
        game.rank_action(state, 0, [])
    assert game.rank_question(state, 1) == "Which option is likeliest, for Player 2?"
    assert Counter().min_interval == 0.0


class Slowing(Player):
    """Answers at once, but may only be asked every so often."""

    kind = PlayerKind.BOT
    min_interval = 0.05

    def __init__(self) -> None:
        super().__init__("slowing")
        self.asked = 0

    @override
    async def act(self, ctx: TurnContext) -> Decision:
        self.asked += 1
        return Decision(action=ctx.legal_actions[0])


async def test_a_seat_waits_out_its_interval_then_is_asked() -> None:
    seat = Slowing()
    match = Match(Scribble(), [seat, Idle()])
    match.state = match.state.model_copy(update={"limit": 0.12})
    await match.run()
    assert 2 <= seat.asked <= 3


class Blindfold(Scribble):
    """Nobody's view ever changes."""

    @override
    def view_key(self, state: Board, player: int) -> Hashable:
        return None


async def test_a_seat_whose_view_never_changes_is_asked_once() -> None:
    seat = Counter()
    await Match(Blindfold(), [seat, Idle()]).run()
    assert seat.asked == 1


async def test_viewers_are_told_when_the_clock_moves() -> None:
    match = Match(Scribble(), [Idle(), Idle()])
    clocks: list[float] = []
    match.subscribe(lambda m: clocks.append(m.state.clock))  # type: ignore[ty:unresolved-attribute]
    await match.run()
    # Nobody acts after the first answers, yet the clock is reported as it runs.
    assert len({round(c, 2) for c in clocks}) > 5


class Timeless(Scribble):
    """A live game that does not keep time."""

    @override
    def clock(self, state: Board, elapsed: float) -> Board:
        return state


async def test_viewers_are_not_told_when_nothing_moves() -> None:
    match = Match(Timeless(), [Idle(), Idle()], max_turns=3)
    notified: list[int] = []
    match.subscribe(lambda m: notified.append(m.turn))
    task = match.start()
    await asyncio.sleep(0.1)
    # Both idle seats answered once; their second answers never come.
    count = len(notified)
    await asyncio.sleep(0.1)
    assert len(notified) == count
    match.abort()
    await task

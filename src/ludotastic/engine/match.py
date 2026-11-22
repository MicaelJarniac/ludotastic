"""Matches: a game, its players, and the loop that runs them."""

__all__: tuple[str, ...] = (
    "Match",
    "MatchStatus",
    "MoveRecord",
    "SeatInfo",
)

import asyncio
import contextlib
import math
import random
import uuid
from collections.abc import Awaitable, Callable, Hashable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from loguru import logger
from pydantic import BaseModel, Field

from ludotastic.engine.game import Game, Outcome, Timing
from ludotastic.engine.players import Decision, HumanPlayer, Player, TurnContext
from ludotastic.engine.usage import Usage
from ludotastic.types import Count, NonNegativeFloat, Seat


class MatchStatus(StrEnum):
    """Where a match is in its life."""

    PENDING = "pending"
    RUNNING = "running"
    FINISHED = "finished"
    ABORTED = "aborted"


class SeatInfo(BaseModel):
    """Who sits in a seat."""

    seat: Seat
    label: str
    """The game's name for the seat, such as `X`."""
    player: str
    """The player's display name."""
    kind: str
    """`human`, `bot` or `ai`."""
    group: str
    """The player's statistics label."""


class MoveRecord(BaseModel):
    """One action in a match's history."""

    turn: Count
    seat: Seat
    action: dict[str, Any]
    description: str
    seconds: NonNegativeFloat
    """How long the player took."""
    details: dict[str, Any] = Field(default_factory=dict)
    """Whatever the player reported, such as model usage."""
    forced: bool = False
    """Whether the engine chose this action, because the player was too slow."""
    usage: Usage | None = None
    """What the move cost, for players that use a model."""
    at: NonNegativeFloat | None = None
    """When the move was applied, in seconds since the match started.

    Only recorded in [`Timing.LIVE`][ludotastic.Timing.LIVE] games, whose
    state depends on the time.
    """


type Listener = Callable[[Match], Awaitable[None] | None]


@dataclass
class _Live:
    """What a live match keeps track of for each seat."""

    tasks: dict[int, asyncio.Task[Decision]] = field(default_factory=dict)
    """The requests out, by seat."""
    asked_at: dict[int, float] = field(default_factory=dict)
    """When each seat was last asked, in seconds since the start."""
    contexts: dict[int, TurnContext] = field(default_factory=dict)
    """What each seat was last asked with."""
    views: dict[int, Hashable] = field(default_factory=dict)
    """Each seat's view of the game when it was last asked."""


class Match:
    """A game being played by a set of players.

    Create one, then [`run`][ludotastic.Match.run] it as a task. Watch it with
    [`subscribe`][ludotastic.Match.subscribe].
    """

    def __init__(  # noqa: PLR0913
        self,
        game: Game[Any, Any],
        players: Sequence[Player],
        *,
        seed: int | None = None,
        turn_timeout: float | None = None,
        max_turns: int | None = None,
        delay: float = 0.0,
    ) -> None:
        """Seat the players.

        Args:
            game: The game to play.
            players: One player per seat, in seat order.
            seed: Seed for the match's randomness; random when not given.
            turn_timeout: For step-based games, how long a player may think
                before forfeiting the match; `None` waits forever.
            max_turns: End the match as a draw after this many turns.
            delay: Pause between turns, so people can follow along.

        Raises:
            ValueError: If the number of players does not fit the game.
        """
        if len(players) != game.num_players:
            msg = f"{game.info.name} is set up for {game.num_players} players"
            raise ValueError(msg)
        self.id = uuid.uuid4().hex
        self.game = game
        self.players = list(players)
        self.seed = seed if seed is not None else random.SystemRandom().getrandbits(32)
        self.rng = random.Random(self.seed)
        self.turn_timeout = turn_timeout
        self.max_turns = max_turns
        self.delay = delay
        self.state: BaseModel = game.initial_state(self.rng)
        self.history: list[MoveRecord] = []
        self.status = MatchStatus.PENDING
        self.outcome: Outcome | None = None
        self.error: str | None = None
        self.thinking: set[int] = set()
        self.created_at = datetime.now(UTC)
        self.finished_at: datetime | None = None
        self._listeners: list[Listener] = []
        self._turns = 0

    @property
    def turn(self) -> int:
        """The number of turns played so far."""
        return self._turns

    @property
    def seats(self) -> list[SeatInfo]:
        """Who sits where."""
        return [
            SeatInfo(
                seat=i,
                label=self.game.player_label(i),
                player=p.name,
                kind=str(p.kind),
                group=p.label,
            )
            for i, p in enumerate(self.players)
        ]

    def usage(self, seat: int | None = None) -> Usage:
        """Model usage so far, for one seat or the whole match."""
        return Usage.total(
            move.usage
            for move in self.history
            if move.usage is not None and (seat is None or move.seat == seat)
        )

    @property
    def done(self) -> bool:
        """Whether the match is over, one way or another."""
        return self.status in {MatchStatus.FINISHED, MatchStatus.ABORTED}

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        """Call `listener` whenever the match changes; returns an unsubscriber."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    async def _notify(self) -> None:
        for listener in list(self._listeners):
            try:
                result = listener(self)
                if isinstance(result, Awaitable):
                    await result
            except Exception:  # noqa: BLE001 - one broken viewer must not stop the match
                logger.exception("Match listener failed")

    def submit(self, seat: int, action: BaseModel | dict[str, Any]) -> None:
        """Hand in an action for a human seat.

        In a [`Timing.LIVE`][ludotastic.Timing.LIVE] game, the action is applied
        at once.

        Raises:
            TypeError: If the seat is not a human player.
            ValueError: If a step-based or live game is not waiting on that
                seat, or the action is illegal.
        """
        player = self.players[seat]
        if not isinstance(player, HumanPlayer):
            msg = f"Seat {seat} is not played by a human"
            raise TypeError(msg)
        if not isinstance(action, BaseModel):
            action = self.game.parse_action(action)
        if self.game.info.timing is not Timing.TICK:
            if seat not in self.thinking:
                msg = f"It is not seat {seat}'s turn"
                raise ValueError(msg)
            if not self.game.is_legal(self.state, seat, action):
                msg = "Illegal action"
                raise ValueError(msg)
        if self._wake is not None:
            self._apply_live(seat, Decision(action=action), 0.0)
            self._wake.set()
            return
        player.submit(action)

    def abort(self, reason: str = "Aborted") -> None:
        """Stop the match without an outcome."""
        if self.done:
            return
        self.status = MatchStatus.ABORTED
        self.error = reason
        self.finished_at = datetime.now(UTC)
        for player in self.players:
            player.cancel()
        if self._task is not None:
            self._task.cancel()

    _task: asyncio.Task[Outcome | None] | None = None

    def start(self) -> asyncio.Task[Outcome | None]:
        """Run the match in the background."""
        task = self._task
        if task is None:
            task = self._task = asyncio.create_task(self.run(), name=f"match-{self.id}")
        return task

    async def run(self) -> Outcome | None:
        """Play the match to the end."""
        self.status = MatchStatus.RUNNING
        await self._notify()
        try:
            if self.game.info.timing is Timing.LIVE:
                outcome = await self._run_live()
            else:
                outcome = await self._run_turns()
        except asyncio.CancelledError:
            # `abort()` may have already set the status and reason.
            self.status = MatchStatus.ABORTED
            self.error = self.error or "Cancelled"
            self.finished_at = self.finished_at or datetime.now(UTC)
            await self._notify()
            return None
        except Exception as e:  # noqa: BLE001 - record any failure on the match
            logger.exception("Match {} failed", self.id)
            self.status = MatchStatus.ABORTED
            self.error = f"{type(e).__name__}: {e}"
            self.finished_at = datetime.now(UTC)
            await self._notify()
            return None
        finally:
            self.thinking.clear()
            for player in self.players:
                player.cancel()
        self.outcome = outcome
        self.status = MatchStatus.FINISHED
        self.finished_at = datetime.now(UTC)
        await self._notify()
        return outcome

    async def _run_turns(self) -> Outcome:
        """Play turn after turn, for step and tick games."""
        while (outcome := self.game.outcome(self.state)) is None:
            if self.max_turns is not None and self.turn >= self.max_turns:
                return self.game.out_of_turns(self.state)
            forfeit = await self._play_turn()
            if forfeit is not None:
                return forfeit
            await self._notify()
            if self.delay:
                await asyncio.sleep(self.delay)
        return outcome

    async def _run_live(self) -> Outcome:
        """Let every seat act whenever it is ready, for live games.

        Each bot or AI seat has at most one request out at a time, and is asked
        again once its view of the game changes, no sooner than its
        `min_interval` allows. People's actions are applied the moment they
        are [submitted][ludotastic.Match.submit]. Nobody waits for anybody else.
        """
        loop = asyncio.get_running_loop()
        self._started = loop.time()
        self._wake = asyncio.Event()
        live = _Live()
        try:
            while True:
                self._wake.clear()
                before = self.state
                self.state = self.game.clock(self.state, self._elapsed())
                if (outcome := self.game.outcome(self.state)) is not None:
                    return outcome
                if self.max_turns is not None and self.turn >= self.max_turns:
                    return self.game.out_of_turns(self.state)
                thinking = set(self.thinking)
                wake = self._ask_live(live)
                # Viewers follow the clock too, such as a countdown.
                if self.thinking != thinking or self.state != before:
                    await self._notify()
                result = await self._wait_live(live, wake)
                if isinstance(result, Outcome):
                    return result
                if result:
                    await self._notify()
        finally:
            self._wake = None
            for seat, task in live.tasks.items():
                self._drop(seat, task)
            for task in live.tasks.values():
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

    def _ask_live(self, live: _Live) -> float:
        """Ask every seat due for a new answer; returns how long to wait at most."""
        game = self.game
        now = self._elapsed()
        acting = set(game.to_move(self.state))
        for seat in set(live.tasks) - acting:
            self._drop(seat, live.tasks.pop(seat))
        wake = game.info.tick_seconds
        humans: set[int] = set()
        for seat in sorted(acting - set(live.tasks)):
            player = self.players[seat]
            if isinstance(player, HumanPlayer):
                humans.add(seat)
                continue
            view = game.view_key(self.state, seat)
            if seat in live.views and live.views[seat] == view:
                continue
            ready = live.asked_at.get(seat, -math.inf) + player.min_interval
            if now < ready:
                wake = min(wake, ready - now)
                continue
            live.views[seat] = view
            live.contexts[seat] = self._context(seat)
            live.asked_at[seat] = now
            live.tasks[seat] = asyncio.create_task(player.act(live.contexts[seat]))
        self.thinking = set(live.tasks) | humans
        return wake

    async def _wait_live(self, live: _Live, wake: float) -> Outcome | bool:
        """Wait for answers or a submission; a forfeit, or whether anything changed."""
        assert self._wake is not None  # noqa: S101
        woken = asyncio.create_task(self._wake.wait())
        done, _ = await asyncio.wait(
            {*live.tasks.values(), woken},
            timeout=wake,
            return_when=asyncio.FIRST_COMPLETED,
        )
        woken.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await woken
        changed = woken in done
        for seat, task in sorted(live.tasks.items()):
            if task in done:
                del live.tasks[seat]
                result = self._take_live(live, seat, task)
                if isinstance(result, Outcome):
                    return result
                changed |= result
        return changed

    def _take_live(
        self, live: _Live, seat: int, task: asyncio.Task[Decision]
    ) -> Outcome | bool:
        """Take one seat's answer: a forfeit, or whether it changed the game."""
        game = self.game
        label = game.player_label(seat)
        if (error := task.exception()) is not None:
            logger.warning("Seat {} failed in match {}: {!r}", seat, self.id, error)
            reason = f"{label} failed: {type(error).__name__}: {error}"
            return Outcome.forfeit(game.num_players, seat, reason)
        decision = task.result()
        if not game.is_legal(live.contexts[seat].state, seat, decision.action):
            return Outcome.forfeit(
                game.num_players, seat, f"{label} made an illegal move"
            )
        return self._apply_live(seat, decision, self._elapsed() - live.asked_at[seat])

    _started: float = 0.0
    _wake: asyncio.Event | None = None

    def _elapsed(self) -> float:
        return asyncio.get_running_loop().time() - self._started

    def _apply_live(self, seat: int, decision: Decision, seconds: float) -> bool:
        """Apply one seat's action in a live game; whether it still applied.

        The game may have moved on while the seat decided, such as a round
        ending, in which case the action is dropped.
        """
        at = self._elapsed()
        self.state = self.game.clock(self.state, at)
        if self.game.outcome(self.state) is not None or not self.game.is_legal(
            self.state, seat, decision.action
        ):
            return False
        self.state = self.game.apply(self.state, {seat: decision.action}, self.rng)
        self._record(
            seat, decision.action, seconds, decision.details, decision.usage, at=at
        )
        self._turns += 1
        return True

    def _drop(self, seat: int, task: asyncio.Task[Decision]) -> None:
        """Stop waiting for a seat that no longer has to act."""
        self.players[seat].cancel()
        task.cancel()

    def _context(self, seat: int) -> TurnContext:
        return TurnContext(
            game=self.game,
            state=self.state,
            player=seat,
            legal_actions=list(self.game.legal_actions(self.state, seat)),
            turn=self.turn,
            rng=random.Random(self.rng.getrandbits(64)),
        )

    async def _play_turn(self) -> Outcome | None:
        """Ask every seat to move, then advance the game; returns a forfeit, if any."""
        game = self.game
        seats = list(game.to_move(self.state))
        ticking = game.info.timing is Timing.TICK
        timeout = game.info.tick_seconds if ticking else self.turn_timeout
        decisions = await self._ask(seats, timeout, ticking=ticking)

        actions: dict[int, BaseModel] = {}
        for seat in seats:
            result = decisions.get(seat)
            label = game.player_label(seat)
            if isinstance(result, BaseException):
                logger.warning(
                    "Seat {} failed in match {}: {!r}", seat, self.id, result
                )
                reason = f"{label} failed: {type(result).__name__}: {result}"
                return Outcome.forfeit(game.num_players, seat, reason)
            if result is not None:
                decision, seconds = result
                if not game.is_legal(self.state, seat, decision.action):
                    reason = f"{label} made an illegal move"
                    return Outcome.forfeit(game.num_players, seat, reason)
                actions[seat] = decision.action
                self._record(
                    seat, decision.action, seconds, decision.details, decision.usage
                )
            elif not ticking:
                return Outcome.forfeit(
                    game.num_players, seat, f"{label} ran out of time"
                )
            elif (default := game.default_action(self.state, seat)) is not None:
                actions[seat] = default
                self._record(seat, default, timeout or 0.0, {}, forced=True)
        self.state = game.apply(self.state, actions, self.rng)
        self._turns += 1
        return None

    async def _ask(
        self, seats: list[int], deadline: float | None, *, ticking: bool = False
    ) -> dict[int, tuple[Decision, float] | BaseException]:
        """Ask seats to act, at once; slow seats are left out of the result.

        On a tick, only people are held to `deadline`, and not on the first
        tick: bots and AI players are always waited for. With a person
        playing, the tick lasts at least `deadline`.
        """
        contexts = {seat: self._context(seat) for seat in seats}
        self.thinking = set(seats)
        await self._notify()
        loop = asyncio.get_running_loop()
        started = loop.time()

        async def ask(seat: int) -> tuple[Decision, float]:
            decision = await self.players[seat].act(contexts[seat])
            return decision, loop.time() - started

        tasks = {seat: asyncio.create_task(ask(seat)) for seat in seats}
        # A real-time game waits for each person's first move, so nobody loses
        # before they have even seen the board.
        first = self.turn == 0
        clocked = {
            seat: task
            for seat, task in tasks.items()
            if not ticking
            or (isinstance(self.players[seat], HumanPlayer) and not first)
        }
        unclocked = [t for t in tasks.values() if t not in clocked.values()]
        if clocked:
            await asyncio.wait(clocked.values(), timeout=deadline)
        if unclocked:
            await asyncio.wait(unclocked)
        if ticking and deadline is not None and clocked:
            # With a person playing, a tick never ends early: a steady pace.
            await asyncio.sleep(max(0.0, started + deadline - loop.time()))
        pending = {task for task in tasks.values() if not task.done()}
        for seat, task in tasks.items():
            if task in pending:
                player = self.players[seat]
                if isinstance(player, HumanPlayer):
                    player.timeout()
                else:  # pragma: no cover - only people are left pending
                    player.cancel()
                task.cancel()
        for task in pending:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self.thinking.clear()
        return {
            seat: task.exception() or task.result()
            for seat, task in tasks.items()
            if task not in pending
        }

    def _record(  # noqa: PLR0913
        self,
        seat: int,
        action: BaseModel,
        seconds: float,
        details: dict[str, Any],
        usage: Usage | None = None,
        *,
        forced: bool = False,
        at: float | None = None,
    ) -> None:
        self.history.append(
            MoveRecord(
                turn=self.turn,
                seat=seat,
                action=action.model_dump(mode="json"),
                description=self.game.describe_action(action),
                seconds=round(seconds, 3),
                details=details,
                forced=forced,
                usage=usage,
                at=None if at is None else round(at, 3),
            )
        )

"""Pictionary rules."""

__all__: tuple[str, ...] = (
    "CANVAS",
    "MAX_POINTS",
    "MAX_STROKES",
    "ROUND_SECONDS",
    "WORDS",
    "WORDS_PER_ROUND",
    "Draw",
    "Finish",
    "Pictionary",
    "PictionaryAction",
    "PictionaryState",
    "Point",
    "Rank",
    "ReplayBot",
    "ShapesBot",
    "Stroke",
    "simplify",
)

import asyncio
import json
import math
import random
from collections.abc import Hashable, Mapping, Sequence
from functools import cache
from importlib.resources import files
from typing import TYPE_CHECKING, Annotated, Final, Literal, cast, override

from annotated_types import Len, MinLen
from pydantic import BaseModel, ConfigDict, Field, RootModel

from ludotastic.engine.drawing import Canvas
from ludotastic.engine.game import (
    ActionMode,
    BotFactory,
    Game,
    GameInfo,
    Outcome,
    Timing,
    View,
)
from ludotastic.engine.players import Decision, Player, PlayerKind, TurnContext
from ludotastic.types import Byte, NonNegativeFloat, Seat

if TYPE_CHECKING:
    from ludotastic.engine.render import Renderer

CANVAS: Final = 256
"""Drawings are on a square of this many units a side, as in Quick, Draw!."""

MAX_POINTS: Final = 200
"""The most points a stroke keeps; longer strokes are thinned out."""

MAX_STROKES: Final = 300
"""The most strokes a drawing may have."""

ROUND_SECONDS: Final = 60.0
"""How long a round lasts, unless the drawer finishes sooner."""

WORDS_PER_ROUND: Final = 12
"""How many words each round's guessers choose among."""

WORDS: Final = (
    "apple",
    "banana",
    "bicycle",
    "bird",
    "book",
    "bridge",
    "butterfly",
    "cactus",
    "cake",
    "camera",
    "car",
    "carrot",
    "cat",
    "chair",
    "circle",
    "clock",
    "cloud",
    "crab",
    "crown",
    "cup",
    "diamond",
    "dog",
    "donut",
    "door",
    "duck",
    "ear",
    "envelope",
    "eye",
    "eyeglasses",
    "face",
    "fish",
    "flower",
    "fork",
    "frog",
    "guitar",
    "hamburger",
    "hammer",
    "hat",
    "house",
    "ice cream",
    "key",
    "ladder",
    "leaf",
    "light bulb",
    "lightning",
    "lollipop",
    "moon",
    "mountain",
    "mushroom",
    "octopus",
    "pants",
    "pencil",
    "pizza",
    "rabbit",
    "rainbow",
    "scissors",
    "sheep",
    "shoe",
    "smiley face",
    "snail",
    "snake",
    "snowman",
    "spider",
    "spoon",
    "square",
    "star",
    "sun",
    "sword",
    "t-shirt",
    "table",
    "tent",
    "tree",
    "triangle",
    "truck",
    "umbrella",
    "whale",
    "wheel",
    "windmill",
    "wine glass",
)
"""Every word a round can ask for: easy to draw, and all Quick, Draw! categories."""

type Point = Annotated[tuple[Byte, Byte], Len(2, 2)]
"""A point on the drawing, `x` then `y`, from 0 to 255, from the top left."""

type Stroke = Annotated[tuple[Point, ...], MinLen(1)]
"""One continuous line, as the points it passes through."""


def simplify(points: Sequence[Sequence[float]], limit: int = MAX_POINTS) -> Stroke:
    """Clamp points to the drawing, and thin them out to at most `limit`.

    The first and last points are always kept.

    Examples:
        >>> simplify([(0, 0), (300, -5)])
        ((0, 0), (255, 0))
        >>> len(simplify([(i, i) for i in range(250)], limit=10))
        10
    """
    clamped = tuple(
        (min(max(round(x), 0), CANVAS - 1), min(max(round(y), 0), CANVAS - 1))
        for x, y in points
    )
    if len(clamped) <= limit:
        return clamped
    step = (len(clamped) - 1) / (limit - 1)
    return tuple(clamped[round(i * step)] for i in range(limit))


class Draw(BaseModel):
    """The drawer adds a stroke to the drawing."""

    model_config = ConfigDict(frozen=True, use_attribute_docstrings=True)

    kind: Literal["draw"] = "draw"
    points: Annotated[tuple[Point, ...], MinLen(1)]
    """The points the stroke passes through, from 0 to 255 each way."""


class Finish(BaseModel):
    """The drawer is done, which ends the round."""

    model_config = ConfigDict(frozen=True, use_attribute_docstrings=True)

    kind: Literal["finish"] = "finish"


class Rank(BaseModel):
    """A guesser orders every word, likeliest first."""

    model_config = ConfigDict(frozen=True, use_attribute_docstrings=True)

    kind: Literal["rank"] = "rank"
    order: tuple[str, ...]
    """Every word in the round, exactly once, likeliest first."""


class PictionaryAction(
    RootModel[Annotated[Draw | Finish | Rank, Field(discriminator="kind")]]
):
    """Any action in Pictionary: drawing, finishing, or ranking the words."""

    model_config = ConfigDict(frozen=True)


def _act(action: Draw | Finish | Rank) -> PictionaryAction:
    return PictionaryAction(root=action)


class PictionaryState(BaseModel):
    """The words, the drawing so far, and each guesser's ranking."""

    model_config = ConfigDict(frozen=True)

    words: tuple[str, ...]
    """The words the guessers choose among, in the order first shown."""
    answer: str
    """The word being drawn; only the drawer may know it."""
    strokes: tuple[Stroke, ...] = ()
    """The drawing so far."""
    rankings: tuple[tuple[str, ...] | None, ...]
    """Each seat's current ranking, likeliest first; `None` until it ranks.

    Seat 0 is the drawer, and never ranks.
    """
    credit: tuple[NonNegativeFloat, ...]
    """Each seat's score so far: seconds spent weighted by 1 over the answer's rank."""
    elapsed: NonNegativeFloat = 0.0
    """Seconds since the round started, up to the time limit."""
    limit: NonNegativeFloat = ROUND_SECONDS
    """How long the round may last."""
    finished: bool = False
    """Whether the drawer has finished."""

    @property
    def over(self) -> bool:
        """Whether the round has ended."""
        return self.finished or self.elapsed >= self.limit

    @property
    def remaining(self) -> NonNegativeFloat:
        """Seconds left in the round."""
        return 0.0 if self.over else self.limit - self.elapsed

    def rank_of_answer(self, seat: Seat) -> int | None:
        """Where the answer sits in a seat's ranking, from 1; `None` if unranked."""
        ranking = self.rankings[seat]
        return None if ranking is None else ranking.index(self.answer) + 1

    def score(self, seat: Seat) -> float:
        """A guesser's score: the average of 1 over the answer's rank, so far.

        1 means the answer was on top the whole round; time spent unranked
        counts as 0.
        """
        return self.credit[seat] / self.elapsed if self.elapsed else 0.0


class Pictionary(Game[PictionaryState, PictionaryAction]):
    """One seat draws a word; everybody else ranks the words as the drawing grows."""

    info = GameInfo(
        id="pictionary",
        name="Pictionary",
        description="Draw a word; guessers rank every word, live, as you draw.",
        min_players=2,
        max_players=5,
        timing=Timing.LIVE,
        tick_seconds=0.25,
        action_modes=(ActionMode.RANK,),
        views=(View.IMAGE, View.BOTH),
        tags=("party", "live", "drawing"),
    )
    rules = (
        "The drawer draws a secret word, picked at random from a few shown to "
        "everybody. Every guesser keeps all the words in order, likeliest first, "
        "and can change the order at any time while the drawing grows. The round "
        f"lasts {ROUND_SECONDS:g} seconds, or until the drawer finishes.\n\n"
        "A guesser scores the average, over the round, of 1 over where the right "
        "word sat in their order: 1 for having it on top the whole time, 0.5 for "
        "second, and nothing before ranking at all. So it pays to get it early, "
        "and to stay sure of it. The drawer scores the guessers' average: draw so "
        "that everybody gets it."
    )
    state_type = PictionaryState
    action_type = PictionaryAction

    @override
    def initial_state(self, rng: random.Random) -> PictionaryState:
        words = tuple(rng.sample(WORDS, WORDS_PER_ROUND))
        return PictionaryState(
            words=words,
            answer=rng.choice(words),
            rankings=(None,) * self.num_players,
            credit=(0.0,) * self.num_players,
        )

    @override
    def to_move(self, state: PictionaryState) -> Sequence[Seat]:
        if state.over:
            return ()
        return tuple(range(self.num_players))

    @override
    def legal_actions(
        self, state: PictionaryState, player: Seat
    ) -> Sequence[PictionaryAction]:
        """A few actions to pick from; any stroke and any ordering are legal too.

        The drawer gets a handful of simple shapes, and finishing once there is
        a drawing. A guesser gets each word put on top of the round's order.
        """
        if state.over:
            return ()
        if player == 0:
            shapes = [_act(Draw(points=points)) for points in _SHAPES]
            return [*shapes, _act(Finish())] if state.strokes else shapes
        base = state.rankings[player] or state.words
        return [
            _act(Rank(order=(word, *(w for w in base if w != word)))) for word in base
        ]

    @override
    def is_legal(
        self, state: PictionaryState, player: Seat, action: PictionaryAction
    ) -> bool:
        if state.over:
            return False
        match action.root:
            case Draw():
                return player == 0 and len(state.strokes) < MAX_STROKES
            case Finish():
                return player == 0
            case Rank(order=order):  # pragma: no branch - the last kind of action
                return player != 0 and sorted(order) == sorted(state.words)

    @override
    def apply(
        self,
        state: PictionaryState,
        actions: Mapping[int, PictionaryAction],
        rng: random.Random,
    ) -> PictionaryState:
        for seat, action in actions.items():
            match action.root:
                case Draw(points=points):
                    stroke = simplify(points)
                    state = state.model_copy(
                        update={"strokes": (*state.strokes, stroke)}
                    )
                case Finish():
                    state = state.model_copy(update={"finished": True})
                case Rank(order=order):  # pragma: no branch - the last kind
                    rankings = list(state.rankings)
                    rankings[seat] = order
                    state = state.model_copy(update={"rankings": tuple(rankings)})
        return state

    @override
    def clock(self, state: PictionaryState, elapsed: float) -> PictionaryState:
        """Credit each guesser for the time since the last clock, then move on."""
        if state.over:
            return state
        now = min(elapsed, state.limit)
        spent = max(now - state.elapsed, 0.0)
        credit = tuple(
            c + (0.0 if (rank := state.rank_of_answer(seat)) is None else spent / rank)
            for seat, c in enumerate(state.credit)
        )
        return state.model_copy(update={"elapsed": now, "credit": credit})

    @override
    def outcome(self, state: PictionaryState) -> Outcome | None:
        if not state.over:
            return None
        guessers = [state.score(seat) for seat in range(1, self.num_players)]
        drawer = sum(guessers) / len(guessers)
        how = "The drawer finished" if state.finished else "Time ran out"
        return Outcome(
            scores=(drawer, *guessers),
            reason=f"{how}; the word was {state.answer}",
        )

    @override
    def out_of_turns(self, state: PictionaryState) -> Outcome:
        outcome = self.outcome(state.model_copy(update={"finished": True}))
        assert outcome is not None  # noqa: S101
        return outcome

    @override
    def view_key(self, state: PictionaryState, player: Seat) -> Hashable:
        """Guessers are asked again with every new stroke.

        A drawer's view changes with the time, so it is asked as often as its
        [`min_interval`][ludotastic.Player.min_interval] allows.
        """
        if player == 0:
            return state.elapsed
        return (len(state.strokes), state.finished)

    @override
    def observe(self, state: PictionaryState, player: Seat) -> str:
        words = ", ".join(state.words)
        lines = [f"You are {self.player_label(player)}.", f"The words: {words}."]
        if player == 0:
            lines.append(f"You are drawing: {state.answer}.")
        lines.append(
            f"The drawing has {len(state.strokes)} "
            f"stroke{'' if len(state.strokes) == 1 else 's'} so far, "
            f"and {state.remaining:.0f} seconds are left."
        )
        if player != 0 and (ranking := state.rankings[player]) is not None:
            lines.append(f"Your current order: {', '.join(ranking)}.")
        return "\n".join(lines)

    @override
    def brief(self, state: PictionaryState, player: Seat) -> str:
        return (
            f"You are {self.player_label(player)}. The picture shows the drawing "
            f"so far, {len(state.strokes)} strokes. It is one of: "
            f"{', '.join(state.words)}."
        )

    @override
    def snapshot(self, state: PictionaryState, player: Seat) -> bytes:
        """The drawing in black on white, 512 pixels a side."""
        scale = 2
        canvas = Canvas(CANVAS * scale, CANVAS * scale)
        for stroke in state.strokes:
            points = [(x * scale, y * scale) for x, y in stroke]
            if len(points) == 1:
                (x, y), r = points[0], 3
                canvas.draw.ellipse((x - r, y - r, x + r, y + r), fill="black")
            else:
                canvas.draw.line(points, fill="black", width=6, joint="curve")
        return canvas.png()

    @override
    def rank_options(self, state: PictionaryState, player: Seat) -> Mapping[str, str]:
        """Every word in the round.

        Raises:
            ValueError: For the drawer, who draws instead of ranking.
        """
        if player == 0:
            msg = "The drawer draws: an AI can only guess, from a guesser's seat"
            raise ValueError(msg)
        return {word: word for word in state.words}

    @override
    def rank_question(self, state: PictionaryState, player: Seat) -> str:
        return "Which of these words is the drawing of?"

    @override
    def rank_action(
        self, state: PictionaryState, player: Seat, order: Sequence[str]
    ) -> PictionaryAction:
        return _act(Rank(order=tuple(order)))

    @override
    def describe_action(self, action: PictionaryAction) -> str:
        match action.root:
            case Draw(points=points):
                return f"Drew a stroke ({len(points)} points)"
            case Finish():
                return "Finished the drawing"
            case Rank():  # pragma: no branch - the last kind of action
                # Not which words: a person guessing could copy them mid-round.
                return "Reordered the words"

    @override
    def player_label(self, player: Seat) -> str:
        return "Drawer" if player == 0 else f"Guesser {player}"

    @override
    def ai_seats(self) -> frozenset[Seat]:
        """Only guessers: an AI ranks words, but cannot be asked to draw."""
        return frozenset(range(1, self.num_players))

    @override
    def bots(self) -> Mapping[str, BotFactory]:
        return {"quickdraw": ReplayBot, "shapes": ShapesBot}

    @override
    def renderer(self) -> Renderer:
        from ludotastic.games.pictionary.ui import render  # noqa: PLC0415

        return render


def _circle(cx: int, cy: int, r: int) -> tuple[Point, ...]:
    steps = 24
    return tuple(
        (
            round(cx + r * math.cos(2 * math.pi * i / steps)),
            round(cy + r * math.sin(2 * math.pi * i / steps)),
        )
        for i in range(steps + 1)
    )


_SHAPES: Final[tuple[tuple[Point, ...], ...]] = (
    ((40, 200), (216, 200)),
    ((60, 60), (196, 60), (196, 196), (60, 196), (60, 60)),
    ((128, 40), (216, 210), (40, 210), (128, 40)),
    _circle(128, 128, 70),
)
"""Shapes a scripted drawer can add: a line, a square, a triangle and a circle."""


class ShapesBot(Player):
    """Draws a simple shape every second as the drawer, or ranks at random.

    Only a baseline: its drawings have nothing to do with the word.
    """

    kind = PlayerKind.BOT
    min_interval = 1.0

    def __init__(self, name: str = "shapes") -> None:
        """Name the player."""
        super().__init__(name)

    @override
    async def act(self, ctx: TurnContext) -> Decision:
        """Add a shape, or finish after a few; as a guesser, shuffle the words."""
        state = ctx.state
        assert isinstance(state, PictionaryState)  # noqa: S101
        if ctx.player == 0:
            if len(state.strokes) >= len(_SHAPES):
                return Decision(action=_act(Finish()))
            return Decision(action=_act(Draw(points=_SHAPES[len(state.strokes)])))
        order = list(state.words)
        ctx.rng.shuffle(order)
        return Decision(action=_act(Rank(order=tuple(order))))


@cache
def sketches() -> Mapping[str, tuple[tuple[Stroke, ...], ...]]:
    """Real people's sketches of every word, from Google's Quick, Draw! dataset.

    A few per word, all of which Quick, Draw! recognized. The dataset is by
    Google, under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/):
    https://github.com/googlecreativelab/quickdraw-dataset
    """
    raw = json.loads(files(__package__).joinpath("sketches.json").read_text())
    data = cast("dict[str, list[list[list[list[int]]]]]", raw)
    return {
        word: tuple(tuple(simplify(stroke) for stroke in sketch) for sketch in drawn)
        for word, drawn in data.items()
    }


class ReplayBot(Player):
    """Redraws a real person's sketch of the word, stroke by stroke, then waits.

    The sketches come from Google's Quick, Draw! dataset; see
    [`sketches`][ludotastic.games.pictionary.game.sketches]. Which sketch is
    replayed follows the match's seed, so matches can be repeated.
    """

    kind = PlayerKind.BOT
    min_interval = 1.0

    def __init__(self, name: str = "quickdraw") -> None:
        """Name the player."""
        super().__init__(name)
        self._sketch: tuple[Stroke, ...] | None = None

    @override
    async def act(self, ctx: TurnContext) -> Decision:
        """Add the sketch's next stroke, one a second.

        Once it is all drawn, it waits for the round to end rather than
        finishing, so guessers always get the whole round to make up their
        minds.

        Raises:
            ValueError: If asked to guess: the replay bot only draws.
        """
        state = ctx.state
        assert isinstance(state, PictionaryState)  # noqa: S101
        if ctx.player != 0:
            msg = "The quickdraw bot only draws"
            raise ValueError(msg)
        if self._sketch is None or not state.strokes:
            self._sketch = ctx.rng.choice(sketches()[state.answer])
        drawn = len(state.strokes)
        if drawn >= len(self._sketch):
            await asyncio.Event().wait()  # Until the round ends and this is cancelled.
        return Decision(
            action=_act(Draw(points=self._sketch[drawn])),
            details={"stroke": drawn + 1, "of": len(self._sketch)},
        )

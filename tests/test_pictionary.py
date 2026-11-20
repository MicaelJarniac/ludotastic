"""Pictionary: rules, scoring, AI guessers and the board."""

__all__: tuple[str, ...] = ()

import asyncio
import io
import random
import re
from typing import ClassVar, override

import pytest
from nicegui.testing import User
from PIL import Image
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.decision import (
    ChoiceAnswer,
    ChoiceQuestion,
    DecisionModel,
    DecisionModelSettings,
    DecisionRequest,
    DecisionResponse,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from ludotastic import ActionMode, AIPlayer, HumanPlayer, Match, TurnContext, View
from ludotastic.games.pictionary import (
    Draw,
    Finish,
    Pictionary,
    PictionaryAction,
    PictionaryState,
    Rank,
)
from ludotastic.games.pictionary.game import (
    MAX_STROKES,
    WORDS,
    WORDS_PER_ROUND,
    ShapesBot,
)
from ludotastic.games.pictionary.ui import Sketchpad
from ludotastic.service import AISpec, BotSpec, HumanSpec, MatchService


def act(action: Draw | Finish | Rank) -> PictionaryAction:
    return PictionaryAction(root=action)


def fresh(players: int = 3, seed: int = 0) -> tuple[Pictionary, PictionaryState]:
    game = Pictionary(players)
    return game, game.initial_state(random.Random(seed))


def test_rounds_pick_words_and_an_answer() -> None:
    game, state = fresh()
    assert len(state.words) == WORDS_PER_ROUND
    assert len(set(state.words)) == WORDS_PER_ROUND
    assert set(state.words) <= set(WORDS)
    assert state.answer in state.words
    assert state.rankings == (None, None, None)
    assert game.to_move(state) == (0, 1, 2)
    assert fresh(seed=0)[1] == state
    assert len(set(WORDS)) == len(WORDS)


def test_drawer_and_guessers_have_their_own_actions() -> None:
    game, state = fresh()
    stroke = act(Draw(points=((1, 2), (3, 4))))
    order = act(Rank(order=tuple(reversed(state.words))))
    assert game.is_legal(state, 0, stroke)
    assert game.is_legal(state, 0, act(Finish()))
    assert not game.is_legal(state, 1, stroke)
    assert not game.is_legal(state, 1, act(Finish()))
    assert game.is_legal(state, 1, order)
    assert not game.is_legal(state, 0, order)
    assert not game.is_legal(state, 1, act(Rank(order=state.words[1:])))
    assert not game.is_legal(state, 1, act(Rank(order=(*state.words[1:], "zebra"))))
    # The listed actions are a few useful ones, all legal.
    assert all(game.is_legal(state, 0, a) for a in game.legal_actions(state, 0))
    assert act(Finish()) not in game.legal_actions(state, 0)
    tops = game.legal_actions(state, 1)
    assert [a.root.order[0] for a in tops] == list(state.words)  # type: ignore[ty:unresolved-attribute]
    drawn = game.apply(state, {0: stroke}, random.Random())
    assert act(Finish()) in game.legal_actions(drawn, 0)
    full = state.model_copy(update={"strokes": (((0, 0),),) * MAX_STROKES})
    assert not game.is_legal(full, 0, stroke)


def test_strokes_are_clamped_and_thinned() -> None:
    game, state = fresh()
    long = tuple((i % 256, 10) for i in range(500))
    state = game.apply(state, {0: act(Draw(points=long))}, random.Random())
    (stroke,) = state.strokes
    assert len(stroke) == 200
    assert stroke[0] == (0, 10)
    assert stroke[-1] == long[-1]


def test_scoring_follows_the_answer_over_time() -> None:
    game, state = fresh()
    rng = random.Random()
    on_top = (state.answer, *(w for w in state.words if w != state.answer))
    second = (on_top[1], state.answer, *on_top[2:])
    state = game.apply(state, {1: act(Rank(order=on_top))}, rng)
    state = game.clock(state, 10.0)
    state = game.apply(state, {1: act(Rank(order=second))}, rng)
    state = game.apply(state, {2: act(Rank(order=second))}, rng)
    state = game.clock(state, 20.0)
    assert state.credit == (0.0, 10.0 + 5.0, 5.0)
    assert state.score(1) == pytest.approx(0.75)
    assert state.score(2) == pytest.approx(0.25)
    assert state.rank_of_answer(2) == 2
    assert game.outcome(state) is None
    state = game.apply(state, {0: act(Finish())}, rng)
    assert state.over
    assert game.to_move(state) == ()
    assert game.legal_actions(state, 1) == ()
    assert not game.is_legal(state, 1, act(Rank(order=on_top)))
    assert game.clock(state, 30.0) is state
    outcome = game.outcome(state)
    assert outcome is not None
    assert outcome.scores == pytest.approx((0.5, 0.75, 0.25))
    assert outcome.reason == f"The drawer finished; the word was {state.answer}"


def test_time_running_out_ends_the_round() -> None:
    game, state = fresh()
    assert state.score(1) == 0.0
    assert state.remaining == 60.0
    state = game.clock(state, 100.0)
    assert state.elapsed == 60.0
    assert state.remaining == 0.0
    outcome = game.outcome(state)
    assert outcome is not None
    assert outcome.scores == (0.0, 0.0, 0.0)
    assert outcome.reason.startswith("Time ran out")
    # Going back in time never takes credit away.
    _, early = fresh()
    assert game.clock(game.clock(early, 5.0), 4.0).elapsed == 4.0


def test_turn_limit_ends_the_round_with_the_scores_so_far() -> None:
    game, state = fresh(2)
    state = game.clock(state, 1.0)
    outcome = game.out_of_turns(state)
    assert outcome.reason.startswith("The drawer finished")


def test_what_players_are_told() -> None:
    game, state = fresh()
    state = game.apply(
        state,
        {
            0: act(Draw(points=((1, 1),))),
            1: act(Rank(order=state.words)),
        },
        random.Random(),
    )
    drawer = game.observe(state, 0)
    assert f"You are drawing: {state.answer}." in drawer
    assert "1 stroke so far" in drawer
    guesser = game.observe(state, 1)
    assert "You are drawing" not in guesser
    assert f"Your current order: {', '.join(state.words)}." in guesser
    assert "Your current order" not in game.observe(state, 2)
    assert state.answer not in game.brief(state, 1).split("It is one of")[0]
    assert game.rank_options(state, 1) == {w: w for w in state.words}
    with pytest.raises(ValueError, match="drawer draws"):
        game.rank_options(state, 0)
    assert game.rank_question(state, 1).startswith("Which of these words")
    assert game.rank_action(state, 1, list(state.words)) == act(Rank(order=state.words))
    assert game.player_label(0) == "Drawer"
    assert game.player_label(2) == "Guesser 2"
    assert game.info.views[0] is View.IMAGE


def test_guessers_are_asked_again_only_on_new_strokes() -> None:
    game, state = fresh()
    key = game.view_key(state, 1)
    assert game.view_key(game.clock(state, 5.0), 1) == key
    drawn = game.apply(state, {0: act(Draw(points=((1, 1),)))}, random.Random())
    assert game.view_key(drawn, 1) != key
    # The drawer's view changes with the time.
    assert game.view_key(game.clock(state, 0.5), 0) != game.view_key(state, 0)


def test_descriptions() -> None:
    game = Pictionary(2)
    assert game.describe_action(act(Draw(points=((1, 1), (2, 2))))) == (
        "Drew a stroke (2 points)"
    )
    assert game.describe_action(act(Finish())) == "Finished the drawing"
    assert game.describe_action(act(Rank(order=("a", "b")))) == "Reordered the words"


def test_snapshot_draws_the_strokes() -> None:
    game, state = fresh()
    blank = Image.open(io.BytesIO(game.snapshot(state, 1)))
    assert blank.size == (512, 512)
    assert blank.getextrema() == ((255, 255), (255, 255), (255, 255))
    state = game.apply(
        state, {0: act(Draw(points=((10, 10), (200, 10))))}, random.Random()
    )
    state = game.apply(state, {0: act(Draw(points=((100, 100),)))}, random.Random())
    image = Image.open(io.BytesIO(game.snapshot(state, 1))).convert("L")
    assert image.getpixel((200, 20)) == 0
    assert image.getpixel((200, 200)) == 0
    assert image.getpixel((400, 400)) == 255


def quick_drawer() -> ShapesBot:
    """The shapes bot, drawing twenty times as fast: the round lasts a quarter second."""
    bot = ShapesBot()
    bot.min_interval = 0.05
    return bot


def ctx(game: Pictionary, state: PictionaryState, seat: int) -> TurnContext:
    return TurnContext(
        game=game,
        state=state,
        player=seat,
        legal_actions=game.legal_actions(state, seat),
        turn=0,
        rng=random.Random(0),
    )


async def test_shapes_bot_draws_then_finishes_and_guesses_at_random() -> None:
    game, state = fresh()
    bot = game.bots()["shapes"]()
    assert isinstance(bot, ShapesBot)
    rng = random.Random()
    for _ in range(4):
        decision = await bot.act(ctx(game, state, 0))
        assert isinstance(decision.action.root, Draw)  # type: ignore[ty:unresolved-attribute]
        state = game.apply(state, {0: decision.action}, rng)  # type: ignore[ty:invalid-argument-type]
    decision = await bot.act(ctx(game, state, 0))
    assert decision.action == act(Finish())
    guess = await bot.act(ctx(game, state, 1))
    assert game.is_legal(state, 1, guess.action)  # type: ignore[ty:invalid-argument-type]


class Seer(DecisionModel):
    """A decision model that always sees the answer, and says how sure it is."""

    supports_image_input: ClassVar[bool] = True

    def __init__(self, answer: str) -> None:
        super().__init__()
        self.answer = answer
        self.images = 0

    @property
    @override
    def model_name(self) -> str:
        return "seer"

    @property
    @override
    def system(self) -> str:
        return "fake"

    @override
    async def decide(
        self, request: DecisionRequest, model_settings: DecisionModelSettings
    ) -> DecisionResponse:
        self.images += len(request.images)
        ((name, question),) = request.questions.items()
        assert isinstance(question, ChoiceQuestion)
        labels = list(question.criteria)
        rest = (1 - 0.8) / (len(labels) - 1)
        probabilities = {w: 0.8 if w == self.answer else rest for w in labels}
        return DecisionResponse(
            answers={
                name: ChoiceAnswer(
                    choice=self.answer, confidence=0.8, probabilities=probabilities
                )
            },
            model_name="seer",
        )


async def test_jev_like_guessers_rank_from_the_picture() -> None:
    game, state = fresh(2)
    seer = Seer(state.answer)
    player = AIPlayer(seer, mode=ActionMode.RANK, view=View.IMAGE)
    match = Match(game, [quick_drawer(), player], seed=0)
    outcome = await match.run()
    assert outcome is not None
    assert seer.images >= 1
    guesses = [m for m in match.history if m.seat == 1]
    assert guesses
    assert all(m.action["order"][0] == match.state.answer for m in guesses)  # type: ignore[ty:unresolved-attribute]
    answer = match.state.answer  # type: ignore[ty:unresolved-attribute]
    assert guesses[0].details["probabilities"]["response"][answer] == 0.8
    # On top from its first answer, so only the moment before it costs anything.
    assert outcome.scores[1] > 0.5
    assert outcome.scores[0] == outcome.scores[1]


def language_model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    tool = info.output_tools[0]
    words = re.findall(r"^- (.+?): ", tool.description or "", re.MULTILINE)
    return ModelResponse(parts=[ToolCallPart(tool.name, {"order": sorted(words)})])


async def test_language_model_guessers_write_their_order() -> None:
    game = Pictionary(2)
    player = AIPlayer(
        FunctionModel(language_model), mode=ActionMode.RANK, view=View.IMAGE
    )
    match = Match(game, [quick_drawer(), player], seed=0)
    await match.run()
    guesses = [m for m in match.history if m.seat == 1]
    assert guesses
    assert guesses[0].action["order"] == sorted(match.state.words)  # type: ignore[ty:unresolved-attribute]


async def test_people_draw_and_rank_live() -> None:
    game = Pictionary(2)
    drawer, guesser = HumanPlayer("d"), HumanPlayer("g")
    match = Match(game, [drawer, guesser], seed=0)
    task = match.start()
    await asyncio.sleep(0.01)
    state = match.state
    assert isinstance(state, PictionaryState)
    match.submit(0, {"kind": "draw", "points": [[1, 1], [5, 5]]})
    match.submit(1, {"kind": "rank", "order": list(reversed(state.words))})
    match.submit(0, {"kind": "finish"})
    outcome = await task
    assert outcome is not None
    assert len(match.state.strokes) == 1  # type: ignore[ty:unresolved-attribute]
    assert [m.seat for m in match.history] == [0, 1, 0]


async def until(condition: object, tries: int = 200) -> None:
    for _ in range(tries):
        if callable(condition) and condition():
            return
        await asyncio.sleep(0.01)
    msg = "condition never held"
    raise AssertionError(msg)


async def test_drawing_on_the_board(user: User, service: MatchService) -> None:
    match = service.create("pictionary", [HumanSpec(), BotSpec(bot="shapes")])
    await user.open(f"/match/{match.id}")
    state = match.state
    assert isinstance(state, PictionaryState)
    await user.should_see(f"Draw: {state.answer}")
    await user.should_see("Guesser 1")
    # Nobody but the drawer is a person, so the guesser's list is on show.
    await user.should_see(marker="ranking-1")
    pad = user.find(Sketchpad)
    pad.trigger("stroke", [[1, 1], [50, 60], [300, 300]])
    pad.trigger("stroke", [])
    await until(lambda: len(match.state.strokes) == 1)  # type: ignore[ty:unresolved-attribute]
    assert match.state.strokes[0][-1] == (255, 255)  # type: ignore[ty:unresolved-attribute]
    await user.should_see(marker="finish")
    user.find(marker="finish").click()
    await until(lambda: match.done)
    await user.should_see(f"It was: {state.answer}")
    await user.should_see("Time!")


async def test_ranking_on_the_board(user: User, service: MatchService) -> None:
    match = service.create(
        "pictionary",
        [BotSpec(bot="shapes"), HumanSpec(), BotSpec(bot="shapes")],
    )
    await user.open(f"/match/{match.id}")
    state = match.state
    assert isinstance(state, PictionaryState)
    await user.should_see("Drag to reorder, or click to put on top")
    await user.should_see(f"{len(state.words)} words")
    # Another guesser's order stays hidden from a person guessing.
    await user.should_see("Hidden until the round ends")
    last = state.words[-1]
    user.find(marker=f"word-1-{last}").click()
    await until(lambda: match.state.rankings[1] is not None)  # type: ignore[ty:unresolved-attribute]
    assert match.state.rankings[1][0] == last  # type: ignore[ty:unresolved-attribute]
    column = user.find(marker="ranking-1").elements.pop()
    items = list(column)
    user.find(marker="ranking-1").trigger(
        "sortend",
        {
            "item_id": items[0].id,
            "from_id": column.id,
            "to_id": column.id,
            "old_index": 0,
            "new_index": 2,
        },
    )
    await until(lambda: match.state.rankings[1][2] == last)  # type: ignore[ty:unresolved-attribute]
    match.abort()


async def test_decision_model_probabilities_are_shown(
    user: User, service: MatchService
) -> None:
    match = service.create(
        "pictionary",
        [HumanSpec(), AISpec(model="test", mode=ActionMode.RANK)],
        start=False,
    )
    state = match.state
    assert isinstance(state, PictionaryState)
    seer = Seer(state.answer)
    match.players[1] = AIPlayer(seer, mode=ActionMode.RANK, view=View.IMAGE)
    match.start()
    await user.open(f"/match/{match.id}")
    await until(lambda: any(m.seat == 1 for m in match.history))
    await user.should_see("80%")
    match.abort()
    await user.should_see(f"It was: {state.answer}")


async def test_late_input_is_ignored(user: User, service: MatchService) -> None:
    match = service.create("pictionary", [HumanSpec(), BotSpec(bot="shapes")])
    await user.open(f"/match/{match.id}")
    await user.should_see(marker="pad")
    match.abort()
    await user.should_see("Time!")
    user.find(Sketchpad).trigger("stroke", [[1, 1]])
    assert match.state.strokes == ()  # type: ignore[ty:unresolved-attribute]


def test_only_guessers_can_be_ai(service: MatchService) -> None:
    assert Pictionary(3).ai_seats() == {1, 2}
    with pytest.raises(ValueError, match="An AI cannot play Drawer"):
        service.create(
            "pictionary",
            [AISpec(model="test", mode=ActionMode.RANK), BotSpec(bot="shapes")],
        )


def test_every_word_has_real_sketches() -> None:
    from ludotastic.games.pictionary.game import sketches  # noqa: PLC0415

    bundle = sketches()
    assert set(bundle) == set(WORDS)
    for drawn in bundle.values():
        assert len(drawn) >= 3
        for sketch in drawn:
            assert 0 < len(sketch) <= 20
            assert all(0 <= v <= 255 for stroke in sketch for p in stroke for v in p)


async def test_replay_bot_redraws_a_sketch_then_finishes() -> None:
    from ludotastic.games.pictionary.game import ReplayBot, sketches  # noqa: PLC0415

    game, state = fresh()
    bot = game.bots()["quickdraw"]()
    assert isinstance(bot, ReplayBot)
    rng = random.Random()
    drawn: list[object] = []
    while True:
        task = asyncio.create_task(bot.act(ctx(game, state, 0)))
        done, _ = await asyncio.wait({task}, timeout=0.05)
        if not done:
            # All drawn: it waits for the round to end, instead of finishing.
            task.cancel()
            break
        decision = task.result()
        drawn.append(decision.action.root.points)  # type: ignore[ty:unresolved-attribute]
        assert decision.details["stroke"] == len(drawn)
        state = game.apply(state, {0: decision.action}, rng)  # type: ignore[ty:invalid-argument-type]
    assert tuple(drawn) in sketches()[state.answer]
    with pytest.raises(ValueError, match="only draws"):
        await bot.act(ctx(game, state, 1))


async def test_replay_bot_starts_over_each_round() -> None:
    from ludotastic.games.pictionary.game import ReplayBot  # noqa: PLC0415

    game, state = fresh()
    bot = ReplayBot()
    first = await bot.act(ctx(game, state, 0))
    _, other = fresh(seed=1)
    second = await bot.act(ctx(game, other, 0))
    assert first.details["of"] >= 1
    assert second.details["stroke"] == 1


async def test_jev_against_a_replayed_sketch() -> None:
    from ludotastic.games.pictionary.game import ReplayBot  # noqa: PLC0415

    game, state = fresh(2)
    drawer = ReplayBot()
    drawer.min_interval = 0.01
    seer = Seer(state.answer)
    guesser = AIPlayer(seer, mode=ActionMode.RANK, view=View.IMAGE)
    match = Match(game, [drawer, guesser], seed=0)
    match.state = match.state.model_copy(update={"limit": 0.5})
    outcome = await match.run()
    assert outcome is not None
    assert outcome.reason.startswith("Time ran out")
    # One picture per guess: asked again after every stroke.
    assert seer.images == len([m for m in match.history if m.seat == 1])

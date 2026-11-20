"""AI players, using Pydantic AI's test and function models."""

__all__: tuple[str, ...] = ()

import json
import random
from collections.abc import Sequence
from typing import ClassVar, override

import pytest
from pydantic import BaseModel
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    ToolCallPart,
)
from pydantic_ai.models.decision import (
    ChoiceAnswer,
    ChoiceQuestion,
    DecisionModel,
    DecisionModelSettings,
    DecisionRequest,
    DecisionResponse,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from ludotastic import ActionMode, AIPlayer, TurnContext
from ludotastic.games.tictactoe import Place, TicTacToe, TicTacToeState


def ctx(board: str = ".........") -> TurnContext:
    game = TicTacToe()
    state = TicTacToeState(
        board=tuple(None if c == "." else c for c in board)  # type: ignore[ty:invalid-argument-type]
    )
    (player,) = game.to_move(state)
    return TurnContext(
        game=game,
        state=state,
        player=player,
        legal_actions=game.legal_actions(state, player),
        turn=0,
        rng=random.Random(0),
    )


async def test_choice_mode_picks_a_described_move() -> None:
    seen: dict[str, object] = {}

    def pick(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        tool = info.output_tools[0]
        seen["schema"] = tool.parameters_json_schema
        seen["instructions"] = info.instructions
        return ModelResponse(parts=[ToolCallPart(tool.name, {"response": "4"})])

    player = AIPlayer(
        FunctionModel(pick), mode=ActionMode.CHOICE, instructions="Be bold."
    )
    decision = await player.act(ctx())
    assert decision.action == Place(cell=4)
    assert decision.details["mode"] == "choice"
    assert decision.usage is not None
    assert decision.usage.requests == 1
    assert decision.usage.tokens > 0
    options = seen["schema"]["properties"]["response"]["anyOf"]  # type: ignore[ty:not-subscriptable]
    assert {
        "const": "4",
        "description": "Place your mark in the center (cell 4).",
    } in options
    assert "Be bold." in str(seen["instructions"])
    assert "Rules:" in str(seen["instructions"])


async def test_structured_mode_retries_illegal_moves() -> None:
    calls: list[int] = []

    def play(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls.append(1)
        cell = 0 if len(messages) == 1 else 1
        return ModelResponse(
            parts=[ToolCallPart(info.output_tools[0].name, json.dumps({"cell": cell}))]
        )

    player = AIPlayer(
        FunctionModel(play), mode=ActionMode.STRUCTURED, include_rules=False
    )
    decision = await player.act(ctx("X...O...."))
    assert decision.action == Place(cell=1)
    assert len(calls) == 2


async def test_tools_mode_offers_engine_tools() -> None:
    player = AIPlayer(TestModel(), mode=ActionMode.TOOLS)
    decision = await player.act(ctx())
    assert decision.action == Place(cell=0)
    assert decision.usage is not None
    assert decision.usage.requests == 2


async def test_default_mode_and_label() -> None:
    player = AIPlayer("test")
    assert player.name == "test"
    assert player.label == "ai:test"
    assert AIPlayer("test", mode=ActionMode.TOOLS).label == "ai:test/tools"
    decision = await player.act(ctx())
    assert decision.details["mode"] == "choice"


async def test_provider_details_are_reported() -> None:
    def pick(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(
            parts=[ToolCallPart(info.output_tools[0].name, {"response": "0"})],
            provider_details={"confidence": {"response": 0.9}, "other": 1},
        )

    decision = await AIPlayer(FunctionModel(pick), fallback_model="test").act(ctx())
    assert decision.details["confidence"] == {"response": 0.9}
    assert "other" not in decision.details


async def test_forced_move_skips_the_model() -> None:
    def never(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise AssertionError  # pragma: no cover

    decision = await AIPlayer(FunctionModel(never)).act(ctx("XOXXOOOX."))
    assert decision.action == Place(cell=8)
    assert decision.details == {"mode": "choice", "forced": True}


def test_unknown_model() -> None:
    with pytest.raises(Exception, match="Unknown model"):
        AIPlayer("nope:nothing")


class FakeDecider(DecisionModel):
    """A decision model that never gets as far as deciding."""

    @property
    @override
    def model_name(self) -> str:
        return "fake-decider"

    @property
    @override
    def system(self) -> str:
        return "fake"

    @override
    async def decide(
        self,
        request: DecisionRequest,
        model_settings: DecisionModelSettings,
    ) -> DecisionResponse:  # pragma: no cover
        raise AssertionError


@pytest.mark.parametrize("mode", [ActionMode.STRUCTURED, ActionMode.TOOLS])
def test_decision_models_need_choice_mode(mode: ActionMode) -> None:
    with pytest.raises(ValueError, match="decision model"):
        AIPlayer(FakeDecider(), mode=mode)
    assert AIPlayer(FakeDecider(), mode=mode, fallback_model="test").mode is mode
    assert AIPlayer(FakeDecider(), mode=ActionMode.CHOICE).mode is ActionMode.CHOICE
    assert AIPlayer(FakeDecider()).mode is None


class Guess(BaseModel):
    """An order of the words, likeliest first."""

    order: tuple[str, ...]


class Guessing(TicTacToe):
    """Tic-Tac-Toe, but you rank three words instead."""

    info = TicTacToe.info.model_copy(update={"action_modes": (ActionMode.RANK,)})

    @override
    def rank_options(self, state: object, player: int) -> dict[str, str]:
        return {"cat": "A cat", "dog": "A dog", "1": "The number one"}

    @override
    def rank_action(self, state: object, player: int, order: Sequence[str]) -> Guess:  # type: ignore[ty:invalid-method-override]
        return Guess(order=tuple(order))

    @override
    def rank_question(self, state: object, player: int) -> str:
        return "Which word is it?"


def guess_ctx() -> TurnContext:
    base = ctx()
    return TurnContext(
        game=Guessing(),
        state=base.state,
        player=base.player,
        legal_actions=base.legal_actions,
        turn=0,
        rng=random.Random(0),
    )


class Ranker(DecisionModel):
    """A decision model that likes dogs, then the number one."""

    supports_image_input: ClassVar[bool] = True

    def __init__(self, pick: str = "dog") -> None:
        super().__init__()
        self.pick = pick
        self.questions: list[ChoiceQuestion] = []

    @property
    @override
    def model_name(self) -> str:
        return "ranker"

    @property
    @override
    def system(self) -> str:
        return "fake"

    @override
    async def decide(
        self,
        request: DecisionRequest,
        model_settings: DecisionModelSettings,
    ) -> DecisionResponse:
        (name, question), *_ = request.questions.items()
        assert isinstance(question, ChoiceQuestion)
        self.questions.append(question)
        probabilities = {"dog": 0.6, "1": 0.3, "cat": 0.1}
        return DecisionResponse(
            answers={
                name: ChoiceAnswer(
                    choice=self.pick, confidence=0.6, probabilities=probabilities
                )
            },
            model_name="ranker",
        )


async def test_decision_models_rank_by_their_probabilities() -> None:
    model = Ranker()
    player = AIPlayer(model, mode=ActionMode.RANK)
    assert player.min_interval == 0.0
    decision = await player.act(guess_ctx())
    assert decision.action == Guess(order=("dog", "1", "cat"))
    assert decision.details["order"] == ["dog", "1", "cat"]
    assert decision.details["mode"] == "rank"
    assert decision.details["probabilities"]["response"]["dog"] == 0.6
    (question,) = model.questions
    assert question.criteria == {"cat": "A cat", "dog": "A dog", "1": "The number one"}
    assert isinstance(question.instructions, dict)
    assert question.instructions["question"] == "Which word is it?"


async def test_a_decision_models_pick_always_leads() -> None:
    decision = await AIPlayer(Ranker("cat"), mode=ActionMode.RANK).act(guess_ctx())
    assert decision.action == Guess(order=("cat", "dog", "1"))


async def test_language_models_write_the_order_out() -> None:
    answers = iter(
        [
            {"order": ["cat", "cat", "bird"]},
            {"order": ["1", "cat", "dog"]},
        ]
    )
    seen: list[str] = []

    def rank(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        tool = info.output_tools[0]
        seen.append(tool.description or "")
        if len(messages) > 1:
            seen.append(str(messages[-1].parts[0].content))  # type: ignore[ty:unresolved-attribute]
        return ModelResponse(parts=[ToolCallPart(tool.name, next(answers))])

    player = AIPlayer(FunctionModel(rank), mode=ActionMode.RANK)
    assert player.min_interval == 2.0
    decision = await player.act(guess_ctx())
    assert decision.action == Guess(order=("1", "cat", "dog"))
    assert decision.usage is not None
    assert decision.usage.requests == 2
    assert "Which word is it?" in seen[0]
    assert "- 1: The number one" in seen[0]
    assert "missing: 1, dog" in seen[2]
    assert "not options: bird" in seen[2]
    assert "twice" in seen[2]


def test_decision_models_may_rank() -> None:
    assert AIPlayer(FakeDecider(), mode=ActionMode.RANK).mode is ActionMode.RANK
    assert AIPlayer("test", min_interval=0.5).min_interval == 0.5

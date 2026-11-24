"""Blackjack rules, hidden information and replay."""

__all__: tuple[str, ...] = ()

import random

import pytest
from nicegui import ui
from nicegui.testing import User

from ludotastic.api import MatchView
from ludotastic.games.blackjack import Blackjack
from ludotastic.games.blackjack.game import BlackjackState, Move, hand_total
from ludotastic.service import BotSpec, HumanSpec, MatchService, replay


@pytest.mark.parametrize(
    ("cards", "total"),
    [
        (("A♠", "A♦", "9♣"), 21),
        (("A♠", "A♦", "9♣", "K♥"), 21),
        (("K♥", "Q♦", "J♣"), 30),
    ],
)
def test_hand_total(cards: tuple[str, ...], total: int) -> None:
    assert hand_total(cards) == total


def test_opening_hands() -> None:
    game = Blackjack()
    naturals = set()
    for seed in (0, 4, 7, 3132):
        state = game.initial_state(random.Random(seed))
        assert len(set(state.deck + state.player + state.dealer)) == 52
        outcome = game.outcome(state)
        player, dealer = hand_total(state.player), hand_total(state.dealer)
        if 21 in (player, dealer):
            assert outcome is not None
            score = 0.5 if player == dealer else float(player == 21)
            assert outcome.scores == (score,)
            assert "Blackjack" in outcome.reason
            assert not game.legal_actions(state, 0)
            naturals.add(score)
        else:
            assert outcome is None
    assert naturals == {0.0, 0.5, 1.0}


def test_stand_on_soft_seventeen() -> None:
    game = Blackjack()
    state = BlackjackState(player=("10♦", "9♠"), dealer=("A♣", "6♥"), deck=("5♥",))
    end = game.apply(state, {0: Move(action="stand")}, random.Random())
    assert end.dealer == state.dealer
    assert end.deck == state.deck
    result = game.outcome(end)
    assert result is not None
    assert result.scores == (1.0,)
    assert game.out_of_turns(state).scores == (0.0,)


@pytest.mark.parametrize(
    ("card", "score", "dealer_cards"),
    [
        ("K♥", 0.0, 2),  # player busts before the dealer draws
        ("5♥", 1.0, 3),  # reaching 21 stands automatically; dealer busts
    ],
)
def test_hit_finishes(card: str, score: float, dealer_cards: int) -> None:
    game = Blackjack()
    state = BlackjackState(player=("10♦", "6♠"), dealer=("9♣", "5♦"), deck=(card, "K♠"))
    end = game.apply(state, {0: Move(action="hit")}, random.Random())
    result = game.outcome(end)
    assert result is not None
    assert result.scores == (score,)
    assert len(end.dealer) == dealer_cards


async def test_play_and_replay(user: User, service: MatchService) -> None:
    match = service.create("blackjack", [HumanSpec()], seed=2)
    await user.open(f"/match/{match.id}")
    await user.should_see("Hit")
    await user.should_not_see("Play again")
    view = MatchView.of(match)
    assert view.seed is None
    assert view.state["dealer"] == ["4♥"]
    assert view.state["deck"] == []
    await user.should_see(marker="face-down")
    await user.should_not_see(marker="card-9♦")
    await user.should_see("4 showing")
    user.find("Hit").click()
    await user.should_see(marker="card-5♣")
    await user.should_see("Total 18")
    user.find("Stand").click()
    await match.start()
    await user.should_see("Dealer wins")
    await user.should_see("Play again")
    link = user.find(marker="all-games").elements.pop()
    assert isinstance(link, ui.link)
    assert link.props["href"] == "/"
    button = user.find(marker="play-again")
    button.click()
    button.click()
    assert len(service.matches) == 2
    fresh = list(service.matches.values())[-1]
    assert fresh.id != match.id
    assert fresh.players[0].name == match.players[0].name
    assert MatchView.of(match).seed == 2
    record = service.store.get(match.id)
    assert record is not None
    restored = replay(service.games, record)
    assert restored.state == match.state
    assert restored.outcome == match.outcome
    assert restored.history == match.history
    await user.open(f"/history/{match.id}")
    await user.should_see(marker="card-9♦")
    await user.should_not_see(marker="face-down")
    await user.should_not_see("Stand")
    assert "[face down]" in Blackjack().observe(
        Blackjack().initial_state(random.Random(2)), 0
    )
    assert isinstance(restored.state, BlackjackState)
    assert "Dealer: 4♥ 9♦ 7♣ (20)" in Blackjack().observe(restored.state, 0)


async def test_play_again_after_bot_match(user: User, service: MatchService) -> None:
    match = service.create("blackjack", [BotSpec()], seed=4)
    await match.start()
    await user.open(f"/match/{match.id}")
    user.find(marker="play-again").click()
    fresh = list(service.matches.values())[-1]
    assert fresh.id != match.id
    assert [p.label for p in fresh.players] == [p.label for p in match.players]
    await fresh.start()
    service.forget_finished()  # Its players are forgotten, so link to a new match.
    await user.open(f"/history/{match.id}")
    link = user.find(marker="play-again").elements.pop()
    assert isinstance(link, ui.link)
    assert link.props["href"] == "/play/blackjack"


async def test_split_table_and_replay(user: User, service: MatchService) -> None:
    match = service.create("blackjack", [HumanSpec()], seed=60)
    await user.open(f"/match/{match.id}")
    await user.should_see("Split")
    user.find("Split").click()
    await user.should_see("Hand 1 (your turn)")
    await user.should_not_see("Split")
    user.find("Stand").click()
    await user.should_see("Hand 2 (your turn)")
    await user.should_see(marker="face-down")
    user.find("Stand").click()
    await match.start()
    await user.should_see("Hand 1: win (18) · Hand 2: loss (11) · Dealer: 17")
    record = service.store.get(match.id)
    assert record is not None
    assert record.scores == [0.5]
    assert replay(service.games, record).state == match.state


def test_split_eligibility_and_bust() -> None:
    game, rng = Blackjack(), random.Random(0)
    state = BlackjackState(
        player=("K♦", "Q♠"), dealer=("8♣", "4♦"), deck=("2♦", "3♣", "J♣", "J♠")
    )
    split = {0: Move(action="split")}
    with pytest.raises(ValueError, match="same rank"):
        game.apply(state, split, rng)
    state = game.apply(state.model_copy(update={"player": ("K♦", "K♠")}), split, rng)
    assert not state.can_split
    for active in (0, 1):
        assert state.active_hand == active
        state = game.apply(state, {0: Move(action="hit")}, rng)
    assert state.finished
    assert state.dealer == ("8♣", "4♦")
    outcome = game.outcome(state)
    assert outcome is not None
    assert outcome.scores == (0.0,)


def test_split_twenty_ones_are_not_naturals() -> None:
    game = Blackjack()
    state = BlackjackState(player=("A♦", "A♣"), dealer=("9♠", "8♠"), deck=("K♣", "J♦"))
    state = game.apply(state, {0: Move(action="split")}, random.Random())
    outcome = game.outcome(state)
    assert outcome is not None
    assert outcome.scores == (1.0,)
    assert "Blackjack!" not in outcome.reason

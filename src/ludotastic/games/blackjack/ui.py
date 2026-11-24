"""A blackjack table for the NiceGUI match page."""

__all__ = ("render",)

from typing import Final

from nicegui import ui

from ludotastic.engine.render import RenderContext
from ludotastic.games.blackjack.game import (
    Blackjack,
    BlackjackState,
    Move,
    hand_label,
    hand_total,
)

CARD: Final = "w-14 h-20 sm:w-20 sm:h-28 flex-none rounded-lg shadow-md"


def _card(card: str) -> None:
    """Draw a visible card with its rank and suit."""
    rank, suit = card[:-1], card[-1]
    ink = "text-rose-700" if suit in "♥♦" else "text-slate-900"
    with (
        ui.column()
        .classes(f"{CARD} {ink} bg-white justify-between gap-0 p-2 leading-none")
        .mark(f"card-{card}")
    ):
        ui.label(rank).classes("font-bold text-xl sm:text-2xl")
        ui.label(suit).classes("self-end text-3xl sm:text-5xl")


def _hand(name: str, cards: tuple[str, ...], *, hidden: bool = False) -> None:
    with ui.column().classes("w-full gap-2"):
        with ui.row().classes("w-full items-center justify-between"):
            ui.label(name).classes("font-semibold text-lg")
            score = hand_total(cards)
            ui.label(f"{score} showing" if hidden else f"Total {score}").classes(
                "text-sm font-medium tabular-nums"
            )
        with ui.row().classes("w-full flex-wrap gap-2"):
            for card in cards:
                _card(card)
            if hidden:
                with (
                    ui.element("div")
                    .classes(f"{CARD} p-1 bg-slate-100")
                    .props('role="img" aria-label="Face-down card"')
                    .mark("face-down")
                ):
                    ui.element("div").classes(
                        "h-full rounded border-2 border-blue-200"
                    ).style(
                        "background: repeating-linear-gradient(45deg, "
                        "#1e3a8a 0 5px, #315595 5px 7px)"
                    )


def render(ctx: RenderContext) -> None:
    """Draw only public cards and offer controls to the active human seat."""
    game, state = ctx.match.game, ctx.match.state
    assert isinstance(game, Blackjack)  # noqa: S101
    assert isinstance(state, BlackjackState)  # noqa: S101
    visible = game.public_state(state)
    with ui.column().classes("w-full max-w-2xl gap-4"):
        with ui.column().classes(
            "w-full gap-5 rounded-2xl border-4 border-emerald-950 "
            "bg-emerald-800 dark:bg-emerald-950 text-white shadow-md p-4 sm:p-6"
        ):
            _hand("Dealer", visible.dealer, hidden=not visible.finished)
            ui.separator().classes("bg-emerald-600")
            for i, hand in enumerate(visible.hands):
                _hand(hand_label(visible, i), hand)
        if ctx.can_act(0):
            buttons: list[ui.button] = []

            def play(move: Move) -> None:
                for button in buttons:
                    button.disable()
                ctx.submit(0, move)

            with ui.row().classes("w-full justify-center gap-3"):
                for move in game.legal_actions(state, 0):
                    button = (
                        ui.button(
                            game.describe_action(move),
                            icon={
                                "hit": "add",
                                "stand": "pan_tool",
                                "split": "call_split",
                            }[move.action],
                            on_click=lambda _, move=move: play(move),
                        )
                        .props("unelevated no-caps")
                        .classes("min-w-28")
                    )
                    buttons.append(button)

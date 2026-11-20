"""Pictionary in NiceGUI."""

__all__: tuple[str, ...] = (
    "Sketchpad",
    "render",
)

from collections.abc import Callable, Sequence
from typing import Any, cast

from nicegui import ui
from nicegui.element import Element
from nicegui.events import GenericEventArguments, SortableEventArguments

from ludotastic.engine.render import RenderContext
from ludotastic.games.pictionary.game import (
    Draw,
    Finish,
    PictionaryAction,
    PictionaryState,
    Rank,
    Stroke,
    simplify,
)


class Sketchpad(Element, component="sketchpad.js"):
    """A square pad that shows a drawing and, when editable, takes new strokes."""

    def __init__(
        self,
        strokes: Sequence[Stroke],
        *,
        editable: bool,
        on_stroke: Callable[[Stroke], None],
    ) -> None:
        """Show `strokes`, calling `on_stroke` with every stroke drawn."""
        super().__init__()
        self._props["strokes"] = [list(map(list, s)) for s in strokes]
        self._props["editable"] = editable

        def handle(e: GenericEventArguments) -> None:
            if e.args:
                on_stroke(simplify(e.args))

        self.on("stroke", handle)

    def show(self, strokes: Sequence[Stroke], *, editable: bool) -> None:
        """Show another drawing, or the same one grown."""
        drawn = [list(map(list, s)) for s in strokes]
        if drawn != self._props["strokes"] or editable != self._props["editable"]:
            self._props["strokes"] = drawn
            self._props["editable"] = editable
            self.update()


def _state(ctx: RenderContext) -> PictionaryState:
    state = ctx.match.state
    assert isinstance(state, PictionaryState)  # noqa: S101
    return state


def _probabilities(ctx: RenderContext, seat: int) -> dict[str, float]:
    """The latest probabilities a decision model gave for each word, if any."""
    for move in reversed(ctx.match.history):
        if move.seat == seat:
            found = move.details.get("probabilities", {}).get("response")
            return cast("dict[str, float]", found) if isinstance(found, dict) else {}
    return {}


def render(ctx: RenderContext) -> Callable[[], None]:
    """Draw the pad, a ranking list per guesser, and the clock; update in place."""
    match = ctx.match
    game = match.game
    state = _state(ctx)
    drawer = 0 in ctx.seats
    guessers = range(1, game.num_players)

    def submit(seat: int, action: Draw | Finish | Rank) -> None:
        # A stroke or a drag can land just as the round ends.
        if ctx.can_act(seat):
            ctx.submit(seat, PictionaryAction(root=action))

    with ui.column().classes("w-full items-center gap-3"):
        with ui.row().classes("w-full max-w-2xl items-center gap-3"):
            clock = ui.label().classes("text-2xl font-bold font-mono").mark("clock")
            ui.space()
            word = ui.label().classes("text-lg").mark("word")
            finish = (
                ui.button("Done", icon="check", on_click=lambda: submit(0, Finish()))
                .props("outline")
                .mark("finish")
            )
        with ui.row().classes(
            "w-full max-w-2xl gap-4 items-start no-wrap max-sm:flex-wrap"
        ):
            with ui.card().classes("p-1 grow min-w-64").props("flat bordered"):
                pad = Sketchpad(
                    state.strokes,
                    editable=drawer,
                    on_stroke=lambda stroke: submit(0, Draw(points=stroke)),
                ).mark("pad")
            lists = ui.column().classes("gap-3 w-64 max-sm:w-full")

    views: dict[int, _Ranking] = {}
    with lists:
        for seat in guessers:
            views[seat] = _Ranking(ctx, seat, submit)

    def update() -> None:
        state = _state(ctx)
        over = state.over or match.done
        clock.set_text(f"{state.remaining:0.0f}s" if not over else "Time!")
        if drawer and not over:
            word.set_text(f"Draw: {state.answer}")
        elif over:
            word.set_text(f"It was: {state.answer}")
        else:
            word.set_text(f"{len(state.words)} words")
        finish.set_visibility(drawer and not over and bool(state.strokes))
        pad.show(state.strokes, editable=drawer and not over)
        for view in views.values():
            view.update(state, over=over)

    update()
    return update


class _Ranking:
    """One guesser's list of words, likeliest first."""

    def __init__(
        self,
        ctx: RenderContext,
        seat: int,
        submit: Callable[[int, Draw | Finish | Rank], None],
    ) -> None:
        self.ctx = ctx
        self.seat = seat
        self.submit = submit
        self.mine = seat in ctx.seats
        # A person guessing must not copy anybody else's order mid-round.
        self.hidden = not self.mine and any(s != 0 for s in ctx.seats)
        self.shown: tuple[Any, ...] | None = None
        game = ctx.match.game
        with ui.card().classes("w-full p-2 gap-1").props("flat bordered"):
            with ui.row().classes("w-full items-center gap-2 no-wrap"):
                ui.label(game.player_label(seat)).classes("font-bold")
                ui.label(ctx.match.players[seat].name).classes(
                    "text-xs text-gray-500 truncate"
                )
                ui.space()
                self.score = (
                    ui.label().classes("text-sm font-mono").mark(f"score-{seat}")
                )
            if self.mine:
                ui.label("Drag to reorder, or click to put on top").classes(
                    "text-xs text-gray-500"
                )
            self.items = ui.column().classes("w-full gap-1").mark(f"ranking-{seat}")
            if self.mine:
                self.items.make_sortable(on_end=self._dragged, animation=0.1)

    def _order(self, state: PictionaryState) -> tuple[str, ...]:
        return state.rankings[self.seat] or state.words

    def _send(self, order: Sequence[str]) -> None:
        self.submit(self.seat, Rank(order=tuple(order)))

    def _dragged(self, e: SortableEventArguments) -> None:
        order = list(self._order(_state(self.ctx)))
        word = order.pop(e.old_index)
        order.insert(e.new_index, word)
        self._send(order)

    def _to_top(self, word: str) -> None:
        order = [w for w in self._order(_state(self.ctx)) if w != word]
        self._send([word, *order])

    def update(self, state: PictionaryState, *, over: bool) -> None:
        order = self._order(state)
        probabilities = _probabilities(self.ctx, self.seat)
        reveal = over or not self.hidden
        if not reveal:
            order, probabilities = (), {}
        shown = (order, tuple(sorted(probabilities.items())), over)
        rank = state.rank_of_answer(self.seat)
        self.score.set_text(
            f"{state.score(self.seat):.2f}"
            + (f" · #{rank}" if over and rank is not None else "")
        )
        if shown == self.shown:
            return
        self.shown = shown
        self.items.clear()
        with self.items:
            if not reveal:
                ui.label("Hidden until the round ends").classes(
                    "text-sm text-gray-500 italic"
                )
            for index, word in enumerate(order):
                self._item(word, index, probabilities.get(word), state, over=over)

    def _item(
        self,
        word: str,
        index: int,
        probability: float | None,
        state: PictionaryState,
        *,
        over: bool,
    ) -> None:
        right = over and word == state.answer
        color = (
            "bg-green-100 dark:bg-green-900" if right else "bg-gray-50 dark:bg-gray-800"
        )
        row = (
            ui.row()
            .classes(
                f"w-full items-center gap-2 no-wrap px-2 py-1 rounded {color} relative"
            )
            .mark(f"word-{self.seat}-{word}")
        )
        if self.mine and not over:
            row.classes("cursor-grab")
            row.on("click", lambda _, word=word: self._to_top(word))
        with row:
            if probability is not None:
                ui.element("div").classes(
                    "absolute left-0 top-0 h-full rounded opacity-60 "
                    "bg-indigo-200 dark:bg-indigo-700"
                ).style(f"width:{probability:.1%}")
            ui.label(f"{index + 1}.").classes("text-xs text-gray-500 w-5 z-10")
            ui.label(word).classes("grow z-10")
            if probability is not None:
                ui.label(f"{probability:.0%}").classes("text-xs font-mono z-10")

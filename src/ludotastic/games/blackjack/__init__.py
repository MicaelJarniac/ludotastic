"""Blackjack against an automatic dealer, with pair splitting."""

__all__: tuple[str, ...] = ("Blackjack", "BlackjackState", "Move")

from .game import Blackjack, BlackjackState, Move

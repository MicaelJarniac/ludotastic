# Blackjack: rules, splits and hidden cards

This note describes the bundled Blackjack game in
`src/ludotastic/games/blackjack/`: its rules, state and action models, what
AI models receive, and how the card table behaves.

## The game

Blackjack is played by one person against an automatic dealer, with a
standard 52-card deck shuffled from the match's seed:

- The player and the dealer get two cards each, dealt alternately. Only the
  dealer's first card is shown.
- Face cards count as 10; an ace counts as 11 when that does not go over 21,
  and as 1 otherwise.
- An opening 21 is a blackjack and ends the round at once: it wins, unless
  both hands have one, which is a push.
- On each turn, **hit** to take a card or **stand** to stop. Reaching 21
  stands automatically; going over 21 loses.
- Once the player stands, the dealer draws to 17 and stands on soft 17. The
  dealer does not draw when every player hand is bust.
- Higher total wins; equal totals push.

### Splitting

An opening pair of the same rank, such as two eights or a king and a king,
may be **split** once:

- Each card starts a hand of its own and gets one more card at once.
- The hands are played in order, each with hit and stand. Split aces may hit
  like any other hand.
- A 21 after a split is an ordinary 21, not a blackjack.
- Both hands face the same dealer, and the round scores the average of the
  two: a win and a loss score 0.5, the same as a push.

## State and actions

[`BlackjackState`][ludotastic.games.blackjack.BlackjackState] stores the
player's first hand, the second hand after a split, which of the two is being
played, the dealer's hand, the rest of the deck, and whether the round is over.

The REST API sends the state through
[`public_state`][ludotastic.Game.public_state], which leaves out the deck and
the dealer's face-down card until the round ends. Because the game hides
information, the API also withholds the match's seed until then: the seed
shuffles the deck, so it would give the cards away.

A [`Move`][ludotastic.games.blackjack.Move] holds an `action`: `hit`, `stand`,
or `split` when the opening pair allows it.

## What a human sees

The NiceGUI renderer draws a green table: the dealer's cards with one face
down and the total showing, then each player hand with its total. After a
split, the hand being played is marked `(your turn)`. Hit, Stand and, when
allowed, Split buttons sit under the table. Once the round is over, every
card is shown, the banner gives the result, and **Play again** deals a new
round to the same players.

## What an AI player receives

Blackjack supports the `choice` and `structured` action modes, with the text
view. The observation lists each hand and the dealer's visible card:

```text
Hand 1 (your turn): 8♣ Q♥ (18)
Hand 2: 8♠ 3♠ (11)
Dealer: J♠ [face down]
```

## Running it

From the repository root:

```bash
uv run ludotastic play blackjack typesafe:jev-latest --rounds 5
uv run ludotastic serve
```

In the web playground, start a Blackjack match with seed `60` to be dealt two
eights. Split them and stand on both hands: one wins and one loses against a
dealer's 17.

## Tests

`tests/test_blackjack.py` covers hand totals, opening blackjacks, the dealer
standing on soft 17, busting and automatic stands, splits and their scoring,
the hidden card and seed, replays, and the NiceGUI table.

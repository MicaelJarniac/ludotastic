# Connect Four: rules, AI views and bots

This note describes the bundled Connect Four game in
`src/ludotastic/games/connect_four/`: its rules, state and action models, what
AI models receive in text and image views, and how the `greedy` and `minimax`
bots make decisions.

## The game

Two players, **Red (`R`)** and **Yellow (`Y`)**, take turns dropping one disc
into any non-full column of a 6-row by 7-column vertical grid (columns `0` to
`6`):

- `R` (Player 1, seat `0`) always goes first.
- Gravity applies: a disc falls to the lowest empty row (`row 5` at the bottom
  up to `row 0` at the top) in the chosen column.
- The first player to align **4** of their discs horizontally, vertically, or
  diagonally wins the match.
- If all 42 cells are filled without four in a row, the match ends in a draw.

## State and actions

[`ConnectFourState`][ludotastic.games.connect_four.ConnectFourState] stores the
board as an immutable 6×7 tuple of tuples (`None`, `"R"`, or `"Y"`), ordered from
top row (`0`) to bottom row (`5`):

- `next_disc` derives whose turn it is from the disc counts (`"R"` when counts
  are equal, otherwise `"Y"`).
- [`Drop`][ludotastic.games.connect_four.Drop] validates a single integer
  `column` constrained to `0 <= column <= 6`.

## What an AI player receives

Connect Four supports all three action modes (`choice`, `structured`, `tools`)
and all three view modes (`text`, `image`, `both`):

- **Text view (`observe`)**: reports the player's color (`R` or `Y`), whose
  turn it is, an ASCII 6×7 grid with column headers `0..6` and row indices
  `0..5`, and a list of open columns along with the exact row where a disc
  would land (`3 (row 5)`).
- **Choice options (`describe_option`)**: describes each legal column along
  with its landing row (for example,
  `Drop your disc in column 3 (center, lands on row 5).`). This exposes public
  geometry (gravity) without computing tactical hints.
- **Image view (`snapshot` + `brief`)**: renders a 560×500 PNG picture of the
  blue board with red and yellow discs and column labels `0`–`6` across the top.

## Scripted bots

Connect Four provides two scripted opponents on top of the engine's `bot:random`:

| Bot | Strategy | Purpose |
|---|---|---|
| `bot:greedy` ([`GreedyBot`][ludotastic.games.connect_four.GreedyBot]) | Wins in 1 move if possible, blocks an immediate 4-in-a-row threat, otherwise prefers columns closest to center | Tactical 1-ply baseline |
| `bot:minimax` ([`MinimaxBot`][ludotastic.games.connect_four.MinimaxBot]) | Depth-limited Negamax with alpha-beta pruning, center-first move ordering, and positional window heuristics | Search-based adversary |

### Heuristic evaluation (`evaluate`)

At leaf nodes (`depth == 0`), `evaluate` scores the board from the active disc's
perspective:

- **Center column control**: `+3` per own disc and `-3` per opponent disc in
  column `3`.
- **69 four-cell windows (`LINES`)**:
  - Uncontested window with 2 own discs: `+5` (`-5` for opponent).
  - Uncontested window with 3 own discs: `+50` (`-50` for opponent).
  - Connected 4: `+10,000` (`-10,000` for opponent), adjusted by remaining
    search depth so faster wins and slower losses are preferred.

## Running experiments

From the repository root:

```bash
uv run ludotastic games
uv run ludotastic play connect-four bot:minimax bot:greedy --rounds 4
uv run ludotastic play connect-four openai:gpt-4o@choice bot:minimax --rounds 4
uv run ludotastic serve
```

## Tests

`tests/test_connect_four.py` verifies board physics, all four winning
directions, draw detection, text and PNG observations, `GreedyBot` and
`MinimaxBot` tactics, and the NiceGUI interactive grid at 100% line and branch
coverage.

# Minesweeper: rules, AI view and the field

This note describes the bundled Minesweeper game in
`src/ludotastic/games/minesweeper/`: its rules, state and action models, what
AI models receive, and how the interactive grid behaves.

## The game

Minesweeper is played by one person on a **9×13** grid with **20** mines
hidden under some of the cells:

- Cells are numbered `0` to `116`: cell `i` is row `i // 9`, column `i % 9`.
- On each turn, reveal one cell or toggle the flag on one cell.
- The first reveal is always safe: the mines are placed only then, away from
  that cell and its neighbours, so it opens an area.
- Revealing a cell that holds a mine ends the game in a loss.
- A revealed cell shows how many mines touch it, from `0` to `8`.
- A cell with no touching mines opens every connected empty cell.
- Revealing every cell without a mine wins the match.

Flagged cells cannot be revealed until they are unflagged. Flags are a memory
aid only: they do not count toward the win, which is decided by revealed cells.

## State and actions

[`MinesweeperState`][ludotastic.games.minesweeper.MinesweeperState] stores:

- `seed`: where the mines go, drawn from the match's seed, so replays place
  the same mines.
- `mines`: the mines' cell numbers, empty until the first reveal.
- `revealed`: one flag per cell, by cell number.
- `flagged`: one flag per cell, by cell number.
- `exploded`: the mine that was revealed, ending the game, if any.

The REST API sends the state through
[`public_state`][ludotastic.Game.public_state], which blanks `seed` and `mines`
until the game ends, so API clients cannot read where the mines are.

A [`Move`][ludotastic.games.minesweeper.Move] holds an `action` (`reveal` or
`flag`) and a `cell` from `0` to `116`. Action keys are unique per kind, such
as `reveal-3` and `flag-3`, so the choice options never collide.

## What a human sees

The NiceGUI renderer draws a status line and a 9×13 grid of small cells:

- Covered cells are dark; revealed cells are light and show their number, or
  stay blank when no mine touches them.
- Flagged cells are red with an `F`.
- Left-click reveals a cell; right-click toggles its flag. On touch screens,
  the **Tap to flag** switch makes a tap toggle the flag instead.
- The status line reads `Flags: n/20`, then switches to
  `Game over — you hit a mine!` on a loss or
  `You win! Every safe cell is cleared.` on a win.
- After a loss the field is shown: the mine that went off is red, the other
  mines are grey with a `*`, correct flags turn green and wrong flags amber.
- The banner above the board gives the result, such as
  `All safe cells revealed — you win!`.

## What an AI player receives

Minesweeper supports the `choice`, `structured` and `tools` action modes:

- **Text view (`observe`)**: a column header `0..8`, one line per row with the
  row number on the left, `?` for hidden cells, `F` for flags and the adjacent
  mine count (or `.` for zero) for revealed cells. The mines themselves are
  never shown.
- **Choice options (`describe_option`)**: names each move and the cell's row
  and column, for example `Reveal cell 40 (row 4, column 4).` This exposes
  public geometry without hinting at where the mines are.

The engine also has a forced-move shortcut: when only one action is legal, it
plays that action without asking the model.

## Running it

From the repository root:

```bash
uv run ludotastic games
uv run ludotastic play minesweeper typesafe:jev-latest --rounds 5
uv run ludotastic serve
```

In the web playground, open <http://127.0.0.1:8080>, create a Minesweeper
match and confirm the grid, the revealed numbers and the result at the end.

## Tests

`tests/test_minesweeper.py` covers the initial state, the safe first reveal
and mine placement, revealing numbered cells and flood-opening empty ones,
flagging and unflagging, the win and loss outcomes, the mines hidden from the
API, replays, the text observation, the descriptions and action keys, a full
match against the random bot, and the NiceGUI grid at 100% line and branch
coverage.

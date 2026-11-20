"""Download a few Quick, Draw! sketches per Pictionary word, for the replay drawer.

Run from the repository root: `uv run python scripts/fetch_quickdraw.py`. It
reads only the first part of each word's simplified data, keeps the first few
drawings Quick, Draw! recognized, and writes them to
`src/ludotastic/games/pictionary/sketches.json`.

The Quick, Draw! dataset is by Google, under CC BY 4.0:
https://github.com/googlecreativelab/quickdraw-dataset
"""

__all__: tuple[str, ...] = ()

import json
from pathlib import Path
from typing import Final
from urllib.parse import quote

import httpx2

from ludotastic.games.pictionary.game import WORDS

PER_WORD: Final = 5
"""How many sketches to keep for each word."""

MAX_STROKES: Final = 20
"""Skip sketches with more strokes than this, to keep the bundle small."""

URL: Final = (
    "https://storage.googleapis.com/quickdraw_dataset/full/simplified/{}.ndjson"
)
OUT: Final = Path("src/ludotastic/games/pictionary/sketches.json")


def fetch(client: httpx2.Client, word: str) -> list[list[list[list[int]]]]:
    """The first recognized sketches of a word, as strokes of `[x, y]` points."""
    response = client.get(URL.format(quote(word)), headers={"Range": "bytes=0-400000"})
    response.raise_for_status()
    sketches: list[list[list[list[int]]]] = []
    for line in response.text.splitlines()[:-1]:  # The last line may be cut off.
        record = json.loads(line)
        drawing = record["drawing"]
        if not record["recognized"] or len(drawing) > MAX_STROKES:
            continue
        sketches.append(
            [[list(p) for p in zip(*stroke, strict=True)] for stroke in drawing]
        )
        if len(sketches) == PER_WORD:
            break
    return sketches


def main() -> None:
    """Fetch every word and write the bundle."""
    with httpx2.Client(timeout=60) as client:
        bundle = {word: fetch(client, word) for word in WORDS}
    OUT.write_text(json.dumps(bundle, separators=(",", ":")) + "\n")
    print(f"{OUT}: {OUT.stat().st_size:,} bytes")  # noqa: T201


if __name__ == "__main__":
    main()

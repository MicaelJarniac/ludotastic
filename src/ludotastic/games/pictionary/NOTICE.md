# Third-party data

`sketches.json` holds sketches from the
[Quick, Draw! dataset](https://github.com/googlecreativelab/quickdraw-dataset)
by Google, made available under the
[Creative Commons Attribution 4.0 International license](https://creativecommons.org/licenses/by/4.0/).

Changes: only a few sketches per word are kept, and each stroke is thinned to at
most 200 points. `scripts/fetch_quickdraw.py` in the Ludotastic repository
rebuilds the file.

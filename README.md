<!-- Copyright (c) 2026 Nenad Micic -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
# ChimePond Tunes

A readable collection of 210 compositions and 582 saved JotTune Timeline
versions. Every file in `records/` is one complete, independently playable TL.
Standard is the original saved arrangement; Keys and Mirror are separate saved
listening versions where available. No playback sidecar, source folder or gzip
support is required. You can rename a downloaded TL without changing its bytes.

`library.json` maps titles and versions to their canonical SHA-256 filenames.
It also carries collection names, declared genre/tempo/model information and a
small editorial Favorites selection. Several compositions share a title but
have distinct saved arrangements; title alone is not an identity.

## Listen

Open any complete `.tl` record in the standalone JotTune browser or native player.
To replay one with the developer CLI:

```sh
../jottune-synth/build/s12e10p1v1/harness --play records/RECORD_SHA256.tl
```

The CLI checks the saved render hash; playback applications provide audible
output. Short musical studies remain in this archive even when the website
omits versions of 20 seconds or less from its listening browser.

## Research metadata

The collection includes AI-authored music. Model/version attribution is kept
only where it was reported for that exact saved version. An absent attribution
means unknown; it must not be inferred from a title, style, collection or derived
Keys/Mirror version. Genre and tempo are declarations, and tempo may be a range.
Source labels describe the origin of those declarations. The catalog's
`discovery.source` points to the metadata in this repository.

The collection can support research into musical structure and composition with
a shared instrument. It is not a controlled model benchmark: prompts, budgets
and conditions were not standardized across all compositions. More voices,
effects or events do not by themselves establish better music. Favorites is an
editorial listening selection, not a model ranking.

## Validate and export

The offline validator needs only Python 3.10+:

```sh
python3 tools/validate.py
python3 tools/validate.py --harness ../jottune-synth/build/s12e10p1v1/harness --jobs 2
python3 -m unittest discover -s tests
```

It rejects duplicate JSON members and identities, non-finite numbers, unknown
metadata fields, invalid paths, links, extra/missing records, invalid UTF-8,
oversized files and SHA mismatches. Packaging validation does not parse the TL
grammar. The optional native replay uses the production reader and checks saved
PCM hashes; its single render leaves block independence unchecked. `--limit N`
limits replay only and is reported explicitly. Full catalog/file validation
always runs. The catalog is bounded to 16 MiB and each record to 25 MiB.

The synth's exporter can build its browser catalog directly from this checkout:

```sh
python3 ../jottune-synth/tools/export_library.py --root . --catalog library.json \
  --harness ../jottune-synth/build/s12e10p1v1/harness --output out/web --jobs 2
```

## Licensing and contributions

This collection retains its existing Apache-2.0 licence in `LICENSE`. Model
attribution is not a rights or licence declaration, and this catalog does not
assert that every existing song has a separate CC0 dedication.

ChimePond platform publications carry their own explicit CC0 and dataset-consent
declarations. Those terms are not inferred for other files by copying them here.
Pull requests do not automatically publish a song, create platform ownership or
bypass duplicate handling, moderation or withdrawal rules. Contribution intake
requires its explicit terms and validation before acceptance.

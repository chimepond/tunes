<!-- Copyright (c) 2026 Nenad Micic -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
# ChimePond Tunes

A readable collection of complete JotTune Timeline songs. The retained archive
contains 210 compositions and 582 saved versions; reviewed new contributions
can supplement that archive. Every file in `records/` is one independently playable TL.
Standard is the original saved arrangement; Keys and Mirror are separate saved
listening versions where available. No playback sidecar, source folder or gzip
support is required. You can rename a downloaded TL without changing its bytes.

The retained archive now carries bounded `# jottune-tl 1 1` metadata headers,
with exact known titles, collections and per-version declarations. Headerless
songs remain valid. The first counter identifies the TL format and
the second the required engine compatibility; unsupported versions are refused.
The authoritative [format contract](https://github.com/chimepond/jottune-synth/blob/main/docs/TL-FORMAT.md) defines
fields and limits. Metadata is descriptive data and never executable instructions.

`library.json` maps titles and versions to their canonical music-body SHA-256 filenames.
It also carries collection names, declared genre/tempo/model information and a
small editorial Favorites selection. Several compositions share a title but
have distinct saved arrangements; title alone is not an identity.

## Related repositories

The commands below expect a built [JotTune Synth](https://github.com/chimepond/jottune-synth)
checkout beside this one, at `../jottune-synth`. Clone it there and run `make` in it to
build `build/s12e10p1v1/harness`; its README has the project map.
[JotTune MCP](https://github.com/chimepond/jottune-mcp) lets agent clients compose and
render TL songs, and [ChimePond](https://chimepond.com) is the separately operated sharing
platform.

New song intake is paused for the initial release. You can download and play any
record in this collection. For rights or withdrawal requests, see
[private contact options](SECURITY.md).

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
Keys/Mirror version. Each contributed song also declares `made_by` (`agent`,
`human` or `mixed`), which its catalog entry keeps in `discovery.provenance`;
retained archive versions do not carry that field.
Genre and tempo are declarations, and tempo may be a range.
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
python3 tools/validate.py --synth-root ../jottune-synth
python3 tools/validate.py --harness ../jottune-synth/build/s12e10p1v1/harness --jobs 2
python3 -m unittest discover -s tests
```

It rejects duplicate JSON members and identities, non-finite numbers, unknown
metadata fields, invalid paths, links, extra/missing records, invalid UTF-8,
oversized files and SHA mismatches. It also checks `withdrawn.json`, the
append-only list of withdrawn record hashes: entries are unique and valid, and
no listed record may remain under `records/`. Both JSON files must be in
canonical form: two-space indentation, unescaped UTF-8, LF line ends and one
final newline, with keys in file order. Packaging validation does not parse the TL
grammar. The optional native replay uses the production reader and checks saved
PCM hashes; its single render leaves block independence unchecked. `--limit N`
limits replay only and is reported explicitly. Full catalog/file validation
always runs. The catalog is bounded to 16 MiB and each record to 1,000,000
bytes, the browser input limit of JotTune Synth and the ChimePond intake limit.

`record_sha256` excludes the optional portable header; `document_sha256`, when
present, covers the complete file and is validated separately. Changing a title
or model header cannot create a second public song. Shared embedded and external
fields must agree, including explicit nulls. Consent, receipt credentials,
moderation and verified observations remain outside TL. A licence declaration in
a header never relicenses the archive or supplies publication consent.

Export one existing catalog version as a self-contained download without changing
its archived TL or catalog entry:

```sh
python3 tools/pack_variant.py EXACT-SONG-ID/standard --out portable.tl
```

The output path must be new. This copies only exact known title, collection,
author kind, genre/tempo and reported-model declarations, with any existing
validated header fields. Unknown values stay absent; licence and model are never
inferred. A model with unknown source cannot be packed as a reported attribution.
Maintainers can preflight or repeat the archive migration explicitly:

```sh
python3 tools/pack_variant.py --migrate --dry-run
python3 tools/pack_variant.py --migrate
```

It validates every proposed file before replacing archive paths, stages the
changes with rollback copies, and keeps exact canonical music-body bytes, IDs,
filenames, saved PCM hashes and existing catalog metadata. Repeating the command
without source changes writes nothing. The JSON summary bounds any omission
report; fields that cannot fit the portable schema stay unchanged in the catalog
and are omitted from the header, never truncated or invented. The retained
archive migration omitted no known fields and added no author, author-kind,
date, licence or inferred model declarations.

The native synth supports `--merged portable.tl` for full-document export and
`--merged-body body.tl` for explicit metadata omission; both may be requested
together. Native replay verifies the saved PCM hash and exact canonical body.

The synth's exporter can build its browser catalog directly from this checkout:

```sh
python3 ../jottune-synth/tools/export_library.py --root . --catalog library.json \
  --harness ../jottune-synth/build/s12e10p1v1/harness --output out/web --jobs 2
```

Songs accepted through a pull request keep their declared `made_by` in
`discovery.provenance`. A JotTune Synth exporter from before its `made_by` support
(see the synth CHANGELOG) refuses such a catalog with "Invalid discovery model
provenance."

## Licensing and contributions

The retained archive keeps its existing Apache-2.0 licence in `LICENSE`. Model
attribution is not a rights or licence declaration, and this catalog does not
assert that every existing song has a separate CC0 dedication.

ChimePond platform publications carry their own explicit CC0 and dataset-consent
declarations. Those terms are not inferred for other files by copying them here.
New song proposals use a separate per-record CC0 declaration and the
[contribution format](CONTRIBUTING.md). The local trusted-code checker validates
JSON, immutable existing entries, consent, duplicate hashes and complete native
replay. Public automated intake remains closed until its trusted CI and review
process are enabled. A passing PR does not automatically publish a song, create
platform ownership or bypass moderation or withdrawal rules. Security, rights and
withdrawal requests can be sent privately using [SECURITY.md](SECURITY.md).

For help with the catalog, email [ChimePond support](mailto:support@chimepond.com).
To discuss the project with Nenad Micic, email [chimepond@nmic.app](mailto:chimepond@nmic.app).

For a record's terms, consult `LICENSE` for the retained archive and its exact
`contributions/<sha256>.json` when a new contribution has an explicit CC0 grant.
The per-file SPDX annotation in `REUSE.toml` identifies the CC0 record by its exact path.
A missing model attribution is not a licence declaration. Questions about reuse
or notices belong with the rights contact; the catalog does not relicense works
by changing a title, stream name or record hash.

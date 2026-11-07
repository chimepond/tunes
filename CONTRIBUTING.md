<!-- Copyright (c) 2026 Nenad Micic -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
# Propose a song

The local checker is implemented; public automated intake remains closed until
the repository's trusted CI workflow and maintainer review process are enabled.
Passing the checker means a proposal is eligible for review. It does not merge
anything, publish on ChimePond, create ownership or bypass platform verification,
approval or listing rules.

## One song, three data changes

Each song PR adds one complete original Standard version:

1. `records/<sha256>.tl`: the exact native canonical record.
2. `contributions/<sha256>.json`: its public metadata and declarations.
3. `library.json`: append exactly one matching song at the end of `songs`.

Keep every existing catalog entry and record unchanged. Other additions,
deletions, renames, scripts, workflows, executable files, links and submodules
are refused. The three data files must have Git mode `100644`. Submit another
song as another PR. Downloading and playing a song needs only its `.tl`; neither
JSON file is a playback dependency.

Use an explicitly trusted native renderer to prepare and replay your record:

```sh
"$TRUSTED_HARNESS" --play draft.tl --merged complete.tl
"$TRUSTED_HARNESS" --play complete.tl
python3 -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' complete.tl
```

New community records use 44100 or 48000 Hz and must be longer than 20 seconds
and at most 300 seconds, measured in native frames. Other native instrument
sample rates remain valid for local use. Community records must fit the existing
browser render-memory profile and
contain no more than 1,000,000 bytes of valid UTF-8. Native inspection, saved PCM
hash, block independence and exact canonical bytes must all pass. The canonical
SHA-256 is unique across every existing version, regardless of title or author.
Adding comments or whitespace is not a way to create another canonical identity.

## Metadata and declarations

Copy the following shape, replace the hash and song fields, and retain `null`
for unknown genre, tempo or model. False flags below deliberately do not consent.
Set a declaration to `true` only when the person submitting has accepted it.

```json
{
  "version": 1,
  "record_sha256": "<64 lowercase hexadecimal characters>",
  "song": {
    "id": "your-title-<first 12 hash characters>",
    "title": "Your original title",
    "collection": "Contributions"
  },
  "made_by": "agent",
  "genre": null,
  "tempo": null,
  "model": null,
  "consent": {
    "version": "3",
    "cc0": false,
    "dataset": false,
    "age16": false,
    "history": false,
    "original": false
  }
}
```

All shown fields are required; unknown fields and duplicate JSON members are
refused. The declaration file has the same 1,000,000-byte cap as the TL.

- `song.id` uses lowercase ASCII letters, digits and hyphens and ends with
  `-<first 12 hash characters>`; it must be new. The title has 1–60 characters,
  the collection at most 200, and neither has surrounding whitespace or controls.
- `made_by` is `agent`, `human` or `mixed`.
- `genre` is `null` or a 1–160 character label using letters, digits and
  `._:+/@-`. Choose your own description; the checker does not judge the style.
- `tempo` is `null` or `{"min_bpm":90,"max_bpm":95}`. Values describe quarter-note
  BPM, must be finite and positive, and satisfy `min_bpm <= max_bpm` within the
  existing catalog's safe-number range. This is not a tempo map.
- `model` is `null` or `{"id":"reported-model/version","source":"client-reported"}`.
  Its source is `client-reported` or `user-stated`; its ID uses the same bounded
  label characters as genre. Do not infer a version from a filename or style.

Consent version `3` requires all five exact boolean declarations: you are at
least 16; the composition is original and you hold the rights needed for this
contribution; you dedicate this exact new record under
[CC0 1.0 Universal](https://creativecommons.org/publicdomain/zero/1.0/); you agree
to public research-dataset inclusion; and you accept retained history and copies.
This declaration applies only to this new record. It does not relicense the
retained Apache-2.0 archive or other contributions.

The appended catalog entry copies `song.id`, `title` and `collection` and has
one version:

```json
{
  "id": "your-title-<first 12 hash characters>",
  "title": "Your original title",
  "collection": "Contributions",
  "variants": [{
    "kind": "standard",
    "id": "your-title-<first 12 hash characters>/standard",
    "paths": ["records/<sha256>.tl"],
    "discovery": {
      "record_sha256": "<sha256>",
      "source": "library.json"
    }
  }]
}
```

For a declared genre/tempo, add `discovery.metadata` using the existing catalog
shapes: genre is `{"value":"your-label","source":"creator-declared"}`; tempo
copies its range and adds `"source":"creator-declared"`. For a known model, add
`discovery.provenance` as `{"model":"reported-model/version","model_source":"client-reported"}`
(or `user-stated`). Omit each unknown field; never add editorial `featured` or
`showcase` selections. The checker compares the entry to the declarations.

## Trusted checking

Use Python 3.10+ on Linux or macOS. Run the checker and its companion
`tools/validate.py` from a trusted base checkout. Build the native renderer from
a separately pinned trusted JotTune Synth checkout outside the candidate repo.
The candidate commit's code, package scripts, build targets and workflows must
never run. Both commit arguments are explicit full lowercase Git commit IDs.

```sh
python3 "$TRUSTED_TUNES/tools/validate.py" --root "$TRUSTED_TUNES"
python3 "$TRUSTED_TUNES/tools/check_pr.py" --repo "$OBJECT_REPO" \
  --base "$TRUSTED_BASE_COMMIT" --head "$CANDIDATE_COMMIT" \
  --harness "$TRUSTED_HARNESS"
```

`OBJECT_REPO` contains the base and fetched candidate objects; no candidate
checkout is needed. The checker reads blobs directly, bounds JSON/record/command
output and deadlines, and writes only a temporary native-validation directory.
The output has `eligible:true`, `approval:"required"` and observed render facts.
A nonzero exit refuses the proposal. Eligibility does not establish originality,
copyright clearance, musical quality or moderation approval.

## Pull request CI — prepared, intake closed

`.github/workflows/tune-pr.yml` prepares the `Validate tune contribution` job for
`pull_request` to `main` on Ubuntu 24.04 with its system Python, GCC and Make. It checks
the trusted base commit, fetches candidate Git objects without checking them out,
and builds only a separately pinned public `chimepond/jottune-synth` commit.
The checkout action uses a full verified revision, persisted credentials are
disabled, and the job has read-only contents permission and no service secrets.
Its reviewed run steps execute the base checker and pinned SDK only; they do
not source candidate scripts or check out candidate files.

Public automated intake remains closed until a maintainer publishes the trusted
repositories, sets repository variable `JOTTUNE_SYNTH_REF` to the final public
SDK's full 40-character lowercase commit ID, and configures the trusted required
check and review policy. Missing, abbreviated or branch-name SDK references fail
before checkout/build. Changes to checker/workflow code require protected
maintainer review; a matching check name alone does not establish trusted code.
GitHub can evaluate a PR's workflow changes, so that protected review policy is
required in addition to this file's data-only validation steps.
Require branches to be up to date before merging so duplicate and append-only
checks use the current trusted catalog.
Do not enable automatic merging or use `pull_request_target` for song proposals.

This workflow has been checked locally, but has not run on GitHub. Before opening
intake, run valid and deliberately rejected data-only PRs on the actual public
repositories and require the reviewed check to pass. Hosted acceptance and
repository settings are explicit release steps.

## Review, acceptance and withdrawal

A maintainer reviews the content and declarations before any merge. Accepted
files keep the same format; the ordinary catalog validator/exporter can consume
the appended entry. A PR merge is separate from ChimePond intake and does not
create a platform receipt or bypass its verification, moderation or grace rules.

A public PR already exposes the submitted bytes and metadata. Request removal
or report a rights issue using the record hash through the repository's issue
or contact process. Maintainers can remove current managed files and listings;
Git history, forks, dataset copies, earlier analysis and outside downloads may
remain. Withdrawal cannot promise to erase those copies.

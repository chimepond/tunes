# Changelog

## Unreleased

- Add portable v1/v1 metadata headers to all 582 retained TL versions and record
  their separate full-file checksums in `library.json`. Exact music bodies,
  filenames, song IDs, saved PCM hashes, existing declarations and archive terms
  stay unchanged; no author, date, licence or derived-version model is inferred.
- Extend `tools/pack_variant.py` with complete migration preflight, rollback,
  bounded omission reporting, dry run and byte-idempotent repeat runs.

- Link the related repositories from the README and say where the JotTune Synth
  checkout that the commands use comes from.
- Check every commit of a song proposal: at most 20 single-parent commits on the base, each holding only base or final files.
- Add the append-only `withdrawn.json`; validation keeps listed records out of `records/` and the PR check refuses listed record hashes and song IDs.
- Require the canonical byte form (two-space indentation, unescaped UTF-8, LF, final newline) for `library.json` and `withdrawn.json`; a proposal's catalog must be the trusted bytes with only the declared entry appended.
- Bound every record to the shared 1,000,000-byte browser and intake limit in validation and PR checks (was 25 MiB in validation).
- Classify pull requests with the trusted base checker: when no commit changes song data the job passes with a notice and skips the song check; every other pull request runs it. Report identity, closed-intake and refusal failures as error annotations with a short job summary, and print checker output with workflow commands stopped.
- Name existing records whose saved PCM hash equals a proposal's rendered hash in `pcm_duplicate_of` and a `Possible duplicate audio` CI warning; identity stays the canonical TL SHA-256 and a match never refuses a proposal.
- Refuse catalog titles and collections that are not NFC, that contain private-use, unassigned, line or paragraph separator or format characters, more than four combining marks in a row or no visible character, or that start or end with a blank filler. ZWNJ and ZWJ stay allowed after a virama or between letters, and ZWJ between emoji; a refusal names the code point.
- Keep a proposal's declared `made_by` in its catalog entry's `discovery.provenance`, and accept that field in validation.
- Say in the README and CONTRIBUTING.md that a catalog with contributed songs needs a JotTune Synth exporter that accepts `made_by` provenance; an older exporter refuses it.
- Name sorted, bounded candidate paths, the observed sample rate, length or render bytes and the first native error line in refusals; a timeout names its command and limit.
- Limit native inspection and replay to 120 seconds of CPU time each on POSIX hosts, in addition to the 120-second deadline.
- Add `tools/duration_table.py`, which generates the longest admissible song per sample rate and space shown in CONTRIBUTING.md and lists retained archive versions outside the current admission rules.
- Name existing songs whose folded title equals a proposal's title in `title_collision` and a `Possible title collision` CI warning; a match never refuses a proposal.
- Read Git objects through one `git cat-file --batch` process; a check over the shipped catalog starts 5 Git processes instead of about 1,200.

- Validate the complete proposed catalog and JSON types through shared filesystem/Git-object rules.
- Reject invisible Unicode formatting/control characters in display metadata.
- Bound native Linux validation address space and explain closed intake and private reporting.

- Check data-only song proposals with explicit CC0/metadata declarations and trusted native replay.
- Provide complete canonical TL records with title-based catalog identities.
- Preserve saved listening versions, declared composition metadata and Favorites.
- Validate catalog membership and optionally replay saved hashes with JotTune Synth.

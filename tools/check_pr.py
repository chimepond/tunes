#!/usr/bin/env python3
# Copyright (c) 2026 Nenad Micic
# SPDX-License-Identifier: Apache-2.0
"""Check one data-only song proposal from Git objects using trusted local code."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import selectors
import subprocess
import sys
import tempfile
import time
import unicodedata

_spec = importlib.util.spec_from_file_location("tunes_catalog", Path(__file__).with_name("validate.py"))
catalog = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(catalog)
need = catalog.need

MAX_RECORD_BYTES = catalog.MAX_RECORD_BYTES
MAX_NATIVE_OUTPUT = 1_000_000
NATIVE_TIMEOUT = 120
# Match the existing synth browser's verified-render admission in web/policy.mjs.
MAX_RENDER_BYTES = 192 * 1024 * 1024
MAX_MEMORY_BYTES = 256 * 1024 * 1024
# Match community music admission; the native instrument supports other rates.
SAMPLE_RATES = (44100, 48000)
MAX_SAFE_INTEGER = 9007199254740991
# Keep equal to the candidate fetch depth in .github/workflows/tune-pr.yml.
MAX_PROPOSAL_COMMITS = 20
MAX_COMMIT_BYTES = 64 * 1024
COMMIT = re.compile(r"[a-f0-9]{40}\Z")
CONTRIBUTION = re.compile(r"contributions/([a-f0-9]{64})\.json\Z")
# Paths that hold or change song data; case-insensitive because macOS checkouts fold case.
SONG_DATA = re.compile(r"(?:records|contributions)(?:/|\Z)|(?:library|withdrawn)\.json\Z|\.gitattributes\Z", re.IGNORECASE)
# The saved native PCM hash line of a complete TL record.
SAVED_HASH = re.compile(rb"(?m)^hash ([a-f0-9]{16})$")
CONSENT = {"version": "3", "cc0": True, "dataset": True, "age16": True, "history": True, "original": True}
# Refusals quote at most this many candidate paths and this many characters of one trusted stderr line.
MAX_LISTED = 5
MAX_QUOTED = 200
ARCHIVE_NOTE = " Older archive songs outside these rules are explained under Retained archive exceptions in CONTRIBUTING.md."
# Default-ignorable marks and joiners that change no visible letter of a title.
IGNORABLE = re.compile("[\u034f\u180b-\u180f\u200c\u200d\ufe00-\ufe0f\U000e0100-\U000e01ef]")


def run(command, *, limit=catalog.MAX_CATALOG_BYTES, timeout=30, cwd=None, memory_bytes=None, file_bytes=None,
        cpu_seconds=None, name="Trusted command"):
    """Bound both output pipes, elapsed time and, when given, CPU time; never invoke a shell."""
    def constrain_resources():
        import resource
        if memory_bytes is not None and sys.platform.startswith("linux"):
            resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
        if file_bytes is not None:
            resource.setrlimit(resource.RLIMIT_FSIZE, (file_bytes, file_bytes))
        if cpu_seconds is not None:
            # Equal soft and hard limits stop the process (SIGKILL on Linux, SIGXCPU on macOS) even if this checker dies.
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))

    # Linux enforces address space; both supported POSIX hosts bound file output and CPU time.
    # macOS does not provide a reliable RLIMIT_AS; retain inspection/time limits there.
    limited = memory_bytes is not None or file_bytes is not None or cpu_seconds is not None
    constrain = constrain_resources if os.name == "posix" and limited else None
    expired = name + " exceeded its " + format(timeout, "g") + " s time limit."
    process = subprocess.Popen(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, preexec_fn=constrain)
    result = {process.stdout: bytearray(), process.stderr: bytearray()}
    deadline = time.monotonic() + timeout
    try:
        with selectors.DefaultSelector() as selector:
            for pipe in result:
                selector.register(pipe, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                need(remaining > 0, expired)
                ready = selector.select(remaining)
                need(ready, expired)
                for key, _ in ready:
                    data = os.read(key.fileobj.fileno(), 65536)
                    if not data:
                        selector.unregister(key.fileobj)
                        continue
                    result[key.fileobj].extend(data)
                    need(sum(len(value) for value in result.values()) <= limit,
                         name + " exceeded its output limit of " + str(limit) + " bytes.")
            remaining = deadline - time.monotonic()
            need(remaining > 0, expired)
            status = process.wait(timeout=remaining)
        return status, bytes(result[process.stdout]), bytes(result[process.stderr])
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()
        process.stderr.close()


def failure(status, errors):
    """Describe a failed trusted command: exit status or signal, then its first stderr line, bounded and escaped."""
    reason = " (stopped by signal " + str(-status) if status < 0 else " (exit status " + str(status)
    line = next((row.strip() for row in errors.decode("utf8", errors="replace").splitlines() if row.strip()), "")
    return reason + ("): " + json.dumps(line[:MAX_QUOTED]) if line else ")")


def listed(names):
    """Quote sorted candidate paths for a refusal, bounded in count and length; JSON quoting shows hidden characters."""
    names = sorted(names)
    quoted = ", ".join(json.dumps(name[:MAX_QUOTED]) for name in names[:MAX_LISTED])
    return quoted + (" and " + str(len(names) - MAX_LISTED) + " more" if len(names) > MAX_LISTED else "")


class GitObjects:
    """Read Git objects with trusted local Git; one long-lived cat-file process serves every object read."""
    def __init__(self, repo):
        self.repo = Path(repo).resolve(strict=True)
        self.batch, self.pending = None, bytearray()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        if self.batch is not None:
            self.batch.kill()
            self.batch.wait()
            self.batch.stdin.close()
            self.batch.stdout.close()
            self.batch = None

    def command(self, *args, limit=catalog.MAX_CATALOG_BYTES):
        status, output, errors = run(["git", "--no-replace-objects", "--no-optional-locks", "-C", str(self.repo), *args],
                                     limit=limit, name="Git")
        need(status == 0, "Git could not read the selected trusted base or candidate objects" + failure(status, errors) + ".")
        return output

    def receive(self, ready, maximum, deadline):
        """Return the next complete unit from the cat-file pipe within its byte and time bounds."""
        with selectors.DefaultSelector() as selector:
            selector.register(self.batch.stdout, selectors.EVENT_READ)
            while not (count := ready()):
                need(len(self.pending) <= maximum, "Git exceeded its output limit of " + str(maximum) + " bytes.")
                remaining = deadline - time.monotonic()
                need(remaining > 0 and selector.select(remaining), "Git exceeded its 30 s time limit.")
                data = os.read(self.batch.stdout.fileno(), 65536)
                need(data, "Git could not read the selected trusted base or candidate objects.")
                self.pending.extend(data)
        unit = bytes(self.pending[:count])
        del self.pending[:count]
        return unit

    def read(self, identity, kind, maximum, message):
        """Read one object, refusing its type and declared size before any content is read."""
        if self.batch is None:
            self.batch = subprocess.Popen(["git", "--no-replace-objects", "--no-optional-locks", "-C", str(self.repo),
                                           "cat-file", "--batch"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                          stderr=subprocess.DEVNULL)
        try:
            self.batch.stdin.write(identity.encode("ascii") + b"\n")
            self.batch.stdin.flush()
        except BrokenPipeError:
            need(False, "Git could not read the selected trusted base or candidate objects.")
        deadline = time.monotonic() + 30
        fields = self.receive(lambda: self.pending.find(b"\n") + 1, 200, deadline).decode("ascii", errors="replace").split()
        need(len(fields) == 3 and fields[0] == identity and fields[2].isdigit(),
             "Git could not read the selected trusted base or candidate objects.")
        need(fields[1] == kind, "Select a Git " + kind + ".")
        size = int(fields[2])
        need(0 < size <= maximum, message)
        content = self.receive(lambda: size + 1 if len(self.pending) > size else 0, size + 1, deadline)
        need(content.endswith(b"\n"), "Git object size differs from its declaration.")
        return content[:-1]

    def tree(self, revision):
        need(COMMIT.fullmatch(revision or ""), "Use an explicit full lowercase Git commit ID.")
        need(self.command("cat-file", "-t", revision, limit=100) == b"commit\n", "Select a Git commit.")
        entries = {}
        for row in self.command("ls-tree", "-r", "--full-tree", "-z", revision).split(b"\0"):
            if not row:
                continue
            attributes, name = row.split(b"\t", 1)
            mode, kind, identity = attributes.decode("ascii").split()
            name = name.decode("utf8", errors="strict")
            need(name not in entries, "Duplicate Git tree path.")
            entries[name] = (mode, kind, identity)
        return entries

    def blob(self, tree, name, maximum):
        need(name in tree and tree[name][:2] == ("100644", "blob"), "Song data must be regular Git files with mode 100644: " + name)
        return self.read(tree[name][2], "blob", maximum, "Song data file is empty or exceeds " + str(maximum) + " bytes: " + name)

    def parents(self, revision):
        """Read parent IDs from the raw commit object; shallow-clone grafts would hide them from rev-list."""
        need(COMMIT.fullmatch(revision or ""), "Use an explicit full lowercase Git commit ID.")
        output = self.read(revision, "commit", MAX_COMMIT_BYTES, "Proposal commit object exceeds " + str(MAX_COMMIT_BYTES) + " bytes.")
        header = output.split(b"\n\n", 1)[0].decode("ascii", errors="replace").split("\n")
        parents = [line.removeprefix("parent ") for line in header if line.startswith("parent ")]
        need(all(COMMIT.fullmatch(parent) for parent in parents), "Invalid Git parent commit ID.")
        return parents


def proposal_commits(git, base, head):
    """Return every commit in base..head, newest first, when they form one short single-parent chain on base."""
    commits, current = [], head
    while current != base:
        need(len(commits) < MAX_PROPOSAL_COMMITS, "A proposal may hold at most " + str(MAX_PROPOSAL_COMMITS) +
             " commits on the selected base; squash it and rebase onto the current main branch.")
        parents = git.parents(current)
        need(len(parents) == 1, "Proposal commits must form one single-parent chain on the selected base; "
             "rebase onto the current main branch without merge commits.")
        commits.append(current)
        current = parents[0]
    return commits


def maintenance_only(repo, base, head):
    """Return True only when no commit in base..head changes song data, so the song check has nothing to check."""
    with GitObjects(repo) as git:
        songs = lambda revision: {name: entry for name, entry in git.tree(revision).items() if SONG_DATA.match(name)}
        baseline = songs(base)
        return all(songs(commit) == baseline for commit in proposal_commits(git, base, head))


def read_json(data):
    def integer(value):
        need(len(value.lstrip("-")) <= 16, "JSON integer exceeds the catalog safe-number range.")
        result = int(value)
        need(abs(result) <= MAX_SAFE_INTEGER, "JSON integer exceeds the catalog safe-number range.")
        return result

    def number(value):
        result = float(value)
        need(math.isfinite(result) and abs(result) <= MAX_SAFE_INTEGER, "Non-finite or out-of-range JSON number.")
        return result

    try:
        return json.loads(data.decode("utf8", errors="strict"), object_pairs_hook=catalog.unique_object,
                          parse_constant=catalog.invalid_constant, parse_int=integer, parse_float=number)
    except RecursionError as error:
        raise ValueError("JSON nesting exceeds the parser limit.") from error


def declared_song(value, digest):
    need(isinstance(value, dict) and set(value) == {
        "version", "record_sha256", "song", "made_by", "genre", "tempo", "model", "consent"}, "Invalid contribution fields.")
    need(type(value["version"]) is int and value["version"] == 1 and value["record_sha256"] == digest,
         "Contribution version or record identity differs.")
    consent = value["consent"]
    need(isinstance(consent, dict) and set(consent) == set(CONSENT) and
         consent.get("version") == CONSENT["version"] and
         all(consent.get(key) is True for key in CONSENT if key != "version"),
         "All current age, original-work, CC0, dataset and retained-history declarations are required.")
    need(value["made_by"] in ("agent", "human", "mixed"), "Invalid made_by declaration.")
    song = value["song"]
    need(isinstance(song, dict) and set(song) == {"id", "title", "collection"}, "Invalid song declarations.")
    problem = catalog.text_problem(song["title"], 60)
    need(problem is None and "\x7f" not in song["title"], "Title must contain 1 to 60 ordinary characters: it " + str(problem) + ".")
    problem = catalog.text_problem(song["collection"], 200)
    need(problem is None and "\x7f" not in song["collection"], "Invalid collection declaration: it " + str(problem) + ".")
    need(catalog.text(song["id"], 200) and song["id"].endswith("-" + digest[:12]),
         "Song ID must end in its record hash prefix: -" + digest[:12] + ".")
    discovery = {"record_sha256": digest, "source": "library.json"}
    metadata = {}
    if value["genre"] is not None:
        metadata["genre"] = {"value": value["genre"], "source": "creator-declared"}
    if value["tempo"] is not None:
        tempo = value["tempo"]
        need(isinstance(tempo, dict) and set(tempo) == {"min_bpm", "max_bpm"} and
             all(type(tempo[key]) in (int, float) for key in ("min_bpm", "max_bpm")) and
             0 < tempo["min_bpm"] <= tempo["max_bpm"] <= 9007199254740991, "Invalid declared tempo fields.")
        metadata["tempo"] = {"min_bpm": tempo["min_bpm"], "max_bpm": tempo["max_bpm"], "source": "creator-declared"}
    if metadata:
        discovery["metadata"] = metadata
    # The declared author kind is kept in the catalog, so AI-made songs stay marked after merge.
    discovery["provenance"] = {"made_by": value["made_by"]}
    if value["model"] is not None:
        model = value["model"]
        need(isinstance(model, dict) and set(model) == {"id", "source"} and
             model["source"] in ("client-reported", "user-stated"), "Invalid model declaration fields.")
        discovery["provenance"].update(model=model["id"], model_source=model["source"])
    catalog.labels(discovery)
    # Key order is the documented catalog order, so the appended entry has one accepted byte form.
    return {"id": song["id"], "title": song["title"], "collection": song["collection"],
            "variants": [{"kind": "standard", "id": song["id"] + "/standard",
            "paths": ["records/" + digest + ".tl"], "discovery": discovery}]}


def native_row(output, prefix):
    rows = [line for line in output.decode("utf8", errors="strict").splitlines() if line.startswith(prefix + " ")]
    need(len(rows) == 1, "Native renderer returned no unique " + prefix + " result.")
    return catalog.unique_object(token.split("=", 1) for token in rows[0].split()[1:])


def admission(info):
    """Apply the community sample-rate, duration and memory rules to one native INSPECT row."""
    number = lambda key: int(info[key])
    fs, frames, channels = (number(key) for key in ("fs", "frames", "channels"))
    need(fs in SAMPLE_RATES, "Community record sample rate must be 44100 or 48000 Hz; this record uses " + str(fs) + " Hz." +
         ARCHIVE_NOTE)
    need(20 * fs < frames <= 300 * fs and number("length_ns") <= 300_000_000_000,
         "New songs must be longer than 20 seconds and no longer than 300 seconds; this record lasts %.3f s "
         "(%d frames at %d Hz, length_ns %d)." % (frames / fs, frames, fs, number("length_ns")) + ARCHIVE_NOTE)
    need(channels > 0 and number("pcm_bytes") == frames * channels * 2, "Invalid native PCM size.")
    lower, pcm = number("render_bytes_lower_bound"), number("pcm_bytes")
    need(0 < lower <= MAX_RENDER_BYTES and lower + pcm * 3 <= MAX_MEMORY_BYTES,
         "Record exceeds the browser verification budget (render limit " + str(MAX_RENDER_BYTES) +
         " bytes; combined render plus three PCM buffers " + str(MAX_MEMORY_BYTES) + " bytes); this record needs " +
         str(lower) + " render bytes and " + str(lower + pcm * 3) + " combined bytes." + ARCHIVE_NOTE)
    return fs, frames, channels


def inspect_and_replay(harness, record, directory):
    limits = {"limit": MAX_NATIVE_OUTPUT, "timeout": NATIVE_TIMEOUT, "cwd": directory, "memory_bytes": MAX_MEMORY_BYTES,
              "file_bytes": MAX_RECORD_BYTES, "cpu_seconds": math.ceil(NATIVE_TIMEOUT)}
    status, output, errors = run([str(harness), "--inspect", str(record)], name="Native inspection", **limits)
    need(status == 0, "Native inspection refused the record" + failure(status, errors) + ".")
    info = native_row(output, "INSPECT")
    fs, frames, channels = admission(info)
    need(info.get("hash_present") == "1" and re.fullmatch(r"[a-f0-9]{16}", info.get("expected_hash", "")),
         "A complete record needs its saved native hash.")
    canonical = directory / "canonical.tl"
    status, output, errors = run([str(harness), "--play", str(record), "--merged", str(canonical)], name="Native replay", **limits)
    need(status == 0, "Native replay refused the record" + failure(status, errors) + ".")
    play = native_row(output, "PLAY")
    need(play.get("match") == "yes" and play.get("block_indep") == "yes" and
         play.get("hash") == play.get("expected") == info["expected_hash"],
         "Native hash or block independence differs.")
    need(canonical.is_file() and not canonical.is_symlink() and canonical.stat().st_size <= MAX_RECORD_BYTES and
         canonical.read_bytes() == record.read_bytes(), "Record is not the exact complete native canonical TL.")
    return {"fs": fs, "frames": frames, "channels": channels, "pcm_hash": play["hash"], "block_independence": "verified"}


def title_key(title):
    """Fold a title for the reviewer collision warning: NFKC, case folding, no ignorable marks, fillers as spaces."""
    folded = IGNORABLE.sub("", unicodedata.normalize("NFKC", unicodedata.normalize("NFKC", title).casefold()))
    return " ".join(folded.translate({ord(character): " " for character in catalog.FILLERS}).split())


def check(repo, base, head, harness):
    with GitObjects(repo) as git:
        return check_objects(git, base, head, harness)


def check_objects(git, base, head, harness):
    harness = Path(harness).resolve(strict=True)
    need(harness.is_file() and not harness.is_relative_to(git.repo),
         "Use a trusted native renderer outside the candidate repository.")
    baseline, candidate = git.tree(base), git.tree(head)
    # Merge and rebase merges keep intermediate commits, so each may hold only base or final head entries.
    for commit in proposal_commits(git, base, head)[1:]:
        tree = git.tree(commit)
        for name in sorted(tree.keys() | baseline.keys()):
            need(tree.get(name) in (baseline.get(name), candidate.get(name)), "Proposal commit " + commit +
                 " holds data outside the final proposal: " + json.dumps(name))
    changed = {name for name in baseline.keys() | candidate.keys() if baseline.get(name) != candidate.get(name)}
    proposals = [name for name in changed if CONTRIBUTION.fullmatch(name) and name not in baseline]
    need(len(proposals) == 1, "Each proposal must add exactly one contribution JSON file." +
         (" It adds " + listed(proposals) + "." if proposals else ""))
    declaration_path = proposals[0]
    digest = CONTRIBUTION.fullmatch(declaration_path)[1]
    old_bytes = git.blob(baseline, "library.json", catalog.MAX_CATALOG_BYTES)
    new_bytes = git.blob(candidate, "library.json", catalog.MAX_CATALOG_BYTES)
    old, new = read_json(old_bytes), read_json(new_bytes)
    need(isinstance(old, dict) and set(old) == {"version", "songs"} and type(old["version"]) is int and
         old["version"] == 1 and isinstance(old["songs"], list) and old["songs"], "Trusted base catalog is invalid.")
    need(old_bytes == catalog.canonical_json(old), "Trusted base catalog is not in canonical form.")
    existing = {item["discovery"]["record_sha256"] for song in old["songs"] for item in song["variants"]}
    need(digest not in existing, "This exact record hash already exists in the trusted library.")
    withdrawn = git.blob(baseline, "withdrawn.json", catalog.MAX_CATALOG_BYTES)
    retired, retired_songs = catalog.validate_withdrawn(read_json(withdrawn), [name for name in baseline if name.startswith("records/")])
    need(digest not in retired, "This record was withdrawn and cannot be proposed again: records/" + digest + ".tl")
    record_path = "records/" + digest + ".tl"
    expected = {"library.json", declaration_path, record_path}
    need(changed == expected and record_path not in baseline,
         "Only one new TL, its contribution JSON and an appended catalog entry may change." +
         (" Unexpected changes: " + listed(changed - expected) + "." if changed - expected else "") +
         (" Missing: " + listed(expected - changed) + "." if expected - changed else ""))
    need(isinstance(new, dict) and set(new) == {"version", "songs"} and type(new["version"]) is int and
         new["version"] == 1 and isinstance(new["songs"], list) and len(new["songs"]) == len(old["songs"]) + 1 and
         json.dumps(new["songs"][:-1], sort_keys=True, allow_nan=False) ==
         json.dumps(old["songs"], sort_keys=True, allow_nan=False),
         "Append exactly one song without changing existing catalog entries.")
    declaration = read_json(git.blob(candidate, declaration_path, MAX_RECORD_BYTES))
    song = declared_song(declaration, digest)
    need(song["id"] not in {item["id"] for item in old["songs"]}, "Song ID already exists.")
    need(song["id"] not in retired_songs, "This song ID was withdrawn and cannot be proposed again.")
    need(json.dumps(new["songs"][-1], sort_keys=True, allow_nan=False) ==
         json.dumps(song, sort_keys=True, allow_nan=False),
         "Appended catalog entry differs from its declared metadata, including JSON types.")
    need(new_bytes == catalog.canonical_json({**old, "songs": old["songs"] + [song]}),
         "library.json must hold the trusted catalog bytes with only the declared entry appended in canonical form: "
         "two-space JSON indentation, keys in the documented order, unescaped UTF-8, LF line ends and one final newline.")
    data = git.blob(candidate, record_path, MAX_RECORD_BYTES)
    data.decode("utf8", errors="strict")
    need(b"\0" not in data and hashlib.sha256(data).hexdigest() == digest, "TL bytes differ from their canonical SHA identity.")
    saved = {}

    def read_record(name):
        blob = git.blob(candidate, name, catalog.MAX_RECORD_BYTES)
        if name != record_path:
            for found in SAVED_HASH.finditer(blob):
                saved.setdefault(found[1].decode("ascii"), set()).add(name)
        return blob

    catalog.validate_catalog(new, read_record, [name for name in candidate if name.startswith("records/")])
    with tempfile.TemporaryDirectory(prefix="tunes-proposal-") as scratch:
        directory = Path(scratch)
        (directory / "records").mkdir()
        record = directory / record_path
        record.write_bytes(data)
        (directory / "library.json").write_bytes(catalog.canonical_json({"version": 1, "songs": [song]}))
        (directory / "withdrawn.json").write_bytes(withdrawn)
        catalog.validate(directory)
        observed = inspect_and_replay(harness, record, directory)
    # Identity stays the canonical TL SHA-256; equal saved PCM hashes only alert reviewers, never refuse.
    observed["pcm_duplicate_of"] = sorted(saved.get(observed["pcm_hash"], ()))
    # Equal folded titles also only alert reviewers; look-alikes across scripts are not detected.
    key = title_key(song["title"])
    observed["title_collision"] = sorted(item["id"] for item in old["songs"] if title_key(item["title"]) == key)
    return {"eligible": True, "approval": "required", "record_sha256": digest, "title": song["title"],
            "new_records": 1, "existing_records": len(existing), "declared_licence": "CC0-1.0", **observed}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True, help="trusted Git repository containing base and fetched head objects")
    parser.add_argument("--base", required=True, help="explicit trusted base commit ID")
    parser.add_argument("--head", required=True, help="candidate commit ID; its code is never executed")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--harness", type=Path, help="native renderer built from a separately pinned trusted SDK")
    mode.add_argument("--classify", action="store_true",
                      help="exit 0 only when no commit changes song data; otherwise the full song check must run")
    args = parser.parse_args()
    try:
        if args.classify:
            need(maintenance_only(args.repo, args.base, args.head), "A proposal commit changes song data.")
            print(json.dumps({"maintenance": True}))
        else:
            print(json.dumps(check(args.repo, args.base, args.head, args.harness), sort_keys=True))
    except (OSError, ValueError, KeyError, TypeError, UnicodeError, RecursionError, subprocess.SubprocessError) as error:
        parser.exit(1, ("Running the full song check: " if args.classify else "Proposal refused: ") + str(error) + "\n")


if __name__ == "__main__":
    main()

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
import tempfile
import time

_spec = importlib.util.spec_from_file_location("tunes_catalog", Path(__file__).with_name("validate.py"))
catalog = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(catalog)
need = catalog.need

MAX_RECORD_BYTES = 1_000_000
MAX_NATIVE_OUTPUT = 1_000_000
NATIVE_TIMEOUT = 120
# Match the existing synth browser's verified-render admission in web/policy.mjs.
MAX_RENDER_BYTES = 192 * 1024 * 1024
MAX_MEMORY_BYTES = 256 * 1024 * 1024
MAX_SAFE_INTEGER = 9007199254740991
COMMIT = re.compile(r"[a-f0-9]{40}\Z")
CONTRIBUTION = re.compile(r"contributions/([a-f0-9]{64})\.json\Z")
CONSENT = {"version": "3", "cc0": True, "dataset": True, "age16": True, "history": True, "original": True}


def run(command, *, limit=catalog.MAX_CATALOG_BYTES, timeout=30, cwd=None):
    """Bound both output pipes and elapsed time; never invoke a shell."""
    process = subprocess.Popen(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    result = {process.stdout: bytearray(), process.stderr: bytearray()}
    deadline = time.monotonic() + timeout
    try:
        with selectors.DefaultSelector() as selector:
            for pipe in result:
                selector.register(pipe, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                need(remaining > 0, "Trusted command exceeded its time limit.")
                ready = selector.select(remaining)
                need(ready, "Trusted command exceeded its time limit.")
                for key, _ in ready:
                    data = os.read(key.fileobj.fileno(), 65536)
                    if not data:
                        selector.unregister(key.fileobj)
                        continue
                    result[key.fileobj].extend(data)
                    need(sum(len(value) for value in result.values()) <= limit,
                         "Trusted command exceeded its output limit.")
            remaining = deadline - time.monotonic()
            need(remaining > 0, "Trusted command exceeded its time limit.")
            status = process.wait(timeout=remaining)
        return status, bytes(result[process.stdout]), bytes(result[process.stderr])
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()
        process.stderr.close()


class GitObjects:
    def __init__(self, repo):
        self.repo = Path(repo).resolve(strict=True)

    def command(self, *args, limit=catalog.MAX_CATALOG_BYTES):
        status, output, _ = run(["git", "--no-replace-objects", "--no-optional-locks", "-C", str(self.repo), *args], limit=limit)
        need(status == 0, "Git could not read the selected trusted base or candidate objects.")
        return output

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
        need(name in tree and tree[name][:2] == ("100644", "blob"), "Song data must be regular Git files with mode 100644.")
        identity = tree[name][2]
        size = int(self.command("cat-file", "-s", identity, limit=100))
        need(0 < size <= maximum, "Song data file is empty or exceeds its byte limit.")
        output = self.command("cat-file", "blob", identity, limit=maximum)
        need(len(output) == size, "Git object size differs from its declaration.")
        return output


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
    need(catalog.text(song["title"], 60) and "\x7f" not in song["title"], "Title must contain 1 to 60 ordinary characters.")
    need(catalog.text(song["collection"], 200) and "\x7f" not in song["collection"], "Invalid collection declaration.")
    need(catalog.text(song["id"], 200) and song["id"].endswith("-" + digest[:12]), "Song ID must end in its record hash prefix.")
    discovery = {"record_sha256": digest, "source": "library.json"}
    metadata = {}
    if value["genre"] is not None:
        metadata["genre"] = {"value": value["genre"], "source": "creator-declared"}
    if value["tempo"] is not None:
        tempo = value["tempo"]
        need(isinstance(tempo, dict) and set(tempo) == {"min_bpm", "max_bpm"} and
             all(type(tempo[key]) in (int, float) for key in ("min_bpm", "max_bpm")) and
             0 < tempo["min_bpm"] <= tempo["max_bpm"] <= 9007199254740991, "Invalid declared tempo fields.")
        metadata["tempo"] = {**tempo, "source": "creator-declared"}
    if metadata:
        discovery["metadata"] = metadata
    if value["model"] is not None:
        model = value["model"]
        need(isinstance(model, dict) and set(model) == {"id", "source"} and
             model["source"] in ("client-reported", "user-stated"), "Invalid model declaration fields.")
        discovery["provenance"] = {"model": model["id"], "model_source": model["source"]}
    catalog.labels(discovery)
    return {**song, "variants": [{"kind": "standard", "id": song["id"] + "/standard",
            "paths": ["records/" + digest + ".tl"], "discovery": discovery}]}


def native_row(output, prefix):
    rows = [line for line in output.decode("utf8", errors="strict").splitlines() if line.startswith(prefix + " ")]
    need(len(rows) == 1, "Native renderer returned no unique " + prefix + " result.")
    return catalog.unique_object(token.split("=", 1) for token in rows[0].split()[1:])


def inspect_and_replay(harness, record, directory):
    status, output, _ = run([str(harness), "--inspect", str(record)], limit=MAX_NATIVE_OUTPUT,
                            timeout=NATIVE_TIMEOUT, cwd=directory)
    need(status == 0, "Native inspection refused the record.")
    info = native_row(output, "INSPECT")
    number = lambda key: int(info[key])
    fs, frames, channels = (number(key) for key in ("fs", "frames", "channels"))
    need(fs > 0 and 20 * fs < frames <= 300 * fs and number("length_ns") <= 300_000_000_000,
         "New songs must be longer than 20 seconds and no longer than 300 seconds.")
    need(channels > 0 and number("pcm_bytes") == frames * channels * 2, "Invalid native PCM size.")
    lower, pcm = number("render_bytes_lower_bound"), number("pcm_bytes")
    need(0 < lower <= MAX_RENDER_BYTES and lower + pcm * 3 <= MAX_MEMORY_BYTES,
         "Record exceeds the existing browser rendering memory profile.")
    need(info.get("hash_present") == "1" and re.fullmatch(r"[a-f0-9]{16}", info.get("expected_hash", "")),
         "A complete record needs its saved native hash.")
    canonical = directory / "canonical.tl"
    status, output, _ = run([str(harness), "--play", str(record), "--merged", str(canonical)],
                            limit=MAX_NATIVE_OUTPUT, timeout=NATIVE_TIMEOUT, cwd=directory)
    need(status == 0, "Native replay refused the record.")
    play = native_row(output, "PLAY")
    need(play.get("match") == "yes" and play.get("block_indep") == "yes" and
         play.get("hash") == play.get("expected") == info["expected_hash"],
         "Native hash or block independence differs.")
    need(canonical.is_file() and not canonical.is_symlink() and canonical.stat().st_size <= MAX_RECORD_BYTES and
         canonical.read_bytes() == record.read_bytes(), "Record is not the exact complete native canonical TL.")
    return {"fs": fs, "frames": frames, "channels": channels, "pcm_hash": play["hash"], "block_independence": "verified"}


def check(repo, base, head, harness):
    git = GitObjects(repo)
    harness = Path(harness).resolve(strict=True)
    need(harness.is_file() and not harness.is_relative_to(git.repo),
         "Use a trusted native renderer outside the candidate repository.")
    baseline, candidate = git.tree(base), git.tree(head)
    changed = {name for name in baseline.keys() | candidate.keys() if baseline.get(name) != candidate.get(name)}
    proposals = [name for name in changed if CONTRIBUTION.fullmatch(name) and name not in baseline]
    need(len(proposals) == 1, "Each proposal must add exactly one contribution JSON file.")
    declaration_path = proposals[0]
    digest = CONTRIBUTION.fullmatch(declaration_path)[1]
    old = read_json(git.blob(baseline, "library.json", catalog.MAX_CATALOG_BYTES))
    new = read_json(git.blob(candidate, "library.json", catalog.MAX_CATALOG_BYTES))
    need(isinstance(old, dict) and set(old) == {"version", "songs"} and type(old["version"]) is int and
         old["version"] == 1 and isinstance(old["songs"], list) and old["songs"], "Trusted base catalog is invalid.")
    existing = {item["discovery"]["record_sha256"] for song in old["songs"] for item in song["variants"]}
    need(digest not in existing, "This exact record hash already exists in the trusted library.")
    record_path = "records/" + digest + ".tl"
    need(changed == {"library.json", declaration_path, record_path} and record_path not in baseline,
         "Only one new TL, its contribution JSON and an appended catalog entry may change.")
    need(isinstance(new, dict) and set(new) == {"version", "songs"} and type(new["version"]) is int and
         new["version"] == 1 and isinstance(new["songs"], list) and len(new["songs"]) == len(old["songs"]) + 1 and
         json.dumps(new["songs"][:-1], sort_keys=True, allow_nan=False) ==
         json.dumps(old["songs"], sort_keys=True, allow_nan=False),
         "Append exactly one song without changing existing catalog entries.")
    declaration = read_json(git.blob(candidate, declaration_path, MAX_RECORD_BYTES))
    song = declared_song(declaration, digest)
    need(song["id"] not in {item["id"] for item in old["songs"]}, "Song ID already exists.")
    need(new["songs"][-1] == song, "Appended catalog entry differs from its declared metadata.")
    data = git.blob(candidate, record_path, MAX_RECORD_BYTES)
    data.decode("utf8", errors="strict")
    need(b"\0" not in data and hashlib.sha256(data).hexdigest() == digest, "TL bytes differ from their canonical SHA identity.")
    with tempfile.TemporaryDirectory(prefix="tunes-proposal-") as scratch:
        directory = Path(scratch)
        (directory / "records").mkdir()
        record = directory / record_path
        record.write_bytes(data)
        (directory / "library.json").write_text(json.dumps({"version": 1, "songs": [song]}), encoding="utf8")
        catalog.validate(directory)
        observed = inspect_and_replay(harness, record, directory)
    return {"eligible": True, "approval": "required", "record_sha256": digest, "title": song["title"],
            "new_records": 1, "existing_records": len(existing), "declared_licence": "CC0-1.0", **observed}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True, help="trusted Git repository containing base and fetched head objects")
    parser.add_argument("--base", required=True, help="explicit trusted base commit ID")
    parser.add_argument("--head", required=True, help="candidate commit ID; its code is never executed")
    parser.add_argument("--harness", type=Path, required=True, help="native renderer built from a separately pinned trusted SDK")
    args = parser.parse_args()
    try:
        print(json.dumps(check(args.repo, args.base, args.head, args.harness), sort_keys=True))
    except (OSError, ValueError, KeyError, TypeError, UnicodeError, RecursionError, subprocess.SubprocessError) as error:
        parser.exit(1, "Proposal refused: " + str(error) + "\n")


if __name__ == "__main__":
    main()

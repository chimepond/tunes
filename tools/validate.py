#!/usr/bin/env python3
# Copyright (c) 2026 Nenad Micic
# SPDX-License-Identifier: Apache-2.0
"""Check catalog identities and complete file membership without network access."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess

MAX_CATALOG_BYTES = 16 * 1024 * 1024
MAX_RECORD_BYTES = 25 * 1024 * 1024
SHA = re.compile(r"[a-f0-9]{64}\Z")
LABEL = re.compile(r"[A-Za-z0-9._:+/@-]{1,160}\Z")


def need(condition, message):
    if not condition:
        raise ValueError(message)


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        need(key not in value, "Duplicate JSON member: " + key)
        value[key] = item
    return value


def invalid_constant(value):
    raise ValueError("Non-finite JSON number: " + value)


def text(value, maximum=500):
    return isinstance(value, str) and 0 < len(value) <= maximum and value.strip() == value and not any(
        ord(character) < 32 or 0xD800 <= ord(character) <= 0xDFFF for character in value)


def labels(discovery):
    if "metadata" in discovery:
        metadata = discovery["metadata"]
        need(isinstance(metadata, dict) and set(metadata) <= {"genre", "tempo"}, "Invalid declared metadata.")
        if "genre" in metadata:
            genre = metadata["genre"]
            need(isinstance(genre, dict) and set(genre) == {"value", "source"} and
                 isinstance(genre["value"], str) and LABEL.fullmatch(genre["value"]) and
                 genre["source"] in ("retained-score", "creator-declared"), "Invalid declared genre.")
        if "tempo" in metadata:
            tempo = metadata["tempo"]
            need(isinstance(tempo, dict) and set(tempo) == {"min_bpm", "max_bpm", "source"} and
                 tempo["source"] in ("retained-score", "creator-declared") and
                 all(type(tempo[key]) in (int, float) and math.isfinite(tempo[key]) for key in ("min_bpm", "max_bpm")) and
                 0 < tempo["min_bpm"] <= tempo["max_bpm"] <= 9007199254740991, "Invalid declared tempo.")
    if "provenance" in discovery:
        provenance = discovery["provenance"]
        need(isinstance(provenance, dict) and set(provenance) == {"model", "model_source"} and
             isinstance(provenance["model"], str) and LABEL.fullmatch(provenance["model"]) and
             provenance["model_source"] in ("client-reported", "user-stated", "unknown"), "Invalid model declaration.")


def validate(root):
    root = Path(root).resolve()
    catalog = root / "library.json"
    need(not catalog.is_symlink() and catalog.is_file() and catalog.stat().st_size <= MAX_CATALOG_BYTES,
         "Use a regular bounded library.json file.")
    value = json.loads(catalog.read_text(encoding="utf8"), object_pairs_hook=unique_object, parse_constant=invalid_constant)
    need(isinstance(value, dict) and set(value) == {"version", "songs"} and type(value["version"]) is int and
         value["version"] == 1 and isinstance(value["songs"], list) and value["songs"], "Invalid catalog shape.")
    directory = root / "records"
    need(directory.is_dir() and not directory.is_symlink(), "Use a regular records directory.")
    songs, variants, hashes, files = set(), set(), set(), []
    orders = {"featured": set(), "showcase": set()}
    total_bytes = 0
    for song in value["songs"]:
        need(isinstance(song, dict) and {"id", "title", "collection", "variants"} <= set(song) and
             set(song) <= {"id", "title", "collection", "revision", "variants"}, "Invalid song fields.")
        need(text(song["id"], 200) and re.fullmatch(r"[a-z0-9][a-z0-9-]*-[a-f0-9]{12}", song["id"]) and
             song["id"] not in songs and text(song["title"]) and text(song["collection"], 200) and
             ("revision" not in song or text(song["revision"], 200)), "Invalid or duplicate song identity.")
        songs.add(song["id"])
        need(isinstance(song["variants"], list) and 1 <= len(song["variants"]) <= 3, "Song needs one to three versions.")
        kinds = set()
        for variant in song["variants"]:
            need(isinstance(variant, dict) and set(variant) == {"kind", "id", "paths", "discovery"}, "Invalid version fields.")
            kind = variant["kind"]
            need(kind in ("standard", "keys", "mirror") and kind not in kinds and
                 variant["id"] == song["id"] + "/" + kind and variant["id"] not in variants,
                 "Invalid or duplicate version identity.")
            kinds.add(kind)
            variants.add(variant["id"])
            discovery = variant["discovery"]
            need(isinstance(discovery, dict) and {"record_sha256", "source"} <= set(discovery) and
                 set(discovery) <= {"record_sha256", "source", "metadata", "provenance", "featured", "showcase"} and
                 isinstance(discovery["record_sha256"], str) and SHA.fullmatch(discovery["record_sha256"]) and
                 discovery["source"] == "library.json", "Invalid metadata linkage.")
            digest = discovery["record_sha256"]
            need(digest not in hashes, "Duplicate canonical TL hash.")
            hashes.add(digest)
            need(variant["paths"] == ["records/" + digest + ".tl"], "Each version needs its one canonical record path.")
            record = directory / (digest + ".tl")
            need(record.is_file() and not record.is_symlink() and 0 < record.stat().st_size <= MAX_RECORD_BYTES,
                 "Missing, linked, empty or oversized TL file.")
            data = record.read_bytes()
            data.decode("utf8", errors="strict")
            need(len(data) == record.stat().st_size and hashlib.sha256(data).hexdigest() == digest,
                 "TL bytes or size differ from their canonical identity.")
            total_bytes += len(data)
            files.append(record)
            labels(discovery)
            for section in orders:
                if section not in discovery:
                    continue
                editorial = discovery[section]
                need(isinstance(editorial, dict) and set(editorial) == {"order", "note"} and
                     type(editorial["order"]) is int and 0 < editorial["order"] <= 9007199254740991 and
                     editorial["order"] not in orders[section] and text(editorial["note"], 2000),
                     "Invalid or duplicate editorial order.")
                orders[section].add(editorial["order"])
        need("standard" in kinds, "Every song needs an original Standard version.")
    members = list(directory.iterdir())
    need(len(members) == len(files) and {member.name for member in members} == {member.name for member in files},
         "Records directory contains missing or unreferenced members.")
    return {"songs": len(songs), "versions": len(variants), "bytes": total_bytes}, files


def replay(harness, record):
    result = subprocess.run([str(harness), "--play-once", "--", str(record)], capture_output=True, text=True, timeout=600)
    need(result.returncode == 0, "Native replay refused " + record.name + ": " + result.stderr.strip()[:300])
    rows = [line for line in result.stdout.splitlines() if line.startswith("PLAY ")]
    need(len(rows) == 1, "Native replay returned no unique PLAY result.")
    fields = dict(token.split("=", 1) for token in rows[0].split()[1:])
    need(fields.get("match") == "yes" and fields.get("hash") == fields.get("expected") and
         fields.get("block_indep") == "unchecked", "Native saved PCM hash differs.")
    return record.name


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--harness", type=Path, help="also replay records through this explicit native renderer")
    parser.add_argument("--jobs", type=int, choices=range(1, 9), default=1)
    parser.add_argument("--limit", type=int, help="replay only this many records; package validation remains complete")
    args = parser.parse_args()
    try:
        need(args.limit is None or args.limit > 0, "Replay limit must be positive.")
        summary, files = validate(args.root)
        count = 0
        if args.harness:
            harness = args.harness.resolve()
            need(harness.is_file(), "Native renderer is missing.")
            selected = files if args.limit is None else files[:args.limit]
            with ThreadPoolExecutor(max_workers=args.jobs) as executor:
                count = sum(1 for _ in executor.map(lambda record: replay(harness, record), selected))
        summary.update(native_replayed=count, native_replay_complete=bool(args.harness) and count == len(files),
                       block_independence="unchecked" if args.harness else "not-run")
        print(json.dumps(summary, sort_keys=True))
    except (OSError, ValueError, UnicodeError, subprocess.SubprocessError) as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()

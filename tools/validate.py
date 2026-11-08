#!/usr/bin/env python3
# Copyright (c) 2026 Nenad Micic
# SPDX-License-Identifier: Apache-2.0
"""Check catalog identities and complete file membership without network access."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import datetime
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import unicodedata

MAX_CATALOG_BYTES = 16 * 1024 * 1024
# Equal to the JotTune Synth browser input limit (web/policy.mjs recordBytes) and ChimePond intake.
MAX_RECORD_BYTES = 1_000_000
SHA = re.compile(r"[a-f0-9]{64}\Z")
LABEL = re.compile(r"[A-Za-z0-9._:+/@-]{1,160}\Z")
SONG_ID = re.compile(r"[a-z0-9][a-z0-9-]*-[a-f0-9]{12}\Z")
DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
# ZWNJ and ZWJ shape Persian, Indic and emoji text; every other format character can hide or reorder text.
JOINERS = "\u200c\u200d"
# Hangul and Braille fillers draw nothing, although Unicode classes them as letters or symbols.
FILLERS = "\u115f\u1160\u2800\u3164\uffa0"
# Skin-tone modifiers may sit between an emoji and its ZWJ.
EMOJI_MODIFIERS = "\U0001f3fb\U0001f3fc\U0001f3fd\U0001f3fe\U0001f3ff"
# UTS #39 section 5.4: more than four nonspacing marks in a row is refused as a likely spoof.
MAX_MARKS = 4


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


def canonical_json(value):
    """Return the one accepted byte form: two-space indent, unescaped UTF-8, file key order, final newline."""
    return (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf8")


def read_json_file(path):
    need(not path.is_symlink() and path.is_file() and path.stat().st_size <= MAX_CATALOG_BYTES,
         "Use a regular bounded " + path.name + " file.")
    data = path.read_bytes()
    return data, json.loads(data.decode("utf8"), object_pairs_hook=unique_object, parse_constant=invalid_constant)


def canonical_form(data, value, name):
    need(data == canonical_json(value), name + " is not in canonical form: use two-space JSON indentation, "
         "unescaped UTF-8, LF line ends and one final newline.")


def iso_date(value):
    if not (isinstance(value, str) and DATE.fullmatch(value)):
        return False
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        return False
    return True


def joined(value, index):
    """ZWNJ and ZWJ may follow a virama (as in RFC 5892) or join two letters; ZWJ may also join emoji symbols."""
    if index > 0 and unicodedata.combining(value[index - 1]) == 9:
        return True
    position = index - 1
    while position >= 0 and (unicodedata.category(value[position])[0] == "M" or value[position] in EMOJI_MODIFIERS):
        position -= 1
    before = unicodedata.category(value[position]) if position >= 0 else ""
    after = unicodedata.category(value[index + 1]) if index + 1 < len(value) else ""
    return before[:1] == "L" and after[:1] in ("L", "M") or value[index] == "\u200d" and before == after == "So"


def text_problem(value, maximum=500):
    """Return why catalog text is refused, or None; a reason names code points and never repeats hidden text."""
    if not isinstance(value, str):
        return "must be a JSON string"
    if not 0 < len(value) <= maximum:
        return "must hold 1 to " + str(maximum) + " characters, not " + str(len(value))
    if value.strip() != value or value[0] in FILLERS or value[-1] in FILLERS:
        return "must not start or end with white space or blank filler characters"
    if unicodedata.normalize("NFC", value) != value:
        return "must be in Unicode NFC form"
    marks = 0
    for index, character in enumerate(value):
        category = unicodedata.category(character)
        marks = marks + 1 if category in ("Mn", "Me") else 0
        if marks > MAX_MARKS:
            return "has more than " + str(MAX_MARKS) + " combining marks on one character"
        if character in JOINERS and not joined(value, index):
            return "uses the joiner U+%04X outside a word or an emoji sequence" % ord(character)
        if category in ("Cc", "Cs", "Co", "Cn", "Zl", "Zp") or category == "Cf" and character not in JOINERS:
            return "contains the control, format, private-use, unassigned or line-break character U+%04X" % ord(character)
    if not any(unicodedata.category(character)[0] in "LNPS" and character not in FILLERS for character in value):
        return "must show at least one visible character"
    return None


def text(value, maximum=500):
    return text_problem(value, maximum) is None


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
        # Contributions declare made_by; the retained archive carries only the model pair, or no provenance.
        need(isinstance(provenance, dict) and provenance and set(provenance) <= {"made_by", "model", "model_source"} and
             ("model" in provenance) == ("model_source" in provenance) and
             provenance.get("made_by", "agent") in ("agent", "human", "mixed"), "Invalid provenance declaration.")
        need("model" not in provenance or isinstance(provenance["model"], str) and LABEL.fullmatch(provenance["model"]) and
             provenance["model_source"] in ("client-reported", "user-stated", "unknown"), "Invalid model declaration.")


def validate_catalog(value, read_record, members):
    """Validate the complete catalog with a bounded, trusted record reader."""
    need(isinstance(value, dict) and set(value) == {"version", "songs"} and type(value["version"]) is int and
         value["version"] == 1 and isinstance(value["songs"], list) and value["songs"], "Invalid catalog shape.")
    songs, variants, hashes, files = set(), set(), set(), []
    orders = {"featured": set(), "showcase": set()}
    total_bytes = 0
    for song in value["songs"]:
        need(isinstance(song, dict) and {"id", "title", "collection", "variants"} <= set(song) and
             set(song) <= {"id", "title", "collection", "revision", "variants"}, "Invalid song fields.")
        need(text(song["id"], 200) and SONG_ID.fullmatch(song["id"]) and
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
            relative = variant["paths"][0]
            data = read_record(relative)
            need(isinstance(data, bytes) and 0 < len(data) <= MAX_RECORD_BYTES,
                 "Empty or oversized TL file: " + relative)
            data.decode("utf8", errors="strict")
            need(b"\0" not in data and hashlib.sha256(data).hexdigest() == digest,
                 "TL bytes differ from their canonical identity: " + relative)
            total_bytes += len(data)
            files.append(relative)
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
    need(len(members) == len(files) and set(members) == set(files),
         "Records directory contains missing or unreferenced members.")
    return {"songs": len(songs), "versions": len(variants), "bytes": total_bytes}, files


def validate_withdrawn(value, members):
    """Validate the append-only withdrawal list; a listed record must never return to records/."""
    need(isinstance(value, dict) and set(value) == {"version", "records"} and type(value["version"]) is int and
         value["version"] == 1 and isinstance(value["records"], list), "Invalid withdrawn.json shape.")
    members = set(members)
    digests, songs = set(), set()
    for entry in value["records"]:
        need(isinstance(entry, dict) and set(entry) == {"record_sha256", "song_id", "date"} and
             isinstance(entry["record_sha256"], str) and SHA.fullmatch(entry["record_sha256"]) and
             text(entry["song_id"], 200) and SONG_ID.fullmatch(entry["song_id"]) and iso_date(entry["date"]),
             "Invalid withdrawn record entry.")
        relative = "records/" + entry["record_sha256"] + ".tl"
        need(entry["record_sha256"] not in digests, "Duplicate withdrawn record: " + relative)
        need(relative not in members, "Withdrawn record is still present: " + relative)
        digests.add(entry["record_sha256"])
        songs.add(entry["song_id"])
    return digests, songs


def validate(root):
    root = Path(root).resolve()
    catalog, value = read_json_file(root / "library.json")
    directory = root / "records"
    need(directory.is_dir() and not directory.is_symlink(), "Use a regular records directory.")

    def read_record(relative):
        record = root / relative
        need(record.is_file() and not record.is_symlink() and 0 < record.stat().st_size <= MAX_RECORD_BYTES,
             "Missing, linked, empty or oversized TL file: " + relative)
        data = record.read_bytes()
        need(len(data) == record.stat().st_size, "TL size changed while reading: " + relative)
        return data

    members = ["records/" + member.name for member in directory.iterdir()]
    summary, files = validate_catalog(value, read_record, members)
    canonical_form(catalog, value, "library.json")
    data, withdrawn = read_json_file(root / "withdrawn.json")
    validate_withdrawn(withdrawn, members)
    canonical_form(data, withdrawn, "withdrawn.json")
    return summary, [root / relative for relative in files]


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

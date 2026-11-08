#!/usr/bin/env python3
# Copyright (c) 2026 Nenad Micic
# SPDX-License-Identifier: Apache-2.0
"""Print the longest admissible new song per community sample rate and space, or the archive versions outside the rules.

Duration and the browser verification budget are separate admission rules. The trusted renderer's render-memory lower
bound grows with the PCM size, so more channels or a higher sample rate allow fewer seconds. This tool bisects the
longest admissible whole-millisecond length of a minimal one-voice reference draft with `harness --inspect` and the
checker's own admission rule, and prints the Markdown table shown in CONTRIBUTING.md.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
from pathlib import Path
import subprocess
import tempfile

_spec = importlib.util.spec_from_file_location("tunes_pr", Path(__file__).with_name("check_pr.py"))
checker = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(checker)
need = checker.need

SPACES = ("off", "stereo", "binaural", "amb1", "amb3")
SHORTEST, LONGEST = 20_000, 300_000  # milliseconds; the duration rule refuses 20 s and admits 300 s
RULES = (("Community record sample rate", "sample rate"), ("New songs must be", "duration"),
         ("Record exceeds the browser verification budget", "memory budget"), ("Invalid native PCM size", "PCM size"))


def inspect(harness, record):
    """Return the INSPECT row of one record under the checker's native limits."""
    directory = record.parent
    status, output, errors = checker.run([str(harness), "--inspect", str(record)], limit=checker.MAX_NATIVE_OUTPUT,
                                         timeout=checker.NATIVE_TIMEOUT, cwd=directory,
                                         memory_bytes=checker.MAX_MEMORY_BYTES, file_bytes=checker.MAX_RECORD_BYTES,
                                         cpu_seconds=math.ceil(checker.NATIVE_TIMEOUT), name="Native inspection")
    need(status == 0, "Native inspection refused " + record.name + checker.failure(status, errors) + ".")
    return checker.native_row(output, "INSPECT")


def refusal(info):
    """Name the first admission rule that refuses this INSPECT row, or None when it is admitted."""
    try:
        checker.admission(info)
    except ValueError as error:
        for prefix, rule in RULES:
            if str(error).startswith(prefix):
                return rule
        raise
    return None


def reference(engine, fs, space, milliseconds):
    """A minimal one-voice draft: one sine note at a fixed position, released two seconds before the end."""
    length = milliseconds * 1_000_000
    return "\n".join(["timeline draft2", "engine " + engine, "fs %d" % fs, "length_ns %d" % length, "space " + space,
                      "block 64", "near 100", "streams melody", "patch sine 0 65536 0 0 0 256 5 500 200 800",
                      "patch sine 1 65536 0 0 0 0 5 500 200 800", "voice 0 sine", "0 melody 0 pos 300 1000 0",
                      "0 melody 0 on 392000 30000", "%d melody 0 off" % (length - 2_000_000_000)]) + "\n"


def longest(harness, engine, fs, space, directory):
    """Return (milliseconds, channels, rule) for the longest admitted reference draft; admission falls with length."""
    draft = directory / "reference.tl"

    def row(milliseconds):
        draft.write_text(reference(engine, fs, space, milliseconds))
        return inspect(harness, draft)

    info = row(LONGEST)
    if refusal(info) is None:
        return LONGEST, int(info["channels"]), "duration rule"
    low, high = SHORTEST, LONGEST
    while high - low > 1:
        middle = (low + high) // 2
        low, high = (middle, high) if refusal(row(middle)) is None else (low, middle)
    info = row(high)
    return (low if low > SHORTEST else None), int(info["channels"]), refusal(info)


def table(harness):
    """Return the Markdown table of the longest admissible reference song per space and community sample rate."""
    status, output, errors = checker.run([str(harness), "--engine"], limit=10_000, name="Native engine query")
    need(status == 0 and output.startswith(b"ENGINE "), "Native engine query failed" + checker.failure(status, errors) + ".")
    engine = output.decode("ascii").splitlines()[0].removeprefix("ENGINE ")
    rows = ["| Space | Channels | " + " | ".join("%d Hz" % fs for fs in checker.SAMPLE_RATES) + " |",
            "| --- | ---: | " + " | ".join("---:" for _ in checker.SAMPLE_RATES) + " |"]
    with tempfile.TemporaryDirectory(prefix="tunes-duration-") as scratch:
        for space in SPACES:
            cells = [longest(harness, engine, fs, space, Path(scratch)) for fs in checker.SAMPLE_RATES]
            need(len({channels for _, channels, _ in cells}) == 1, "Channel count differs between sample rates.")
            rows.append("| `" + space + "` | " + str(cells[0][1]) + " | " + " | ".join(
                ("none" if milliseconds is None else "%.3f s" % (milliseconds / 1000)) + " (" + rule + ")"
                for milliseconds, _, rule in cells) + " |")
    return "\n".join(rows) + "\n"


def archive(harness, root):
    """Return a summary line and one line per retained catalog version that the current admission rules refuse."""
    catalog = checker.catalog
    catalog.validate(root)
    value = json.loads((root / "library.json").read_bytes())
    found = []
    for song in value["songs"]:
        for variant in song["variants"]:
            info = inspect(harness, root / variant["paths"][0])
            rule = refusal(info)
            if rule:
                fs, frames = int(info["fs"]), int(info["frames"])
                found.append((rule, "- `%s`: %s (%d Hz, %.3f s, %s channels)" % (variant["id"], rule, fs, frames / fs,
                                                                               info["channels"])))
    counts = [(sum(rule == name for rule, _ in found), name) for _, name in RULES]
    parts = ["%d on %s" % (count, "the " + name if name == "memory budget" else name) for count, name in counts if count]
    summary = "%d retained %s outside the current admission rules" % (len(found), "version falls" if len(found) == 1
                                                                      else "versions fall")
    summary += (": " + ", ".join(parts[:-1]) + (" and " if len(parts) > 1 else "") + parts[-1] if parts else "") + "."
    return summary + "\n" + "".join(line + "\n" for _, line in sorted(found, key=lambda item: item[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--harness", type=Path, required=True, help="native renderer built from a trusted SDK")
    parser.add_argument("--archive", action="store_true", help="list retained catalog versions outside the current rules")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1], help="repository root")
    args = parser.parse_args()
    try:
        print(archive(args.harness, args.root) if args.archive else table(args.harness), end="")
    except (OSError, ValueError, KeyError, UnicodeError, subprocess.SubprocessError) as error:
        parser.exit(1, "Duration table failed: " + str(error) + "\n")


if __name__ == "__main__":
    main()

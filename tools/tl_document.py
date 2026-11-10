#!/usr/bin/env python3
# Copyright (c) 2026 Nenad Micic
# SPDX-License-Identifier: Apache-2.0
"""Bounded portable TL framing and metadata; native alone validates music syntax.

Import parse_document, compose_document and body_sha256 without dependencies.
The CLI validates documents, packs explicit JSON metadata, or exports exact body
bytes. Every output path must be new; a header never supplies publication consent.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import stat
import unicodedata

MAX_BYTES = 1_000_000
MAX_LINE_BYTES = 1022
MAX_VERSION = 0xFFFFFFFF
TL_VERSION = ENGINE_VERSION = 1
LABEL = re.compile(r"[A-Za-z0-9._:+/@-]{1,160}\Z")
RELEASE = re.compile(r"[A-Za-z0-9._-]{1,128}\Z")
MARKER = re.compile(rb"# jottune-tl ([1-9][0-9]{0,9}) ([1-9][0-9]{0,9})\Z")
FIELDS = frozenset(("title", "collection", "author", "made_by", "genre", "tempo", "model",
                    "utc_day", "license", "renderer_release", "prompt_id"))


class DocumentError(ValueError):
    """A malformed, oversized or unsupported portable TL document."""


def _require(condition, message):
    if not condition:
        raise DocumentError(message)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, "duplicate metadata key: " + key)
        result[key] = value
    return result


def _constant(value):
    raise DocumentError("metadata numbers must be finite: " + value)


def _json(data):
    try:
        return json.loads(data.decode("utf-8") if isinstance(data, bytes) else data,
                          object_pairs_hook=_pairs, parse_constant=_constant)
    except (ValueError, UnicodeError, RecursionError) as error:
        if isinstance(error, DocumentError):
            raise
        raise DocumentError("invalid metadata JSON: " + str(error)) from error


def _text(value, limit, field):
    _require(type(value) is str and 1 <= len(value) <= limit, field + " has invalid text length")
    _require(value == value.strip() and unicodedata.normalize("NFC", value) == value,
             field + " must be NFC text without surrounding whitespace")
    _require(not any(unicodedata.category(c) in ("Cc", "Cs", "Zl", "Zp") for c in value),
             field + " contains a control, surrogate or line separator")


def validate_metadata(metadata):
    """Validate the closed descriptive schema and return an independent JSON copy."""
    _require(type(metadata) is dict, "metadata must be an object")
    _require(all(type(key) is str and key in FIELDS for key in metadata), "unknown metadata field")
    for field, value in metadata.items():
        if field in ("title", "collection", "author"):
            _text(value, 60 if field == "title" else 200, field)
        elif field == "made_by":
            _require(type(value) is str and value in ("agent", "human", "mixed"), "invalid made_by")
        elif field in ("genre", "license", "prompt_id"):
            _require(field == "genre" and value is None or type(value) is str and LABEL.fullmatch(value),
                     "invalid " + field + " label")
        elif field == "renderer_release":
            _require(type(value) is str and RELEASE.fullmatch(value), "invalid renderer_release")
        elif field == "utc_day":
            _require(type(value) is str and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value),
                     "invalid utc_day")
            try:
                datetime.date.fromisoformat(value)
            except ValueError as error:
                raise DocumentError("invalid utc_day") from error
        elif field == "model" and value is not None:
            _require(type(value) is dict and set(value) == {"id", "source"}, "invalid model fields")
            _require(type(value["id"]) is str and LABEL.fullmatch(value["id"]), "invalid model id")
            _require(type(value["source"]) is str and value["source"] in ("client-reported", "user-stated"),
                     "invalid model source")
        elif field == "tempo" and value is not None:
            _require(type(value) is dict and set(value) == {"min_bpm", "max_bpm"}, "invalid tempo fields")
            for bpm in value.values():
                _require(type(bpm) in (int, float) and 0 < bpm <= 9007199254740991 and
                         (type(bpm) is int or math.isfinite(bpm)), "invalid tempo number")
            _require(value["min_bpm"] <= value["max_bpm"], "tempo minimum exceeds maximum")
    return _json(json.dumps(metadata, ensure_ascii=False, allow_nan=False))


def metadata_schema():
    """Closed JSON Schema discovery shape; validate_metadata enforces NFC/dates."""
    def obj(properties, required=()):
        return dict(type="object", properties=properties, required=list(required), additionalProperties=False)
    def nullable(schema):
        return {"anyOf": [{"type": "null"}, schema]}
    label = dict(type="string", pattern=r"^[A-Za-z0-9._:+/@-]{1,160}$(?![\s\S])")
    text = lambda limit: dict(type="string", minLength=1, maxLength=limit,
                             description="NFC text without surrounding whitespace, controls, surrogates or line/paragraph separators.")
    bpm = dict(type="number", exclusiveMinimum=0, maximum=9007199254740991)
    return obj(dict(title=text(60), collection=text(200), author=text(200),
        made_by=dict(type="string", enum=["agent", "human", "mixed"]), genre=nullable(label),
        tempo=nullable(obj(dict(min_bpm=bpm, max_bpm=bpm), ("min_bpm", "max_bpm"))),
        model=nullable(obj(dict(id=label, source=dict(type="string", enum=["client-reported", "user-stated"])),
                           ("id", "source"))),
        utc_day=dict(type="string", pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$(?![\s\S])", format="date"),
        license=label, renderer_release=dict(type="string", pattern=r"^[A-Za-z0-9._-]{1,128}$(?![\s\S])"),
        prompt_id=label))


def _version(value, supported, field):
    _require(type(value) is int and 1 <= value <= MAX_VERSION, field + " must be a positive uint32")
    _require(value <= supported, "unsupported " + field + ": " + str(value))


def _bytes(data):
    _require(type(data) is bytes, "document must be bytes")
    _require(bool(data), "TL document must not be empty")
    _require(len(data) <= MAX_BYTES, "TL document exceeds 1000000 bytes")
    _require(not data.startswith(b"\xef\xbb\xbf"), "TL document must not contain a UTF-8 BOM")
    _require(b"\x00" not in data, "TL document must not contain NUL")
    try:
        data.decode("utf-8", errors="strict")
    except UnicodeError as error:
        raise DocumentError("TL document must be strict UTF-8") from error
    # Only LF/CRLF are physical newlines. Other UTF-8 separators are body data
    # for the native grammar, but never delimit or evade framing/line limits.
    lines = data.split(b"\n")
    _require(all(len(line[:-1] if line.endswith(b"\r") else line) <= MAX_LINE_BYTES
                 for line in lines), "TL line exceeds 1022 bytes")
    return lines


def _recognized(line):
    comment = line.lstrip(b" \t\r")
    return (comment.startswith(b"#") and
            comment[1:].lstrip(b" \t\r").startswith((b"jottune-tl", b"end-jottune-tl")))


def parse_document(data: bytes) -> dict:
    """Return exact header/body bytes and validated metadata, accepting legacy TL."""
    lines = _bytes(data)
    physical = [line[:-1] if line.endswith(b"\r") else line for line in lines]
    first = physical[0]
    header, body, metadata, tl_version, engine_version = b"", data, {}, 1, 1
    if _recognized(first):
        marker = MARKER.fullmatch(first)
        _require(marker is not None, "malformed TL document header")
        tl_version, engine_version = map(int, marker.groups())
        _version(tl_version, TL_VERSION, "TL version")
        _version(engine_version, ENGINE_VERSION, "engine version")
        json_lines, end = [], None
        for index, line in enumerate(physical[1:], 1):
            if line == b"# end-jottune-tl":
                end = index
                break
            _require(not _recognized(line), "repeated or malformed TL document marker")
            _require(line.startswith(b"# "), "metadata line must start with '# '")
            json_lines.append(line[2:])
        _require(end is not None, "unterminated TL document header")
        metadata = validate_metadata(_json(b"\n".join(json_lines)))
        # Include the end marker's newline only when it was present.
        offset = sum(len(line) + 1 for line in lines[:end]) + len(lines[end])
        if end < len(lines) - 1:
            offset += 1
        header, body = data[:offset], data[offset:]
    _require(bool(body), "TL document is missing its music body")
    for line in body.split(b"\n"):
        _require(not _recognized(line), "misplaced TL document marker")
    return dict(body=body, header=header, metadata=metadata,
                tl_version=tl_version, engine_version=engine_version)


def compose_document(body: bytes, metadata: dict, tl_version=1, engine_version=1) -> bytes:
    """Wrap a headerless body with explicit validated metadata and version needs."""
    _version(tl_version, TL_VERSION, "TL version")
    _version(engine_version, ENGINE_VERSION, "engine version")
    parsed = parse_document(body)
    _require(not parsed["header"], "compose body already has a document header")
    metadata = validate_metadata(metadata)
    encoded = json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
    header = (f"# jottune-tl {tl_version} {engine_version}\n" +
              "".join("# " + line + "\n" for line in encoded.split("\n")) + "# end-jottune-tl\n")
    result = header.encode("utf-8") + body
    parse_document(result)
    return result


def body_sha256(data: bytes) -> str:
    """Hash exact body bytes; canonical music must first come from the native tool."""
    return hashlib.sha256(parse_document(data)["body"]).hexdigest()


def read_bounded(path):
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        _require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), "input must be a regular file")
        data = stream.read(MAX_BYTES + 1)
    _require(len(data) <= MAX_BYTES, "input exceeds 1000000 bytes")
    return data


def _write_new(path, data):
    with Path(path).open("xb") as stream:
        stream.write(data)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)
    validate = commands.add_parser("validate", help="validate framing/metadata and report both identities; no music rendering")
    validate.add_argument("input", type=Path)
    body = commands.add_parser("body", help="write exact music body to a new file (explicit metadata omission)")
    body.add_argument("input", type=Path)
    body.add_argument("--out", required=True, type=Path)
    pack = commands.add_parser("pack", help="wrap a headerless body with explicit metadata JSON in a new file")
    pack.add_argument("body", type=Path)
    pack.add_argument("--metadata", required=True, type=Path, help="closed-schema JSON object; never publication consent")
    pack.add_argument("--out", required=True, type=Path)
    pack.add_argument("--tl-version", type=int, default=1)
    pack.add_argument("--engine-version", type=int, default=1)
    args = parser.parse_args(argv)
    try:
        if args.operation == "pack":
            data = compose_document(read_bounded(args.body), _json(read_bounded(args.metadata)),
                                    args.tl_version, args.engine_version)
            _write_new(args.out, data)
        else:
            data = read_bounded(args.input)
            parsed = parse_document(data)
            if args.operation == "body":
                _write_new(args.out, parsed["body"])
            else:
                print(json.dumps(dict(metadata=parsed["metadata"], tl_version=parsed["tl_version"],
                                      engine_version=parsed["engine_version"], bytes=len(data),
                                      body_bytes=len(parsed["body"]), record_sha256=body_sha256(data),
                                      document_sha256=hashlib.sha256(data).hexdigest()), ensure_ascii=False, sort_keys=True))
    except (OSError, ValueError, TypeError) as error:
        parser.exit(2, "tl_document: " + str(error) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

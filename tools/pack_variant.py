#!/usr/bin/env python3
# Copyright (c) 2026 Nenad Micic
# SPDX-License-Identifier: Apache-2.0
"""Pack one catalog version or migrate the archive to portable TL headers."""
import argparse
from collections import Counter
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile

_spec = importlib.util.spec_from_file_location("pack_catalog", Path(__file__).with_name("validate.py"))
catalog = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(catalog)


def portable_bytes(song, variant, data, omissions):
    """Copy exact known declarations; report an unrepresentable field without editing it."""
    parsed = catalog.document.parse_document(data)
    known = catalog.catalog_declarations(song, variant["discovery"])
    catalog.check_metadata_agreement(parsed["metadata"], known)
    metadata = dict(parsed["metadata"])
    for field, value in known.items():
        try:
            catalog.document.validate_metadata({field: value})
        except catalog.document.DocumentError as error:
            omissions.append((field, str(error), variant["id"]))
        else:
            metadata[field] = value
    # A repeated migration preserves even an existing header's whitespace.
    portable = data if parsed["header"] and metadata == parsed["metadata"] else catalog.document.compose_document(
        parsed["body"], metadata, parsed["tl_version"], parsed["engine_version"])
    packed = catalog.document.parse_document(portable)
    catalog.need(packed["body"] == parsed["body"] and
                 hashlib.sha256(packed["body"]).hexdigest() == variant["discovery"]["record_sha256"],
                 "Packed canonical body identity differs.")
    return portable, packed["metadata"]


def omission_summary(omissions):
    """Bound output by the closed field schema and five example version IDs per reason."""
    counts = Counter((field, reason) for field, reason, _ in omissions)
    return [{"field": field, "reason": reason, "count": count,
             "examples": [identity for f, r, identity in omissions if (f, r) == (field, reason)][:5]}
            for (field, reason), count in sorted(counts.items())]


def pack_variant(root, variant_id, output):
    root, output = Path(root).resolve(), Path(output)
    catalog.validate(root)
    _, value = catalog.read_json_file(root / "library.json")
    matches = [(song, variant) for song in value["songs"] for variant in song["variants"] if variant["id"] == variant_id]
    catalog.need(len(matches) == 1, "Select one exact catalog variant ID.")
    song, variant = matches[0]
    omissions = []
    portable, _ = portable_bytes(song, variant, (root / variant["paths"][0]).read_bytes(), omissions)
    with output.open("xb") as stream:
        stream.write(portable)
    return {"record_sha256": variant["discovery"]["record_sha256"],
            "document_sha256": hashlib.sha256(portable).hexdigest(), "bytes": len(portable),
            "omissions": omission_summary(omissions)}


def migrate_archive(root, *, dry_run=False):
    """Preflight every document and catalog before replacing any archive path.

    Changes are staged locally, with exact originals retained until complete
    validation. A failed replacement restores installed paths. A second run is
    a no-op, and neither canonical bodies nor external metadata are rewritten.
    """
    root = Path(root).resolve()
    before_summary, _ = catalog.validate(root)
    before_catalog, value = catalog.read_json_file(root / "library.json")
    before, after, omissions, fields = {}, {}, [], Counter()
    for song in value["songs"]:
        for variant in song["variants"]:
            relative = variant["paths"][0]
            data = (root / relative).read_bytes()
            portable, metadata = portable_bytes(song, variant, data, omissions)
            before[relative], after[relative] = data, portable
            fields.update(metadata.keys())
            variant["discovery"]["document_sha256"] = hashlib.sha256(portable).hexdigest()
    # Validate the complete planned archive before creating staging files.
    catalog.validate_catalog(value, lambda name: after[name], list(after))
    next_catalog = catalog.canonical_json(value)
    changes = {relative: data for relative, data in after.items() if before[relative] != data}
    if before_catalog != next_catalog:
        changes["library.json"] = next_catalog
    summary = {"songs": before_summary["songs"], "versions": before_summary["versions"],
               "changed_records": sum(name != "library.json" for name in changes),
               "catalog_changed": "library.json" in changes, "body_bytes": sum(
                   len(catalog.document.parse_document(data)["body"]) for data in after.values()),
               "document_bytes": sum(map(len, after.values())), "metadata_fields": dict(sorted(fields.items())),
               "omissions": omission_summary(omissions), "dry_run": bool(dry_run)}
    if dry_run or not changes:
        return summary
    # Refuse any concurrent source change before replacing the first path.
    catalog.need((root / "library.json").read_bytes() == before_catalog and
                 set("records/" + item.name for item in (root / "records").iterdir()) == set(before) and
                 all((root / relative).read_bytes() == data for relative, data in before.items()),
                 "Archive changed during portable migration preflight.")
    originals = {**before, "library.json": before_catalog}
    stage = Path(tempfile.mkdtemp(prefix=".portable-migration-", dir=root))
    installed, cleanup = [], True
    try:
        for relative, data in changes.items():
            for name, content in (("new", data), ("old", originals[relative])):
                target = stage / name / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                os.chmod(target, stat.S_IMODE((root / relative).stat().st_mode))
        try:
            for relative in changes:
                os.replace(stage / "new" / relative, root / relative)
                installed.append(relative)
            catalog.validate(root)
        except BaseException:
            try:
                for relative in reversed(installed):
                    os.replace(stage / "old" / relative, root / relative)
            except BaseException as error:
                cleanup = False
                raise RuntimeError("Migration rollback failed; staged originals retained at " + str(stage)) from error
            raise
    finally:
        if cleanup:
            shutil.rmtree(stage)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("variant_id", nargs="?", help="exact version ID, including /standard, /keys or /mirror")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--out", type=Path, help="new portable file; existing paths are refused")
    parser.add_argument("--migrate", action="store_true", help="add portable headers and full-file checksums to the archive")
    parser.add_argument("--dry-run", action="store_true", help="preflight the complete migration and print its bounded summary")
    args = parser.parse_args()
    try:
        if args.migrate:
            catalog.need(args.variant_id is None and args.out is None, "Migration takes no version ID or output path.")
            result = migrate_archive(args.root, dry_run=args.dry_run)
        else:
            catalog.need(args.variant_id is not None and args.out is not None and not args.dry_run,
                         "Single-version packing needs a version ID and --out; --dry-run needs --migrate.")
            result = pack_variant(args.root, args.variant_id, args.out)
        print(json.dumps(result, sort_keys=True))
    except (OSError, ValueError, TypeError, RuntimeError) as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()

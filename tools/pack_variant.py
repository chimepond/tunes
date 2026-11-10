#!/usr/bin/env python3
# Copyright (c) 2026 Nenad Micic
# SPDX-License-Identifier: Apache-2.0
"""Pack one catalog version as a portable TL without changing archive files."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

_spec = importlib.util.spec_from_file_location("pack_catalog", Path(__file__).with_name("validate.py"))
catalog = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(catalog)


def pack_variant(root, variant_id, output):
    root, output = Path(root).resolve(), Path(output)
    catalog.validate(root)
    _, value = catalog.read_json_file(root / "library.json")
    matches = [(song, variant) for song in value["songs"] for variant in song["variants"] if variant["id"] == variant_id]
    catalog.need(len(matches) == 1, "Select one exact catalog variant ID.")
    song, variant = matches[0]
    data = (root / variant["paths"][0]).read_bytes()
    parsed = catalog.document.parse_document(data)
    known = catalog.catalog_declarations(song, variant["discovery"])
    # An unknown-source model cannot be represented by the portable model contract.
    if known.get("model", {}).get("source") == "unknown":
        known.pop("model")
    catalog.check_metadata_agreement(parsed["metadata"], known)
    metadata = {**parsed["metadata"], **known}
    portable = catalog.document.compose_document(parsed["body"], metadata, parsed["tl_version"], parsed["engine_version"])
    catalog.need(catalog.document.body_sha256(portable) == variant["discovery"]["record_sha256"], "Packed body identity differs.")
    with output.open("xb") as stream:
        stream.write(portable)
    return {"record_sha256": variant["discovery"]["record_sha256"],
            "document_sha256": hashlib.sha256(portable).hexdigest(), "bytes": len(portable)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("variant_id", help="exact library.json version ID, including /standard, /keys or /mirror")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--out", type=Path, required=True, help="new portable file; existing paths are refused")
    args = parser.parse_args()
    try:
        print(json.dumps(pack_variant(args.root, args.variant_id, args.out), sort_keys=True))
    except (OSError, ValueError, TypeError) as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()

# Copyright (c) 2026 Nenad Micic
# SPDX-License-Identifier: Apache-2.0
"""Catalog packaging checks; TL syntax remains the native reader's responsibility."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("tunes_validate", ROOT / "tools/validate.py")
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="tunes-validator-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / "records").mkdir()
        self.data = b"timeline draft2\n"
        self.digest = hashlib.sha256(self.data).hexdigest()
        self.record = self.root / "records" / (self.digest + ".tl")
        self.record.write_bytes(self.data)
        identity = "sample-" + self.digest[:12]
        self.value = {"version": 1, "songs": [{"id": identity, "title": "Sample", "collection": "Examples", "variants": [
            {"kind": "standard", "id": identity + "/standard", "paths": ["records/" + self.record.name],
             "discovery": {"record_sha256": self.digest, "source": "library.json"}}]}]}
        self.save()

    def save(self):
        (self.root / "library.json").write_text(json.dumps(self.value))

    def test_complete_membership_and_size(self):
        summary, files = validator.validate(self.root)
        self.assertEqual(summary, {"songs": 1, "versions": 1, "bytes": len(self.data)})
        self.assertEqual(files, [self.record.resolve()])

    def test_duplicate_json_member(self):
        (self.root / "library.json").write_text('{"version":1,"version":1,"songs":[]}')
        with self.assertRaisesRegex(ValueError, "Duplicate JSON"):
            validator.validate(self.root)

    def test_duplicate_canonical_hash(self):
        version = copy.deepcopy(self.value["songs"][0]["variants"][0])
        version.update(kind="keys", id=self.value["songs"][0]["id"] + "/keys")
        self.value["songs"][0]["variants"].append(version)
        self.save()
        with self.assertRaisesRegex(ValueError, "Duplicate canonical"):
            validator.validate(self.root)

    def test_extra_or_missing_files(self):
        extra = self.root / "records" / ("a" * 64 + ".tl")
        extra.write_bytes(self.data)
        with self.assertRaisesRegex(ValueError, "unreferenced"):
            validator.validate(self.root)
        extra.unlink()
        self.record.unlink()
        with self.assertRaisesRegex(ValueError, "Missing"):
            validator.validate(self.root)

    def test_damaged_bytes(self):
        self.record.write_bytes(self.data + b"changed\n")
        with self.assertRaisesRegex(ValueError, "canonical identity"):
            validator.validate(self.root)

    def test_traversal_and_link_are_refused(self):
        version = self.value["songs"][0]["variants"][0]
        version["paths"] = ["../" + self.record.name]
        self.save()
        with self.assertRaisesRegex(ValueError, "canonical record path"):
            validator.validate(self.root)
        version["paths"] = ["records/" + self.record.name]
        self.save()
        target = self.root / "target.tl"
        self.record.rename(target)
        self.record.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "linked"):
            validator.validate(self.root)

    def test_invalid_declared_metadata(self):
        discovery = self.value["songs"][0]["variants"][0]["discovery"]
        discovery["provenance"] = {"model": "example-model", "model_source": "guessed"}
        self.save()
        with self.assertRaisesRegex(ValueError, "model declaration"):
            validator.validate(self.root)
        discovery.pop("provenance")
        discovery["unexpected"] = True
        self.save()
        with self.assertRaisesRegex(ValueError, "metadata linkage"):
            validator.validate(self.root)

    def test_non_finite_json_is_refused(self):
        (self.root / "library.json").write_text('{"version":NaN,"songs":[]}')
        with self.assertRaisesRegex(ValueError, "Non-finite"):
            validator.validate(self.root)

    def test_invalid_utf8_is_refused_even_when_digest_matches(self):
        self.record.unlink()
        data = b"\xff"
        digest = hashlib.sha256(data).hexdigest()
        (self.root / "records" / (digest + ".tl")).write_bytes(data)
        version = self.value["songs"][0]["variants"][0]
        version["paths"] = ["records/" + digest + ".tl"]
        version["discovery"]["record_sha256"] = digest
        self.save()
        with self.assertRaises(UnicodeDecodeError):
            validator.validate(self.root)

    def test_empty_and_oversized_records(self):
        self.record.write_bytes(b"")
        with self.assertRaisesRegex(ValueError, "empty or oversized"):
            validator.validate(self.root)
        with self.record.open("wb") as output:
            output.truncate(validator.MAX_RECORD_BYTES + 1)
        with self.assertRaisesRegex(ValueError, "empty or oversized"):
            validator.validate(self.root)

    def test_metadata_links_only_to_product_catalog(self):
        self.value["songs"][0]["variants"][0]["discovery"]["source"] = "archive/notes.md"
        self.save()
        with self.assertRaisesRegex(ValueError, "metadata linkage"):
            validator.validate(self.root)


if __name__ == "__main__":
    unittest.main()

# Copyright (c) 2026 Nenad Micic
# SPDX-License-Identifier: Apache-2.0
"""Portable headers preserve catalog identity and reject metadata disagreement."""
import copy
import hashlib
import json
import os
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
import test_validate as baseline
validator = baseline.validator

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("pack_variant", ROOT / "tools/pack_variant.py")
packer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packer)


class PortableTests(unittest.TestCase):
    setUp = baseline.CatalogTests.setUp
    save = baseline.CatalogTests.save
    save_withdrawn = baseline.CatalogTests.save_withdrawn

    def test_portable_metadata_changes_do_not_change_body_identity(self):
        for metadata in ({"title":"Sample"}, {"title":"Sample", "author":"Known author"}):
            data = validator.document.compose_document(self.data, metadata)
            self.record.write_bytes(data)
            self.assertEqual(validator.validate(self.root)[0]["versions"], 1)
            self.assertEqual(validator.document.body_sha256(data), self.digest)

    def test_external_title_and_nullable_model_conflicts_are_refused(self):
        self.record.write_bytes(validator.document.compose_document(self.data, {"title":"Other title"}))
        with self.assertRaisesRegex(ValueError, "conflicts with external title"):
            validator.validate(self.root)
        self.value["songs"][0]["variants"][0]["discovery"]["provenance"] = {
            "model":"reported-v1", "model_source":"client-reported"}
        self.save()
        self.record.write_bytes(validator.document.compose_document(self.data, {"model":None}))
        with self.assertRaisesRegex(ValueError, "conflicts with external model"):
            validator.validate(self.root)

    def test_full_file_checksum_is_separate_and_checked(self):
        data = validator.document.compose_document(self.data, {"title":"Sample"})
        self.record.write_bytes(data)
        discovery = self.value["songs"][0]["variants"][0]["discovery"]
        discovery["document_sha256"] = hashlib.sha256(data).hexdigest()
        self.save()
        validator.validate(self.root)
        discovery["document_sha256"] = self.digest
        self.save()
        with self.assertRaisesRegex(ValueError, "full-file checksum"):
            validator.validate(self.root)

    def test_pack_one_variant_preserves_archive_and_never_infers_license_or_model(self):
        before = self.record.read_bytes(), (self.root / "library.json").read_bytes()
        output = self.root / "portable.tl"
        result = packer.pack_variant(self.root, self.value["songs"][0]["variants"][0]["id"], output)
        parsed = validator.document.parse_document(output.read_bytes())
        self.assertEqual(parsed["metadata"], {"title":"Sample", "collection":"Examples"})
        self.assertEqual(result["record_sha256"], self.digest)
        self.assertEqual(before, (self.record.read_bytes(), (self.root / "library.json").read_bytes()))
        with self.assertRaises(FileExistsError):
            packer.pack_variant(self.root, self.value["songs"][0]["variants"][0]["id"], output)

    def add_keys_version(self):
        body = b"independent keys arrangement\n"
        digest = hashlib.sha256(body).hexdigest()
        relative = "records/" + digest + ".tl"
        (self.root / relative).write_bytes(body)
        song = self.value["songs"][0]
        song["variants"].append({"kind":"keys", "id":song["id"] + "/keys", "paths":[relative],
                                 "discovery":{"record_sha256":digest,"source":"library.json"}})
        self.save()

    def archive_snapshot(self):
        return {path.relative_to(self.root).as_posix():path.read_bytes()
                for path in [self.root / "library.json", *(self.root / "records").iterdir()]}

    def test_migration_preserves_all_bodies_metadata_and_identities_and_is_idempotent(self):
        discovery = self.value["songs"][0]["variants"][0]["discovery"]
        discovery["provenance"] = {"model":"reported/v1","model_source":"client-reported"}
        discovery["metadata"] = {"genre":{"value":"ambient","source":"retained-score"},
                                 "tempo":{"min_bpm":90,"max_bpm":95,"source":"retained-score"}}
        self.add_keys_version()
        before = self.archive_snapshot()
        old_catalog = copy.deepcopy(self.value)
        dry = packer.migrate_archive(self.root, dry_run=True)
        self.assertEqual(dry["changed_records"], 2)
        self.assertEqual(before, self.archive_snapshot())
        result = packer.migrate_archive(self.root)
        self.assertEqual((result["versions"], result["changed_records"], result["omissions"]), (2, 2, []))
        updated = json.loads((self.root / "library.json").read_text())
        for song in updated["songs"]:
            for variant in song["variants"]:
                raw = (self.root / variant["paths"][0]).read_bytes()
                parsed = validator.document.parse_document(raw)
                self.assertEqual(parsed["body"], before[variant["paths"][0]])
                self.assertEqual(variant["discovery"]["document_sha256"], hashlib.sha256(raw).hexdigest())
                self.assertFalse({"author","made_by","utc_day","license"} & parsed["metadata"].keys())
                self.assertEqual("model" in parsed["metadata"], variant["kind"] == "standard")
                variant["discovery"].pop("document_sha256")
        self.assertEqual(updated, old_catalog)
        snapshot = self.archive_snapshot()
        mtimes = {name:(self.root / name).stat().st_mtime_ns for name in snapshot}
        repeated = packer.migrate_archive(self.root)
        self.assertEqual((repeated["changed_records"], repeated["catalog_changed"]), (0, False))
        self.assertEqual(snapshot, self.archive_snapshot())
        self.assertEqual(mtimes, {name:(self.root / name).stat().st_mtime_ns for name in snapshot})

    def test_migration_reports_unrepresentable_title_without_truncating_catalog(self):
        self.value["songs"][0]["title"] = "x" * 61
        self.save()
        result = packer.migrate_archive(self.root)
        parsed = validator.document.parse_document(self.record.read_bytes())
        self.assertNotIn("title", parsed["metadata"])
        self.assertEqual(result["omissions"][0]["field"], "title")
        self.assertEqual(result["omissions"][0]["count"], 1)
        self.assertEqual(json.loads((self.root / "library.json").read_text())["songs"][0]["title"], "x" * 61)

    def test_migration_preflights_every_version_before_replacing_any_file(self):
        self.add_keys_version()
        before = self.archive_snapshot()
        original = packer.catalog.document.compose_document
        calls = 0
        def compose(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise packer.catalog.document.DocumentError("fixture second-version refusal")
            return original(*args, **kwargs)
        with patch.object(packer.catalog.document, "compose_document", side_effect=compose):
            with self.assertRaisesRegex(ValueError, "second-version refusal"):
                packer.migrate_archive(self.root)
        self.assertEqual(calls, 2)
        self.assertEqual(before, self.archive_snapshot())
        self.assertFalse(list(self.root.glob(".portable-migration-*")))

    def test_migration_restores_record_if_catalog_replacement_fails(self):
        before = self.archive_snapshot()
        replace = os.replace
        def fail_catalog(source, target):
            if Path(source).parent.name == "new" and Path(target).name == "library.json":
                raise OSError("fixture catalog rename refusal")
            return replace(source, target)
        with patch.object(packer.os, "replace", side_effect=fail_catalog):
            with self.assertRaisesRegex(OSError, "catalog rename refusal"):
                packer.migrate_archive(self.root)
        self.assertEqual(before, self.archive_snapshot())
        validator.validate(self.root)
        self.assertFalse(list(self.root.glob(".portable-migration-*")))

    def test_version_and_bounded_metadata_validation(self):
        for metadata in ({"title":"x"*61}, {"model":{"id":"x"*161,"source":"client-reported"}}, {"consent":True}):
            with self.assertRaises(ValueError):
                validator.document.compose_document(self.data, metadata)
        for marker in (b"# jottune-tl 4294967296 1", b"# jottune-tl 2 1", b"# jottune-tl 1 2"):
            with self.assertRaises(ValueError):
                validator.document.parse_document(marker + b'\n# {}\n# end-jottune-tl\n' + self.data)

    def test_vendored_helper_matches_synth_when_available(self):
        synth = ROOT.parent / "jottune-synth"
        if not (synth / "tools/tl_document.py").is_file():
            self.skipTest("Sibling synth checkout is not available.")
        validator.check_helper_parity(synth)


if __name__ == "__main__":
    unittest.main()

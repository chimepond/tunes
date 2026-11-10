# Copyright (c) 2026 Nenad Micic
# SPDX-License-Identifier: Apache-2.0
"""Portable headers preserve catalog identity and reject metadata disagreement."""
import hashlib
import importlib.util
from pathlib import Path
import unittest
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

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
        self.save_withdrawn()

    def save(self):
        try:
            data = (json.dumps(self.value, indent=2, ensure_ascii=False) + "\n").encode()
        except UnicodeEncodeError:
            data = json.dumps(self.value).encode()
        (self.root / "library.json").write_bytes(data)

    def save_withdrawn(self, records=()):
        (self.root / "withdrawn.json").write_text(json.dumps({"version": 1, "records": list(records)}, indent=2) + "\n")

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

    def test_declared_author_kind_is_accepted_in_catalog_provenance(self):
        discovery = self.value["songs"][0]["variants"][0]["discovery"]
        for provenance in ({"made_by": "agent"}, {"made_by": "human"},
                           {"made_by": "mixed", "model": "reported-model/v1", "model_source": "user-stated"},
                           {"model": "archive-model", "model_source": "unknown"}):
            with self.subTest(provenance=provenance):
                discovery["provenance"] = provenance
                self.save()
                validator.validate(self.root)
        for provenance in ({}, {"made_by": "robot"}, {"made_by": True}, {"made_by": "agent", "model": "reported-model/v1"},
                           {"made_by": "agent", "model_source": "client-reported"}, {"made_by": "agent", "note": "x"}):
            with self.subTest(provenance=provenance):
                discovery["provenance"] = provenance
                self.save()
                with self.assertRaisesRegex(ValueError, "provenance declaration"):
                    validator.validate(self.root)

    def test_display_controls_and_surrogates_are_refused(self):
        for field in ("title", "collection"):
            for control in ("\x00", "\x7f", "\x85", "\u200b", "\u202e", "\ufeff", "\ud800"):
                with self.subTest(field=field, control=repr(control)):
                    self.value["songs"][0][field] = "Music" + control
                    self.save()
                    with self.assertRaisesRegex(ValueError, "song identity"):
                        validator.validate(self.root)
            self.value["songs"][0][field] = "Music"

    def test_invisible_spoofing_text_is_refused_with_its_code_point(self):
        for value, reason in (("Music Box", "U+2028"), ("Music Box", "U+2029"), ("Music", "U+E000"),
                              ("Mu\U000f0000sic", "U+F0000"), ("Mu͸sic", "U+0378"), ("Mu­sic", "U+00AD"),
                              ("⁦Music⁩", "U+2066"), ("Café", "NFC"), ("ㅤ", "blank filler"),
                              ("⠀", "blank filler"), ("Musicᅟ", "blank filler"), ("́", "visible character"),
                              ("Z" + "̶" * 5, "more than 4 combining marks"), ("‌Music", "joiner U+200C"),
                              ("Music‍", "joiner U+200D"), ("1‍2", "joiner U+200D"), ("A ‌ B", "joiner U+200C"),
                              ("a‌‌b", "joiner U+200C"), ("x" * 61, "1 to 60 characters, not 61"), (7, "JSON string")):
            with self.subTest(value=repr(value)):
                self.assertIn(reason, validator.text_problem(value, 60))
        for field in ("title", "collection"):
            for value in ("Music Box", "ㅤ", "Z" + "̶" * 5, "Music", "Café"):
                with self.subTest(field=field, value=repr(value)):
                    self.value["songs"][0][field] = value
                    self.save()
                    with self.assertRaisesRegex(ValueError, "song identity"):
                        validator.validate(self.root)
            self.value["songs"][0][field] = "Music"

    def test_joiners_and_marks_that_real_scripts_and_emoji_need_are_accepted(self):
        for value in ("می‌خواهم",  # Persian ZWNJ between letters
                      "क्‍ष", "क्‌ष",  # Devanagari half form and explicit virama
                      "അവന്‍ പോയി",  # Malayalam chillu written with ZWJ
                      "ශ්‍රී",  # Sinhala ZWJ
                      "\U0001f469‍\U0001f4bb", "\U0001f469\U0001f3fd‍\U0001f4bb",  # emoji ZWJ, skin tone
                      "\U0001f468‍\U0001f469‍\U0001f467‍\U0001f466",  # family
                      "\U0001f3f3️‍\U0001f308",  # rainbow flag
                      "\U0001f1fa\U0001f1f8", "❤️", "1️⃣",  # flag, VS16, keycap
                      "שלום", "नमस्ते", "བཀྲ་ཤིས",
                      "Z" + "̶" * 4, "AㅤB", "Café"):
            with self.subTest(value=repr(value)):
                self.assertIsNone(validator.text_problem(value, 60))
            self.value["songs"][0]["title"] = value
            self.save()
            validator.validate(self.root)

    def test_catalog_tempo_booleans_are_refused(self):
        self.value["songs"][0]["variants"][0]["discovery"]["metadata"] = {
            "tempo": {"min_bpm": True, "max_bpm": True, "source": "creator-declared"}}
        self.save()
        with self.assertRaisesRegex(ValueError, "declared tempo"):
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

    def test_record_limit_is_the_one_megabyte_browser_and_intake_bound(self):
        version = self.value["songs"][0]["variants"][0]
        for size in (1_000_000, 1_000_001):
            with self.subTest(size=size):
                self.record.unlink()
                data = (b"a\n" * (size // 2)) + b"a" * (size % 2)
                digest = hashlib.sha256(data).hexdigest()
                self.record = self.root / "records" / (digest + ".tl")
                self.record.write_bytes(data)
                version["paths"] = ["records/" + self.record.name]
                version["discovery"]["record_sha256"] = digest
                self.save()
                if size == 1_000_000:
                    self.assertEqual(validator.validate(self.root)[0]["bytes"], size)
                else:
                    with self.assertRaisesRegex(ValueError, "empty or oversized TL file: records/" + digest):
                        validator.validate(self.root)
        self.assertEqual(validator.MAX_RECORD_BYTES, 1_000_000)

    def test_catalog_bytes_must_be_canonical(self):
        self.value["songs"][0]["title"] = "Café"
        self.save()
        self.assertEqual(validator.validate(self.root)[0]["songs"], 1)
        canonical = (self.root / "library.json").read_bytes()
        for data in (json.dumps(self.value).encode(), json.dumps(self.value, indent=2).encode() + b"\n",
                     json.dumps(self.value, indent=4).replace("\n", "\r\n").encode() + b"\r\n",
                     canonical[:-1], canonical + b"\n", canonical.replace(b",\n", b", \n", 1)):
            with self.subTest(data=data[:40]):
                (self.root / "library.json").write_bytes(data)
                with self.assertRaisesRegex(ValueError, "library.json is not in canonical form"):
                    validator.validate(self.root)

    def test_withdrawn_list_is_unique_valid_and_absent_from_records(self):
        entry = {"record_sha256": "a" * 64, "song_id": "gone-" + "a" * 12, "date": "2026-11-08"}
        self.save_withdrawn([entry, dict(entry, record_sha256="b" * 64, song_id=self.value["songs"][0]["id"])])
        self.assertEqual(validator.validate(self.root)[0]["songs"], 1)
        self.save_withdrawn([entry, dict(entry, song_id="other-" + "a" * 12)])
        with self.assertRaisesRegex(ValueError, "Duplicate withdrawn record"):
            validator.validate(self.root)
        self.save_withdrawn([dict(entry, record_sha256=self.digest)])
        with self.assertRaisesRegex(ValueError, "still present: records/" + self.digest):
            validator.validate(self.root)
        for invalid in ({"date": "2026-02-30"}, {"date": "08.11.2026"}, {"record_sha256": "A" * 64},
                        {"song_id": "Gone"}, {"reason": "rights"}):
            with self.subTest(invalid=invalid):
                self.save_withdrawn([{**entry, **invalid}])
                with self.assertRaisesRegex(ValueError, "Invalid withdrawn record"):
                    validator.validate(self.root)
        (self.root / "withdrawn.json").write_text('{"version": 2, "records": []}')
        with self.assertRaisesRegex(ValueError, "Invalid withdrawn.json"):
            validator.validate(self.root)
        (self.root / "withdrawn.json").write_text('{"version":1,"records":[]}')
        with self.assertRaisesRegex(ValueError, "withdrawn.json is not in canonical form"):
            validator.validate(self.root)
        (self.root / "withdrawn.json").unlink()
        with self.assertRaisesRegex(ValueError, "withdrawn.json"):
            validator.validate(self.root)

    def test_shipped_catalog_and_withdrawn_list_pass(self):
        summary, files = validator.validate(ROOT)
        self.assertEqual(summary["versions"], len(files))
        self.assertEqual((ROOT / "withdrawn.json").read_bytes(), b'{\n  "version": 1,\n  "records": []\n}\n')
        self.assertIn("*.json text eol=lf", (ROOT / ".gitattributes").read_text().splitlines())

    def test_metadata_links_only_to_product_catalog(self):
        self.value["songs"][0]["variants"][0]["discovery"]["source"] = "archive/notes.md"
        self.save()
        with self.assertRaisesRegex(ValueError, "metadata linkage"):
            validator.validate(self.root)


if __name__ == "__main__":
    unittest.main()

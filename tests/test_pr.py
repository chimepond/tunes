# Copyright (c) 2026 Nenad Micic
# SPDX-License-Identifier: Apache-2.0
"""Data-only Git proposals, hostile metadata and the native admission boundary."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("tunes_pr", ROOT / "tools/check_pr.py")
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)

FAKE_HARNESS = '''#!/usr/bin/env python3
import json,pathlib,sys,time
config=json.loads(pathlib.Path(__file__).with_suffix('.json').read_text())
if config.get('sleep'):time.sleep(1)
if config.get('flood'):print('x'*10000);sys.exit(0)
fs=config.get('fs',48000);frames=config.get('frames',1008000)
pcm=config.get('pcm_bytes',frames*2)
if '--inspect' in sys.argv:
 print('INSPECT fs=%s frames=%s channels=1 length_ns=%s pcm_bytes=%s render_bytes_lower_bound=%s hash_present=%s expected_hash=1234567890abcdef'%
       (fs,frames,config.get('length_ns',21000000000),pcm,config.get('lower',8000000),config.get('saved',1)))
else:
 source=pathlib.Path(sys.argv[2]);target=pathlib.Path(sys.argv[sys.argv.index('--merged')+1])
 target.write_bytes(b'changed' if config.get('rewrite') else source.read_bytes())
 print('PLAY hash=%s expected=1234567890abcdef match=%s block_indep=%s'%
       (config.get('hash','1234567890abcdef'),config.get('match','yes'),config.get('block','yes')))
sys.exit(config.get('status',0))
'''


class ProposalTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="tunes-pr-test-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.repo = self.directory / "repository"
        self.repo.mkdir()
        self.git("init", "-q")
        self.git("config", "user.name", "Test contributor")
        self.git("config", "user.email", "test@example.invalid")
        (self.repo / "records").mkdir()
        data = b"baseline record\n"
        self.old_digest = hashlib.sha256(data).hexdigest()
        (self.repo / "records" / (self.old_digest + ".tl")).write_bytes(data)
        identity = "baseline-" + self.old_digest[:12]
        self.baseline = {"version": 1, "songs": [{"id": identity, "title": "Baseline", "collection": "Examples",
            "variants": [{"kind": "standard", "id": identity + "/standard", "paths": ["records/" + self.old_digest + ".tl"],
                          "discovery": {"record_sha256": self.old_digest, "source": "library.json",
                                        "featured": {"order": 1, "note": "Retained example"}}}]}]}
        self.save_catalog(self.baseline)
        self.base = self.commit()
        self.harness = self.directory / "native-renderer"
        self.harness.write_text(FAKE_HARNESS)
        self.harness.chmod(0o755)
        self.native_config()

    def git(self, *args):
        return subprocess.check_output(["git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false",
            "-c", "core.fsmonitor=false", "-C", str(self.repo), *args], stderr=subprocess.DEVNULL).decode().strip()

    def commit(self):
        self.git("add", "--all")
        self.git("commit", "-q", "--allow-empty", "-m", "Check data proposal")
        return self.git("rev-parse", "HEAD")

    def save_catalog(self, value):
        (self.repo / "library.json").write_text(json.dumps(value))

    def native_config(self, **values):
        self.harness.with_suffix(".json").write_text(json.dumps(values))

    def proposal(self, data=b"canonical proposed record\n"):
        digest = hashlib.sha256(data).hexdigest()
        self.digest = digest
        self.record = self.repo / "records" / (digest + ".tl")
        self.record.write_bytes(data)
        (self.repo / "contributions").mkdir(exist_ok=True)
        self.declaration_path = self.repo / "contributions" / (digest + ".json")
        self.declaration = {"version": 1, "record_sha256": digest,
            "song": {"id": "proposal-" + digest[:12], "title": "New proposal", "collection": "Contributions"},
            "made_by": "agent", "genre": None, "tempo": None, "model": None, "consent": copy.deepcopy(checker.CONSENT)}
        self.candidate = copy.deepcopy(self.baseline)
        self.song = {**self.declaration["song"], "variants": [{"kind": "standard", "id": "proposal-" + digest[:12] + "/standard",
            "paths": ["records/" + digest + ".tl"], "discovery": {"record_sha256": digest, "source": "library.json"}}]}
        self.candidate["songs"].append(self.song)
        self.save()

    def save(self):
        self.declaration_path.write_text(json.dumps(self.declaration))
        self.save_catalog(self.candidate)

    def check(self, harness=None):
        return checker.check(self.repo, self.base, self.commit(), harness or self.harness)

    def test_valid_unknown_metadata_is_explicit_and_not_inferred(self):
        self.proposal()
        result = self.check()
        self.assertTrue(result["eligible"])
        self.assertEqual(result["approval"], "required")
        self.assertEqual(result["existing_records"], 1)
        self.assertEqual(result["block_independence"], "verified")
        self.assertNotIn("provenance", self.song["variants"][0]["discovery"])

    def test_declared_metadata_matches_the_appended_catalog(self):
        self.proposal()
        self.declaration.update(genre="ambient", tempo={"min_bpm": 90, "max_bpm": 95},
                                model={"id": "reported-model/v1", "source": "client-reported"})
        discovery = self.song["variants"][0]["discovery"]
        discovery.update(metadata={"genre": {"value": "ambient", "source": "creator-declared"},
            "tempo": {"min_bpm": 90, "max_bpm": 95, "source": "creator-declared"}},
            provenance={"model": "reported-model/v1", "model_source": "client-reported"})
        self.save()
        self.assertTrue(self.check()["eligible"])

    def test_each_consent_is_exact_true(self):
        self.proposal()
        for key in ("cc0", "dataset", "age16", "history", "original"):
            for refused in (False, 1, "true", None):
                with self.subTest(key=key, value=refused):
                    self.declaration["consent"] = copy.deepcopy(checker.CONSENT)
                    self.declaration["consent"][key] = refused
                    self.save()
                    with self.assertRaisesRegex(ValueError, "declarations"):
                        self.check()

    def test_old_consent_and_unknown_fields_are_refused(self):
        self.proposal()
        self.declaration["consent"]["version"] = "2"
        self.save()
        with self.assertRaisesRegex(ValueError, "declarations"):
            self.check()
        self.declaration["consent"] = copy.deepcopy(checker.CONSENT)
        self.declaration["unexpected"] = True
        self.save()
        with self.assertRaisesRegex(ValueError, "fields"):
            self.check()

    def test_missing_unknown_declarations_and_invented_model_source(self):
        self.proposal()
        del self.declaration["model"]
        self.save()
        with self.assertRaisesRegex(ValueError, "fields"):
            self.check()
        self.declaration["model"] = {"id": "invented", "source": "guessed"}
        self.save()
        with self.assertRaisesRegex(ValueError, "model"):
            self.check()

    def test_bad_labels_tempo_and_title_are_refused(self):
        self.proposal()
        for item in ({"genre": "<script>"}, {"tempo": {"min_bpm": 0, "max_bpm": 90}},
                     {"tempo": {"min_bpm": True, "max_bpm": 90}},
                     {"tempo": {"min_bpm": 1, "max_bpm": 10**400}}):
            with self.subTest(item=list(item)):
                self.declaration.update(genre=None, tempo=None)
                self.declaration.update(item)
                self.save()
                with self.assertRaises(ValueError):
                    self.check()
        self.declaration.update(genre=None, tempo=None)
        self.declaration["song"]["title"] = "x" * 61
        self.save()
        with self.assertRaisesRegex(ValueError, "Title"):
            self.check()

    def test_duplicate_json_keys_and_nonfinite_numbers(self):
        self.proposal()
        self.declaration_path.write_text(self.declaration_path.read_text().replace('"version": 1', '"version": 1, "version": 1', 1))
        with self.assertRaisesRegex(ValueError, "Duplicate JSON"):
            self.check()
        self.save()
        self.declaration_path.write_text(self.declaration_path.read_text().replace('"tempo": null', '"tempo": NaN'))
        with self.assertRaisesRegex(ValueError, "Non-finite"):
            self.check()
        self.save()
        self.declaration_path.write_text(self.declaration_path.read_text().replace('"tempo": null', '"tempo": {"min_bpm":1,"max_bpm":1e999}'))
        with self.assertRaises(ValueError):
            self.check()

    def test_existing_hash_cannot_be_resubmitted(self):
        self.proposal(b"baseline record\n")
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.check()

    def test_existing_catalog_is_immutable_including_json_types(self):
        self.proposal()
        self.candidate["songs"][0]["variants"][0]["discovery"]["featured"]["order"] = True
        self.save()
        with self.assertRaisesRegex(ValueError, "existing catalog"):
            self.check()

    def test_existing_record_and_unrelated_code_changes_are_refused(self):
        self.proposal()
        old = self.repo / "records" / (self.old_digest + ".tl")
        old.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "Only one"):
            self.check()
        old.write_bytes(b"baseline record\n")
        (self.repo / "execute-me.py").write_text("raise SystemExit('must not execute')")
        with self.assertRaisesRegex(ValueError, "Only one"):
            self.check()

    def test_symlink_and_executable_modes_are_refused(self):
        self.proposal()
        self.record.chmod(0o755)
        with self.assertRaisesRegex(ValueError, "mode 100644"):
            self.check()
        self.record.unlink()
        self.record.symlink_to(self.harness)
        with self.assertRaisesRegex(ValueError, "mode 100644"):
            self.check()

    def test_identity_utf8_nul_and_size_boundaries(self):
        self.proposal()
        self.record.write_bytes(b"different")
        with self.assertRaisesRegex(ValueError, "SHA identity"):
            self.check()
        for data in (b"\xff", b"bad\0data", b"x" * (checker.MAX_RECORD_BYTES + 1)):
            with self.subTest(bytes=len(data)):
                self.setUp_proposal_data(data)
                with self.assertRaises(ValueError):
                    self.check()

    def setUp_proposal_data(self, data):
        self.record.unlink()
        self.declaration_path.unlink()
        self.proposal(data)

    def test_catalog_must_match_the_declaration_without_editorial_flags(self):
        self.proposal()
        self.song["title"] = "Different title"
        self.save()
        with self.assertRaisesRegex(ValueError, "differs"):
            self.check()
        self.song["title"] = self.declaration["song"]["title"]
        self.song["variants"][0]["discovery"]["featured"] = {"order": 2, "note": "Self-selected"}
        self.save()
        with self.assertRaisesRegex(ValueError, "differs"):
            self.check()

    def test_duration_boundaries_and_memory_profile_before_replay(self):
        self.proposal()
        for values in ({"frames": 20 * 48000}, {"frames": 301 * 48000}, {"fs": 0},
                       {"length_ns": 300_000_000_001}, {"lower": 193 * 1024 * 1024}, {"pcm_bytes": 1}):
            with self.subTest(values=values):
                self.native_config(**values)
                with self.assertRaises(ValueError):
                    self.check()
        self.native_config(frames=300 * 48000, length_ns=300_000_000_000)
        self.assertTrue(self.check()["eligible"])
        self.native_config(frames=20 * 48000 + 1)
        self.assertTrue(self.check()["eligible"])

    def test_community_sample_rates_are_explicit_and_other_native_rates_are_refused(self):
        self.proposal()
        for rate in (44100, 48000):
            with self.subTest(accepted_rate=rate):
                self.native_config(fs=rate, frames=21 * rate)
                self.assertEqual(self.check()["fs"], rate)
        for rate in (1, 8000, 32000, 96000):
            with self.subTest(refused_rate=rate):
                self.native_config(fs=rate, frames=21 * rate)
                with self.assertRaisesRegex(ValueError, "sample rate"):
                    self.check()

    def test_native_saved_hash_block_independence_and_canonical_bytes(self):
        self.proposal()
        for values in ({"saved": 0}, {"hash": "ffffffffffffffff"}, {"match": "no"},
                       {"block": "unchecked"}, {"rewrite": True}, {"status": 1}):
            with self.subTest(values=values):
                self.native_config(**values)
                with self.assertRaises(ValueError):
                    self.check()

    def test_native_time_and_output_limits(self):
        self.proposal()
        self.native_config(sleep=True)
        with patch.object(checker, "NATIVE_TIMEOUT", 0.05), self.assertRaisesRegex(ValueError, "time limit"):
            self.check()
        self.native_config(flood=True)
        with patch.object(checker, "MAX_NATIVE_OUTPUT", 100), self.assertRaisesRegex(ValueError, "output limit"):
            self.check()

    def test_full_commit_ids_and_renderer_outside_candidate_are_required(self):
        self.proposal()
        head = self.commit()
        with self.assertRaisesRegex(ValueError, "commit ID"):
            checker.check(self.repo, "HEAD", head, self.harness)
        untrusted = self.repo / "renderer"
        untrusted.write_text(FAKE_HARNESS)
        with self.assertRaisesRegex(ValueError, "outside"):
            checker.check(self.repo, self.base, head, untrusted)

    def test_cli_returns_eligibility_without_merging_or_executing_candidate_code(self):
        self.proposal()
        head = self.commit()
        command = [sys.executable, str(ROOT / "tools/check_pr.py"), "--repo", str(self.repo),
                   "--base", self.base, "--head", head, "--harness", str(self.harness)]
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout)["approval"], "required")
        self.assertEqual(self.git("rev-parse", "HEAD"), head)
        (self.repo / "untrusted-script.py").write_text("raise RuntimeError('must never run')")
        command[command.index("--head") + 1] = self.commit()
        refused = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(refused.returncode, 1)
        self.assertIn("Proposal refused:", refused.stderr)
        self.assertNotIn("Traceback", refused.stderr)

    def test_candidate_worktree_contents_are_not_loaded(self):
        self.proposal()
        head = self.commit()
        self.record.write_bytes(b"uncommitted poisoned record")
        self.declaration_path.write_text("not JSON")
        (self.repo / "library.json").write_text("not JSON")
        self.assertTrue(checker.check(self.repo, self.base, head, self.harness)["eligible"])

    @unittest.skipUnless(os.environ.get("JOTTUNE_PR_HARNESS"), "Set JOTTUNE_PR_HARNESS for the real native boundary.")
    def test_real_native_record_and_hash_refusal(self):
        harness = Path(os.environ["JOTTUNE_PR_HARNESS"]).resolve()
        engine = subprocess.check_output([str(harness), "--engine"], text=True).strip().removeprefix("ENGINE ")
        source = self.directory / "draft.tl"
        canonical = self.directory / "complete.tl"
        for index, rate in enumerate((44100, 48000)):
            source.write_text("timeline draft1\nengine " + engine + "\nfs " + str(rate) + "\nlength_ns 21000000000\nstreams test\n"
                "patch sine 0 65536 0 0 0 256 5 500 200 800\npatch sine 1 65536 0 0 0 0 5 500 200 800\n"
                "voice 0 sine\n0 test 0 on 441000 32767\n1000000000 test 0 off\n")
            subprocess.run([str(harness), "--play", str(source), "--merged", str(canonical)], check=True, capture_output=True)
            if index:
                self.setUp_proposal_data(canonical.read_bytes())
            else:
                self.proposal(canonical.read_bytes())
            observed = self.check(harness)
            self.assertTrue(observed["eligible"])
            self.assertEqual(observed["fs"], rate)
            self.assertEqual(observed["frames"], 21 * rate)
        damaged = re.sub(rb"(?m)^hash ([a-f0-9])", lambda found: b"hash " +
                         (b"f" if found[1] != b"f" else b"e"), canonical.read_bytes(), count=1)
        self.setUp_proposal_data(damaged)
        with self.assertRaisesRegex(ValueError, "inspection|replay"):
            self.check(harness)
        for rate in (1, 96000):
            source.write_text(source.read_text().replace("fs 48000\n", "fs " + str(rate) + "\n").replace("fs 1\n", "fs " + str(rate) + "\n"))
            subprocess.run([str(harness), "--play", str(source), "--merged", str(canonical)], check=True, capture_output=True)
            self.setUp_proposal_data(canonical.read_bytes())
            with self.assertRaisesRegex(ValueError, "sample rate"):
                self.check(harness)


if __name__ == "__main__":
    unittest.main()

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
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
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
 data=source.read_bytes()
 target.write_bytes(b'changed' if config.get('rewrite') else data)
 if '--merged-body' in sys.argv:
  body=data.split(b'# end-jottune-tl\\n',1)[1] if data.startswith(b'# jottune-tl ') else data
  pathlib.Path(sys.argv[sys.argv.index('--merged-body')+1]).write_bytes(body)
 print('PLAY hash=%s expected=1234567890abcdef match=%s block_indep=%s'%
       (config.get('hash','1234567890abcdef'),config.get('match','yes'),config.get('block','yes')))
if config.get('stderr'):sys.stderr.write(config['stderr'])
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
        self.save_withdrawn()
        self.base = self.commit()
        self.harness = self.directory / "native-renderer"
        self.harness.write_text(FAKE_HARNESS)
        self.harness.chmod(0o755)
        self.native_config()

    def test_portable_proposal_preserves_body_identity_and_publication_consent(self):
        body = b"canonical proposed record\n"
        data = checker.catalog.document.compose_document(body, {"title": "New proposal", "made_by": "agent", "license": "CC0-1.0"})
        self.proposal(data)
        discovery = self.song["variants"][0]["discovery"]
        discovery["document_sha256"] = hashlib.sha256(data).hexdigest()
        self.save()
        self.assertTrue(self.check()["eligible"])
        self.assertEqual(self.digest, hashlib.sha256(body).hexdigest())
        self.declaration["consent"]["dataset"] = False
        self.save()
        with self.assertRaisesRegex(ValueError, "declarations are required"):
            self.check()

    def test_portable_proposal_conflicting_declaration_is_refused(self):
        self.proposal(checker.catalog.document.compose_document(b"new body\n", {"title": "Different title"}))
        with self.assertRaisesRegex(ValueError, "conflicts with external title"):
            self.check()

    def test_metadata_only_proposal_cannot_duplicate_existing_body(self):
        data = checker.catalog.document.compose_document(b"baseline record\n", {"title": "Another label"})
        self.proposal(data)
        with self.assertRaisesRegex(ValueError, "hash already exists"):
            self.check()

    def git(self, *args):
        return subprocess.check_output(["git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false",
            "-c", "core.fsmonitor=false", "-C", str(self.repo), *args], stderr=subprocess.DEVNULL).decode().strip()

    def commit(self):
        self.git("add", "--all")
        self.git("commit", "-q", "--allow-empty", "-m", "Check data proposal")
        return self.git("rev-parse", "HEAD")

    def save_catalog(self, value):
        (self.repo / "library.json").write_bytes((json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode())

    def save_withdrawn(self, records=()):
        (self.repo / "withdrawn.json").write_text(json.dumps({"version": 1, "records": list(records)}, indent=2) + "\n")

    def native_config(self, **values):
        self.harness.with_suffix(".json").write_text(json.dumps(values))

    def proposal(self, data=b"canonical proposed record\n"):
        digest = checker.catalog.document.body_sha256(data) if data.startswith(b"# jottune-tl ") else hashlib.sha256(data).hexdigest()
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
            "paths": ["records/" + digest + ".tl"], "discovery": {"record_sha256": digest, "source": "library.json",
                                                              "provenance": {"made_by": "agent"}}}]}
        self.candidate["songs"].append(self.song)
        self.save()

    def save(self):
        self.declaration_path.write_text(json.dumps(self.declaration))
        self.save_catalog(self.candidate)

    def propose(self, parent=None):
        """Commit the worktree as one proposal commit on the trusted base, or on the given parent."""
        self.git("add", "--all")
        return self.git("commit-tree", self.git("write-tree"), "-p", parent or self.base, "-m", "Check data proposal")

    def check(self, harness=None):
        return checker.check(self.repo, self.base, self.propose(), harness or self.harness)

    def add_base_record(self, name, data):
        """Commit one more retained song holding these record bytes as the trusted base."""
        digest = hashlib.sha256(data).hexdigest()
        (self.repo / "records" / (digest + ".tl")).write_bytes(data)
        identity = name + "-" + digest[:12]
        self.baseline["songs"].append({"id": identity, "title": name.title(), "collection": "Examples",
            "variants": [{"kind": "standard", "id": identity + "/standard", "paths": ["records/" + digest + ".tl"],
                          "discovery": {"record_sha256": digest, "source": "library.json"}}]})
        self.save_catalog(self.baseline)
        self.base = self.commit()
        return "records/" + digest + ".tl"

    def propose_entries(self, parent, entries):
        """Commit the parent tree plus top-level entries that a case-insensitive worktree cannot hold."""
        rows = self.git("ls-tree", parent).splitlines()
        for name, data in entries.items():
            blob = subprocess.run(["git", "-C", str(self.repo), "hash-object", "-w", "--stdin"], input=data,
                                  capture_output=True, check=True).stdout.decode().strip()
            rows.append("100644 blob " + blob + "\t" + name)
        tree = subprocess.run(["git", "-C", str(self.repo), "mktree"], input="\n".join(rows).encode() + b"\n",
                              capture_output=True, check=True).stdout.decode().strip()
        return self.git("commit-tree", tree, "-p", parent, "-m", "Check data proposal")

    def workflow_steps(self):
        """Map each workflow step name to its YAML body and its run script."""
        workflow = (ROOT / ".github/workflows/tune-pr.yml").read_text()
        steps = {}
        for block in workflow.split("\n      - name: ")[1:]:
            name, _, body = block.partition("\n")
            steps[name] = (body, textwrap.dedent(body.partition("        run: |\n")[2]))
        return steps

    def workspace(self):
        """Lay out the CI workspace: a base checkout holding fetched candidate objects, trusted tools and renderer."""
        workspace = self.directory / "workspace"
        tunes = workspace / "trusted-tunes"
        subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "clone", "-q", "--no-tags", str(self.repo), str(tunes)],
                       check=True, capture_output=True)
        shutil.copytree(ROOT / "tools", tunes / "tools", ignore=shutil.ignore_patterns("__pycache__"))
        harness = workspace / "trusted-sdk/build/s12e10p1v1/harness"
        harness.parent.mkdir(parents=True)
        shutil.copy2(self.harness, harness)
        shutil.copy2(self.harness.with_suffix(".json"), harness.with_suffix(".json"))
        return workspace

    def run_step(self, name, workspace=None, **values):
        """Run one workflow step script in bash with GitHub's file-command environment."""
        files = {key: self.directory / (key.lower() + ".txt") for key in ("GITHUB_STEP_SUMMARY", "GITHUB_OUTPUT")}
        for path in files.values():
            path.write_text("")
        tools = self.directory / "step-bin"
        tools.mkdir(exist_ok=True)
        (tools / "python3").write_text("#!/bin/sh\nexec " + json.dumps(sys.executable) + ' "$@"\n')
        (tools / "python3").chmod(0o755)
        environment = {**os.environ, "PATH": str(tools) + os.pathsep + os.environ["PATH"], "RUNNER_TEMP": str(self.directory),
                       "GITHUB_WORKSPACE": str(workspace or self.directory), "BASE_SHA": self.base, "HEAD_SHA": self.base,
                       "SDK_SHA": "", **{key: str(path) for key, path in files.items()}, **values}
        result = subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", self.workflow_steps()[name][1]],
                                cwd=self.directory, env=environment, capture_output=True, text=True, timeout=120)
        return result, files["GITHUB_STEP_SUMMARY"].read_text(), files["GITHUB_OUTPUT"].read_text()

    def test_valid_unknown_metadata_is_explicit_and_not_inferred(self):
        self.proposal()
        result = self.check()
        self.assertTrue(result["eligible"])
        self.assertEqual(result["approval"], "required")
        self.assertEqual(result["existing_records"], 1)
        self.assertEqual(result["block_independence"], "verified")
        self.assertEqual(result["pcm_duplicate_of"], [])
        self.assertEqual(result["title_collision"], [])
        # Only the declared author kind is kept: no model is inferred.
        self.assertEqual(self.song["variants"][0]["discovery"]["provenance"], {"made_by": "agent"})

    def test_declared_metadata_matches_the_appended_catalog(self):
        self.proposal()
        self.declaration.update(genre="ambient", tempo={"min_bpm": 90, "max_bpm": 95},
                                model={"id": "reported-model/v1", "source": "client-reported"})
        discovery = self.song["variants"][0]["discovery"]
        # The documented key order puts metadata before provenance.
        del discovery["provenance"]
        discovery.update(metadata={"genre": {"value": "ambient", "source": "creator-declared"},
            "tempo": {"min_bpm": 90, "max_bpm": 95, "source": "creator-declared"}},
            provenance={"made_by": "agent", "model": "reported-model/v1", "model_source": "client-reported"})
        self.save()
        self.assertTrue(self.check()["eligible"])

    def test_appended_tempo_boolean_cannot_equal_a_numeric_declaration(self):
        self.proposal()
        self.declaration["tempo"] = {"min_bpm": 1, "max_bpm": 1}
        discovery = self.song["variants"][0]["discovery"]
        discovery["metadata"] = {"tempo": {"min_bpm": True, "max_bpm": True, "source": "creator-declared"}}
        discovery["provenance"] = discovery.pop("provenance")
        self.save()
        with self.assertRaisesRegex(ValueError, "differs.*JSON types"):
            self.check()

    def test_complete_post_change_catalog_checks_unchanged_records_too(self):
        (self.repo / "records" / (self.old_digest + ".tl")).write_bytes(b"damaged trusted baseline")
        self.base = self.commit()
        self.proposal()
        with self.assertRaisesRegex(ValueError, "canonical identity.*" + self.old_digest):
            self.check()

    def test_hidden_display_controls_in_title_and_collection_are_refused(self):
        self.proposal()
        for field in ("title", "collection"):
            for control in ("\x7f", "\x85", "\u200b", "\u202e", "\ufeff"):
                with self.subTest(field=field, control=repr(control)):
                    self.declaration["song"][field] = "Music" + control
                    self.song[field] = self.declaration["song"][field]
                    self.save()
                    with self.assertRaises(ValueError):
                        self.check()
            self.declaration["song"][field] = self.song[field] = "Music"

    def test_title_rules_refuse_invisible_text_by_code_point_and_keep_joined_scripts(self):
        self.proposal()
        for title, reason in (("Music\u2028Box", "U\\+2028"), ("\u3164", "blank filler"), ("Mu\ue000sic", "U\\+E000"),
                              ("Cafe\u0301", "NFC"), ("Z" + "\u0336" * 5, "more than 4 combining marks"),
                              ("Music \u200d Box", "joiner U\\+200D")):
            with self.subTest(title=repr(title)):
                self.declaration["song"]["title"] = self.song["title"] = title
                self.save()
                with self.assertRaisesRegex(ValueError, "Title must contain 1 to 60 ordinary characters: it .*" + reason):
                    self.check()
        for title in ("\u0645\u06cc\u200c\u062e\u0648\u0627\u0647\u0645", "\U0001f469\U0001f3fd\u200d\U0001f4bb",
                      "\U0001f3f3\ufe0f\u200d\U0001f308"):
            with self.subTest(title=repr(title)):
                self.declaration["song"]["title"] = self.song["title"] = title
                self.save()
                self.assertTrue(self.check()["eligible"])
        self.declaration["song"]["collection"] = self.song["collection"] = "\u2800"
        self.save()
        with self.assertRaisesRegex(ValueError, "Invalid collection declaration: it must not start or end"):
            self.check()

    def test_declared_author_kind_is_kept_in_the_catalog_entry(self):
        self.proposal()
        provenance = self.song["variants"][0]["discovery"]["provenance"]
        for made_by in ("human", "mixed"):
            with self.subTest(made_by=made_by):
                self.declaration["made_by"] = provenance["made_by"] = made_by
                self.save()
                self.assertTrue(self.check()["eligible"])
        provenance["made_by"] = "agent"
        self.save()
        with self.assertRaisesRegex(ValueError, "differs from its declared metadata"):
            self.check()
        del self.song["variants"][0]["discovery"]["provenance"]
        self.save()
        with self.assertRaisesRegex(ValueError, "differs from its declared metadata"):
            self.check()

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

    def test_withdrawn_records_and_song_ids_cannot_be_proposed_again(self):
        data = b"canonical proposed record\n"
        digest = hashlib.sha256(data).hexdigest()
        entry = {"record_sha256": digest, "song_id": "proposal-" + digest[:12], "date": "2026-11-08"}
        self.save_withdrawn([entry])
        self.base = self.commit()
        self.proposal(data)
        with self.assertRaisesRegex(ValueError, "record was withdrawn.*" + digest):
            self.check()
        self.save_withdrawn()
        with self.assertRaisesRegex(ValueError, "record was withdrawn"):
            self.check()
        for listed in ([dict(entry, record_sha256="a" * 64)], None):
            with self.subTest(listed=listed):
                self.record.unlink()
                self.declaration_path.unlink()
                self.save_catalog(self.baseline)
                if listed is None:
                    (self.repo / "withdrawn.json").unlink()
                else:
                    self.save_withdrawn(listed)
                self.base = self.commit()
                self.proposal(data)
                with self.assertRaisesRegex(ValueError, "song ID was withdrawn" if listed else "withdrawn.json"):
                    self.check()

    def test_existing_catalog_is_immutable_including_json_types(self):
        self.proposal()
        self.candidate["songs"][0]["variants"][0]["discovery"]["featured"]["order"] = True
        self.save()
        with self.assertRaisesRegex(ValueError, "existing catalog"):
            self.check()

    def test_catalog_bytes_are_canonical_and_a_pure_append(self):
        self.proposal()
        self.declaration["song"]["title"] = self.song["title"] = "Café proposal"
        self.save()
        self.assertTrue(self.check()["eligible"])
        canonical = (self.repo / "library.json").read_bytes()
        first = self.candidate["songs"][0]
        reordered = [{**self.candidate, "songs": [dict(reversed(list(first.items()))), self.song]},
                     {**self.candidate, "songs": [first, {"title": self.song["title"], **self.song}]}]
        variants = [json.dumps(self.candidate).encode(), json.dumps(self.candidate, indent=2).encode() + b"\n",
                    json.dumps(self.candidate, indent=4).replace("\n", "\r\n").encode() + b"\r\n",
                    canonical[:-1], canonical + b"\n", canonical.replace(b",\n", b", \n", 1)]
        variants += [(json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode() for value in reordered]
        for data in variants:
            with self.subTest(data=data[:60]):
                (self.repo / "library.json").write_bytes(data)
                with self.assertRaisesRegex(ValueError, "only the declared entry appended in canonical form"):
                    self.check()
        self.record.unlink()
        self.declaration_path.unlink()
        (self.repo / "library.json").write_text(json.dumps(self.baseline))
        self.base = self.commit()
        self.proposal()
        with self.assertRaisesRegex(ValueError, "Trusted base catalog is not in canonical form"):
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

    def test_refusals_list_sorted_bounded_paths(self):
        self.proposal()
        for index in range(7):
            (self.repo / ("extra-" + str(6 - index) + ".txt")).write_text("x")
        with self.assertRaises(ValueError) as caught:
            self.check()
        self.assertIn('Unexpected changes: "extra-0.txt", "extra-1.txt", "extra-2.txt", "extra-3.txt", "extra-4.txt" '
                      "and 2 more.", str(caught.exception))
        for index in range(7):
            (self.repo / ("extra-" + str(index) + ".txt")).unlink()
        self.save_catalog(self.baseline)
        with self.assertRaisesRegex(ValueError, 'may change\\. Missing: "library\\.json"\\.\\Z'):
            self.check()
        self.save()
        (self.repo / "contributions" / ("0" * 64 + ".json")).write_text("{}")
        with self.assertRaisesRegex(ValueError, 'It adds "contributions/0{64}\\.json", "contributions/' + self.digest +
                                    '\\.json"\\.\\Z'):
            self.check()

    def test_intermediate_commits_cannot_add_unchecked_history(self):
        self.proposal()
        smuggled = self.repo / "notes" / "smuggled.txt"
        smuggled.parent.mkdir()
        smuggled.write_text("never checked, yet kept by a merge or rebase merge")
        first = self.propose()
        smuggled.unlink()
        head = self.propose(first)
        with self.assertRaisesRegex(ValueError, first + ".*outside the final proposal.*notes/smuggled.txt"):
            checker.check(self.repo, self.base, head, self.harness)

    def test_intermediate_variants_modes_and_deletions_are_refused(self):
        self.proposal()
        old = self.repo / "records" / (self.old_digest + ".tl")
        final = self.record.read_bytes()
        cases = (("records/" + self.digest + ".tl", lambda: self.record.write_bytes(b"earlier unchecked take\n"),
                  lambda: self.record.write_bytes(final)),
                 ("records/" + self.digest + ".tl", lambda: self.record.chmod(0o755), lambda: self.record.chmod(0o644)),
                 ("contributions/" + self.digest + ".json", lambda: self.declaration_path.write_text("{}"), self.save),
                 ("library.json", lambda: self.save_catalog({"version": 1, "songs": []}), self.save),
                 ("records/" + self.old_digest + ".tl", old.unlink, lambda: old.write_bytes(b"baseline record\n")))
        for path, change, restore in cases:
            with self.subTest(path=path):
                change()
                first = self.propose()
                restore()
                with self.assertRaisesRegex(ValueError, "outside the final proposal: " + re.escape(json.dumps(path))):
                    checker.check(self.repo, self.base, self.propose(first), self.harness)

    def test_split_commits_holding_only_base_or_final_content_are_eligible(self):
        self.proposal()
        self.declaration_path.unlink()
        self.save_catalog(self.baseline)
        first = self.propose()
        self.save()
        self.assertTrue(checker.check(self.repo, self.base, self.propose(first), self.harness)["eligible"])

    def test_merge_heads_long_chains_and_unrelated_histories_are_refused(self):
        self.proposal()
        side = self.propose()
        tree = self.git("write-tree")
        merge = self.git("commit-tree", tree, "-p", self.base, "-p", side, "-m", "Merge main into the proposal")
        orphan = self.git("commit-tree", tree, "-m", "Unrelated history")
        for head in (merge, orphan):
            with self.subTest(head=head), self.assertRaisesRegex(ValueError, "single-parent"):
                checker.check(self.repo, self.base, head, self.harness)
        head = self.base
        for _ in range(checker.MAX_PROPOSAL_COMMITS):
            head = self.propose(head)
        self.assertTrue(checker.check(self.repo, self.base, head, self.harness)["eligible"])
        with self.assertRaisesRegex(ValueError, "at most"):
            checker.check(self.repo, self.base, self.propose(head), self.harness)

    def test_workflow_fetch_depth_reaches_the_base_of_the_longest_accepted_proposal(self):
        workflow = (ROOT / ".github/workflows/tune-pr.yml").read_text()
        depth = int(re.search(r'fetch [^\n]*--depth=(\d+) origin "\$HEAD_SHA"', workflow)[1])
        self.assertEqual(depth, checker.MAX_PROPOSAL_COMMITS)
        self.proposal()
        head = self.base
        for _ in range(depth):
            head = self.propose(head)
        git = ["git", "-c", "core.hooksPath=/dev/null"]
        for fetched, eligible in ((1, False), (depth, True)):
            with self.subTest(depth=fetched):
                clone = self.directory / ("trusted-tunes-" + str(fetched))
                subprocess.run(git + ["clone", "-q", "--depth=1", "--no-tags", self.repo.as_uri(), str(clone)],
                               check=True, capture_output=True)
                subprocess.run(git + ["-C", str(clone), "fetch", "-q", "--no-tags", "--no-recurse-submodules",
                                      "--depth=" + str(fetched), "origin", head], check=True, capture_output=True)
                if eligible:
                    self.assertTrue(checker.check(clone, self.base, head, self.harness)["eligible"])
                else:
                    with self.assertRaisesRegex(ValueError, "could not read"):
                        checker.check(clone, self.base, head, self.harness)

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
        # One bound for intake, browser input, proposals and the whole-catalog validator.
        self.assertEqual((checker.MAX_RECORD_BYTES, checker.catalog.MAX_RECORD_BYTES), (1_000_000, 1_000_000))

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

    def test_native_refusals_name_the_command_its_limit_and_observed_values(self):
        self.proposal()
        archive = ".* Older archive songs outside these rules are explained under Retained archive exceptions in CONTRIBUTING\\.md\\."
        for values, reason in (({"fs": 32000, "frames": 21 * 32000}, "this record uses 32000 Hz\\."),
                               ({"frames": 20 * 48000}, "lasts 20\\.000 s \\(960000 frames at 48000 Hz, length_ns 21000000000\\)"),
                               ({"lower": 193 * 1024 * 1024}, "needs 202375168 render bytes and 208423168 combined bytes")):
            with self.subTest(values=values):
                self.native_config(**values)
                with self.assertRaisesRegex(ValueError, reason + archive):
                    self.check()
        line = 'render "failed"\x1b[2J at block 7'
        self.native_config(status=3, stderr="\n  " + line + "  \nsecond line\n")
        with self.assertRaises(ValueError) as caught:
            self.check()
        self.assertEqual(str(caught.exception), "Native inspection refused the record (exit status 3): " + json.dumps(line) + ".")
        self.native_config(status=3, stderr="y" * 1000)
        with self.assertRaises(ValueError) as caught:
            self.check()
        self.assertTrue(str(caught.exception).endswith(": " + json.dumps("y" * checker.MAX_QUOTED) + "."))
        self.assertEqual(checker.failure(-9, b""), " (stopped by signal 9)")
        self.native_config(sleep=True)
        with patch.object(checker, "NATIVE_TIMEOUT", 0.05), self.assertRaisesRegex(
                ValueError, "\\ANative inspection exceeded its 0\\.05 s time limit\\.\\Z"):
            self.check()
        self.native_config(flood=True)
        with patch.object(checker, "MAX_NATIVE_OUTPUT", 100), self.assertRaisesRegex(
                ValueError, "\\ANative inspection exceeded its output limit of 100 bytes\\.\\Z"):
            self.check()

    @unittest.skipUnless(os.name == "posix", "POSIX enforces the native CPU-time limit.")
    def test_native_cpu_time_is_bounded_for_inspection_and_replay(self):
        started = time.monotonic()
        status, _, _ = checker.run([sys.executable, "-c", "while True: pass"], cpu_seconds=1, timeout=60)
        self.assertLess(status, 0)
        self.assertLess(time.monotonic() - started, 30)
        self.proposal()
        with patch.object(checker, "run", wraps=checker.run) as run:
            self.assertTrue(self.check()["eligible"])
        native = {call.kwargs["name"]: call.kwargs["cpu_seconds"] for call in run.call_args_list
                  if call.kwargs.get("name", "").startswith("Native")}
        self.assertEqual(native, {"Native inspection": 120, "Native replay": 120})

    @unittest.skipUnless(os.name == "posix", "POSIX enforces the native file-size limit.")
    def test_native_process_cannot_fill_disk_with_expanded_canonical_output(self):
        target = self.directory / "expanded.tl"
        command = [sys.executable, "-c", "import pathlib;pathlib.Path(" + repr(str(target)) +
                   ").write_bytes(b'x' * 2000000)"]
        status, _, _ = checker.run(command, file_bytes=checker.MAX_RECORD_BYTES, timeout=5)
        self.assertNotEqual(status, 0)
        self.assertLessEqual(target.stat().st_size, checker.MAX_RECORD_BYTES)

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux enforces the native address-space limit.")
    def test_linux_native_process_cannot_allocate_beyond_the_memory_budget(self):
        command = [sys.executable, "-c", "try:\n bytearray(" + str(checker.MAX_MEMORY_BYTES * 2) +
                   ")\nexcept MemoryError:\n print('bounded')"]
        status, output, _ = checker.run(command, memory_bytes=checker.MAX_MEMORY_BYTES, timeout=5)
        self.assertEqual(status, 0)
        self.assertEqual(output, b"bounded\n")

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

    def test_maintenance_needs_unchanged_song_data_in_every_commit(self):
        (self.repo / "README.md").write_text("Maintenance note\n")
        (self.repo / "tools").mkdir()
        (self.repo / "tools" / "helper.py").write_text("raise SystemExit('reviewed by maintainers, never run by the check')\n")
        docs = self.propose()
        self.assertTrue(checker.maintenance_only(self.repo, self.base, docs))
        old = self.repo / "records" / (self.old_digest + ".tl")
        attributes, contributions = self.repo / ".gitattributes", self.repo / "contributions"
        withdrawn = {"record_sha256": "0" * 64, "song_id": "gone-" + "0" * 12, "date": "2026-11-08"}
        cases = (("record mode", lambda: old.chmod(0o755), lambda: old.chmod(0o644)),
                 ("new record", lambda: (self.repo / "records" / "extra.tl").write_bytes(b"x"),
                  lambda: (self.repo / "records" / "extra.tl").unlink()),
                 ("catalog bytes", lambda: (self.repo / "library.json").write_text(json.dumps(self.baseline)),
                  lambda: self.save_catalog(self.baseline)),
                 ("withdrawn list", lambda: self.save_withdrawn([withdrawn]), self.save_withdrawn),
                 ("attributes", lambda: attributes.write_text("*.tl text eol=crlf\n"), attributes.unlink),
                 ("contribution", lambda: (contributions.mkdir(), (contributions / "note.json").write_text("{}")),
                  lambda: shutil.rmtree(contributions)))
        for name, change, restore in cases:
            with self.subTest(name=name):
                change()
                changed = self.propose(docs)
                restore()
                self.assertFalse(checker.maintenance_only(self.repo, self.base, changed))
                # The final tree equals the docs-only tree, yet an intermediate commit changed song data.
                self.assertFalse(checker.maintenance_only(self.repo, self.base, self.propose(changed)))
        for name in ("LIBRARY.json", "Records", "Contributions", ".GitAttributes"):
            with self.subTest(name=name):
                self.assertFalse(checker.maintenance_only(self.repo, self.base, self.propose_entries(docs, {name: b"{}\n"})))
        merge = self.git("commit-tree", docs + "^{tree}", "-p", self.base, "-p", docs, "-m", "Merge main")
        with self.assertRaisesRegex(ValueError, "single-parent"):
            checker.maintenance_only(self.repo, self.base, merge)

    def test_cli_classifies_maintenance_only_in_its_explicit_mode(self):
        (self.repo / "README.md").write_text("Maintenance note\n")
        docs = self.propose()
        (self.repo / "README.md").unlink()
        self.proposal()
        song = self.propose()
        command = [sys.executable, str(ROOT / "tools/check_pr.py"), "--repo", str(self.repo), "--base", self.base, "--head"]
        result = subprocess.run(command + [docs, "--classify"], capture_output=True, text=True)
        self.assertEqual((result.returncode, json.loads(result.stdout)), (0, {"maintenance": True}))
        refused = subprocess.run(command + [docs, "--harness", str(self.harness)], capture_output=True, text=True)
        self.assertEqual(refused.returncode, 1)
        self.assertIn("Proposal refused: Each proposal must add exactly one contribution JSON file.", refused.stderr)
        result = subprocess.run(command + [song, "--classify"], capture_output=True, text=True)
        self.assertEqual((result.returncode, result.stdout), (1, ""))
        self.assertTrue(result.stderr.startswith("Running the full song check: "), result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        for extra in ([], ["--classify", "--harness", str(self.harness)]):
            with self.subTest(extra=extra):
                self.assertEqual(subprocess.run(command + [song, *extra], capture_output=True).returncode, 2)

    def test_workflow_runs_song_steps_unless_trusted_classification_proves_maintenance(self):
        steps = self.workflow_steps()
        names = list(steps)
        self.assertEqual(names, ["Require explicit commit identities", "Check out trusted base tools and catalog",
                                 "Fetch candidate objects without checking them out",
                                 "Classify song data changes with trusted base code", "Require the trusted synth commit",
                                 "Check out separately pinned public synth",
                                 "Verify checkouts and build only trusted native source",
                                 "Validate proposal with trusted base checker"])
        classify = steps[names[3]][0]
        self.assertIn("        id: classify\n", classify)
        self.assertIn('"$GITHUB_WORKSPACE/trusted-tunes/tools/check_pr.py" --classify', classify)
        for name in names[:4]:
            self.assertNotIn("        if: ", steps[name][0])
            self.assertNotIn("SDK_SHA", steps[name][0])
        for name in names[4:]:
            self.assertIn("        if: steps.classify.outputs.maintenance != 'true'\n", steps[name][0])

    def test_workflow_annotates_invalid_identities_and_closed_intake(self):
        for values in ({"BASE_SHA": "main"}, {"HEAD_SHA": self.base[:12]}):
            with self.subTest(values=values):
                result, summary, _ = self.run_step("Require explicit commit identities", **values)
                self.assertEqual((result.returncode, result.stdout), (1, "::error title=Invalid commit identity::"
                                 "Pull request base and head must be full lowercase commit IDs.\n"))
                self.assertEqual(summary, "### Tune proposal check\n\nPull request base and head must be full lowercase commit IDs.\n")
        gate = "Require the trusted synth commit"
        for sdk, title in (("", "Song intake closed"), ("main", "Invalid commit identity"),
                           (self.base.upper(), "Invalid commit identity")):
            with self.subTest(sdk=sdk):
                result, summary, _ = self.run_step(gate, SDK_SHA=sdk)
                self.assertEqual(result.returncode, 1)
                self.assertRegex(result.stdout, r"\A::error title=" + title + r"::[^\n]+\n\Z")
                self.assertEqual(summary, "### Tune proposal check\n\n" + result.stdout.split("::", 2)[2])
        self.assertIn("Song intake is closed: a maintainer must publish the synth and configure the trusted "
                      "JOTTUNE_SYNTH_REF", self.run_step(gate)[0].stdout)
        result, summary, _ = self.run_step(gate, SDK_SHA=self.base)
        self.assertEqual((result.returncode, result.stdout, summary), (0, "", ""))

    def test_workflow_summarises_and_annotates_without_running_candidate_commands(self):
        (self.repo / "README.md").write_text("Maintenance note\n")
        docs = self.propose()
        (self.repo / "README.md").unlink()
        self.proposal()
        song = self.propose()
        injected_key = json.dumps("x\n::warning title=Injected::spoofed")
        self.declaration_path.write_text("{" + injected_key + ": 1, " + injected_key + ": 2}")
        injected = self.propose()
        for name, head in (("docs", docs), ("song", song), ("injected", injected)):
            self.git("update-ref", "refs/heads/" + name, head)
        workspace = self.workspace()
        classify, validate = "Classify song data changes with trusted base code", "Validate proposal with trusted base checker"
        result, summary, output = self.run_step(classify, workspace, HEAD_SHA=docs)
        self.assertEqual((result.returncode, output), (0, "maintenance=true\n"), result.stderr)
        self.assertIn("::notice title=Song check not needed::No commit changes song data", result.stdout)
        self.assertIn("song check did not run", summary)
        result, summary, output = self.run_step(classify, workspace, HEAD_SHA=song)
        self.assertEqual((result.returncode, output, summary), (0, "", ""))
        self.assertIn("Running the full song check: ", result.stderr)
        result, summary, _ = self.run_step(validate, workspace, HEAD_SHA=song)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(summary, "### Tune proposal check\n\nEligible for maintainer review: `records/" + self.digest +
                         ".tl` passed the trusted checks. A passing check is not approval or publication.\n")
        result, summary, _ = self.run_step(validate, workspace, HEAD_SHA=injected)
        self.assertEqual(result.returncode, 1)
        lines = result.stdout.splitlines()
        start = next(index for index, line in enumerate(lines) if line.startswith("::stop-commands::"))
        token = lines[start].removeprefix("::stop-commands::")
        self.assertRegex(token, r"\A[0-9a-f]{32}\Z")
        end = lines.index("::" + token + "::")
        self.assertIn("::warning title=Injected::spoofed", lines[start:end])
        self.assertEqual([line for line in lines[:start] + lines[end + 1:] if line.startswith("::")],
                         ["::error title=Proposal refused::Proposal refused: Duplicate JSON member: x%0A"
                          "::warning title=Injected::spoofed"])
        self.assertNotIn("Injected", summary)
        self.assertIn("Refused by the trusted checker", summary)

    def test_identical_saved_audio_is_flagged_for_reviewers_but_never_refused(self):
        # The fake renderer reports PCM hash 1234567890abcdef for every record.
        duplicate = self.add_base_record("retained", b"timeline draft1\nhash 1234567890abcdef\n")
        self.add_base_record("near", b"note hash 1234567890abcdef\nhash 1234567890abcdeff\n hash 1234567890abcdef\n")
        self.proposal()
        song = self.propose()
        result = checker.check(self.repo, self.base, song, self.harness)
        self.assertTrue(result["eligible"])
        self.assertEqual((result["pcm_hash"], result["pcm_duplicate_of"]), ("1234567890abcdef", [duplicate]))
        self.git("update-ref", "refs/heads/song", song)
        result, summary, _ = self.run_step("Validate proposal with trusted base checker", self.workspace(), HEAD_SHA=song)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        warning = ("The rendered audio equals the saved PCM hash of " + duplicate +
                   ". Identity stays the canonical TL SHA-256; check originality before approval.")
        self.assertIn("\n::warning title=Possible duplicate audio::" + warning + "\n", result.stdout)
        self.assertTrue(summary.endswith("\n\nWarning: " + warning + "\n"), summary)

    def test_folded_title_collisions_are_flagged_for_reviewers_but_never_refused(self):
        baseline = "baseline-" + self.old_digest[:12]
        self.proposal()
        for title in ("BASELINE", "\uff22\uff41\uff53\uff45\uff4c\uff49\uff4e\uff45", "Base\u200dline"):
            with self.subTest(title=repr(title)):
                self.declaration["song"]["title"] = self.song["title"] = title
                self.save()
                result = self.check()
                self.assertTrue(result["eligible"])
                self.assertEqual(result["title_collision"], [baseline])
        song = self.propose()
        self.git("update-ref", "refs/heads/song", song)
        result, summary, _ = self.run_step("Validate proposal with trusted base checker", self.workspace(), HEAD_SHA=song)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        warning = ("The title equals the title of " + baseline + " after Unicode compatibility and case folding; "
                   "check for a copy or an impersonation before approval.")
        self.assertIn("\n::warning title=Possible title collision::" + warning + "\n", result.stdout)
        self.assertTrue(summary.endswith("\n\nWarning: " + warning + "\n"), summary)

    def test_git_process_count_does_not_grow_with_the_catalog(self):
        def spawns():
            self.proposal()
            head = self.propose()
            with patch.object(checker.subprocess, "Popen", wraps=subprocess.Popen) as popen:
                self.assertTrue(checker.check(self.repo, self.base, head, self.harness)["eligible"])
            self.record.unlink()
            self.declaration_path.unlink()
            return sum(call.args[0][0] == "git" for call in popen.call_args_list)
        small = spawns()
        for index in range(4):
            self.add_base_record("extra", ("extra record %d\n" % index).encode())
        self.assertEqual(spawns(), small)

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
            body_identity = self.digest
            portable = checker.catalog.document.compose_document(canonical.read_bytes(),
                {"title":"New proposal", "made_by":"agent", "license":"CC0-1.0"})
            self.setUp_proposal_data(portable)
            self.song["variants"][0]["discovery"]["document_sha256"] = hashlib.sha256(portable).hexdigest()
            self.save()
            self.assertTrue(self.check(harness)["eligible"])
            self.assertEqual(self.digest, body_identity)
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

    @unittest.skipUnless(os.environ.get("JOTTUNE_PR_HARNESS"), "Set JOTTUNE_PR_HARNESS for the real native boundary.")
    def test_real_stream_renamed_copy_is_flagged_but_eligible(self):
        harness = Path(os.environ["JOTTUNE_PR_HARNESS"]).resolve()
        engine = subprocess.check_output([str(harness), "--engine"], text=True).strip().removeprefix("ENGINE ")
        source, canonical = self.directory / "draft.tl", self.directory / "complete.tl"
        draft = ("timeline draft1\nengine " + engine + "\nfs 48000\nlength_ns 21000000000\nstreams test\n"
                 "patch sine 0 65536 0 0 0 256 5 500 200 800\npatch sine 1 65536 0 0 0 0 5 500 200 800\n"
                 "voice 0 sine\n0 test 0 on 441000 32767\n1000000000 test 0 off\n")
        records = []
        for streams in ("test", "renamed"):
            source.write_text(draft.replace("test", streams))
            subprocess.run([str(harness), "--play", str(source), "--merged", str(canonical)], check=True, capture_output=True)
            records.append(canonical.read_bytes())
        self.assertNotEqual(records[0], records[1])
        retained = self.add_base_record("retained", records[0])
        self.proposal(records[1])
        observed = self.check(harness)
        self.assertTrue(observed["eligible"])
        self.assertEqual(observed["pcm_duplicate_of"], [retained])
        self.assertIn(b"\nhash " + observed["pcm_hash"].encode() + b"\n", records[0])


if __name__ == "__main__":
    unittest.main()

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _clear_playground_modules():
    to_remove = [k for k in list(sys.modules.keys()) if k.startswith("playground")]
    for k in to_remove:
        del sys.modules[k]


def _clear_harness_modules():
    to_remove = [k for k in list(sys.modules.keys()) if k.startswith("protected.harness")]
    for k in to_remove:
        del sys.modules[k]


class TestModuleReload(unittest.TestCase):
    def setUp(self):
        _clear_playground_modules()

    def tearDown(self):
        _clear_playground_modules()

    def test_reload_purges_playground_modules(self):
        import os
        os.environ.setdefault("LLAMA_CPP_BASE_URL", "http://localhost:8080/v1")

        from protected.harness.interface_validator import reload_playground

        extractor_path = PROJECT_ROOT / "playground" / "extractor.py"
        dummy_code = extractor_path.read_text()
        sentinel = "# TEST_SENTINEL_12345"
        sentinel2 = "# TEST_SENTINEL_67890"

        test_code = dummy_code + "\n" + sentinel + "\n"
        extractor_path.write_text(test_code)

        try:
            import importlib
            mod1 = importlib.import_module("playground.extractor")
            self.assertIn("playground.extractor", sys.modules)
            source1 = Path(mod1.__file__).read_text()
            self.assertIn(sentinel, source1)

            new_code = test_code + "\n" + sentinel2 + "\n"
            extractor_path.write_text(new_code)

            reload_playground()
            self.assertNotIn("playground.extractor", sys.modules)

            mod2 = importlib.import_module("playground.extractor")
            source2 = Path(mod2.__file__).read_text()
            self.assertIn(sentinel2, source2)
        finally:
            extractor_path.write_text(dummy_code)
            _clear_playground_modules()


class TestAllowlist(unittest.TestCase):
    def setUp(self):
        _clear_harness_modules()

    def tearDown(self):
        _clear_harness_modules()

    def test_protected_paths_denied(self):
        from protected.harness.allowlist import is_allowed
        self.assertFalse(is_allowed("protected/schema.py", "replace_file"))
        self.assertFalse(is_allowed("corpus/ground_truth.jsonl", "replace_file"))
        self.assertFalse(is_allowed("experiments/study_001/pre-registration.md", "replace_file"))

    def test_core_file_deletion_denied(self):
        from protected.harness.allowlist import is_allowed
        self.assertFalse(is_allowed("playground/extractor.py", "delete_file"))
        self.assertFalse(is_allowed("playground/__init__.py", "delete_file"))

    def test_core_file_modification_allowed(self):
        from protected.harness.allowlist import is_allowed
        self.assertTrue(is_allowed("playground/extractor.py", "replace_string"))
        self.assertTrue(is_allowed("playground/extractor.py", "replace_file"))

    def test_prompt_files_allowed(self):
        from protected.harness.allowlist import is_allowed
        self.assertTrue(is_allowed("prompts/system_prompt.md", "replace_file"))
        self.assertTrue(is_allowed("prompts/examples.md", "replace_string"))

    def test_playground_create_file_allowed(self):
        from protected.harness.allowlist import is_allowed
        self.assertTrue(is_allowed("playground/pipeline.py", "create_file"))

    def test_playground_create_non_py_denied(self):
        from protected.harness.allowlist import is_allowed
        self.assertFalse(is_allowed("playground/notes.txt", "create_file"))

    def test_evaluation_denied(self):
        from protected.harness.allowlist import is_allowed
        self.assertFalse(is_allowed("evaluation/scorer.py", "replace_file"))

    def test_scripts_denied(self):
        from protected.harness.allowlist import is_allowed
        self.assertFalse(is_allowed("scripts/build_corpus.py", "replace_file"))


class TestEditApplier(unittest.TestCase):
    """Verify all-or-nothing validation and atomic writes.

    Strategy: Clear modules, import fresh, then directly set _PROJECT_ROOT.
    """

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.tmp_root = Path(self.tmpdir.name)
        playground = self.tmp_root / "playground"
        playground.mkdir(parents=True, exist_ok=True)
        (playground / "extractor.py").write_text("extractor content")
        (playground / "__init__.py").write_text("")

        _clear_harness_modules()

        import protected.harness.allowlist as al_mod
        al_mod._PROJECT_ROOT = self.tmp_root

        import protected.harness.edit_applier as ap_mod
        ap_mod._PROJECT_ROOT = self.tmp_root

    def tearDown(self):
        self.tmpdir.cleanup()
        _clear_harness_modules()

    def test_replace_string_success(self):
        import protected.harness.edit_applier as ap_mod
        from protected.harness.edit_protocol import Edit

        test_file = self.tmp_root / "playground" / "test.py"
        test_file.write_text("hello world")

        result = ap_mod.apply_edits([
            Edit(
                file_path="playground/test.py",
                operation="replace_string",
                old_string="hello",
                new_string="goodbye",
                new_content=None,
            )
        ])
        self.assertTrue(result.applied)
        self.assertEqual(test_file.read_text(), "goodbye world")

    def test_replace_string_ambiguous_match(self):
        _clear_harness_modules()
        import protected.harness.allowlist as al_mod
        al_mod._PROJECT_ROOT = self.tmp_root
        import protected.harness.edit_applier as ap_mod
        ap_mod._PROJECT_ROOT = self.tmp_root
        from protected.harness.edit_protocol import Edit

        test_file = self.tmp_root / "playground" / "test.py"
        test_file.write_text("foo bar foo")

        result = ap_mod.apply_edits([
            Edit(
                file_path="playground/test.py",
                operation="replace_string",
                old_string="foo",
                new_string="baz",
                new_content=None,
            )
        ])
        self.assertFalse(result.applied)
        self.assertEqual(result.reason, "ambiguous_match")

    def test_replace_file_success(self):
        _clear_harness_modules()
        import protected.harness.allowlist as al_mod
        al_mod._PROJECT_ROOT = self.tmp_root
        import protected.harness.edit_applier as ap_mod
        ap_mod._PROJECT_ROOT = self.tmp_root
        from protected.harness.edit_protocol import Edit

        test_file = self.tmp_root / "playground" / "test.py"
        test_file.write_text("old content")

        result = ap_mod.apply_edits([
            Edit(
                file_path="playground/test.py",
                operation="replace_file",
                old_string=None,
                new_string=None,
                new_content="brand new content",
            )
        ])
        self.assertTrue(result.applied)
        self.assertEqual(test_file.read_text(), "brand new content")

    def test_create_file_success(self):
        _clear_harness_modules()
        import protected.harness.allowlist as al_mod
        al_mod._PROJECT_ROOT = self.tmp_root
        import protected.harness.edit_applier as ap_mod
        ap_mod._PROJECT_ROOT = self.tmp_root
        from protected.harness.edit_protocol import Edit

        new_file = self.tmp_root / "playground" / "new.py"

        result = ap_mod.apply_edits([
            Edit(
                file_path="playground/new.py",
                operation="create_file",
                old_string=None,
                new_string=None,
                new_content="new file content",
            )
        ])
        self.assertTrue(result.applied)
        self.assertTrue(new_file.exists())
        self.assertEqual(new_file.read_text(), "new file content")

    def test_delete_file_success(self):
        _clear_harness_modules()
        import protected.harness.allowlist as al_mod
        al_mod._PROJECT_ROOT = self.tmp_root
        import protected.harness.edit_applier as ap_mod
        ap_mod._PROJECT_ROOT = self.tmp_root
        from protected.harness.edit_protocol import Edit

        test_file = self.tmp_root / "playground" / "delete_me.py"
        test_file.write_text("to be deleted")

        result = ap_mod.apply_edits([
            Edit(
                file_path="playground/delete_me.py",
                operation="delete_file",
                old_string=None,
                new_string=None,
                new_content=None,
            )
        ])
        self.assertTrue(result.applied)
        self.assertFalse(test_file.exists())

    def test_all_or_nothing(self):
        _clear_harness_modules()
        import protected.harness.allowlist as al_mod
        al_mod._PROJECT_ROOT = self.tmp_root
        import protected.harness.edit_applier as ap_mod
        ap_mod._PROJECT_ROOT = self.tmp_root
        from protected.harness.edit_protocol import Edit

        file1 = self.tmp_root / "playground" / "good.py"
        file1.write_text("aaa bbb aaa")

        result = ap_mod.apply_edits([
            Edit(
                file_path="playground/good.py",
                operation="replace_string",
                old_string="aaa",
                new_string="zzz",
                new_content=None,
            )
        ])
        self.assertFalse(result.applied)
        self.assertEqual(result.reason, "ambiguous_match")
        self.assertEqual(file1.read_text(), "aaa bbb aaa")

    def test_empty_edits_valid(self):
        _clear_harness_modules()
        import protected.harness.allowlist as al_mod
        al_mod._PROJECT_ROOT = self.tmp_root
        import protected.harness.edit_applier as ap_mod
        ap_mod._PROJECT_ROOT = self.tmp_root

        result = ap_mod.apply_edits([])
        self.assertTrue(result.applied)
        self.assertEqual(result.files_changed, [])


class TestInterfaceValidator(unittest.TestCase):
    def setUp(self):
        _clear_playground_modules()
        import os
        os.environ.setdefault("LLAMA_CPP_BASE_URL", "http://localhost:8080/v1")

    def tearDown(self):
        _clear_playground_modules()

    def test_valid_interface(self):
        from protected.harness.interface_validator import validate_interface
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(validate_interface())
            self.assertTrue(result.valid, f"Interface not valid: {result.error}")
        finally:
            loop.close()

    def test_reload_purges_modules(self):
        from protected.harness.interface_validator import reload_playground
        import importlib
        mod = importlib.import_module("playground.extractor")
        self.assertIn("playground.extractor", sys.modules)
        reload_playground()
        self.assertNotIn("playground.extractor", sys.modules)


class TestEpisodeStore(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.tmp_root = Path(self.tmpdir.name)
        _clear_harness_modules()

        import protected.harness.episode_store as es_mod
        es_mod._PROJECT_ROOT = self.tmp_root

    def tearDown(self):
        self.tmpdir.cleanup()
        _clear_harness_modules()

    def test_append_and_load(self):
        import protected.harness.episode_store as es_mod
        from protected.harness.edit_protocol import Episode

        ep1 = Episode(observation="obs1", hypothesis="hyp1", action="act1", expectation="exp1")
        es_mod.append("test_study", 1, ep1)
        self.assertEqual(es_mod.count("test_study"), 1)

        ep2 = Episode(observation="obs2", hypothesis="hyp2", action="act2", expectation="exp2")
        es_mod.append("test_study", 2, ep2)
        self.assertEqual(es_mod.count("test_study"), 2)

        episodes = es_mod.load_all("test_study")
        self.assertEqual(len(episodes), 2)
        self.assertEqual(episodes[0]["iteration_n"], 1)
        self.assertEqual(episodes[1]["iteration_n"], 2)
        self.assertEqual(episodes[0]["observation"], "obs1")

    def test_empty_load(self):
        import protected.harness.episode_store as es_mod
        episodes = es_mod.load_all("nonexistent_study")
        self.assertEqual(episodes, [])
        self.assertEqual(es_mod.count("nonexistent_study"), 0)


class TestAnomalyLogger(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.tmp_root = Path(self.tmpdir.name)
        _clear_harness_modules()

        import protected.harness.anomaly_logger as an_mod
        an_mod._PROJECT_ROOT = self.tmp_root

    def tearDown(self):
        self.tmpdir.cleanup()
        _clear_harness_modules()

    def test_log_and_read(self):
        import protected.harness.anomaly_logger as an_mod
        an_mod.log_anomaly("test_study", 5, "allowlist_violation", {"path": "protected/x.py"})
        an_mod.log_anomaly("test_study", 5, "repair_exhausted", None)

        path = self.tmp_root / "experiments" / "test_study" / "anomalies.jsonl"
        lines = path.read_text().strip().splitlines()
        self.assertEqual(len(lines), 2)
        record1 = json.loads(lines[0])
        self.assertEqual(record1["iteration_n"], 5)
        self.assertEqual(record1["anomaly_type"], "allowlist_violation")
        self.assertEqual(record1["details"]["path"], "protected/x.py")


class TestArtifactWriter(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.tmp_root = Path(self.tmpdir.name)

        playground = self.tmp_root / "playground"
        playground.mkdir(parents=True, exist_ok=True)
        (playground / "extractor.py").write_text("print('hello')")
        (playground / "__init__.py").write_text("")
        prompts = self.tmp_root / "prompts"
        prompts.mkdir(parents=True, exist_ok=True)
        (prompts / "system_prompt.md").write_text("You are...")
        (prompts / "examples.md").write_text("")

        _clear_harness_modules()
        import protected.harness.artifact_writer as aw_mod
        aw_mod._PROJECT_ROOT = self.tmp_root

    def tearDown(self):
        self.tmpdir.cleanup()
        _clear_harness_modules()

    def test_snapshot_playground(self):
        import protected.harness.artifact_writer as aw_mod
        aw_mod.snapshot_playground(1, "test_study")
        snap = self.tmp_root / "experiments" / "test_study" / "iterations" / "iteration_01_playground"
        self.assertTrue(snap.exists(), f"Snapshot dir not found: {snap}")
        self.assertTrue((snap / "playground" / "extractor.py").exists())
        self.assertTrue((snap / "prompts" / "system_prompt.md").exists())

    def test_append_metrics(self):
        _clear_harness_modules()
        import protected.harness.artifact_writer as aw_mod
        aw_mod._PROJECT_ROOT = self.tmp_root
        aw_mod.append_metrics(1, "test_study", {"macro_f1": 0.5, "scanned": True})
        path = self.tmp_root / "experiments" / "test_study" / "metrics.jsonl"
        self.assertTrue(path.exists(), f"Metrics file not found: {path}")
        lines = path.read_text().strip().splitlines()
        self.assertEqual(len(lines), 1)
        record = json.loads(lines[0])
        self.assertEqual(record["iteration_n"], 1)
        self.assertEqual(record["macro_f1"], 0.5)


class TestRepairLoop(unittest.TestCase):
    def setUp(self):
        _clear_playground_modules()
        import os
        os.environ.setdefault("LLAMA_CPP_BASE_URL", "http://localhost:8080/v1")

    def tearDown(self):
        _clear_playground_modules()

    def test_interface_validation_catches_syntax_error(self):
        from protected.harness.interface_validator import validate_interface, reload_playground
        extractor_path = PROJECT_ROOT / "playground" / "extractor.py"
        orig_code = extractor_path.read_text()
        broken_code = orig_code + "\nif True:\n  raise SyntaxError('boom')\n"
        extractor_path.write_text(broken_code)

        try:
            reload_playground()
            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(validate_interface())
                self.assertFalse(result.valid, "Should fail on syntax error")
            finally:
                loop.close()
        finally:
            extractor_path.write_text(orig_code)
            reload_playground()


class TestEpisodicMemoryPlumbing(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.tmp_root = Path(self.tmpdir.name)
        _clear_harness_modules()

        import protected.harness.episode_store as es_mod
        es_mod._PROJECT_ROOT = self.tmp_root

    def tearDown(self):
        self.tmpdir.cleanup()
        _clear_harness_modules()

    def test_episode_flow(self):
        import protected.harness.episode_store as es_mod
        from protected.harness.edit_protocol import Episode

        ep1 = Episode(
            observation="Low precision in claims",
            hypothesis="System prompt too broad",
            action="Narrowed system prompt",
            expectation="Higher precision in next iteration",
        )
        es_mod.append("test_study", 1, ep1)

        episodes = es_mod.load_all("test_study")
        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0]["iteration_n"], 1)

        ep2 = Episode(
            observation="Precision improved",
            hypothesis="Recall could still improve",
            action="Added claim validation step",
            expectation="Better recall in next iteration",
        )
        es_mod.append("test_study", 2, ep2)

        episodes = es_mod.load_all("test_study")
        self.assertEqual(len(episodes), 2)
        self.assertEqual(episodes[1]["iteration_n"], 2)


class TestScorer(unittest.TestCase):
    def test_score_corpus_basic(self):
        from protected.scorer import score_corpus
        from protected.schema import Claim, ExtractionResult

        results = [
            ExtractionResult(
                abstract_id="A1",
                claims=[Claim(claim_text="X activates Y"), Claim(claim_text="Z inhibits W")],
            ),
            ExtractionResult(
                abstract_id="A2",
                claims=[Claim(claim_text="P correlates with Q")],
            ),
        ]
        ground_truth = {
            "A1": ["X activates Y", "Z inhibits W"],
            "A2": ["P correlates with Q", "R affects S"],
        }

        s = score_corpus(results, ground_truth)
        self.assertGreater(s["macro_precision"], 0)
        self.assertEqual(s["micro_tp"], 3)
        self.assertEqual(s["micro_fp"], 0)
        self.assertEqual(s["micro_fn"], 1)


if __name__ == "__main__":
    unittest.main()

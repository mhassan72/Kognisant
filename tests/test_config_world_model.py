"""Tests for world model config loader functions in cli_kognisant/config.py."""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from cli_kognisant.config import (
    GLOBAL_CORE_DIR,
    init_world_model,
    is_world_model_enabled,
    load_autonomy_config,
    load_world_model,
    save_autonomy_config,
)


class TestIsWorldModelEnabled(unittest.TestCase):
    def test_returns_true_when_enabled(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = os.path.join(tmpdir, ".kognisant")
            os.makedirs(config_dir)
            config_path = os.path.join(config_dir, "config.json")
            with open(config_path, "w") as f:
                json.dump({"project_name": "test", "world_model_enabled": True}, f)

            self.assertTrue(is_world_model_enabled(tmpdir))

    def test_returns_false_when_disabled(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = os.path.join(tmpdir, ".kognisant")
            os.makedirs(config_dir)
            config_path = os.path.join(config_dir, "config.json")
            with open(config_path, "w") as f:
                json.dump({"project_name": "test", "world_model_enabled": False}, f)

            self.assertFalse(is_world_model_enabled(tmpdir))

    def test_returns_false_when_key_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = os.path.join(tmpdir, ".kognisant")
            os.makedirs(config_dir)
            config_path = os.path.join(config_dir, "config.json")
            with open(config_path, "w") as f:
                json.dump({"project_name": "test"}, f)

            self.assertFalse(is_world_model_enabled(tmpdir))

    def test_returns_false_when_file_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertFalse(is_world_model_enabled(tmpdir))

    def test_returns_false_on_corrupted_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = os.path.join(tmpdir, ".kognisant")
            os.makedirs(config_dir)
            config_path = os.path.join(config_dir, "config.json")
            with open(config_path, "w") as f:
                f.write("not valid json{{{")

            self.assertFalse(is_world_model_enabled(tmpdir))


class TestLoadAutonomyConfig(unittest.TestCase):
    def test_returns_empty_dict_when_file_missing(self):
        with patch("cli_kognisant.config.GLOBAL_CORE_DIR", "/nonexistent/path"):
            result = load_autonomy_config()
            self.assertEqual(result, {})

    def test_reads_existing_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "autonomy_config.json")
            data = {"auto_execute_threshold": 0.85, "suppress_threshold": 0.2}
            with open(config_path, "w") as f:
                json.dump(data, f)

            with patch("cli_kognisant.config.GLOBAL_CORE_DIR", tmpdir):
                result = load_autonomy_config()
                self.assertEqual(result, data)

    def test_returns_empty_dict_on_corrupted_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "autonomy_config.json")
            with open(config_path, "w") as f:
                f.write("invalid json!")

            with patch("cli_kognisant.config.GLOBAL_CORE_DIR", tmpdir):
                result = load_autonomy_config()
                self.assertEqual(result, {})


class TestSaveAutonomyConfig(unittest.TestCase):
    def test_saves_and_reads_back(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data = {"auto_execute_threshold": 0.85, "suppress_threshold": 0.2}
            with patch("cli_kognisant.config.GLOBAL_CORE_DIR", tmpdir):
                save_autonomy_config(data)
                result = load_autonomy_config()
                self.assertEqual(result, data)

    def test_atomic_write_creates_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("cli_kognisant.config.GLOBAL_CORE_DIR", tmpdir):
                save_autonomy_config({"key": "value"})
                config_path = os.path.join(tmpdir, "autonomy_config.json")
                self.assertTrue(os.path.exists(config_path))
                # Verify no .tmp file left behind
                self.assertFalse(os.path.exists(config_path + ".tmp"))


class TestInitWorldModel(unittest.TestCase):
    def test_creates_directory_structure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            init_world_model(tmpdir)

            wm_dir = os.path.join(tmpdir, ".kognisant", "world_model")
            self.assertTrue(os.path.isdir(wm_dir))
            self.assertTrue(os.path.isdir(os.path.join(wm_dir, "graph")))
            self.assertTrue(os.path.isdir(os.path.join(wm_dir, "graph", "modules")))
            self.assertTrue(os.path.isdir(os.path.join(wm_dir, "snapshots")))

    def test_creates_initial_json_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            init_world_model(tmpdir)

            wm_dir = os.path.join(tmpdir, ".kognisant", "world_model")

            # Check graph/index.json
            index_path = os.path.join(wm_dir, "graph", "index.json")
            self.assertTrue(os.path.exists(index_path))
            with open(index_path) as f:
                self.assertEqual(json.load(f), {})

            # Check beliefs.json
            beliefs_path = os.path.join(wm_dir, "beliefs.json")
            self.assertTrue(os.path.exists(beliefs_path))
            with open(beliefs_path) as f:
                self.assertEqual(json.load(f), [])

            # Check contracts.json
            contracts_path = os.path.join(wm_dir, "contracts.json")
            self.assertTrue(os.path.exists(contracts_path))
            with open(contracts_path) as f:
                self.assertEqual(json.load(f), [])

            # Check epistemic_gaps.json
            gaps_path = os.path.join(wm_dir, "epistemic_gaps.json")
            self.assertTrue(os.path.exists(gaps_path))
            with open(gaps_path) as f:
                self.assertEqual(json.load(f), [])

            # Check change_log.json
            change_log_path = os.path.join(wm_dir, "change_log.json")
            self.assertTrue(os.path.exists(change_log_path))
            with open(change_log_path) as f:
                self.assertEqual(json.load(f), {})

            # Check test_health.json
            test_health_path = os.path.join(wm_dir, "test_health.json")
            self.assertTrue(os.path.exists(test_health_path))
            with open(test_health_path) as f:
                self.assertEqual(json.load(f), [])

    def test_does_not_overwrite_existing_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Pre-create a file with content
            wm_dir = os.path.join(tmpdir, ".kognisant", "world_model")
            os.makedirs(os.path.join(wm_dir, "graph", "modules"), exist_ok=True)
            beliefs_path = os.path.join(wm_dir, "beliefs.json")
            with open(beliefs_path, "w") as f:
                json.dump([{"id": "existing"}], f)

            init_world_model(tmpdir)

            # Verify existing file was not overwritten
            with open(beliefs_path) as f:
                self.assertEqual(json.load(f), [{"id": "existing"}])


class TestLoadWorldModel(unittest.TestCase):
    def test_returns_json_world_model_store(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            from cli_kognisant.world_model_store import JsonWorldModelStore

            store = load_world_model(tmpdir)
            self.assertIsInstance(store, JsonWorldModelStore)


class TestInitGlobalCoreCreatesAutonomyAndGoalStats(unittest.TestCase):
    def test_creates_autonomy_config_and_goal_stats(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("cli_kognisant.config.GLOBAL_CORE_DIR", tmpdir):
                from cli_kognisant.config import init_global_core

                init_global_core()

                autonomy_path = os.path.join(tmpdir, "autonomy_config.json")
                self.assertTrue(os.path.exists(autonomy_path))
                with open(autonomy_path) as f:
                    self.assertEqual(json.load(f), {})

                goal_stats_path = os.path.join(tmpdir, "goal_stats.json")
                self.assertTrue(os.path.exists(goal_stats_path))
                with open(goal_stats_path) as f:
                    self.assertEqual(json.load(f), {})


if __name__ == "__main__":
    unittest.main()

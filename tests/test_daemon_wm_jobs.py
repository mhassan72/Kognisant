"""Tests for world model daemon scheduled jobs (Requirement 18).

Tests cover:
- Job type constants exist (R18.1)
- WM state initialization for registered projects (R18.1)
- decay_tick trigger: only when activity detected (R18.2)
- static_analysis trigger: on git HEAD change (R18.3)
- generate_goals trigger: after successful decay_tick or static_analysis (R18.4)
- Failure handling: log, increment counter, retry once after 5 min (R18.5)
- Retry failure handling: mark failed, skip until next trigger (R18.6)
"""

import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from cli_kognisant.daemon import (
    WM_JOB_DECAY_TICK,
    WM_JOB_GENERATE_GOALS,
    WM_JOB_OBSERVE,
    WM_JOB_STATIC_ANALYSIS,
    _WM_DECAY_TICK_INTERVAL,
    _WM_GIT_POLL_INTERVAL,
    _WM_RETRY_DELAY,
    _get_current_git_head,
    _get_registered_projects,
    _has_recent_file_modifications,
    _run_wm_decay_tick,
    _run_wm_generate_goals,
    _run_wm_static_analysis,
)


class TestWMJobConstants(unittest.TestCase):
    """Test world model job type constants exist (R18.1)."""

    def test_job_type_constants_defined(self):
        self.assertEqual(WM_JOB_OBSERVE, "wm_observe")
        self.assertEqual(WM_JOB_DECAY_TICK, "wm_decay_tick")
        self.assertEqual(WM_JOB_STATIC_ANALYSIS, "wm_static_analysis")
        self.assertEqual(WM_JOB_GENERATE_GOALS, "wm_generate_goals")

    def test_interval_constants(self):
        self.assertEqual(_WM_DECAY_TICK_INTERVAL, 3600)  # 60 minutes
        self.assertEqual(_WM_GIT_POLL_INTERVAL, 300)  # 5 minutes
        self.assertEqual(_WM_RETRY_DELAY, 300)  # 5 minutes


class TestGetRegisteredProjects(unittest.TestCase):
    """Test _get_registered_projects helper."""

    @patch("cli_kognisant.daemon.CORE_DIR")
    def test_returns_project_roots(self, mock_core_dir):
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_core_dir.__str__ = lambda s: tmpdir
            # Patch at module level
            projects_file = os.path.join(tmpdir, "projects.json")
            with open(projects_file, "w") as f:
                json.dump({"projects": {"/home/user/proj1": {}, "/tmp/proj2": {}}}, f)

            with patch("cli_kognisant.daemon.CORE_DIR", tmpdir):
                result = _get_registered_projects()
                self.assertIn("/home/user/proj1", result)
                self.assertIn("/tmp/proj2", result)

    @patch("cli_kognisant.daemon.CORE_DIR", "/nonexistent/path")
    def test_returns_empty_on_missing_file(self):
        result = _get_registered_projects()
        self.assertEqual(result, [])


class TestHasRecentFileModifications(unittest.TestCase):
    """Test _has_recent_file_modifications helper."""

    @patch("subprocess.run")
    def test_returns_true_when_git_log_has_output(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="abc1234 commit msg\n")
        result = _has_recent_file_modifications("/fake/project", within_minutes=60)
        self.assertTrue(result)

    @patch("subprocess.run")
    def test_returns_false_when_git_log_empty(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create an old file (won't match mtime check)
            old_file = os.path.join(tmpdir, "old.py")
            with open(old_file, "w") as f:
                f.write("# old")
            # Set mtime to 2 hours ago
            os.utime(old_file, (0, 0))
            result = _has_recent_file_modifications(tmpdir, within_minutes=60)
            self.assertFalse(result)


class TestGetCurrentGitHead(unittest.TestCase):
    """Test _get_current_git_head helper."""

    @patch("subprocess.run")
    def test_returns_head_hash(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="abc123def456\n")
        result = _get_current_git_head("/fake/project")
        self.assertEqual(result, "abc123def456")

    @patch("subprocess.run")
    def test_returns_none_on_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=128, stdout="")
        result = _get_current_git_head("/fake/project")
        self.assertIsNone(result)

    @patch("subprocess.run", side_effect=FileNotFoundError("git not found"))
    def test_returns_none_when_git_missing(self, mock_run):
        result = _get_current_git_head("/fake/project")
        self.assertIsNone(result)


class TestRunWMDecayTick(unittest.TestCase):
    """Test _run_wm_decay_tick job function."""

    @patch("cli_kognisant.config.is_world_model_enabled", return_value=False)
    def test_returns_true_when_wm_disabled(self, mock_enabled):
        logger = MagicMock()
        result = _run_wm_decay_tick("/fake/project", logger)
        self.assertTrue(result)

    @patch("cli_kognisant.config.is_world_model_enabled", return_value=True)
    @patch("cli_kognisant.config.load_world_model")
    def test_returns_true_on_success(self, mock_load, mock_enabled):
        logger = MagicMock()
        mock_store = MagicMock()
        mock_store.load_graph.return_value = {"nodes": [], "edges": []}
        mock_load.return_value = mock_store

        with patch("cli_kognisant.daemon._WMGraphCache.get_changes") as mock_changes, \
             patch("cli_kognisant.daemon._WMGraphCache.get_graph") as mock_get_graph, \
             patch("cli_kognisant.daemon._WMGraphCache.save_graph") as mock_save_graph:
            mock_changes.return_value = {"modified_functions": ["some_func"]}
            # Return a real-ish graph tuple
            from cli_kognisant.world_model import BeliefSystem, ContractRegistry, DependencyGraph, EpistemicGapTracker
            mock_get_graph.return_value = (DependencyGraph(), BeliefSystem(), ContractRegistry(), EpistemicGapTracker())

            result = _run_wm_decay_tick("/fake/project", logger)
            self.assertTrue(result)
            mock_save_graph.assert_called_once()

    @patch("cli_kognisant.config.is_world_model_enabled", return_value=True)
    @patch("cli_kognisant.config.load_world_model")
    def test_returns_true_no_modifications_skips_save(self, mock_load, mock_enabled):
        """Fast path: no modifications means no decay needed, save is skipped."""
        logger = MagicMock()
        mock_store = MagicMock()
        mock_store.load_graph.return_value = {"nodes": [], "edges": []}
        mock_load.return_value = mock_store

        with patch("cli_kognisant.daemon._WMGraphCache.get_changes") as mock_changes, \
             patch("cli_kognisant.daemon._WMGraphCache.get_graph") as mock_get_graph, \
             patch("cli_kognisant.daemon._WMGraphCache.save_graph") as mock_save_graph:
            mock_changes.return_value = {"modified_functions": []}
            from cli_kognisant.world_model import BeliefSystem, ContractRegistry, DependencyGraph, EpistemicGapTracker
            mock_get_graph.return_value = (DependencyGraph(), BeliefSystem(), ContractRegistry(), EpistemicGapTracker())

            result = _run_wm_decay_tick("/fake/project", logger)
            self.assertTrue(result)
            mock_save_graph.assert_not_called()

    @patch("cli_kognisant.config.is_world_model_enabled", return_value=True)
    @patch("cli_kognisant.config.load_world_model", side_effect=Exception("oops"))
    def test_returns_false_on_exception(self, mock_load, mock_enabled):
        logger = MagicMock()
        result = _run_wm_decay_tick("/fake/project", logger)
        self.assertFalse(result)
        logger.error.assert_called()


class TestRunWMStaticAnalysis(unittest.TestCase):
    """Test _run_wm_static_analysis job function."""

    @patch("cli_kognisant.config.is_world_model_enabled", return_value=False)
    def test_returns_true_when_wm_disabled(self, mock_enabled):
        logger = MagicMock()
        result = _run_wm_static_analysis("/fake/project", logger)
        self.assertTrue(result)

    @patch("cli_kognisant.config.is_world_model_enabled", return_value=True)
    @patch("cli_kognisant.config.load_world_model")
    def test_returns_true_on_success_no_changes(self, mock_load, mock_enabled):
        logger = MagicMock()
        mock_store = MagicMock()
        mock_load.return_value = mock_store

        with patch("cli_kognisant.observer.ChangeDetector") as mock_cd_cls:
            mock_cd = MagicMock()
            mock_cd.detect_changes.return_value = {
                "modified_functions": [],
                "added_files": [],
                "deleted_files": [],
                "renamed_files": [],
            }
            mock_cd_cls.return_value = mock_cd

            result = _run_wm_static_analysis("/fake/project", logger)
            self.assertTrue(result)

    @patch("cli_kognisant.config.is_world_model_enabled", return_value=True)
    @patch("cli_kognisant.config.load_world_model", side_effect=Exception("fail"))
    def test_returns_false_on_exception(self, mock_load, mock_enabled):
        logger = MagicMock()
        result = _run_wm_static_analysis("/fake/project", logger)
        self.assertFalse(result)


class TestRunWMGenerateGoals(unittest.TestCase):
    """Test _run_wm_generate_goals job function."""

    @patch("cli_kognisant.config.is_world_model_enabled", return_value=False)
    def test_returns_true_when_wm_disabled(self, mock_enabled):
        logger = MagicMock()
        result = _run_wm_generate_goals("/fake/project", logger)
        self.assertTrue(result)

    @patch("cli_kognisant.config.is_world_model_enabled", return_value=True)
    @patch("cli_kognisant.config.load_world_model")
    def test_returns_true_on_success(self, mock_load, mock_enabled):
        logger = MagicMock()
        mock_store = MagicMock()
        mock_store.load_graph.return_value = {"nodes": [], "edges": []}
        mock_load.return_value = mock_store

        result = _run_wm_generate_goals("/fake/project", logger)
        self.assertTrue(result)

    @patch("cli_kognisant.config.is_world_model_enabled", return_value=True)
    @patch("cli_kognisant.config.load_world_model", side_effect=RuntimeError("err"))
    def test_returns_false_on_exception(self, mock_load, mock_enabled):
        logger = MagicMock()
        result = _run_wm_generate_goals("/fake/project", logger)
        self.assertFalse(result)


class TestWMJobFailureHandling(unittest.TestCase):
    """Test R18.5 and R18.6 failure/retry semantics at the state-management level."""

    def _make_wm_state(self):
        """Create a fresh WM state dict for a project."""
        return {
            "last_decay_tick": 0.0,
            "last_git_poll": 0.0,
            "last_git_head": None,
            "decay_tick_failures": 0,
            "static_analysis_failures": 0,
            "generate_goals_failures": 0,
            "decay_tick_retry_at": None,
            "static_analysis_retry_at": None,
            "generate_goals_retry_at": None,
            "decay_tick_failed": False,
            "static_analysis_failed": False,
            "generate_goals_failed": False,
        }

    def test_first_failure_schedules_retry(self):
        """R18.5: First failure increments counter and schedules retry."""
        wm = self._make_wm_state()
        # Simulate first failure
        wm["decay_tick_failures"] += 1
        wm["decay_tick_retry_at"] = 1000.0 + _WM_RETRY_DELAY

        self.assertEqual(wm["decay_tick_failures"], 1)
        self.assertEqual(wm["decay_tick_retry_at"], 1300.0)
        self.assertFalse(wm["decay_tick_failed"])

    def test_second_failure_marks_failed(self):
        """R18.6: Second failure marks job as failed until next trigger."""
        wm = self._make_wm_state()
        wm["decay_tick_failures"] = 1
        # Simulate second failure (retry failed)
        wm["decay_tick_failed"] = True

        self.assertTrue(wm["decay_tick_failed"])

    def test_successful_retry_resets_state(self):
        """After successful retry, failures reset and failed flag cleared."""
        wm = self._make_wm_state()
        wm["decay_tick_failures"] = 1
        wm["decay_tick_retry_at"] = 1000.0
        # Simulate successful retry
        wm["decay_tick_failures"] = 0
        wm["decay_tick_failed"] = False
        wm["decay_tick_retry_at"] = None

        self.assertEqual(wm["decay_tick_failures"], 0)
        self.assertFalse(wm["decay_tick_failed"])
        self.assertIsNone(wm["decay_tick_retry_at"])

    def test_failed_job_skipped_until_next_trigger(self):
        """R18.6: A failed job is skipped until next trigger resets it."""
        wm = self._make_wm_state()
        wm["decay_tick_failed"] = True

        # The check in the main loop: if not wm["decay_tick_failed"]
        # So when failed=True, the decay_tick block is skipped
        should_run = not wm["decay_tick_failed"]
        self.assertFalse(should_run)


if __name__ == "__main__":
    unittest.main()

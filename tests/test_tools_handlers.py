"""Unit tests for job and script tool handlers in cli_kognisant/tools.py.

Tests both success paths and error paths for:
schedule_job, cancel_job, list_jobs, job_logs, create_script,
read_script, edit_script, delete_script, list_scripts, remove_job.

Requirements covered: 36.1, 36.2, 36.3
"""

import json
import os

import pytest

from cli_kognisant.tools import execute_tool


class TestScheduleJobTool:
    """Tests for the schedule_job tool handler."""

    def test_schedule_persistent_job_success(self, tmp_core_dir, patch_paths):
        """schedule_job creates a persistent job when script exists."""
        scripts_dir = str(tmp_core_dir / "scripts")
        # Create the script file
        script_path = os.path.join(scripts_dir, "my_bot.py")
        with open(script_path, "w") as f:
            f.write("print('hello')")

        project_info = {"root": str(tmp_core_dir), "files": []}
        args = json.dumps({
            "name": "test-bot",
            "job_type": "persistent",
            "script_path": "my_bot.py",
        })
        result = execute_tool("schedule_job", args, project_info)
        assert "added successfully" in result

    def test_schedule_job_missing_name(self, tmp_core_dir, patch_paths):
        """schedule_job returns validation error when name is missing."""
        project_info = {"root": str(tmp_core_dir), "files": []}
        args = json.dumps({"job_type": "persistent", "script_path": "x.py"})
        result = execute_tool("schedule_job", args, project_info)
        assert "validation" in result
        assert "name is required" in result

    def test_schedule_job_missing_job_type(self, tmp_core_dir, patch_paths):
        """schedule_job returns validation error when job_type is missing."""
        project_info = {"root": str(tmp_core_dir), "files": []}
        args = json.dumps({"name": "test-job"})
        result = execute_tool("schedule_job", args, project_info)
        assert "validation" in result
        assert "job_type is required" in result

    def test_schedule_scheduled_job_without_cron(self, tmp_core_dir, patch_paths):
        """schedule_job returns error when scheduled type has no cron_expression."""
        project_info = {"root": str(tmp_core_dir), "files": []}
        args = json.dumps({
            "name": "cron-job",
            "job_type": "scheduled",
            "script_path": "test.py",
        })
        result = execute_tool("schedule_job", args, project_info)
        assert "validation" in result
        assert "cron_expression is required" in result

    def test_schedule_job_invalid_cron(self, tmp_core_dir, patch_paths):
        """schedule_job returns error for invalid cron expression."""
        project_info = {"root": str(tmp_core_dir), "files": []}
        args = json.dumps({
            "name": "bad-cron",
            "job_type": "scheduled",
            "script_path": "test.py",
            "cron_expression": "invalid cron",
        })
        result = execute_tool("schedule_job", args, project_info)
        assert "validation" in result
        assert "Invalid cron expression" in result

    def test_schedule_job_script_not_found(self, tmp_core_dir, patch_paths):
        """schedule_job returns not_found when script doesn't exist."""
        project_info = {"root": str(tmp_core_dir), "files": []}
        args = json.dumps({
            "name": "no-script",
            "job_type": "persistent",
            "script_path": "nonexistent.py",
        })
        result = execute_tool("schedule_job", args, project_info)
        assert "not_found" in result
        assert "not found" in result

    def test_schedule_agent_job_success(self, tmp_core_dir, patch_paths):
        """schedule_job creates agent job with task description."""
        project_info = {"root": str(tmp_core_dir), "files": []}
        args = json.dumps({
            "name": "agent-task",
            "job_type": "agent",
            "task": "Refactor the utils module",
        })
        result = execute_tool("schedule_job", args, project_info)
        assert "added successfully" in result

    def test_schedule_agent_job_missing_task(self, tmp_core_dir, patch_paths):
        """schedule_job returns error when agent type has no task."""
        project_info = {"root": str(tmp_core_dir), "files": []}
        args = json.dumps({
            "name": "agent-no-task",
            "job_type": "agent",
        })
        result = execute_tool("schedule_job", args, project_info)
        assert "validation" in result
        assert "task is required" in result

    def test_schedule_job_duplicate_name(self, tmp_core_dir, patch_paths):
        """schedule_job returns error for duplicate job name."""
        project_info = {"root": str(tmp_core_dir), "files": []}
        # Create first job
        args = json.dumps({
            "name": "dup-job",
            "job_type": "agent",
            "task": "First task",
        })
        execute_tool("schedule_job", args, project_info)

        # Try to create duplicate
        args2 = json.dumps({
            "name": "dup-job",
            "job_type": "agent",
            "task": "Second task",
        })
        result = execute_tool("schedule_job", args2, project_info)
        assert "validation" in result
        assert "already exists" in result


class TestCancelJobTool:
    """Tests for the cancel_job tool handler."""

    def test_cancel_pending_job_success(self, tmp_core_dir, patch_paths):
        """cancel_job cancels a pending job successfully."""
        project_info = {"root": str(tmp_core_dir), "files": []}
        # Create a job
        execute_tool("schedule_job", json.dumps({
            "name": "cancel-me",
            "job_type": "agent",
            "task": "Test task",
        }), project_info)

        result = execute_tool("cancel_job", json.dumps({"name": "cancel-me"}), project_info)
        assert "cancelled successfully" in result

    def test_cancel_job_not_found(self, tmp_core_dir, patch_paths):
        """cancel_job returns not_found for non-existent job."""
        project_info = {"root": str(tmp_core_dir), "files": []}
        result = execute_tool("cancel_job", json.dumps({"name": "ghost-job"}), project_info)
        assert "not_found" in result
        assert "does not exist" in result

    def test_cancel_job_missing_name(self, tmp_core_dir, patch_paths):
        """cancel_job returns validation error when name is missing."""
        project_info = {"root": str(tmp_core_dir), "files": []}
        result = execute_tool("cancel_job", json.dumps({}), project_info)
        assert "validation" in result
        assert "name is required" in result

    def test_cancel_job_already_completed(self, tmp_core_dir, patch_paths):
        """cancel_job returns state error for completed job."""
        project_info = {"root": str(tmp_core_dir), "files": []}
        # Create job via tool handler (uses patched path)
        execute_tool("schedule_job", json.dumps({
            "name": "done-job",
            "job_type": "agent",
            "task": "x",
        }), project_info)

        # Manually update status via direct queue (using same patched path)
        from cli_kognisant.jobs import JobQueue
        queue = JobQueue()  # Uses patched expanduser → tmp_core_dir
        queue.update_status("done-job", "completed")

        result = execute_tool("cancel_job", json.dumps({"name": "done-job"}), project_info)
        assert "state" in result
        assert "cannot be cancelled" in result


class TestListJobsTool:
    """Tests for the list_jobs tool handler."""

    def test_list_jobs_empty(self, tmp_core_dir, patch_paths):
        """list_jobs returns 'No jobs found' when queue is empty."""
        project_info = {"root": str(tmp_core_dir), "files": []}
        result = execute_tool("list_jobs", json.dumps({}), project_info)
        assert "No jobs found" in result

    def test_list_jobs_with_entries(self, tmp_core_dir, patch_paths):
        """list_jobs returns formatted listing of all jobs."""
        project_info = {"root": str(tmp_core_dir), "files": []}
        execute_tool("schedule_job", json.dumps({
            "name": "my-agent",
            "job_type": "agent",
            "task": "Test",
        }), project_info)

        result = execute_tool("list_jobs", json.dumps({}), project_info)
        assert "my-agent" in result
        assert "agent" in result
        assert "pending" in result


class TestJobLogsTool:
    """Tests for the job_logs tool handler."""

    def test_job_logs_success(self, tmp_core_dir, patch_paths):
        """job_logs returns log content for existing job."""
        project_info = {"root": str(tmp_core_dir), "files": []}
        # Create job via tool handler
        execute_tool("schedule_job", json.dumps({
            "name": "log-job",
            "job_type": "agent",
            "task": "x",
        }), project_info)

        # Write some log content
        logs_dir = str(tmp_core_dir / "logs")
        os.makedirs(logs_dir, exist_ok=True)
        with open(os.path.join(logs_dir, "log-job.log"), "w") as f:
            f.write("Line 1\nLine 2\nLine 3\n")

        result = execute_tool("job_logs", json.dumps({"name": "log-job"}), project_info)
        assert "Line 1" in result

    def test_job_logs_not_found(self, tmp_core_dir, patch_paths):
        """job_logs returns not_found for non-existent job."""
        project_info = {"root": str(tmp_core_dir), "files": []}
        result = execute_tool("job_logs", json.dumps({"name": "no-job"}), project_info)
        assert "not_found" in result
        assert "does not exist" in result

    def test_job_logs_missing_name(self, tmp_core_dir, patch_paths):
        """job_logs returns validation error when name is missing."""
        project_info = {"root": str(tmp_core_dir), "files": []}
        result = execute_tool("job_logs", json.dumps({}), project_info)
        assert "validation" in result
        assert "name is required" in result

    def test_job_logs_no_log_file(self, tmp_core_dir, patch_paths):
        """job_logs returns appropriate message when no log file exists."""
        project_info = {"root": str(tmp_core_dir), "files": []}
        # Create the job via tool handler
        execute_tool("schedule_job", json.dumps({
            "name": "no-log-job",
            "job_type": "agent",
            "task": "x",
        }), project_info)

        result = execute_tool("job_logs", json.dumps({"name": "no-log-job"}), project_info)
        assert "No logs available" in result


class TestRemoveJobTool:
    """Tests for the remove_job tool handler."""

    def test_remove_job_success(self, tmp_core_dir, patch_paths):
        """remove_job removes an existing job."""
        project_info = {"root": str(tmp_core_dir), "files": []}
        execute_tool("schedule_job", json.dumps({
            "name": "remove-me",
            "job_type": "agent",
            "task": "Test",
        }), project_info)

        result = execute_tool("remove_job", json.dumps({"name": "remove-me"}), project_info)
        assert "removed successfully" in result

    def test_remove_job_not_found(self, tmp_core_dir, patch_paths):
        """remove_job returns not_found for non-existent job."""
        project_info = {"root": str(tmp_core_dir), "files": []}
        result = execute_tool("remove_job", json.dumps({"name": "ghost"}), project_info)
        assert "not_found" in result
        assert "does not exist" in result

    def test_remove_job_missing_name(self, tmp_core_dir, patch_paths):
        """remove_job returns validation error when name is missing."""
        project_info = {"root": str(tmp_core_dir), "files": []}
        result = execute_tool("remove_job", json.dumps({}), project_info)
        assert "validation" in result
        assert "name is required" in result


class TestCreateScriptTool:
    """Tests for the create_script tool handler."""

    def test_create_script_success(self, tmp_core_dir, patch_paths):
        """create_script creates a script file with metadata."""
        project_info = {"root": str(tmp_core_dir), "files": []}
        args = json.dumps({
            "name": "my-script",
            "content": "print('hello world')",
            "description": "A test script",
        })
        result = execute_tool("create_script", args, project_info)
        assert "created successfully" in result

        # Verify files exist
        scripts_dir = str(tmp_core_dir / "scripts")
        assert os.path.exists(os.path.join(scripts_dir, "my-script.py"))
        assert os.path.exists(os.path.join(scripts_dir, "my-script.json"))

    def test_create_script_missing_name(self, tmp_core_dir, patch_paths):
        """create_script returns error when name is missing."""
        project_info = {"root": str(tmp_core_dir), "files": []}
        args = json.dumps({"content": "print('hello')"})
        result = execute_tool("create_script", args, project_info)
        assert "Error" in result
        assert "name is required" in result

    def test_create_script_missing_content(self, tmp_core_dir, patch_paths):
        """create_script returns error when content is missing."""
        project_info = {"root": str(tmp_core_dir), "files": []}
        args = json.dumps({"name": "no-content"})
        result = execute_tool("create_script", args, project_info)
        assert "Error" in result
        assert "content is required" in result

    def test_create_script_duplicate(self, tmp_core_dir, patch_paths):
        """create_script returns error for duplicate script name."""
        project_info = {"root": str(tmp_core_dir), "files": []}
        args = json.dumps({"name": "dupe", "content": "x"})
        execute_tool("create_script", args, project_info)
        result = execute_tool("create_script", args, project_info)
        assert "already exists" in result

    def test_create_script_invalid_name(self, tmp_core_dir, patch_paths):
        """create_script returns error for invalid script name."""
        project_info = {"root": str(tmp_core_dir), "files": []}
        args = json.dumps({"name": "Bad Name!", "content": "x"})
        result = execute_tool("create_script", args, project_info)
        assert "Error" in result


class TestReadScriptTool:
    """Tests for the read_script tool handler."""

    def test_read_script_success(self, tmp_core_dir, patch_paths):
        """read_script returns script content."""
        project_info = {"root": str(tmp_core_dir), "files": []}
        execute_tool("create_script", json.dumps({
            "name": "readable",
            "content": "print('read me')",
        }), project_info)

        result = execute_tool("read_script", json.dumps({"name": "readable"}), project_info)
        assert "print('read me')" in result

    def test_read_script_not_found(self, tmp_core_dir, patch_paths):
        """read_script returns error for non-existent script."""
        project_info = {"root": str(tmp_core_dir), "files": []}
        result = execute_tool("read_script", json.dumps({"name": "nope"}), project_info)
        assert "not found" in result

    def test_read_script_missing_name(self, tmp_core_dir, patch_paths):
        """read_script returns error when name is missing."""
        project_info = {"root": str(tmp_core_dir), "files": []}
        result = execute_tool("read_script", json.dumps({}), project_info)
        assert "Error" in result
        assert "name is required" in result


class TestEditScriptTool:
    """Tests for the edit_script tool handler."""

    def test_edit_script_success(self, tmp_core_dir, patch_paths):
        """edit_script applies find-and-replace edits."""
        project_info = {"root": str(tmp_core_dir), "files": []}
        execute_tool("create_script", json.dumps({
            "name": "editable",
            "content": "x = 1\ny = 2",
        }), project_info)

        result = execute_tool("edit_script", json.dumps({
            "name": "editable",
            "edits": [{"old_text": "x = 1", "new_text": "x = 100"}],
        }), project_info)
        assert "edited successfully" in result

        # Verify content changed
        content = execute_tool("read_script", json.dumps({"name": "editable"}), project_info)
        assert "x = 100" in content

    def test_edit_script_not_found(self, tmp_core_dir, patch_paths):
        """edit_script returns error for non-existent script."""
        project_info = {"root": str(tmp_core_dir), "files": []}
        result = execute_tool("edit_script", json.dumps({
            "name": "ghost",
            "edits": [{"old_text": "x", "new_text": "y"}],
        }), project_info)
        assert "not found" in result

    def test_edit_script_text_not_found(self, tmp_core_dir, patch_paths):
        """edit_script returns error when old_text doesn't match."""
        project_info = {"root": str(tmp_core_dir), "files": []}
        execute_tool("create_script", json.dumps({
            "name": "no-match",
            "content": "hello world",
        }), project_info)

        result = execute_tool("edit_script", json.dumps({
            "name": "no-match",
            "edits": [{"old_text": "not here", "new_text": "replacement"}],
        }), project_info)
        assert "Error" in result
        assert "not found" in result

    def test_edit_script_missing_edits(self, tmp_core_dir, patch_paths):
        """edit_script returns error when edits array is empty."""
        project_info = {"root": str(tmp_core_dir), "files": []}
        result = execute_tool("edit_script", json.dumps({
            "name": "test",
            "edits": [],
        }), project_info)
        assert "Error" in result
        assert "edits" in result


class TestDeleteScriptTool:
    """Tests for the delete_script tool handler."""

    def test_delete_script_success(self, tmp_core_dir, patch_paths):
        """delete_script removes script and metadata files."""
        project_info = {"root": str(tmp_core_dir), "files": []}
        execute_tool("create_script", json.dumps({
            "name": "deletable",
            "content": "x = 1",
        }), project_info)

        result = execute_tool("delete_script", json.dumps({"name": "deletable"}), project_info)
        assert "deleted successfully" in result

        # Verify files are gone
        scripts_dir = str(tmp_core_dir / "scripts")
        assert not os.path.exists(os.path.join(scripts_dir, "deletable.py"))
        assert not os.path.exists(os.path.join(scripts_dir, "deletable.json"))

    def test_delete_script_not_found(self, tmp_core_dir, patch_paths):
        """delete_script returns error for non-existent script."""
        project_info = {"root": str(tmp_core_dir), "files": []}
        result = execute_tool("delete_script", json.dumps({"name": "nope"}), project_info)
        assert "not found" in result

    def test_delete_script_missing_name(self, tmp_core_dir, patch_paths):
        """delete_script returns error when name is missing."""
        project_info = {"root": str(tmp_core_dir), "files": []}
        result = execute_tool("delete_script", json.dumps({}), project_info)
        assert "Error" in result
        assert "name is required" in result


class TestListScriptsTool:
    """Tests for the list_scripts tool handler."""

    def test_list_scripts_empty(self, tmp_core_dir, patch_paths):
        """list_scripts returns 'No scripts found' when empty."""
        project_info = {"root": str(tmp_core_dir), "files": []}
        result = execute_tool("list_scripts", json.dumps({}), project_info)
        assert "No scripts found" in result

    def test_list_scripts_with_entries(self, tmp_core_dir, patch_paths):
        """list_scripts returns script listing after creation."""
        project_info = {"root": str(tmp_core_dir), "files": []}
        execute_tool("create_script", json.dumps({
            "name": "script-a",
            "content": "print(1)",
            "description": "First script",
        }), project_info)
        execute_tool("create_script", json.dumps({
            "name": "script-b",
            "content": "print(2)",
            "description": "Second script",
        }), project_info)

        result = execute_tool("list_scripts", json.dumps({}), project_info)
        assert "script-a" in result
        assert "script-b" in result
        assert "First script" in result

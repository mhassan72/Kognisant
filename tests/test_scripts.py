"""Unit tests for cli_kognisant/scripts.py — Script CRUD operations."""

import json
import os
import tempfile
import unittest

import cli_kognisant.scripts as scripts_module
from cli_kognisant.scripts import (
    create_script,
    delete_script,
    edit_script,
    list_scripts,
    read_script,
    validate_script_name,
)


class ScriptsTestBase(unittest.TestCase):
    """Base class that redirects SCRIPTS_DIR to a temp directory for isolation."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._original_scripts_dir = scripts_module.SCRIPTS_DIR
        scripts_module.SCRIPTS_DIR = os.path.join(self._tmpdir.name, "scripts")

    def tearDown(self):
        scripts_module.SCRIPTS_DIR = self._original_scripts_dir
        self._tmpdir.cleanup()


class TestValidateScriptName(unittest.TestCase):
    """Tests for validate_script_name."""

    def test_valid_lowercase_alpha(self):
        self.assertIsNone(validate_script_name("myscript"))

    def test_valid_with_hyphens(self):
        self.assertIsNone(validate_script_name("my-script"))

    def test_valid_with_underscores(self):
        self.assertIsNone(validate_script_name("my_script"))

    def test_valid_with_numbers(self):
        self.assertIsNone(validate_script_name("script123"))

    def test_valid_mixed(self):
        self.assertIsNone(validate_script_name("telegram-bot_v2"))

    def test_valid_single_char(self):
        self.assertIsNone(validate_script_name("a"))

    def test_valid_64_chars(self):
        name = "a" * 64
        self.assertIsNone(validate_script_name(name))

    def test_invalid_empty(self):
        result = validate_script_name("")
        self.assertIsNotNone(result)
        self.assertIn("empty", result)

    def test_invalid_too_long(self):
        name = "a" * 65
        result = validate_script_name(name)
        self.assertIsNotNone(result)
        self.assertIn("64", result)

    def test_invalid_uppercase(self):
        result = validate_script_name("MyScript")
        self.assertIsNotNone(result)
        self.assertIn("lowercase", result)

    def test_invalid_spaces(self):
        result = validate_script_name("my script")
        self.assertIsNotNone(result)

    def test_invalid_special_chars(self):
        result = validate_script_name("script@name")
        self.assertIsNotNone(result)

    def test_invalid_dots(self):
        result = validate_script_name("script.name")
        self.assertIsNotNone(result)

    def test_invalid_path_traversal_dotdot_slash(self):
        result = validate_script_name("../etc/passwd")
        self.assertIsNotNone(result)
        self.assertIn("path", result.lower())

    def test_invalid_path_traversal_dot_slash(self):
        result = validate_script_name("./myscript")
        self.assertIsNotNone(result)
        self.assertIn("path", result.lower())

    def test_invalid_forward_slash(self):
        result = validate_script_name("path/to/script")
        self.assertIsNotNone(result)
        self.assertIn("path", result.lower())

    def test_invalid_backslash(self):
        result = validate_script_name("path\\to\\script")
        self.assertIsNotNone(result)
        self.assertIn("path", result.lower())


class TestCreateScript(ScriptsTestBase):
    """Tests for create_script."""

    def test_create_success(self):
        result = create_script("hello", "print('hello')", description="A greeting")
        self.assertIn("created successfully", result)

        # Verify .py file written
        py_path = os.path.join(scripts_module.SCRIPTS_DIR, "hello.py")
        self.assertTrue(os.path.exists(py_path))
        with open(py_path, "r") as f:
            self.assertEqual(f.read(), "print('hello')")

        # Verify .json metadata written
        json_path = os.path.join(scripts_module.SCRIPTS_DIR, "hello.json")
        self.assertTrue(os.path.exists(json_path))
        with open(json_path, "r") as f:
            meta = json.load(f)
        self.assertEqual(meta["name"], "hello")
        self.assertEqual(meta["description"], "A greeting")
        self.assertEqual(meta["env_vars"], [])
        self.assertIn("T", meta["created_at"])  # ISO 8601 format

    def test_create_with_env_vars(self):
        result = create_script(
            "bot", "import os", env_vars=["API_KEY", "SECRET"]
        )
        self.assertIn("created successfully", result)

        json_path = os.path.join(scripts_module.SCRIPTS_DIR, "bot.json")
        with open(json_path, "r") as f:
            meta = json.load(f)
        self.assertEqual(meta["env_vars"], ["API_KEY", "SECRET"])

    def test_create_duplicate_error(self):
        create_script("dup", "content1")
        result = create_script("dup", "content2")
        self.assertIn("Error", result)
        self.assertIn("already exists", result)

    def test_create_invalid_name_error(self):
        result = create_script("INVALID", "content")
        self.assertIn("Error", result)

    def test_create_ensures_directory_exists(self):
        # The directory shouldn't exist yet
        self.assertFalse(os.path.exists(scripts_module.SCRIPTS_DIR))
        create_script("first", "content")
        self.assertTrue(os.path.exists(scripts_module.SCRIPTS_DIR))


class TestReadScript(ScriptsTestBase):
    """Tests for read_script."""

    def test_read_success(self):
        create_script("readable", "x = 42\nprint(x)")
        result = read_script("readable")
        self.assertEqual(result, "x = 42\nprint(x)")

    def test_read_not_found(self):
        result = read_script("nonexistent")
        self.assertIn("Error", result)
        self.assertIn("not found", result)

    def test_read_invalid_name(self):
        result = read_script("INVALID")
        self.assertIn("Error", result)


class TestEditScript(ScriptsTestBase):
    """Tests for edit_script."""

    def test_edit_single_replacement(self):
        create_script("editable", "hello world")
        result = edit_script("editable", [{"old_text": "world", "new_text": "python"}])
        self.assertIn("edited successfully", result)

        content = read_script("editable")
        self.assertEqual(content, "hello python")

    def test_edit_multiple_replacements(self):
        create_script("multi", "aaa bbb ccc")
        edits = [
            {"old_text": "aaa", "new_text": "111"},
            {"old_text": "bbb", "new_text": "222"},
        ]
        result = edit_script("multi", edits)
        self.assertIn("edited successfully", result)

        content = read_script("multi")
        self.assertEqual(content, "111 222 ccc")

    def test_edit_text_not_found_rollback(self):
        create_script("rollback", "original content here")
        edits = [
            {"old_text": "original", "new_text": "modified"},
            {"old_text": "NONEXISTENT", "new_text": "replacement"},
        ]
        result = edit_script("rollback", edits)
        self.assertIn("Error", result)
        self.assertIn("not found", result)

        # Verify rollback - content should be unchanged
        content = read_script("rollback")
        self.assertEqual(content, "original content here")

    def test_edit_not_found_script(self):
        result = edit_script("ghost", [{"old_text": "a", "new_text": "b"}])
        self.assertIn("Error", result)
        self.assertIn("not found", result)

    def test_edit_sequential_order(self):
        """Edits are applied in order, so later edits see results of earlier ones."""
        create_script("seq", "foo bar")
        edits = [
            {"old_text": "foo", "new_text": "baz"},
            {"old_text": "baz bar", "new_text": "done"},
        ]
        result = edit_script("seq", edits)
        self.assertIn("edited successfully", result)

        content = read_script("seq")
        self.assertEqual(content, "done")


class TestDeleteScript(ScriptsTestBase):
    """Tests for delete_script."""

    def test_delete_success(self):
        create_script("removeme", "content", description="temp")
        result = delete_script("removeme")
        self.assertIn("deleted successfully", result)

        # Verify both files removed
        py_path = os.path.join(scripts_module.SCRIPTS_DIR, "removeme.py")
        json_path = os.path.join(scripts_module.SCRIPTS_DIR, "removeme.json")
        self.assertFalse(os.path.exists(py_path))
        self.assertFalse(os.path.exists(json_path))

    def test_delete_not_found(self):
        result = delete_script("ghost")
        self.assertIn("Error", result)
        self.assertIn("not found", result)

    def test_delete_invalid_name(self):
        result = delete_script("../bad")
        self.assertIn("Error", result)


class TestListScripts(ScriptsTestBase):
    """Tests for list_scripts."""

    def test_list_empty(self):
        result = list_scripts()
        self.assertIn("No scripts found", result)

    def test_list_multiple_scripts(self):
        create_script("alpha", "pass", description="First script")
        create_script("beta", "pass", description="Second script", env_vars=["KEY"])
        result = list_scripts()

        self.assertIn("Scripts:", result)
        self.assertIn("alpha", result)
        self.assertIn("First script", result)
        self.assertIn("beta", result)
        self.assertIn("Second script", result)
        self.assertIn("KEY", result)

    def test_list_sorted_alphabetically(self):
        create_script("zebra", "pass")
        create_script("alpha", "pass")
        result = list_scripts()

        # alpha should appear before zebra
        alpha_pos = result.index("alpha")
        zebra_pos = result.index("zebra")
        self.assertLess(alpha_pos, zebra_pos)


if __name__ == "__main__":
    unittest.main()

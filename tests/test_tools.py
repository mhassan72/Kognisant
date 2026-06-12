import os
import tempfile
import unittest

from cli_kognisant.tools import clean_html, execute_tool, find_chrome_or_brave


class TestToolsEngineering(unittest.TestCase):
    def test_clean_html_strips_bloat_correctly(self):
        """Verify the HTML cleaner cleanly filters script, style, and metadata bloat."""
        raw_html = (
            "<html>"
            "<head><style>body { color: red; }</style></head>"
            "<body>"
            "<div>"
            "  <h1>Welcome to Kognisant</h1>"
            "  <script>console.log('malicious script run');</script>"
            "  <p>Our goal is <b>autonomous</b> coding.</p>"
            "</div>"
            "</body>"
            "</html>"
        )
        cleaned = clean_html(raw_html)
        self.assertNotIn("malicious", cleaned)
        self.assertNotIn("style", cleaned)
        self.assertIn("Welcome to Kognisant", cleaned)
        self.assertIn("Our goal is autonomous coding.", cleaned)

    def test_execute_tool_directory_traversal_boundaries(self):
        """Verify file tools strictly enforce workspace sandbox boundaries (Mitigates traversal hacks)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_info = {"root": tmpdir, "files": []}
            # Attempt to read a file outside the workspace root
            traversal_attempt_args = '{"file_path": "../../../etc/passwd"}'
            result = execute_tool(
                "read_project_file", traversal_attempt_args, project_info
            )
            self.assertIn("Access denied", result)

    def test_resolve_safe_path_expanded_boundaries(self):
        """Verify resolve_safe_path allows project root, global tools, and global skills, but blocks others."""
        from unittest.mock import patch

        from cli_kognisant.tools import resolve_safe_path

        with tempfile.TemporaryDirectory() as tmp_root, tempfile.TemporaryDirectory() as tmp_global_tools, tempfile.TemporaryDirectory() as tmp_global_skills:
            # Patch expanduser for the global tools and skills
            import os as real_os

            orig_expanduser = real_os.path.expanduser

            def mock_expanduser(path):
                if path.startswith("~/.kognisant_core/tools"):
                    return path.replace("~/.kognisant_core/tools", tmp_global_tools, 1)
                if path.startswith("~/.kognisant_core/skills"):
                    return path.replace(
                        "~/.kognisant_core/skills", tmp_global_skills, 1
                    )
                return orig_expanduser(path)

            with patch("os.path.expanduser", side_effect=mock_expanduser):
                # 1. Path in project root
                in_proj = os.path.join(tmp_root, "src", "main.py")
                resolved = resolve_safe_path("src/main.py", tmp_root)
                self.assertEqual(resolved, os.path.realpath(in_proj))

                # 2. Path in global tools
                global_tool_path = os.path.join(tmp_global_tools, "my_tool.py")
                resolved_tool = resolve_safe_path(
                    "~/.kognisant_core/tools/my_tool.py", tmp_root
                )
                self.assertEqual(resolved_tool, os.path.realpath(global_tool_path))

                # 3. Path in global skills
                global_skill_path = os.path.join(tmp_global_skills, "my_skill.md")
                resolved_skill = resolve_safe_path(
                    "~/.kognisant_core/skills/my_skill.md", tmp_root
                )
                self.assertEqual(resolved_skill, os.path.realpath(global_skill_path))

                # 4. Blocked paths
                with self.assertRaises(PermissionError):
                    resolve_safe_path("../../../etc/passwd", tmp_root)

                with self.assertRaises(PermissionError):
                    resolve_safe_path("/etc/passwd", tmp_root)

    def test_global_file_crud_tools(self):
        """Verify dedicated global file CRUD tools can create, read, and edit global files, but strictly block non-global paths."""
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp_root, tempfile.TemporaryDirectory() as tmp_global_tools, tempfile.TemporaryDirectory() as tmp_global_skills:
            project_info = {"root": tmp_root, "files": []}

            # Patch expanduser for the global tools and skills
            import os as real_os

            orig_expanduser = real_os.path.expanduser

            def mock_expanduser(path):
                if path.startswith("~/.kognisant_core/tools"):
                    return path.replace("~/.kognisant_core/tools", tmp_global_tools, 1)
                if path.startswith("~/.kognisant_core/skills"):
                    return path.replace(
                        "~/.kognisant_core/skills", tmp_global_skills, 1
                    )
                return orig_expanduser(path)

            with patch("os.path.expanduser", side_effect=mock_expanduser):
                # 1. Create a global file
                cr_args = '{"file_path": "~/.kognisant_core/tools/test_cr.json", "content": "{\\"val\\": 100}"}'
                res_create = execute_tool("create_global_file", cr_args, project_info)
                self.assertIn("[Success]", res_create)

                # Check actual existence
                global_tool_file = os.path.join(tmp_global_tools, "test_cr.json")
                self.assertTrue(os.path.exists(global_tool_file))

                # 2. Read the global file
                rd_args = '{"file_path": "~/.kognisant_core/tools/test_cr.json"}'
                res_read = execute_tool("read_global_file", rd_args, project_info)
                self.assertEqual(res_read, '{"val": 100}')

                # 3. Edit the global file
                ed_args = '{"file_path": "~/.kognisant_core/tools/test_cr.json", "edits": [{"old_text": "100", "new_text": "200"}]}'
                res_edit = execute_tool("edit_global_file", ed_args, project_info)
                self.assertIn("[Success]", res_edit)

                # Verify edits
                res_read_after = execute_tool("read_global_file", rd_args, project_info)
                self.assertEqual(res_read_after, '{"val": 200}')

                # 4. Access Denied on non-global paths
                bad_cr_args = '{"file_path": "src/main.py", "content": "print()"}'
                res_bad_create = execute_tool(
                    "create_global_file", bad_cr_args, project_info
                )
                self.assertIn("Access denied", res_bad_create)

    def test_find_chrome_or_brave_returns_string_or_none(self):
        """Verify browser detection finder executes cleanly on all operating systems."""
        path = find_chrome_or_brave()
        if path is not None:
            self.assertIsInstance(path, str)
            self.assertTrue(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()

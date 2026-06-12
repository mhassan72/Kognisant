import os
import tempfile
import unittest

from cli_kognisant.config import GLOBAL_CORE_DIR, load_spec_info


class TestConfigManager(unittest.TestCase):
    def test_load_spec_info_nonexistent_returns_none(self):
        """Verify loader returns None cleanly if the spec folder doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            spec = load_spec_info(tmpdir, "nonexistent-feature")
            self.assertIsNone(spec)

    def test_load_spec_info_reads_files_cleanly(self):
        """Verify loader parses requirements, design, and tasks documents into a single spec object."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Recreate Kognisant workspace structure inside temp folder
            spec_dir = os.path.join(tmpdir, ".kognisant", "specs", "auth_feature")
            os.makedirs(spec_dir, exist_ok=True)

            with open(
                os.path.join(spec_dir, "requirements.md"), "w", encoding="utf-8"
            ) as f:
                f.write("- User story 1")
            with open(os.path.join(spec_dir, "design.md"), "w", encoding="utf-8") as f:
                f.write("`cli_kognisant/auth.py` boundaries")
            with open(os.path.join(spec_dir, "tasks.md"), "w", encoding="utf-8") as f:
                f.write("- [ ] Task 1")

            spec = load_spec_info(tmpdir, "auth_feature")
            self.assertIsNotNone(spec)
            if isinstance(spec, dict):
                self.assertEqual(spec["feature"], "auth_feature")
                self.assertEqual(spec["requirements"].strip(), "- User story 1")
                self.assertEqual(
                    spec["design"].strip(), "`cli_kognisant/auth.py` boundaries"
                )
                self.assertEqual(spec["tasks"].strip(), "- [ ] Task 1")

    def test_global_core_directory_binding(self):
        """Verify global memory folder correctly expands to the user's home directory."""
        self.assertTrue(GLOBAL_CORE_DIR.endswith(".kognisant_core"))


if __name__ == "__main__":
    print_lock = None  # Mock print_lock if needed, but not imported here
    unittest.main()

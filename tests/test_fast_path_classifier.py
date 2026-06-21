"""Tests for the fast-path message classifier.

Validates: Requirements R2.1, R2.2, R2.3, R2.4, R2.5
"""

import time
import unittest

from cli_kognisant.fast_path_classifier import (
    ACTION_VERBS,
    CODE_PATTERN,
    CONTEXT_INDICATORS,
    FILE_PATTERN,
    PROJECT_REFS,
    classify,
)


class TestClassifySimple(unittest.TestCase):
    """R2.2: SIMPLE — short messages with no action verbs, file refs, code, or project refs."""

    def test_hello(self):
        self.assertEqual(classify("hello"), "SIMPLE")

    def test_thanks(self):
        self.assertEqual(classify("thanks"), "SIMPLE")

    def test_good_morning(self):
        self.assertEqual(classify("good morning"), "SIMPLE")

    def test_short_question(self):
        # "yes" is a short non-question-word response
        self.assertEqual(classify("yes please"), "SIMPLE")

    def test_single_word(self):
        self.assertEqual(classify("hi"), "SIMPLE")

    def test_six_words_no_triggers(self):
        self.assertEqual(classify("that sounds like a good idea"), "SIMPLE")


class TestClassifyContext(unittest.TestCase):
    """R2.4: CONTEXT — neither SIMPLE nor COMPLEX (conceptual questions, status queries)."""

    def test_what_are_we_working_on(self):
        self.assertEqual(classify("what are we working on?"), "CONTEXT")

    def test_explain_decorators(self):
        self.assertEqual(classify("explain decorators"), "CONTEXT")

    def test_how_does_caching_work(self):
        self.assertEqual(classify("how does caching work?"), "CONTEXT")

    def test_project_ref_triggers_context_not_simple(self):
        # Contains "our" (project ref) but no action verbs/files/code
        self.assertEqual(classify("what is our status?"), "CONTEXT")

    def test_recap_request(self):
        self.assertEqual(classify("give me a recap"), "CONTEXT")


class TestClassifyComplex(unittest.TestCase):
    """R2.3: COMPLEX — action verbs, file patterns, code patterns, multi-sentence, or 30+ words."""

    def test_fix_bug_in_file(self):
        self.assertEqual(classify("fix the bug in auth.py"), "COMPLEX")

    def test_read_file(self):
        self.assertEqual(classify("read main.py"), "COMPLEX")

    def test_refactor_with_multiple_actions(self):
        self.assertEqual(
            classify("refactor the auth module to use JWT tokens and update all tests"),
            "COMPLEX",
        )

    def test_create_new_file(self):
        self.assertEqual(classify("create a new test file"), "COMPLEX")

    def test_action_verb_deploy(self):
        self.assertEqual(classify("deploy the application"), "COMPLEX")

    def test_action_verb_install(self):
        self.assertEqual(classify("install numpy"), "COMPLEX")

    def test_file_pattern_path(self):
        self.assertEqual(classify("look at src/utils"), "COMPLEX")

    def test_code_pattern_underscore(self):
        self.assertEqual(classify("what does get_user do?"), "COMPLEX")

    def test_code_pattern_camelcase(self):
        self.assertEqual(classify("explain UserModel"), "COMPLEX")

    def test_code_pattern_dunder(self):
        self.assertEqual(classify("what is __init__"), "COMPLEX")

    def test_multi_sentence(self):
        self.assertEqual(
            classify("First do this. Then do that."),
            "COMPLEX",
        )

    def test_long_message_over_30_words(self):
        long_msg = " ".join(["word"] * 31)
        self.assertEqual(classify(long_msg), "COMPLEX")

    def test_exactly_31_words(self):
        msg = " ".join(["thing"] * 31)
        self.assertEqual(classify(msg), "COMPLEX")


class TestClassifyEdgeCases(unittest.TestCase):
    """Edge cases and boundary conditions."""

    def test_empty_string(self):
        # Empty message: 0 words, no triggers → SIMPLE
        self.assertEqual(classify(""), "SIMPLE")

    def test_exactly_six_words_no_triggers(self):
        self.assertEqual(classify("one two three four five six"), "SIMPLE")

    def test_seven_words_no_triggers(self):
        # 7 words, no verbs/file/code but has project ref → CONTEXT
        # Or just 7 plain words: not ≤6 so not SIMPLE, no complex triggers → CONTEXT
        self.assertEqual(classify("one two three four five six seven"), "CONTEXT")

    def test_exactly_30_words_no_triggers(self):
        msg = " ".join(["thing"] * 30)
        self.assertEqual(classify(msg), "CONTEXT")

    def test_verb_with_punctuation(self):
        # "fix?" should still match after stripping punctuation
        self.assertEqual(classify("fix?"), "COMPLEX")

    def test_case_insensitive_verb(self):
        self.assertEqual(classify("Fix the issue"), "COMPLEX")


class TestConstants(unittest.TestCase):
    """Validate the defined constant sets and patterns."""

    def test_action_verbs_count(self):
        self.assertEqual(len(ACTION_VERBS), 32)

    def test_action_verbs_contains_expected(self):
        expected = {"fix", "create", "read", "edit", "write", "deploy", "make"}
        self.assertTrue(expected.issubset(ACTION_VERBS))

    def test_project_refs_contains_expected(self):
        expected = {"we", "our", "project", "working", "progress", "status", "recap", "summary"}
        self.assertEqual(PROJECT_REFS, expected)

    def test_file_pattern_matches_extensions(self):
        self.assertIsNotNone(FILE_PATTERN.search("auth.py"))
        self.assertIsNotNone(FILE_PATTERN.search("main.ts"))
        self.assertIsNotNone(FILE_PATTERN.search("config.json"))
        self.assertIsNotNone(FILE_PATTERN.search("style.css"))

    def test_file_pattern_matches_paths(self):
        self.assertIsNotNone(FILE_PATTERN.search("src/utils"))
        self.assertIsNotNone(FILE_PATTERN.search("lib/helpers"))

    def test_file_pattern_rejects_long_extension(self):
        # Extensions longer than 5 chars should not match as file pattern
        self.assertIsNone(FILE_PATTERN.search("file.longext"))

    def test_code_pattern_matches_underscore(self):
        self.assertIsNotNone(CODE_PATTERN.search("get_user"))
        self.assertIsNotNone(CODE_PATTERN.search("my_function_name"))

    def test_code_pattern_matches_camelcase(self):
        self.assertIsNotNone(CODE_PATTERN.search("UserModel"))
        self.assertIsNotNone(CODE_PATTERN.search("getElementById"))

    def test_code_pattern_matches_dunder(self):
        self.assertIsNotNone(CODE_PATTERN.search("__init__"))
        self.assertIsNotNone(CODE_PATTERN.search("__name__"))


class TestPerformance(unittest.TestCase):
    """R2.5: Classification must complete in <5ms."""

    def test_classification_speed(self):
        messages = [
            "hello",
            "fix the bug in auth.py",
            "what are we working on?",
            "refactor the auth module to use JWT tokens and update all tests",
            " ".join(["word"] * 50),
        ]
        for msg in messages:
            start = time.perf_counter()
            classify(msg)
            elapsed_ms = (time.perf_counter() - start) * 1000
            self.assertLess(elapsed_ms, 5.0, f"classify('{msg[:30]}...') took {elapsed_ms:.2f}ms")


if __name__ == "__main__":
    unittest.main()

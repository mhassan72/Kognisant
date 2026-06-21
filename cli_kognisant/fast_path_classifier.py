"""Fast-path message classifier for the runtime pipeline.

Categorizes every user message into SIMPLE, CONTEXT, or COMPLEX using
rule-based pattern matching only (no LLM calls, no network). Designed to
complete classification in <5ms.

Requirements: R2.1, R2.2, R2.3, R2.4, R2.5
"""

import re

# --- Classification Constants ---

ACTION_VERBS: set[str] = {
    "fix", "create", "read", "edit", "write", "modify", "delete", "remove",
    "refactor", "implement", "build", "run", "execute", "test", "deploy",
    "add", "update", "install", "search", "browse", "download", "schedule",
    "script", "migrate", "optimize", "debug", "change", "move", "rename",
    "copy", "generate", "make",
}

PROJECT_REFS: set[str] = {
    "we", "our", "project", "working", "progress", "status", "recap", "summary",
}

# Question/explanation words that indicate conceptual queries (→ CONTEXT, not SIMPLE)
CONTEXT_INDICATORS: set[str] = {
    "explain", "describe", "how", "why", "what", "when", "where", "which",
    "compare", "difference", "overview", "meaning",
}

# Matches filenames like word.ext (1-5 char extension) or path separators (word/word)
FILE_PATTERN: re.Pattern = re.compile(
    r"\b\w+\.\w{1,5}\b"  # word.ext with 1-5 char extension
    r"|"
    r"\w+/\w+"            # word/word paths
)

# Matches code-like tokens: lower_case_underscore, CamelCase, __dunder__
CODE_PATTERN: re.Pattern = re.compile(
    r"\b\w+_\w+\b"               # underscore-separated identifiers
    r"|"
    r"\b[A-Z][a-z]+[A-Z]\w*\b"  # UpperCamelCase (e.g. UserModel)
    r"|"
    r"\b[a-z]+[A-Z]\w*\b"       # lowerCamelCase (e.g. getElementById)
    r"|"
    r"__\w+__"                    # __dunder__ patterns
)

# Multi-sentence: period followed by whitespace and a capital letter
_MULTI_SENTENCE: re.Pattern = re.compile(r"\.\s+[A-Z]")


def classify(message: str) -> str:
    """Classify a user message into SIMPLE, CONTEXT, or COMPLEX.

    Returns one of: "SIMPLE", "CONTEXT", "COMPLEX".

    Classification gates (applied in order):
      SIMPLE — word_count ≤ 6 AND no action verbs AND no file patterns
               AND no code patterns AND no project refs
      COMPLEX — any action verb OR any file pattern OR any code pattern
                OR multi-sentence OR word_count > 30
      CONTEXT — default fallback (neither SIMPLE nor COMPLEX)
    """
    words = message.split()
    word_count = len(words)
    lower_words = {w.lower().rstrip("?.,!;:") for w in words}

    has_verb = bool(lower_words & ACTION_VERBS)
    has_file = bool(FILE_PATTERN.search(message))
    has_code = bool(CODE_PATTERN.search(message))
    has_refs = bool(lower_words & PROJECT_REFS)
    has_context_indicator = bool(lower_words & CONTEXT_INDICATORS)
    is_multi_sentence = bool(_MULTI_SENTENCE.search(message))

    # SIMPLE gate: all conditions must be true
    if (
        word_count <= 6
        and not has_verb
        and not has_file
        and not has_code
        and not has_refs
        and not has_context_indicator
        and not is_multi_sentence
    ):
        return "SIMPLE"

    # COMPLEX gate: any condition triggers it
    if has_verb or has_file or has_code or is_multi_sentence or word_count > 30:
        return "COMPLEX"

    # Default fallback
    return "CONTEXT"

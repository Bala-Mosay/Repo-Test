"""Prompt construction.

A prompt is built from exactly three things: the generation view, the strategy's
context bundle, and this file's static instructions. It has no access to gold
data (docs/architecture.md invariant 1), and no repository file is ever included
without going through the budget (invariant 3).
"""

from __future__ import annotations

from repotest_bridge.retrieval.types import ContextBundle
from repotest_bridge.tasks import GenerationView

SYSTEM_PROMPT = """You are a senior software engineer writing regression tests \
for a real open-source repository.

You will receive:
1. A bug report (an issue) that describes behaviour that is WRONG today.
2. Optionally, selected files from the repository as context. Those blocks are \
delimited by lines of the form `=== FILE: path ===` -- they are NOT part of \
any patch.

Your job is to write a NEW pytest test that FAILS on the current (buggy) code \
because it correctly asserts the behaviour the issue asks for, and PASSES once \
the bug is fixed.

Rules:
- Output ONE unified diff, fenced in a ```diff block, and nothing else outside \
that block (you may add a short ```rationale block after it).
- The diff must ONLY add or modify test files. Never modify library/production \
code, configuration, or existing test assertions.
- Pick a NEW test filename that does not already exist in the repository. \
Never append to, overwrite, or reuse an existing test file.
- Place the file inside the repository's test directory -- the same directory \
the project's test runner collects from (e.g. `tests/`, not the repo root). A \
test file anywhere else is never executed and scores nothing.
- Use only imports and helpers that actually exist in the provided context or \
are standard for this repository's test framework.
- The test must exercise the behaviour described in the issue, including the \
exact boundary/edge case if one is mentioned.
- Do not skip, xfail, or weaken the assertion. Do not catch the bug away.
- Do not restate the issue; produce a compilable, runnable diff.
- Every `@@` header's line counts MUST equal the number of lines under it. \
Understating or overstating them makes the patch unapplicable.

Output format:

```diff
diff --git a/tests/test_example.py b/tests/test_example.py
new file mode 100644
--- /dev/null
+++ b/tests/test_example.py
@@ -0,0 +1,6 @@
+import pytest
+
+def test_the_boundary():
+    assert thing(20) == expected
+    assert thing(19) != expected
```

If you are modifying an existing file rather than adding one, omit the \
`new file mode` line, use `--- a/path` / `+++ b/path`, and give the `@@` \
headers the real line numbers of the file you are changing.
"""

GENERATION_TEMPLATE = """# Task

**Repository:** {repo_label}
**Language / test framework:** {language} / {test_framework}
**Test command:** `{test_command}`
**Task id:** {task_id}

# Issue

## {issue_title}

{issue_body}

# Repository context (strategy: {strategy}, {context_tokens} tokens)

{context_block}

# Reminder

Produce a single ```diff block containing ONLY new or modified *test* files that
reproduce the issue's expected behaviour. The test must fail before the fix and
pass after it.
"""


def repo_label(view: GenerationView) -> str:
    if view.local_path:
        return f"local checkout at {view.local_path}"
    if view.repo_url:
        return view.repo_url
    return "unknown"


def build_messages(view: GenerationView, bundle: ContextBundle) -> list[dict[str, str]]:
    """Assemble the two-message chat prompt."""
    user = GENERATION_TEMPLATE.format(
        repo_label=repo_label(view),
        language=view.language,
        test_framework=view.test_framework,
        test_command=view.test_command,
        task_id=view.task_id,
        issue_title=view.issue_title.strip(),
        issue_body=view.issue_body.strip(),
        strategy=bundle.strategy,
        context_tokens=bundle.total_tokens,
        context_block=bundle.as_prompt_block(),
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def prompt_digest(view: GenerationView, bundle: ContextBundle) -> str:
    """Stable hash of the exact prompt sent, recorded in the run record."""
    import hashlib

    blob = "\0".join(m["content"] for m in build_messages(view, bundle))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def describe_bundle(view: GenerationView, bundle: ContextBundle) -> dict[str, object]:
    return {
        "task_id": view.task_id,
        "strategy": bundle.strategy,
        "items": [
            {
                "path": i.path,
                "category": i.category.value,
                "tokens": i.tokens,
                "score": round(i.score, 4),
                "sha256": i.sha256,
                "rationale": i.rationale,
            }
            for i in bundle.items
        ],
        "per_category_tokens": bundle.category_totals(),
        "total_tokens": bundle.total_tokens,
        "ceiling_tokens": bundle.ceiling_tokens,
        "dropped": bundle.dropped,
    }

# Run log

Chronological record of every run that hit a **real model provider**. Mock and
`--dry-run` runs are omitted — they carry no external evidence.

Nothing here is a research result unless the entry says so. Protocol §5.4: N is
reported with every percentage, and significance language is withheld below the
interval threshold.

---

## smoke-real-01 — 2026-10-11

**Status:** ✅ Stage-6 gate passed — the live path works end to end.
**Status as data:** ⚠️ **Not a result.** N = 1, one synthetic task, model is not
the model behind any published baseline. Only plumbing was validated.

### Setup

| field | value |
|---|---|
| experiment id | `smoke-real-01` |
| provider / endpoint | `openai-compatible` → `https://openrouter.ai/api/v1` |
| model | `openai/gpt-4o-mini` |
| temperature / seed | `0.0` / `0` |
| prompt template | `2026.10-issue-to-test` |
| manifest | `examples/demo_task/manifest.yaml` (`7dcbfffe62a26c70…`), 1 task |
| task | `demo-001` — **synthetic**, gold produced by `scripts/make_demo_gold.py` |
| budget | `max_context_tokens = 6000` |
| arms | `issue_only`, `ctx_source_only`, `repo_aware`, `ctx_random_matched` |
| split / limit | `demo` / `1` |
| execute mode | `--execute` (explicit opt-in, protocol §4.1) |
| wall clock | 26.9 s |
| API calls | 4 (one per arm; no duplicates) |
| token spend | 5,550 input + 1,216 output — no price asserted; provider pricing not queried |
| git | `sha = None`, `dirty = True` — **no commits exist yet** |

### Per-arm outcome

| strategy | outcome | input | output | ctx tok | latency |
|---|---|---:|---:|---:|---:|
| `issue_only` | `unexpected_pass_on_buggy` | 617 | 150 | 0 | 2590 ms |
| `ctx_source_only` | `collect_fail` | 1 052 | 269 | 308 | 2841 ms |
| `repo_aware` | `patch_apply_fail` | 2 382 | 472 | 1 101 | 7144 ms |
| `ctx_random_matched` | `unexpected_pass_on_buggy` | 1 499 | 325 | 550 | 3338 ms |

F2P: **0/4**. That is a statement about this model on this synthetic task, not
about context selection.

### Verification

| # | Check | Result |
|---|---|---|
| A1 | `meta.json`: provider `openai-compatible`, model recorded, `manifest_sha256` set | ✅ |
| A2 | `report.md` shows no `PIPELINE VALIDATION ONLY` banner | ✅ |
| A3 | every row: non-null int tokens, `latency_ms > 0`, `request_id`, `attempts`, `finish_reason` | ✅ |
| B1 | `usage.model` identical across all four rows | ✅ |
| B2 | four distinct `prompt_digest`s | ✅ |
| B3 | `context.total_tokens ≤ 6000` on every row | ✅ |
| B4 | `ctx_random_matched.ceiling_tokens == repo_aware.total_tokens` (1101) — H4 sizing | ✅ |
| C1 | no API key prefix, full key, or env-var name in any artifact | ✅ NONE |
| D1 | every row carries an outcome code plus `exit_codes` and `timings_ms` | ✅ |
| D2 | `patches/*.diff` written for all four arms | ✅ |
| E1 | exactly one provider call per arm (4 calls, 4 rows) | ✅ |

Reproduce the artifact scan with `python scripts/verify_smoke.py`.

### What this run caught

Two defects were fixed **before** spending money:

1. **`cli.py` — duplicate `repo_aware` API call.** When `repo_aware` and
   `ctx_random_matched` were both selected, `full_spend` was only assigned inside
   the control branch, so the loop re-ran `repo_aware` and discarded the result.
   Would have billed 5 calls for 4 arms and sized the control from an unrecorded
   request. Fixed; guarded by
   `tests/integration/test_cli.py::test_research_generates_each_arm_exactly_once`.
2. **`providers/openai_compat.py`** — unreachable `return MockProvider()` after a
   `return`. Removed.

Two more were found by reading the generated `report.md`:

3. **`metrics.py` — impossible ratio.** `validity_pass` was incremented for every
   record *and* again on the `PATCH_APPLY_FAIL` branch, while `n_admitted` was
   not, producing the report cell `2/0 (n/a)`. Now counted only against N.
   Guarded by `test_validity_numerator_never_exceeds_n`.
4. **`metrics.py` — rejected patches billed as free.** Token, context and
   wall-clock accumulation sat *after* the `PATCH_APPLY_FAIL` `continue`, so the
   arm that failed to apply reported `0` input / `0` output / `0` mean context.
   That directly corrupts protocol §5.3's ΔF2P-per-1k normalisation, because an
   arm with rejected patches would look cheaper than it was. Now summed over
   every attempt. Guarded by
   `test_cost_counts_every_attempt_not_only_admitted` and
   `test_mean_context_tokens_includes_rejected_patches`.

### Findings — decide before the real experiment

Each of these changes either the measurement instrument or the prompt, so all
three must be **decided and frozen before** the benchmark run; changing them
afterwards would invalidate comparisons.

- **Prompt/factor confound.** `retrieval/types.py` renders context blocks as
  `--- {path} ---`, which is visually identical to a unified-diff file header.
  The model echoed it into its output as `--- tests/test_pricing.py ---` /
  `+++ tests/test_pricing.py ---`. Recommend a non-confusable separator (a fenced
  block, or `=== path ===`). **Changes `prompt_digest`.**
- **`looks_like_diff()` is too permissive.** It only requires one `---`/`+++`
  line and one `@@`. The `repo_aware` patch declared `@@ -1,6 +1,7 @@` but supplied
  11 context lines, so `git apply` closed the hunk early and failed with a raw
  `patch fragment without header at line 16` — surfaced as `patch_apply_fail`
  rather than `patch_parse_fail`. Adding hunk line-count consistency would move
  these earlier and give a readable reason. **Changes outcome codes**; needs a
  matching protocol §6 update.
- **Protocol §5.1 makes "test-patch validity" tautological.** Its denominator is
  N (admitted), but admission already requires passing structural validation, so
  the column reads 100% whenever N > 0 and `n/a` otherwise. The informative
  denominator is `tasks_attempted`. Raw `patch_parse_fail` counts in the outcome
  table already expose invalid patches, so nothing is lost by amending §5.1 — or
  by dropping the column.
- **Protocol §6 vs implementation.** §6 promises mock runs carry
  `"pipeline_validation": true`; nothing writes it. The report banner instead
  keys off `provider == "mock"`, which works. Doc/impl mismatch to reconcile.
- **No commits exist.** `git.sha` is `None`, so runs are not pinned to a
  revision. Commit the tree before the benchmark run.

### Why none of this is a result

- One synthetic task; the gold patch and its tests were authored, not discovered.
- `unexpected_pass_on_buggy` (×2), `collect_fail`, `patch_apply_fail` are four
  different *kinds* of failure on a task that a working system should pass. That
  is a signal about the model and the prompt, not evidence for or against H1–H4.
- The model is not the model behind any published baseline (§5.4): only
  within-experiment paired deltas would ever be interpretable.

### Next

Real benchmark tasks — adapter over TDD-Bench-Verified (449) or SWT-Bench Lite
(276), both Apache-2.0. That is the only path to actually answering H1–H4.

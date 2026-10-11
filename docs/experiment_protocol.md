# RepoTest-Bridge — Experiment Protocol

**Version:** 1 · **Status:** binding for all runs · **Updated:** 2026-10-09

This document defines the contract that every experiment must satisfy for its results to
be considered admissible. Deviations are permitted only if they are *recorded in the
result record* — never silently.

---

## 1. Task schema

A manifest is a YAML/JSON document:

```yaml
version: 1
tasks:
  - task_id: demo-001                 # stable, unique, <= 64 chars
    split: demo                       # demo | dev | eval
    generation:                       # <-- THE ONLY BLOCK THE GENERATOR MAY SEE
      repo_url: null                  # null when local_path is used
      local_path: repo                # relative to this manifest file's directory
      base_commit: "..."              # pinned; must resolve in the checkout
      issue_title: "..."
      issue_body:  "..."
      issue_source_url: null
      test_command: "python -m pytest -q"
      language: python
      test_framework: pytest
      runtime_requirements: { python: "3.11" }
      license: "Apache-2.0"
      provenance: "synthetic, authored for this repository"
      notes: null
      exclusion_reason: null          # non-null => task is skipped, reason recorded
    evaluation:                       # <-- EVALUATION-ONLY, never surfaced to generation
      fixed_commit: null
      code_patch_file: null           # path relative to the manifest
      gold_test_patch_file: null
      fail_to_pass: []                # expected F2P test node ids, if known
```

### 1.1 Gold-information separation (hard requirement)

`repotest_bridge.tasks` exposes **two distinct return types**:

- `GenerationView(task_id, split, repo_url, local_path, base_commit, issue_title, issue_body,
  test_command, …)` — the only object ever passed to the retriever, prompt builder, or
  generator.
- `EvaluationInputs(...)` — reachable only via `repotest_bridge.tasks.load_evaluation`,
  whose sole caller is `repotest_bridge.evaluation.runner`.

`GenerationView` is a separate Pydantic model whose field set is asserted **by a unit
test** to contain no `fixed_commit`, `code_patch`, `gold`, `fail_to_pass`, or
`test_patch` member. Gold data may additionally live in a *physically separate* file
referenced by `evaluation.code_patch_file`, so a run can be configured with no gold data
present at all.

**Rule:** if a generation run ever reads the `evaluation:` block, the run is invalid and
its results must be discarded.

### 1.2 Exclusion rules

A task is excluded — with the reason stored in the manifest and echoed into the result
record — when any of the following holds:

1. `generation.exclusion_reason` is set.
2. `local_path`/`repo_url` cannot be resolved, or `base_commit` does not exist in it.
3. `test_command` is empty or not explicitly listed.
4. The repository has no recognisable test directory or test framework.
5. Required runtime (e.g. a non-Python language) is unsupported by the harness.
6. The checkout cannot be made without mutating a user-owned working tree.

Excluded tasks are **never** silently dropped: they appear in the report with status
`excluded` and their reason.

---

## 2. Conditions (strategies)

All strategies share one pipeline and differ **only** in `retrieval/strategies.py`.

| Strategy | Admitted context | Hypothesis |
|---|---|---|
| `issue_only` | Issue + fixed minimal source context (the candidate source file(s) only) | Baseline 1 |
| `repo_aware` | Issue + all six artifact classes | Baseline 2 / H1, H2 |
| `ctx_related_test` | `related_test` only | RQ3 arm |
| `ctx_fixture` | `fixture` only | RQ3 arm |
| `ctx_conftest` | `conftest` only | RQ3 arm |
| `ctx_helper` | `helper` only | RQ3 arm |
| `ctx_import_edge` | `import_edge` only | RQ3 arm |
| `ctx_project_config` | `project_config` only | RQ3 arm |
| `ctx_random_matched` | Same token count as `repo_aware`, filled with *structurally valid but issue-irrelevant* repository files | **H4 control** |

### 2.1 Fixed across every strategy

| Control | Value (default) |
|---|---|
| Model identifier | from config, one value per experiment |
| Temperature | `0.0` |
| Max output tokens | `2048` |
| Max retries | `3` |
| Context ceiling | `6000` tokens (global) |
| Per-class ceilings | see `configs/default.yaml` |
| Prompt template | single version, hash recorded per run |
| Task set | identical manifest, identical checkouts, identical commits |
| Repository state | pinned by commit; file hashes recorded |

**Budget rule:** when an arm cannot fill the ceiling, the unused budget is **not**
reallocated to other classes. This is enforced by `retrieval/budget.py` and asserted by a
unit test.

**Randomness:** decoding is greedy. `ctx_random_matched` is the only arm needing a seed;
the seed is recorded in the run metadata.

---

## 3. Generation contract

The generator receives **only**:

1. the issue title and body;
2. the buggy/base checkout at `base_commit`;
3. the context items admitted by the strategy;
4. the test framework, test command, and target test file locations.

The generator must **not** receive: the fixed commit, gold code patch, gold test patch,
`FAIL_TO_PASS`/`PASS_TO_PASS` lists, post-fix source, or any evaluation outcome.

Model output is untrusted. Before a patch is accepted it must pass, in order:

1. **Format** — parses as the documented structured response.
2. **Paths** — every target path is relative, inside the repository root, not absolute,
   not `..`-escaping, not a symlink target (unless explicitly allowed).
3. **Diff** — parses as a unified diff; hunks are internally consistent.
4. **Scope** — only test-tree files are modified (source files rejected by default).

Failures map to `patch_parse_fail` or `patch_apply_fail`; they are *not* retried with
relaxed validation.

---

## 4. Evaluation contract

Each admissible task runs on **two independent checkouts**:

- **Buggy** = `base_commit` (what the generator saw)
- **Fixed** = `evaluation.fixed_commit` or `evaluation.code_patch_file` applied to
  `base_commit`

The *same* generated patch is applied to both. Outcomes are mutually exclusive:

| Code | Meaning | Counts as success? |
|---|---|---|
| `patch_parse_fail` | structured output or diff would not parse | no |
| `patch_apply_fail` | diff rejected by the target checkout | no |
| `collect_fail` | test collection/import error (also on fixed) | **no — infra, not a bug** |
| `no_candidate_tests` | applied and the suite ran, but **none** of the patch's own test files were collected | **no — model-side, not infra** |
| `infra_fail` | environment, dependency, harness or timeout failure | **no — never a detected bug** |
| `unexpected_pass_on_buggy` | passes on buggy ⇒ does not reproduce the issue | no |
| `fails_on_fixed` | still fails after the fix ⇒ test is wrong or over-specified | no |
| `error_on_both` | raises on both versions (assertion-less error, missing symbol) | **no** |
| `f2p_success` | fails on buggy **and** passes on fixed **and** no new fixed failures | **yes** |

Rules:

- Infrastructure failures are counted and reported separately from model failures.
- A test that fails on both versions *because of a collection/import error* is
  `collect_fail`, never a detected bug.
- `no_candidate_tests` is the mirror image: the collection itself was fine, but the
  patch's target files never entered the suite (the model placed the test outside the
  collected scope, or wrote onto an existing test file). It is classified **only** when
  the buggy run passed everything else and a `--collect-only` probe reports zero of the
  patch's own files. `candidate_files_collected` is `None` when that probe could not run,
  and `None` preserves the pre-probe behaviour rather than guessing.
- Timeouts are `infra_fail` with the timeout value recorded.
- Captured output is truncated to `max_output_bytes`; truncation is flagged.

### 4.1 Execution safety

- Untrusted repository code is executed **only** by an explicit `evaluate` command.
- `install`, `doctor`, `retrieve`, `generate`, `validate`, and `pytest` **never** execute
  repository test code.
- Every subprocess has an explicit timeout, bounded output, and is launched with a
  controlled working directory and environment.
- Work happens in a scratch directory (`.repotest-eval/`), removed on success and left
  for inspection on failure.

---

## 5. Metrics

### 5.1 Primary

Let **N** = number of *admitted* tasks (excluded tasks are reported but not in N).
Let **T** = number of tasks *attempted* — every row written to `tasks.jsonl`, before
admission filtering. T ≥ N always.

| Metric | Numerator | Denominator |
|---|---|---|
| Patch application rate | patches applying to the intended state | N |
| Test execution success rate | tests that collected and executed (either version) | N |
| **Fail-to-pass rate** | `f2p_success` | N |
| Test-patch validity | attempted patches passing format+path+diff+scope validation | **T** |

Raw counts are printed with every percentage: `f2p = k/N (pp%)`.

**Validity uses T, not N.** Admission already requires passing structural validation, so
measuring it over N is tautological: the column would read `100%` whenever N > 0 and
`n/a` otherwise. Over T it actually discriminates between arms. Invalid patches remain
individually visible as `patch_parse_fail` rows in the §4 outcome table, so no
information is lost by this denominator.

### 5.2 Secondary

Changed-line coverage (only when measured identically across arms) · mutation score
(optional; stays secondary per MuTAP) · failure-category counts · number of generation
attempts · input/output token counts · inference cost where the provider reports it ·
wall-clock generation and execution time · per-repository and per-split breakdowns.

### 5.3 Derived (H3/H4)

- ΔF2P vs `issue_only`, in percentage points.
- **ΔF2P per 1k input tokens** = (F2P_arm − F2P_issue_only) / (tokens_arm −
  tokens_issue_only) × 1000, reported only when the denominator is non-zero.
- H4 comparison: same figure for `ctx_random_matched`.

### 5.4 Statistics

- Comparisons are **paired**: every strategy runs on the same task set.
- Report per-task outcomes (JSONL) plus aggregates (CSV + Markdown).
- Confidence intervals and paired tests only when N is large enough to justify them;
  below that, report counts and abstain from significance language.
- **All arms are reported, including null and negative results.**

---

## 6. Run record (reproducibility)

Each run writes `experiments/results/<experiment_id>/` containing:

```
meta.json          experiment id, timestamp, git SHA + dirty flag, host, python,
                   manifest path + hash, strategy, prompt-template version + hash,
                   model id, temperature, budgets, retries, seed
tasks.jsonl        one record per task: inputs hash, context items (paths, scores,
                   rationale, sha256, tokens), generated patch, validation results,
                   per-version commands + exit codes, outcome code, token counts,
                   timings, log references
patches/           generated patches, never overwritten
logs/              sanitized stdout/stderr, truncated
summary.csv        aggregate metrics, one row per strategy
report.md          human-readable summary
```

Rules:

- `experiment_id` is unique; an existing directory is never overwritten — re-running
  creates a new id or resumes a partially-complete one without corrupting finished rows.
- A resumable run skips tasks already present in `tasks.jsonl` unless `--force`.
- No API key, bearer token, or raw environment dump is ever written to any artifact.
- Mock-provider runs carry `"provider": "mock"` and `"pipeline_validation": true`, and
  are excluded from performance tables by `report`.

---

## 7. Dev/eval discipline

1. Prompts, budgets and thresholds are tuned on `split: dev` (and `demo`) **only**.
2. `split: eval` tasks are run once per reported experiment.
3. Tuning against eval samples and then reporting those samples is prohibited.
4. Contamination cannot be eliminated (tasks derive from public GitHub history); it is
   recorded in `threats_to_validity.md` and echoed into every report's limitations.
5. Absolute numbers from our model are **not** compared to published GPT-4o figures;
   only within-experiment deltas between strategies are interpreted.

---

## 8. Required commands for a valid run

```bash
repotest-bridge doctor --provider <mock|openai>   # environment preflight
pytest tests                                      # suite green before any experiment
repotest-bridge retrieve --manifest <path> ...    # inspect the prompt, no network
repotest-bridge validate <patch.diff>             # structural validation, no execution
repotest-bridge research --manifest <path> \
    --strategy <s> --provider <p> --execute       # full run (default is --dry-run)
repotest-bridge report <experiment_id> --print    # aggregate tasks.jsonl
```

`doctor` must succeed and `pytest tests` must be green before `research --execute`
will run. `research` defaults to `--dry-run` (generate + validate only); executing
repository test code requires the explicit `--execute` flag.

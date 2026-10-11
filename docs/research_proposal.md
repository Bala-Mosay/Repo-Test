# RepoTest-Bridge — Research Proposal

**Working title.** *Which repository context actually helps? A budget-normalized ablation
of test-artifact context for issue-conditioned regression-test generation.*

**Status.** Proposal. Hypotheses below are *stated to be tested*; none has been
demonstrated by this project at the time of writing. Any results section produced later
must not be back-propagated into this document as though it had been predicted.

**Novelty basis.** [`novelty_audit.md`](novelty_audit.md) — candidates A–D rejected as
already published; proceeding with narrowed candidate E.

---

## 1. Problem statement

An AI coding agent asked to resolve a GitHub issue should first be able to *prove* the
issue is real: a regression test that fails on the buggy code and passes once the fix is
applied. Prior work shows such tests can be generated (Otter, iCoRe, e-Otter++) and that
retrieving *test-side* context helps (SWT-Bench, iCoRe). What no located work establishes
is **which specific classes of repository test context contribute how much, at what token
cost, under a controlled budget**.

Practitioners therefore have no evidence-based rule for what to put in the prompt. The
default — stuff in whatever the retriever scores highest — conflates fixtures,
`conftest.py`, helpers, import structure, and configuration, and makes it impossible to
tell whether the *retrieval* helped or merely the *extra tokens* did.

## 2. Motivation and practical importance

- **Prompt budgets are finite and paid for.** Every token of retrieved `conftest.py` is a
  token not spent on the issue text or the source under test. Otter reports US$0.06–0.09
  per instance; scaling to a 449-task benchmark × several arms makes context selection a
  direct cost driver.
- **Failure modes are known to be integration failures.** Otter's import-fixing ablation
  (−9.2 tddScore without it) and LIBRO's Checkstyle failure ("tests rely heavily on
  external files") both point at exactly the artifacts we propose to isolate.
- **Benchmarks conflate mechanisms.** Reported fail-to-pass gains rarely attribute *why*.
  A controlled decomposition turns an opaque system improvement into an actionable design
  rule.

## 3. Literature review

Full review with verified venues, methods, findings, and author-stated limitations:
[`literature_review.md`](literature_review.md). Summary of what constrains this proposal:

| Finding | Source | Consequence for us |
|---|---|---|
| Test-side context > code-side context (8.1→15.1 vs →10.5) | SWT-Bench, NeurIPS 2024 | Makes test-artifact retrieval the right axis to decompose |
| `w/o Test` −33.3% relative F→P on TDD-Bench | iCoRe, FSE 2026 | Coarse version of our question is already answered — ours must be finer |
| 5 prompt context masks, 63% F→P | e-Otter++, ICSE 2026 | "Varying context" is not novel; ours must vary *artifacts*, not localizer outputs, and must *measure* rather than *ensemble* |
| Import fixing −9.2 tddScore | Otter, ICML 2025 | Integration artifacts matter; supports the fixture/config arms |
| Build config as context (no ablation) | CATGen, preprint | Config-as-context is untested for its marginal value |
| Coverage ≠ correctness | MuTAP, IST 2024 | Fail-to-pass is primary; coverage/mutation secondary |
| Contamination partly unquantifiable | Riddell ACL 2024; Matton Findings 2024 | Standing threat, reported not eliminated |

## 4. Precisely defined research gap

> For **issue-conditioned** regression-test generation, no located study measures the
> **marginal fail-to-pass contribution of individual repository test-artifact classes**
> (related tests, fixtures, `conftest.py`, helpers, static source→test import edges,
> project/CI configuration) **while holding the model, the context-token budget, the
> retry limit and the temperature constant**, and reporting cost-normalized deltas.

Everything else in the original direction — issue conditioning, structural retrieval,
repository-compatible output, paired fail-to-pass evaluation — is established prior work
and is adopted here as *method*, not claimed as contribution.

## 5. Research question and subquestions

**RQ (primary).** *Under a fixed model, context-token budget, and retry limit, does the
composition of retrieved repository test context change the fail-to-pass rate of
issue-conditioned generated regression tests — and which artifact classes carry the
change per unit of token cost?*

- **RQ1** Does repository-aware context improve *integration success* (patch applies;
  tests collect and execute) over an issue-only baseline?
- **RQ2** Does it improve the proportion of tests that fail on the buggy version and pass
  on the fixed version **for the intended behavioral reason**?
- **RQ3** Which contextual information contributes most: related tests, fixtures,
  `conftest.py`, helpers, source→test import edges, or project configuration?
- **RQ4** Does any improvement justify its additional token usage and wall-clock cost
  (ΔF→P per 1k input tokens)?

## 6. Testable hypotheses

Each is falsifiable and states its metric and denominator.

- **H1 (integration).** Under fixed model/budget/retries, `repo_aware` raises the
  *patch application rate* and *test execution success rate* relative to `issue_only`,
  over the same task set (denominator: all tasks in the manifest).
- **H2 (primary).** Under the same controls, `repo_aware` raises the *fail-to-pass rate*
  relative to `issue_only` (denominator: all tasks in the manifest; success requires
  failure on buggy **and** pass on fixed **and** no new failures on fixed).
- **H3 (localization of the effect).** No single artifact class accounts for the whole
  gain; the arms `import_edge` and `fixture` exhibit the largest ΔF→P **per 1k input
  tokens**. *(Directional guess, explicitly a guess.)*
- **H4 (cost).** The ΔF→P of `repo_aware` over `issue_only` divided by the Δinput-tokens
  exceeds that of a token-matched control arm in which the same number of tokens is spent
  on *irrelevant but structurally valid* repository files. This control distinguishes
  "helpful context" from "more context".

> **H3 and H4 are the novel tests.** H1 and H2 re-test what prior work already suggests;
> they are included because our model/benchmark differ from published setups and their
> outcomes are needed to interpret H3/H4.

## 7. Proposed method

### 7.1 Deterministic, embedding-free repository retriever

No vector database, no embedding model, no paid API. Six artifact-class extractors emit
`ContextItem(category, path, score, rationale, sha256, tokens)`:

1. `related_test` — test modules whose imports reach candidate source modules, ranked by
   issue-entity overlap (BM25-style) over identifiers and names.
2. `fixture` — `@pytest.fixture` definitions collected by AST scan, reachable from
   candidate tests.
3. `conftest` — `conftest.py` files at directory levels between the candidate test and
   the repository root.
4. `helper` — non-test modules under the test tree imported by candidate tests.
5. `import_edge` — static `import` edges test-module → source-module, the structural key
   discussed in the novelty audit.
6. `project_config` — `pytest.ini`, `pyproject.toml`, `tox.ini`, `setup.cfg`, CI
   workflows containing test commands.

A **budget layer** enforces `max_context_tokens` globally and `per_category_tokens`
per class, and records, for every run, the selected files with scores, rationale, hashes,
and consumed tokens.

### 7.2 Strategies (independently selectable)

| Name | Definition |
|---|---|
| `issue_only` | Issue + minimal fixed source context. No retrieved test artifacts. |
| `repo_aware` | Issue + all six artifact classes. |
| *research arms* | One per artifact class, plus token-matched random-context control (H4). |

Selected with `--strategy`; the rest of the pipeline is shared, so arms differ in exactly
one place.

### 7.3 Generation

Provider-independent interface; OpenAI-compatible adapter (Groq / OpenRouter / any
compatible endpoint) configured purely by environment variables; deterministic **mock**
provider for offline tests. Structured output → validated unified diff. Model output is
treated as untrusted: format, paths, and patch structure are checked before use.

**Gold-information separation (hard constraint).** The generator receives only the issue,
the buggy/base checkout, permitted retrieved context, and the test framework/config. A
distinct `GenerationView` type *cannot* expose the fixed commit, gold code patch, gold
test patch, or expected outcomes; this is enforced by a unit test, not by convention.

### 7.4 Paired evaluation harness

Two independent checkouts per task — buggy (base) and fixed — with the generated patch
applied to both. Outcomes are classified into **nine mutually exclusive categories**:

`patch_parse_fail · patch_apply_fail · collect_fail · no_candidate_tests ·
infra_fail · unexpected_pass_on_buggy · fails_on_fixed · error_on_both · f2p_success`

Infrastructure/environment failures are never counted as detected bugs. Timeouts, output
caps, and subprocess isolation are explicit; untrusted repository code is **never**
executed during installation or unit testing — execution requires an explicit
`evaluate` command.

## 8. Baselines

- **Baseline 1 — `issue_only`.** Issue + clearly specified minimal source context.
- **Baseline 2 — `repo_aware`.** Issue + source + retrieved tests, fixtures, conftest,
  helpers, import edges, and project conventions.
- **Proposed method — artifact-class arms + token-matched control.** Differers from
  Baseline 2 in exactly one controlled dimension (which classes are admitted under the
  same ceiling).

Held constant across all arms: model identifier, `temperature=0`, context-token ceiling,
`max_retries`, output-token cap, prompt-template version, task set, and repository
checkout. Any deviation is recorded in the result record rather than silently ignored.

## 9. Datasets

| Set | Content | Role |
|---|---|---|
| `examples/demo_task` | Self-contained synthetic buggy/fixed repository committed to this repo | Offline pipeline validation; **never** reported as model performance |
| **Dev set** | 3–5 tasks, manually selected from a compatible source, disjoint from eval | Prompt/protocol debugging only |
| **Eval set** | Held-out tasks, untouched during dev | Reported results |
| **TDD-Bench-Verified** (optional, second phase) | 449 real GitHub issues, Apache-2.0 | Adapter built; execution requires WSL2 + Docker, documented in `benchmark_setup.md` |

**Dev/eval discipline.** Prompts and thresholds are tuned on dev only. Repeated tuning on
eval samples followed by reporting those same samples is prohibited by
`experiment_protocol.md`.

**Task manifest** records: task ID, repo URL + pinned base commit, issue title/body +
source URL, test command, runtime requirements, licence and provenance, split
(`dev`/`eval`/`demo`), exclusion reason, and a physically separated **evaluation-only**
block (fixed commit / gold patches / F2P lists).

**Leakage.** Issues, patches, and gold tests are public GitHub content and appear in
training corpora; TDD-Bench's own paper offers only a one-sentence rebuttal. We record
this as an uneliminated limitation and avoid strong generalization claims.

## 10. Evaluation metrics

**Primary** (denominator stated, raw counts always printed with percentages):

1. *Patch application rate* — patches applying to the intended repository state / all tasks.
2. *Test execution success rate* — tests that collect and execute / all tasks.
3. *Fail-to-pass rate* — buggy-fails ∧ fixed-passes ∧ no new fixed failures / all tasks.
4. *Test-patch validity* — outputs meeting syntactic, structural, and integration
   requirements / all tasks **attempted** (not all tasks admitted; admission already
   requires passing validation, so the admitted denominator would be tautological).

**Secondary:** changed-line coverage (when consistently measurable); mutation score (only
if a compatible tool integrates reliably — stays secondary per MuTAP); failure-category
counts; generation attempts; input/output tokens; inference cost; wall-clock generation
and execution time; per-repository breakdown.

**Statistics.** Paired comparisons on the same tasks (methods are evaluated on identical
task sets). Confidence intervals and paired tests reported only where sample size
permits; **no strong statistical claims from a handful of examples** — small-N results are
reported descriptively with counts.

## 11. Experimental controls

- Same model, temperature 0, same token ceiling, same retries across arms.
- Same prompt template version; template hash recorded per run.
- Same repository checkouts pinned by commit; file hashes recorded.
- Token-matched irrelevant-context control (H4).
- Fixed seed / deterministic decoding where the provider supports it; nondeterminism
  recorded where it does not.
- Mock provider runs labelled `pipeline_validation` and excluded from performance tables.

## 12. Threats to validity

| Threat | Mitigation |
|---|---|
| **Construct** — coverage mistaken for correctness | Fail-to-pass is primary; coverage/mutation explicitly secondary |
| **Construct** — tests failing for the *wrong* reason | Outcome taxonomy separates assertion failure from collection/infra error; manual inspection step for sampled successes |
| **Internal** — gold leakage to generator | Type-enforced `GenerationView`; unit test asserting gold fields are unreachable |
| **Internal** — arms differing in more than one dimension | Single shared pipeline, `--strategy` as the only branch point |
| **External** — 12 popular Python repos, no other languages | Stated; no generalization claims |
| **External** — small N | Counts alongside percentages; no significance claims at small N |
| **Reliability** — contamination | Reported, not eliminated; post-cut-off tasks noted where identifiable |
| **Reliability** — model differs from published GPT-4o baselines | Absolute numbers not compared across papers; only *within-experiment* deltas interpreted |
| **Conclusion** — multiple comparisons across arms | Pre-registered arms; all arms reported, including null results |

Expanded treatment: [`threats_to_validity.md`](threats_to_validity.md).

## 13. Expected contributions

1. A **reproducible, API-free repository test-artifact retriever** with per-class budgets,
   rationales, and content hashes — an artifact others can run offline.
2. A **controlled, budget-normalized ablation** of six test-artifact context classes for
   issue-conditioned fail-to-pass generation, including a **token-matched control** that
   separates "helpful context" from "more context".
3. An **eight-category failure taxonomy** and harness that cleanly separates model
   failures from infrastructure failures, applicable to any issue→test system.
4. Honest **negative or positive** guidance for practitioners about what is worth putting
   in the prompt, expressed as ΔF→P per 1k tokens.

*No claim of a state-of-the-art system or of being first to do X.*

## 14. Limitations

- Benchmark family is Python-only, popular repositories, public history (contamination).
- A model other than GPT-4o will be used unless one is supplied; absolute rates are not
  comparable to published figures.
- Mutation scoring may not integrate reliably and may be dropped; documented if so.
- The TDD-Bench arm may remain unexecuted if WSL2/Docker execution is unavailable;
  documented, never faked.
- Small dev/eval splits in the first release; power for H3/H4 will be limited.

## 15. Timeline and staged milestones

| Stage | Milestone | Gate |
|---|---|---|
| 1 | Literature review, novelty audit, proposal | **Primary contribution confirmed by reviewer** |
| 2 | Protocol + architecture + validated manifests | Gold separation test passes |
| 3 | Baseline pipeline + offline tests | `pytest`, `ruff`, `mypy` green; `doctor` + `retrieve` + `generate` + `validate` work |
| 4 | Research arms + token-matched control | Arms differ only at `--strategy` |
| 5 | Paired harness + taxonomy + metrics | All 9 outcomes reachable by construction in tests |
| 6 | Offline demo + small real run | Demo runs with no key; real run recorded or explicitly marked not run |
| 7 | Documentation + results report | Another researcher can reproduce from README alone |

## 16. Bibliography

Verified entries with peer-review status: [`references.bib`](references.bib).
Narrative review with per-source detail: [`literature_review.md`](literature_review.md).

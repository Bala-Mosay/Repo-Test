# RepoTest-Bridge — Novelty Audit

**Purpose.** Determine, from evidence rather than intuition, whether the proposed direction
is already solved — and if so, narrow it before any research-specific code is written.

**Method.** Five candidate contributions (A–E) were derived from the initial research
direction. Each was searched against the literature collected in
[`literature_review.md`](literature_review.md). For every candidate we record the
overlap, what is already demonstrated, the remaining gap, evidence *for* the gap, evidence
*against* the gap, and a confidence judgement.

**Date of audit:** 2026-10-09. **Audit status:** *decision pending user confirmation*.

---

## 0. Executive decision

| # | Candidate | Verdict |
|---|---|---|
| A | Retrieve existing tests, fixtures, imports, conventions for issue-based test generation | **Already published** — iCoRe (FSE 2026); partially Issue2Test |
| B | Select context by test-to-source relationships rather than similarity alone | **Already published** — iCoRe (FSE 2026), function-call structure over BM25 |
| C | Emit minimal repository-compatible test patches, not isolated snippets | **Engineering, not a contribution** — Otter/e-Otter++ already do framework-adherent output |
| D | Evaluate real fail-to-pass rather than syntactic validity or coverage | **Standard practice** — SWT-Bench, Otter, iCoRe, e-Otter++ all do this |
| E | Cost-controlled ablations over which kinds of repository context help | **Survives, but only in narrowed form** — see §5 |

**Decision rule applied (from the protocol):** *if published research already establishes
the proposed contribution, narrow the question or choose a different measurable research
gap; record the decision and its evidence before implementing.*

**Recorded decision:** candidates A–D are rejected as headline contributions. The project
proceeds with a **narrowed E**, defined precisely in §5 and re-stated as the research
question in `research_proposal.md`. **No claim of the form "first system to…" is made
anywhere in this repository.**

---

## 1. Candidate A — Repository test/context retrieval for issue-based test generation

| Dimension | Assessment |
|---|---|
| **Existing work** | **iCoRe**, Wang/Cao/Liu, *Proc. ACM Softw. Eng.* 3(FSE), Art. FSE186, 2026, DOI 10.1145/3808193, code `ZJU-CTAG/iCoRe`. Also **Otter** (ICML 2025) localizer; **Issue2Test** (arXiv:2503.16320, preprint); **LIBRO** (ICSE 2023). |
| **Its contribution** | A two-stage retriever that treats production code and test code *separately*, driven by an issue description, with iterative generation→retrieval feedback. |
| **Overlap with our proposal** | **High.** Both condition on an issue, both retrieve test-side context, both evaluate paired fail-to-pass on TDD-Bench-Verified. |
| **Already demonstrated** | That issue-conditioned retrieval of existing tests *works* and *helps*: iCoRe reaches 52.8% F→P on TDD-Bench vs Otter's retriever baseline; `w/o Test` drops it to 35.2%. |
| **Remaining candidate gap** | None of this work retrieves **fixture definitions, `conftest.py`, test helpers, or project configuration** as distinct retrievable classes. Issue2Test *synthesizes* fixture conventions by prompting rather than retrieving real ones. |
| **Evidence supporting the gap** | Issue2Test's own description: fixtures come from meta-prompting "without relying on predefined templates". iCoRe's context is described as "production code + relevant test code" only. CATGen is the only work that parses build config — but in a focal-method, Java setting with no ablation. |
| **Evidence against the gap** | Otter's test generator *does* inject the localized test file's structure and imports, and its import-fixing step consults the codebase — so "give the model existing project test conventions" is at least partially present in the strongest prior system. |
| **Confidence that ours is meaningfully different** | **Low for A as stated.** Reject A as the headline. The fixture/conftest/config *sub-part* is better handled inside E. |

---

## 2. Candidate B — Context selection by test-to-source relationships instead of similarity

| Dimension | Assessment |
|---|---|
| **Existing work** | **iCoRe** (FSE 2026): explicitly attacks "reliance on textual semantics while ignoring function call relationships"; models each test as a pruned **function call tree** and reranks by textual + structural similarity. **RefTest/APT** (TOSEM 35(6), 2026): retrieves pre-existing tests of related methods via *reference relationships*, explicitly framed as beyond similarity. |
| **Its contribution** | Structural/relational signals complement or beat lexical similarity when retrieving tests. |
| **Overlap with our proposal** | **Very high — this is precisely candidate B's thesis, already published twice.** |
| **Already demonstrated** | iCoRe: MAP@file 0.747 on TDD-Bench, +20.9% over Otter's retriever; +8.7pp F→P attributable to its retrieval design. RefTest: significant gains over baselines across correctness/completeness/maintainability on 1,515 methods. |
| **Remaining candidate gap** | Only a narrow sub-claim: an explicit **static issue → source → test *import/dependency* edge** key (as opposed to iCoRe's *test→test function-call* tree), applied to issue-conditioned generation, implemented deterministically without an LLM in the loop. |
| **Evidence supporting the gap** | iCoRe's structural signal is a *pruned function call tree rooted at the test*, i.e. test→test behavioural structure; it is also entangled with an LLM sketch-generation and LLM reranking loop, so the retriever cannot be isolated or run without a model. No located work keys on Python `import` edges from test module → source module as the primary ranking signal. |
| **Evidence against the gap** | iCoRe already *argues and shows* structure > similarity for this exact task and benchmark. A reviewer will regard "we used import edges instead of call trees" as a variant, not a contribution. |
| **Confidence that ours is meaningfully different** | **Low–medium as a standalone claim; medium as an enabling artifact.** Do not headline B. The import-edge key is retained as the *implementation* of the retriever that E studies. |

---

## 3. Candidate C — Minimal, repository-compatible test patches vs isolated snippets

| Dimension | Assessment |
|---|---|
| **Existing work** | **Otter** (ICML 2025): emits complete test functions into the localized file, injects file structure + imports, repairs imports via Flake8, then produces a `git diff`. **e-Otter++** (ICSE 2026): framework-adherent outputs with execution-based repair. |
| **Its contribution** | Generated tests integrate into the existing suite rather than existing as standalone snippets. |
| **Overlap with our proposal** | **Complete on the output side.** |
| **Already demonstrated** | Otter's import-fixing ablation: removing it costs 9.2 points of tddScore (GPT-4o) / 4.3 (Mistral) — i.e. integration *matters*, and the mechanism is published. |
| **Remaining candidate gap** | Nothing measurable. "Minimality" of a patch is not an objective anyone optimizes or evaluates; reviewers will not credit it. |
| **Evidence supporting the gap** | — |
| **Evidence against the gap** | Otter's entire test-generator stage exists to produce repository-compatible output. |
| **Confidence that ours is meaningfully different** | **Very low as a contribution.** Rejected. Retained only as a *quality requirement* on our generator (a test that fails to collect = integration failure, counted in our metrics). |

---

## 4. Candidate D — Evaluate real fail-to-pass, not syntactic validity or coverage

| Dimension | Assessment |
|---|---|
| **Existing work** | **SWT-Bench** (NeurIPS 2024) defines F→P; **Otter/TDD-Bench-Verified** (ICML 2025 / arXiv:2412.02883) define `failToPass × adequacy`; **iCoRe** (FSE 2026) uses F→P *plus* no-new-failures; **e-Otter++** (ICSE 2026). |
| **Its contribution** | Judge generated tests by paired execution on buggy vs fixed checkouts. |
| **Overlap with our proposal** | **Total. This is the standard evaluation for the task.** |
| **Already demonstrated** | Defined, harness-validated, human-filtered, and reproduced across four independent groups. |
| **Remaining candidate gap** | None. |
| **Evidence supporting the gap** | — |
| **Evidence against the gap** | SWT-Bench's W/S/F→×/F→P/P→P taxonomy and TDD-Bench's harness already enumerate almost exactly the failure categories our protocol requires. |
| **Confidence that ours is meaningfully different** | **None as a contribution.** Rejected outright. We *adopt* their definition (and explicitly extend its failure taxonomy to distinguish infrastructure from model failures, which is a correctness requirement, not a novelty claim). |

---

## 5. Candidate E (narrowed) — Budget-normalized ablation over repository **test-artifact** context types

This is the candidate that survives. It is stated here in its *narrowed* form; the original
wording ("cost-controlled ablations to determine which kinds of repository context help")
was too broad and is what prior work partially covers.

### 5.1 What is proposed

Under a **fixed model, fixed context-token budget, fixed retry limit, and fixed temperature**,
measure the marginal contribution of each repository **test-artifact** class to
issue-conditioned fail-to-pass rate, reporting both ΔF→P and ΔF→P per 1,000 input tokens:

| Arm | Artifact class | What is retrieved |
|---|---|---|
| `issue_only` | *(baseline)* | Issue text + minimal source context only |
| `+ related_test` | Related tests | Existing test functions that import/exercise candidate source modules |
| `+ fixture` | Fixtures | `@pytest.fixture` definitions reachable by those tests |
| `+ conftest` | `conftest.py` | Fixture registrations and hooks at the relevant directory levels |
| `+ helper` | Test helpers | Shared utilities/base classes/factories under the test tree |
| `+ import_edge` | Source→test import edges | Static import graph from test module to source module |
| `+ project_config` | Project/CI config | `pytest.ini`/`pyproject`/`tox.ini`/CI workflow test commands |
| `repo_aware` | *(upper arm)* | All of the above |

Each arm receives the **same token ceiling**; when an arm cannot fill it, the freed budget
is *not* reallocated (documented, enforced by `retrieval/budget.py`).

### 5.2 Comparison table

| Dimension | Assessment |
|---|---|
| **Existing work** | **iCoRe** (FSE 2026) — `w/o Test` / `w/o Code`; **e-Otter++** (ICSE 2026) — 5 prompt context masks; **SWT-Bench** (NeurIPS 2024) — test-files-vs-code-patch contrast; **Auto-TDD** (arXiv:2412.02883) — Table V ablations; **CATGen** (arXiv:2607.19682) — token/time reporting, no ablation. |
| **Its contribution (theirs)** | iCoRe: which *component family* matters. e-Otter++: which *prompt composition* yields diverse candidates. SWT-Bench: test context > code context. CATGen: explicit context reduces tokens/time. |
| **Overlap with our proposal** | **Real and must be disclosed.** "Vary what the generator sees" is not new (e-Otter++); "ablate context on/off" is not new (iCoRe, Auto-TDD); "report tokens next to F2P" is not new (iCoRe, Otter's cost table, CATGen). |
| **Already demonstrated** | Test-side context matters more than code-side (SWT-Bench 8.1→15.1 vs →10.5; iCoRe `w/o Test` −33.3% relative on TDD-Bench). Context composition changes outcomes (e-Otter++ masks). Explicit context reduces cost (CATGen). |
| **Remaining candidate gap** | A **fine-grained, budget-normalized decomposition of repository *test-artifact* classes** for issue-conditioned generation, where **no arm may spend more tokens than another**, and results are reported as **F2P per 1k input tokens**. Specifically: nobody treats *fixtures*, *`conftest.py`*, *test helpers*, *project/CI config*, or *static source→test import edges* as retrieved, individually ablatable classes. |
| **Evidence supporting the gap** | (i) iCoRe's ablation has exactly two arms (`w/o Test`, `w/o Code`) with no token accounting; (ii) e-Otter++'s masks vary **localizer/planner output subsets to manufacture diversity for ensembling**, not repository artifacts, and are not analyzed as marginal-value ablations; (iii) Issue2Test *synthesizes* fixture knowledge by prompting instead of retrieving it — so no ablation of real fixture context is possible there; (iv) CATGen enumerates build-config context but has **no context-type ablation** (verified against the full HTML: no `w/o <context>` variants) and is not issue-conditioned; (v) Otter's Table 2 ablates *pipeline stages*, not artifact classes, and reports no token-normalized deltas. |
| **Evidence against the gap** | A hostile reading says: "this is just an ablation study — incremental." It is also possible a 2025–2026 paper performs exactly this taxonomy and was not retrieved (see limitations below). iCoRe's coarse result may already be *sufficient* practical guidance, making the fine-grained decomposition low-value. |
| **Confidence that ours is meaningfully different** | **Medium.** Differentiated on *granularity* (artifact classes vs component families), *normalization* (fixed token ceiling per arm, reported as ΔF2P/1k tokens), and *trigger* (static import edges, no LLM in the retrieval loop). Not differentiated on the general idea of context ablation, which we explicitly credit to prior work. |

### 5.3 Why this framing rather than another

- **A, B, C, D are published.** Claiming them would be indefensible at review time.
- **E-narrowed is measurable with the infrastructure we must build anyway** — the same
  retriever, generator, and paired harness serve both baselines and arms.
- **E-narrowed is falsifiable and cheap to state negatively**: if fixtures/conftest/config
  arms show no delta under fixed budget, that *is* a publishable negative result telling
  practitioners not to spend context on them.
- **It directly answers the original RQ3 and RQ4** ("which contextual information
  contributes most" and "does the improvement justify its token cost") rather than the
  original RQ1/RQ2, which prior work has already answered.

---

## 6. Threats to this audit

1. **Search incompleteness.** Semantic Scholar was rate-limited (HTTP 429); ScienceDirect,
   ACM DL, and OpenReview served bot-challenges. Venue verification used Crossref, arXiv,
   publisher proceedings sites, and official artifacts instead. A paper performing exactly
   the E-narrowed study may exist and not have been retrieved.
2. **Very recent work.** e-Otter++ (ICSE 2026), iCoRe (FSE 2026), CATGen (2026), EvoOtter
   (2026) post-date most surveys. The window between audit and submission is a risk.
3. **Preprints may become peer-reviewed** between this audit and any publication; venue
   status recorded here must be re-checked before submission.
4. **Self-assessment bias.** Confidence levels are ours, not an independent reviewer's.

## 7. Required re-audit triggers

Re-run this audit before any paper submission if: (a) a new issue-conditioned test
generation benchmark is released; (b) a paper appears ablating fixture/conftest/config
context for this task; (c) e-Otter++/iCoRe/EvoOtter publish a journal extension;
(d) more than 6 months elapse.

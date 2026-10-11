# RepoTest-Bridge — Literature Review

**Scope.** Targeted review of issue-conditioned and repository-aware test generation, test
adequacy, repository-level retrieval, and benchmark contamination. Conducted 2026-10-09
against primary sources (publisher proceedings pages, arXiv abstract pages, official
artifacts).

**Reading conventions used throughout.**

- **[PR]** peer-reviewed venue (conference proceedings or journal) — verified on the
  publisher's own page or the official proceedings record.
- **[PRE]** preprint / technical report — arXiv or equivalent, **venue not verified**; not
  to be described as peer-reviewed anywhere in this repository.
- Every URL below was fetched during this review. Where a claim could not be checked
  against a primary source it is marked **UNVERIFIED**.
- No DOI, venue, author, result, or limitation in this document is inferred. Absences are
  stated as absences.

**Search limitation (declared).** Semantic Scholar's API returned HTTP 429 during this
review; ScienceDirect, ACM DL, and OpenReview served bot-challenges and were not
directly readable. Those sources were substituted with Crossref records, arXiv, official
author pages, and the publishers' own proceedings sites where possible. This review is
therefore **targeted, not exhaustive**; it should not be cited as a systematic mapping
study.

---

## 1. Primary reference

### 1.1 Otter: Generating Tests from Issues to Validate SWE Patches

| Field | Value |
|---|---|
| Authors | Toufique Ahmed, Jatin Ganhotra, Rangeet Pan, Avraham Shinnar, Saurabh Sinha, Martin Hirzel |
| Year | 2025 |
| Venue | **[PR]** International Conference on Machine Learning (ICML), *Proceedings of the 42nd ICML*, PMLR 267:752–771 |
| Official URL | https://proceedings.mlr.press/v267/ahmed25b.html |
| Preprint | arXiv:2502.05368 (v2, 2025-05-30); arXiv comment field states "Accepted to the main technical track of ICML, 2025" |
| Code / data | Benchmark + generated outputs only: https://github.com/IBM/TDD-Bench-Verified . **No Otter system code was located.** The paper itself points only to that repository. |
| Problem | Generate a regression test *from an issue before the fix exists*, such that the test fails on the buggy code and passes once the fix is applied (fail-to-pass), supporting both TDD and validation of SWE-agent patches. |
| Method | Three stages: (1) **Localizer** — two LLM calls to pick top-10 test files, then test functions, then focal files/functions, with rule-based validation and edit-distance repair of hallucinated names; (2) **Self-reflective action planner** — produces a plan of `read`/`write`/`modify` actions, validated then re-prompted, with Satisfied/Unsatisfied/Unsure self-assessment, at most 5 iterations; (3) **Test generator** — emits *complete test functions* rather than diffs, receives the localized test file's structure and imports, and is followed by a Flake8-guided import-fixing step. **Otter++** adds an ensemble: five heterogeneous prompts (full, both localizers, test-localizer only, focal-localizer only, neither) with greedy decoding, selected by running each candidate on the buggy code and preferring assertion failures over other failures over errors, with a fixed tie-break ordering. |
| Evaluation | TDD-Bench-Verified (449 instances); GPT-4o (`gpt-4o-2024-08-06`) and Mistral-large; greedy decoding; 7–11 LLM calls per instance for Otter. Metric `tddScore = failToPass × adequacy`, where `adequacy` is coverage restricted to lines added/deleted by the gold fix. |
| Main findings | GPT-4o: zero-shot 18.7% fail-to-pass, Otter 31.4%, Otter++ 37.0% (pass@5 44%). Mistral-large: 12.7% / 26.9% / 32.1%. Ablation (Table 2): removing test localization costs 17.7 points of tddScore (GPT-4o), removing focal localization 14.3, removing action planning 19.7, removing import fixing 9.2. Test-localization correctness correlates with F2P (36.9% vs 18.2%); focal-localization correctness barely does (31.9% vs 29.1%). Cost: US$0.06/instance for Otter, US$0.09 for Otter++. Filtering SWE-bench patches raises precision to 65–92% at 30–41% recall. |
| Limitations stated by authors | No dedicated limitations section. §6.8 addresses contamination directly: the GPT-4o cut-off is October 2023, only **one** post-cut-off instance exists, so no meaningful before/after comparison was possible; the authors report no performance pattern by issue year. Localization accuracy is reported as a weakness (82.4% focal, 70.6% test with GPT-4o). |
| Relationship to RepoTest-Bridge | Defines the task, the benchmark, and the baseline (`tddScore`, zero-shot baseline, fail-to-pass criterion). Otter's localizer is **an LLM selecting from a pre-filtered file list**, not a deterministic retriever, and its ablations switch *localizer outputs* on and off rather than enumerating repository artifact classes. |
| Remains differentiable | Otter never treats fixtures, `conftest.py`, test helpers, project/CI configuration, or source→test import edges as distinct, ablatable context categories, and never holds the context-token budget constant across ablation arms. |

**Verification note.** The local copy of the published PDF (`ahmed25b.pdf`) was read in
full for §4 (methodology), §5–6 (benchmark, evaluation, ablation) and the appendix
prompt templates. Page-level claims above come from that file; bibliographic metadata was
independently confirmed on the PMLR page.

---

## 2. Repository-level testing research

### 2.1 TestExplora: Benchmarking LLMs for Proactive Bug Discovery via Repository-Level Test Generation

| Field | Value |
|---|---|
| Authors | Steven Liu, Jane Luo, Xin Zhang, Aofan Liu, Hao Liu, Jie Wu, Ziyang Huang, Yangyu Huang, Yu Kang, Scarlett Li |
| Year | 2026 |
| Venue | **[PR]** ICML 2026, PMLR 306:77728–77771 |
| Official URL | https://proceedings.mlr.press/v306/liu26ct.html |
| Code | https://github.com/microsoft/TestExplora (linked as "Software" from the proceedings page) |
| Problem | Evaluate LLMs as *proactive* testers: find defects **before** any failure or issue report exists, using documentation-derived intent as the oracle. |
| Method | 2,389 tasks over 482 repositories; all defect-related information concealed from the model; continuous time-aware data collection to reduce static-set leakage. |
| Main findings | Best single-model fail-to-pass **16.06%**; SWEAgent + GPT-5-mini reaches F2P 17.27% and F2P@5 29.7%. Cross-module navigation and agentic exploration identified as the binding constraints. |
| Limitations stated by authors | Framed in the abstract as the field's limitation rather than the paper's: prior evaluations either treat existing code as ground truth ("compliance trap") or rely on post-failure artifacts. |
| Relationship to RepoTest-Bridge | **Different task.** TestExplora is proactive (no issue given); RepoTest-Bridge is *reactive* (an issue is given). It is relevant as evidence that repository-scale, cross-module reasoning is the dominant failure mode, and as a source of benchmark-design practice (concealment, time-aware collection). |
| Remains differentiable | Task definition differs; no overlap with the proposed contribution. |

### 2.2 SWT-Bench: Testing and Validating Real-World Bug-Fixes with Code Agents

| Field | Value |
|---|---|
| Authors | Niels Mündler, Mark Niklas Müller, Jingxuan He, Martin Vechev |
| Year | 2024 |
| Venue | **[PR]** NeurIPS 2024 Datasets and Benchmarks (proceedings PDF verified) |
| Official URL | https://arxiv.org/abs/2406.12952 ; proceedings: https://proceedings.neurips.cc/paper_files/paper/2024/file/94f093b41fc2666376fb1f667fe282f3-Paper-Conference.pdf |
| Code / data | https://github.com/logic-star-ai/SWT-Bench |
| Problem | Build an issue-conditioned test-generation benchmark by transforming SWE-bench from code-repair into test generation, and measure whether generated tests can validate machine-produced fixes. |
| Method | 1,983 instances (276 in SWT-Bench Lite), each with issue + golden code patch + golden tests. Fail-to-pass defined as: test fails before the golden patch, passes after, and no test fails after. Reports well-formedness (W), success rate (S), F→×, F→P, P→P, and coverage change of modified code. |
| Main findings | SWE-Agent+ 18.5% S; LIBRO 14.1%; SWE-Agent 15.9%; ZeroShot 3.6%. **Critically for us:** an ablation shows *providing the test files to change* raises S from 8.1% → 15.1%, whereas *providing the code patch and the files it changed* raises S only to 10.5%. The authors conclude "this highlights the importance of retrieving the correct context for generating relevant tests." Filtering code fixes with generated tests more than doubles SWE-Agent precision to 47.8%. |
| Limitations stated by authors | Reproducing an issue and fixing it are "distinct tasks of different difficulty" and performance does not transfer per-sample; ensemble complementarity suggests no single method dominates. |
| Relationship to RepoTest-Bridge | Supplies the *precursor* evidence for our RQ3: context type matters and **test-side context mattered more than code-side context** in their setting. Their evidence is a single coarse contrast (test files vs code patch), measured while also changing which patch was shown — not a budget-controlled taxonomy. |
| Remains differentiable | SWT-Bench identifies the importance of test context but does not decompose it into fixtures/conftest/helpers/import-edges/config, nor normalize by token budget. |

### 2.3 iCoRe: An Iterative Correlation-Aware Retriever for Bug Reproduction Test Generation — **closest prior work**

| Field | Value |
|---|---|
| Authors | Junyi Wang, Jialun Cao, Zhongxin Liu |
| Year | 2026 |
| Venue | **[PR]** Proceedings of the ACM on Software Engineering, Vol. 3, FSE, Article FSE186, pp. 4231–4252 (July 2026) |
| DOI | 10.1145/3808193 |
| Official URL | https://dl.acm.org/doi/10.1145/3808193 ; preprint https://arxiv.org/abs/2604.19224 |
| Code | https://github.com/ZJU-CTAG/iCoRe |
| Problem | Existing retrieval for issue-conditioned test generation treats source and test code alike, relies on semantic similarity alone, and has no generation→retrieval feedback loop. |
| Method | Two-stage retriever: (1) **production-code retrieval** — keyword extraction from the issue, searched against a hierarchical file/class/method index plus heuristics; (2) **test-code retrieval** — LLM initial exploration, then iterative refinement in which a *sketch BRT* is generated and compared to the existing suite by **BM25 textual similarity and pruned function-call-tree similarity**, reranked by an LLM. Integrated with a LIBRO-inspired generator. |
| Evaluation | SWT-bench Lite (276) and TDD-bench Verified (449), GPT-4o, paired fail-to-pass with the additional condition of *no new failures on the fixed version*. Retrieval quality also measured as MAP/MRR/Hit@k against developer-modified tests. |
| Main findings | F→P **42.0%** (SWT-Lite) and **52.8%** (TDD-Bench), vs SOTA e-Otter++ at 40.2% / 51.4%. Retrieval MAP at file level 0.699 / 0.747, beating Otter's retriever by 18.3% / 20.9%. Ablation: `w/o Test` drops TDD-bench F→P@1 from 52.8 → 35.2 (−33.3%); `w/o Code` drops it to ~36. F→P peaks at iteration 3. |
| Limitations stated by authors | The paper is explicit that its retrieval still depends on LLM calls in the loop and that retrieval/generation are mutually coupled; the ablation is at the granularity of two component families only. |
| Relationship to RepoTest-Bridge | **Substantially overlaps candidates A and B.** iCoRe already (i) conditions on an issue, (ii) retrieves test-side context separately from source-side context, and (iii) argues structure (function-call relations) should complement similarity — exactly candidate B's thesis — and it evaluates on the same benchmark with paired fail-to-pass. |
| Remains differentiable | Its ablation is **coarse and unnormalized**: `w/o Test` vs `w/o Code`, two families, with no accounting of how many tokens each arm consumed. It does not isolate *fixtures*, *`conftest.py`*, *test helpers*, *project/CI configuration*, or *static source→test import edges* as separate artifact classes, and it uses function-call trees (dynamic-ish behavioural structure) rather than static import dependency edges. It also couples retrieval with an LLM sketch-generation loop, so retrieval is not independently controllable. |

### 2.4 Context Matters: Improving the Practical Reliability of LLM-Based Unit Test Generation (CATGen)

| Field | Value |
|---|---|
| Authors | Junjie Chen, Ziqi Wang, Lin Yang, Chen Yang, Xiao Chu, Jianyi Zhou, Guangtai Liang, Qianxiang Wang, Dong Wang |
| Year | 2026 |
| Venue | **[PRE]** arXiv:2607.19682 (2026-07-22), 23 pp. — venue **UNVERIFIED**; structured as an experience report |
| Official URL | https://arxiv.org/abs/2607.19682 |
| Problem | Generated tests fail to compile or need manual repair in real projects with cross-file dependencies. |
| Method | Multi-stage: **structured context retrieval**, deterministic test-skeleton construction, and static-analysis post-processing instead of LLM repair loops. Enumerates five context types: inner-class context, focal-method context, external callee information, **testing framework** and **mocking framework** — the latter two detected by *parsing build descriptors such as `pom.xml`*. |
| Evaluation | Proprietary industrial projects plus Defects4J; metrics are compilation success, structural coverage, generation time, and token consumption. |
| Main findings | Compilation robustness depends on making project-level dependencies explicit; substantial reduction in generation time and token use versus LLM-only baselines. |
| Limitations stated by authors | Framed as industrial-practicality limits: results are on focal methods, with Java/JUnit-style build files, and generalization beyond that setting is not established. |
| Relationship to RepoTest-Bridge | The **only source found** that treats *project configuration as a first-class retrieved context artifact*. Overlaps candidate E's "project config" arm — but in a **focal-method, non-issue-conditioned** setting, without fail-to-pass, and **with no context-type ablation** (verified against the full HTML: no `w/o <context>` variants exist). |
| Remains differentiable | No issue conditioning, no paired buggy/fixed evaluation, no per-artifact-class ablation, no budget normalization. |

### 2.5 Reference-Based Retrieval-Augmented Unit Test Generation (RefTest) / APT

| Field | Value |
|---|---|
| Authors | Zhe Zhang, Xingyu Liu, Yuanzhang Lin, Xiang Gao, Hailong Sun, Yuan Yuan |
| Year | 2026 |
| Venue | **[PR]** ACM Transactions on Software Engineering Methodology (TOSEM) 35(6), 2026 — also presented in the ASE 2025 journal-first track |
| Official URLs | https://conf.researchr.org/details/ase-2025/ase-2025-journal-first-track/46/Reference-Based-Retrieval-Augmented-Unit-Test-Generation ; https://researchr.org/publication/ZhangLLGSY26 |
| Predecessor preprint | APT: arXiv:2410.13542 [PRE] |
| Problem | LLM unit-test generation uses only immediate code context and ignores the reusability of existing tests of *related* methods; optimizing for coverage harms correctness and maintainability. |
| Method | Defines **test reference relationships** — reusability between a focal method and other methods — retrieves the related methods *and their existing unit tests*, and decomposes generation into **Given / When / Then** phases each served by retrieved examples. Incremental generation: each new test informs the next. |
| Evaluation | 12 open-source projects, 1,515 methods; correctness, completeness, maintainability versus existing tools. |
| Main findings | Consistent gains on correctness/completeness/maintainability over baselines. |
| Limitations stated by authors | Framed as a focus limitation: the approach is defined for a *focal method*, and evaluation is metric-based rather than fail-to-pass. |
| Relationship to RepoTest-Bridge | Overlaps the "retrieve existing tests rather than rely on similarity" idea in a general form — but for **focal-method** unit-test generation, not issue-conditioned regression tests, and without paired fail-to-pass. |
| Remains differentiable | Different task (focal method vs issue); no fail-to-pass; no artifact-class decomposition. |

### 2.6 Enhancing LLM's Ability to Generate More Repository-Aware Unit Tests Through Precise Contextual Information Injection (RATester)

| Field | Value |
|---|---|
| Authors | Xin Yin, Chao Ni, Xinrui Li, Liushan Chen, Guojun Ma, Xiaohu Yang |
| Year | 2025 |
| Venue | **[PRE]** arXiv:2501.07425 (submitted 2025-01-13), **single version, no venue field** — publication status **UNVERIFIED**, must not be called peer-reviewed |
| Official URL | https://arxiv.org/abs/2501.07425 |
| Problem | LLMs hallucinate non-existent methods and wrong signatures when generating unit tests because they lack global project context; fixed context patterns are suboptimal. |
| Method | **Iterative, demand-driven injection**: when the model meets an unfamiliar identifier, the Go language server `gopls` is queried for its definition and documentation comment, and that knowledge is fed back. Repository awareness is thus driven by *what the model does not know*, not by up-front retrieval. |
| Evaluation | Focal-method unit-test generation in **Go**; hallucination and test-quality measures. |
| Main findings | Injecting global context on demand reduces hallucinated identifiers and improves generated-test quality. |
| Limitations stated by authors | Language-specific (relies on `gopls`), focal-method scope. |
| Relationship to RepoTest-Bridge | Overlaps "repository context for test generation" in general. But it is *reactive identifier resolution*, not *issue-conditioned context selection*, and it carries no fail-to-pass evaluation. |
| Remains differentiable | Different trigger (unknown identifier vs issue), different artifact granularity, no paired evaluation. |

---

## 3. Test adequacy, oracles, and mutation testing

### 3.1 Effective test generation using pre-trained Large Language Models and mutation testing (MuTAP)

| Field | Value |
|---|---|
| Authors | Arghavan Moradi Dakhel, Amin Nikanjam, Vahid Majdinasab, Foutse Khomh, Michel C. Desmarais |
| Year | 2024 |
| Venue | **[PR]** Information and Software Technology (Elsevier), Vol. 171, article 107468 |
| DOI | 10.1016/j.infsof.2024.107468 (observed in Crossref and on the artifact's DOI badge) |
| Official URL | https://www.sciencedirect.com/science/article/pii/S0950584924000739 — **blocked during this review (HTTP 400)**; metadata obtained from Crossref + arXiv + the artifact |
| Preprint | arXiv:2308.16557 [PRE] |
| Code | https://github.com/ExpertiseModel/MuTAP |
| Problem | Coverage is a weak proxy: tests can be green and still miss faults. |
| Method | Feed **surviving mutants** back into the LLM prompt and iterate, targeting bug-revealing ability rather than coverage; post-processing to repair syntactic/functional errors. |
| Evaluation | 164 synthetic PUTs and 1,710 buggy programs from a Python bug-repair benchmark; baselines include Pynguin and zero/few-shot LLMs. |
| Main findings | Detects up to 28% more faulty human-written snippets (17% undetected by any baseline); mutation score 93.57%; concludes LLM-generated tests need post-processing. |
| Limitations stated by authors | Generated tests "may suffer from syntactic or functional errors" and "may be ineffective in detecting certain types of bugs and testing corner cases"; scoped to Python; relies on Codex / llama-2-chat. |
| Relationship to RepoTest-Bridge | Establishes the methodological position that **coverage ≠ correctness**, which underpins our decision to make fail-to-pass primary and coverage/mutation secondary. |
| Remains differentiable | Mutation testing is not our primary contribution; we adopt it only as an optional secondary metric. |

### 3.2 CodeT: Code Generation with Generated Tests

| Field | Value |
|---|---|
| Authors | Bei Chen, Fengji Zhang, Anh Nguyen, Daoguang Zan, Zeqi Lin, Jian-Guang Lou, Weizhu Chen |
| Year | 2023 |
| Venue | **[PR]** ICLR 2023 |
| Official URL | https://arxiv.org/abs/2207.10397 ; https://iclr.cc/virtual/2023/poster/12011 |
| Code | https://github.com/microsoft/CodeT (**UNVERIFIED** — observed in search results, page not fetched) |
| Problem | Select a correct solution among many LLM samples without human tests. |
| Method / findings | Dual execution agreement over generated tests: pass@1 65.8% on HumanEval (+18.8 over code-davinci-002). |
| Relationship | Evidence that *generated tests are usable as oracles* — but for ranking code, not for issue-conditioned regression testing. Relevant to our decision to treat "the test passes" as insufficient without a paired buggy check. |

### 3.3 CODAMOSA: Escaping Coverage Plateaus in Test Generation with Pre-trained Large Language Models

| Field | Value |
|---|---|
| Authors | Caroline Lemieux, Jeevana Priya Inala, Shuvendu Lahiri, Siddhartha Sen |
| Year | 2023 |
| Venue | **[PR]** ICSE 2023 |
| DOI | 10.1109/ICSE48619.2023.00085 |
| Official URL | https://www.microsoft.com/en-us/research/publication/codamosa-escaping-coverage-plateaus-in-test-generation-with-pre-trained-large-language-models/ |
| Code | https://github.com/microsoft/codamosa (linked from the visited page) |
| Findings | Escaping coverage plateaus with LLM-suggested example tests yields significantly higher coverage on 173/279 benchmarks, with 10/4 regressions. |
| Relationship | Search-based + LLM hybrid for *coverage*; confirms coverage is the field's default metric and that beating it requires extra machinery — supporting our coverage-is-secondary stance. |

---

## 4. Repository-level coding agents and retrieval

### 4.1 SWE-bench: Can Language Models Resolve Real-World GitHub Issues?

| Field | Value |
|---|---|
| Authors | Carlos E. Jimenez, John Yang, Alexander Wettig, Shunyu Yao, Kexin Pei, Ofir Press, Karthik Narasimhan |
| Year | 2024 |
| Venue | **[PR]** ICLR 2024 (Oral) |
| Official URL | https://arxiv.org/abs/2310.06770 ; https://www.iclr.cc/virtual/2024/oral/19757 |
| Code | https://github.com/swe-bench/SWE-bench |
| Findings | 2,294 issue-resolution tasks; best system at the time resolved 1.96%. |
| Relationship | The **ancestor dataset** of both SWT-Bench and TDD-Bench-Verified. Crucially distinct task: it grades *code patches*, we grade *test patches*. Never conflate the two. |
| Note | "SWE-bench Verified" (500 human-validated samples) is an **OpenAI product release/blog**, not a publication — cited as such. |

### 4.2 SWE-bench Multimodal: Do AI Systems Generalize to Visual Software Domains?

| Field | Value |
|---|---|
| Authors | John Yang, Carlos E. Jimenez, Alex L. Zhang, Kilian Lieret, et al. (13) |
| Year | 2025 |
| Venue | **[PR]** ICLR 2025 (per the official SWE-bench repository's BibTeX) |
| Official URL | https://arxiv.org/abs/2410.03859 |
| Findings | Top resolve rate 12.2% in the visual/JavaScript domain — strong evidence of weak cross-domain generalization. |
| Relationship | Supports our threat-to-validity framing: results on 12 popular Python repositories do not generalize automatically. |

### 4.3 RepoCoder: Repository-Level Code Completion Through Iterative Retrieval and Generation

| Field | Value |
|---|---|
| Authors | Fengji Zhang, Bei Chen, Yue Zhang, Jacky Keung, Jin Liu, Daoguang Zan, Yi Mao, Jian-Guang Lou, Weizhu Chen |
| Year | 2023 |
| Venue | **[PR]** EMNLP 2023 main conference (stated in the arXiv comment field) |
| Official URL | https://arxiv.org/abs/2303.12570 |
| Code / benchmark | https://github.com/microsoft/CodeT/tree/main/RepoCoder (RepoEval), stated in the abstract |
| Findings | Iterative retrieval→generation beats single-shot retrieval-augmented completion by >10% in all settings. |
| Relationship | Methodological precedent for iterative retrieval in repositories; supports the *feasibility* of our retriever but targets completion, not test generation. |

---

## 5. Contamination and reproducibility

### 5.1 Quantifying Contamination in Evaluating Code Generation Capabilities of Language Models

| Field | Value |
|---|---|
| Authors | Martin Riddell, Ansong Ni, Arman Cohan |
| Year | 2024 |
| Venue | **[PR]** ACL 2024 Main, Long Papers, pp. 14116–14137 |
| DOI | 10.18653/v1/2024.acl-long.761 |
| Official URL | https://aclanthology.org/2024.acl-long.761/ |
| Findings | Surface- and semantic-level overlap with training corpora is substantial; measured gaps shrink after decontamination. |
| Relationship | Our tasks come from public GitHub history — a direct contamination threat we must report, not eliminate. |

### 5.2 On Leakage of Code Generation Evaluation Datasets

| Field | Value |
|---|---|
| Authors | Alexandre Matton, Tom Sherborne, Dennis Aumiller, Elena Tommasone, Milad Alizadeh, Jingyi He, Raymond Ma, Maxime Voisin, Ellen Gilsenan-McMahon, Matthias Gallé |
| Year | 2024 |
| Venue | **[PR]** Findings of ACL: EMNLP 2024, pp. 13215–13223 — **Findings**, not main |
| DOI | 10.18653/v1/2024.findings-emnlp.772 |
| Official URL | https://aclanthology.org/2024.findings-emnlp.772/ |
| Data | LBPP: https://huggingface.co/datasets/CohereForAI/lbpp |
| Findings | Three leakage routes confirmed (direct, synthetic-data, checkpoint-selection); SOTA models perform up to 43% worse on the uncontaminated LBPP. |
| Relationship | Motivates recording contamination as a standing limitation and avoiding strong claims from a small dev-tuned set. |

---

## 6. Issue-conditioned test generation: the direct lineage

### 6.1 TDD-Bench Verified: Can LLMs Generate Tests for Issues Before They Get Resolved? (Auto-TDD)

| Field | Value |
|---|---|
| Authors | Toufique Ahmed, Martin Hirzel, Rangeet Pan, Avraham Shinnar, Saurabh Sinha |
| Year | 2024 |
| Venue | **[PRE]** arXiv:2412.02883 (2024-12-03) — **no venue field; peer-review status UNVERIFIED** |
| Official URL | https://arxiv.org/abs/2412.02883 |
| Code / data | https://github.com/IBM/TDD-Bench-Verified (Apache-2.0) |
| Problem / method / findings | The benchmark paper: 449 issues from 12 Python repositories filtered from SWE-bench Verified (500 → −7 unrecreatable environments → −44 non-fail-to-pass or zero-coverage → 449), human-judged and harness-verified. Auto-TDD is the LLM solution; the benchmark's own harness computes `tddScore = failToPass × adequacy`. |
| Relationship | **Our primary benchmark candidate.** Full adoption audit is in `docs/benchmark_setup.md`. |

### 6.2 LIBRO: Large Language Models are Few-Shot Testers — Exploring LLM-Based General Bug Reproduction

| Field | Value |
|---|---|
| Authors | Sungmin Kang, Juyeon Yoon, Shin Yoo |
| Year | 2023 |
| Venue | **[PR]** ICSE 2023 |
| DOI | 10.1109/ICSE48619.2023.00194 |
| Official URL | https://arxiv.org/abs/2209.11515 ; https://dlnext.acm.org/doi/10.1109/ICSE48619.2023.00194 |
| Problem | Turn informal bug reports into executable reproduction tests, and know *when* to trust them. |
| Method | Prompt Codex N times → post-process to make tests executable → cluster by failure output → rank with agreement-based heuristics. Defines FIB (fail-in-buggy) and distinguishes it from a true BRT. |
| Findings | 33.5% of Defects4J bugs (251/750) reproduced; top-1 correct for 149; success of the "did we reproduce it?" heuristic 71.4% accurate. On a post-training-cut-off set of 31 reports: 32.2%. |
| Limitations stated by authors | Checkstyle failed because its tests depend on **external files LIBRO could not access**; Jackson failures due to small sample. The paper explicitly studies contamination. |
| Relationship | The intellectual ancestor of issue→test generation. Its "external files we can't access" failure mode is *exactly the class of problem repository-aware fixture/config retrieval addresses* — useful motivating evidence, though it does not itself retrieve that context. |
| Remains differentiable | Java/Defects4J, pre-retrieval era, no repository-context ablation. |

### 6.3 e-Otter / e-Otter++: Heterogeneous Prompting and Execution Feedback for SWE Issue Test Generation and Selection

| Field | Value |
|---|---|
| Authors | Toufique Ahmed, Jatin Ganhotra, Avraham Shinnar, Martin Hirzel |
| Year | 2025/2026 |
| Venue | **[PR]** International Conference on Software Engineering (ICSE 2026); arXiv comment field states "Accepted to the International Conference on Software Engineering (ICSE-2026)" |
| DOI | 10.1145/3744916.3787837 |
| Official URL | https://arxiv.org/abs/2508.06365 (v3, 2026-01-22) |
| Problem | Execution feedback is normally unavailable because the correct code does not exist yet. |
| Method | **e-Otter**: starts from Otter's test, applies execution-augmented test repair using logs on the buggy code, and **heterogeneous prompting = 5 issue-description morphs × 5 prompt context masks = 25 candidates**. **e-Otter++**: adds a selector that runs refined tests against candidate *surrogate* code patches. |
| Main findings | Average fail-to-pass **63%** on TDD-Bench Verified. |
| Limitations stated by authors | Cost/selection complexity; reliance on surrogate patches. |
| Relationship to RepoTest-Bridge | **Second-closest overlap.** Its "prompt context masks" are a systematic variation of *what the generator sees* — so "varying context" is not novel in itself. |
| Remains differentiable | The masks vary **localizer/planner output subsets** to create *diverse candidates for ensembling*; they do **not** vary repository artifact classes, are **not** measured as an ablation of marginal value, and are **not** budget-normalized. This distinction must be stated explicitly in any related-work section. |

### 6.4 Issue2Test: Generating Reproducing Test Cases from Issue Reports

| Field | Value |
|---|---|
| Authors | Noor Nashid, Islem Bouzenia, Michael Pradel, Ali Mesbah |
| Year | 2025 |
| Venue | **[PRE]** arXiv:2503.16320 (v1 2025-03-20, v4 2026-01-05) — venue **UNVERIFIED** |
| Official URL | https://arxiv.org/abs/2503.16320 |
| Problem / method | Generate tests that fail *for the reason stated in the issue*. Hands the LLM a **structured directory tree** and lets the *LLM itself rank* relevant files (not an external retriever); project conventions including *fixtures* are obtained by **meta-prompting** — synthesized from the repository name — not retrieved. Iterative compilation/runtime feedback. |
| Findings | Reproduces 32.9% of issues on SWT-bench-lite (+16.3% relative over the prior best). |
| Relationship | Overlaps candidates A and C. **Confirms rather than closes the gap**: it *synthesizes* fixture knowledge instead of *retrieving* real fixture definitions, and performs no context-type ablation. |

### 6.5 EvoOtter: Evolutionary Reproduction Test Generator

| Field | Value |
|---|---|
| Authors | Toufique Ahmed, Jatin Ganhotra, Avraham Shinnar, Martin Hirzel |
| Year | 2026 |
| Venue | **[PRE]** arXiv:2607.02854 (2026-07-03) — venue **UNVERIFIED** |
| Official URL | https://arxiv.org/abs/2607.02854 |
| Method | Evolutionary programming over reproduction tests: successive halving to control execution cost, batched crossover (one LLM call per generation), rule-based mutations, BRT-specific fitness. |
| Findings | "State-of-the-art quality BRTs at a fraction of the cost of prior inference-scaling approaches." **The numeric F2P figure is UNVERIFIED** (not present in the abstract and the PDF was not read for this review); a secondary source reported 65.9% — treat as unconfirmed. |
| Relationship | Represents the inference-scaling axis. Our contribution is orthogonal (context selection, not sampling count). |

### 6.6 Reproduction Test Generation for Java SWE Issues

| Field | Value |
|---|---|
| Authors | Toufique Ahmed, Jatin Ganhotra, Avraham Shinnar, Martin Hirzel |
| Year | 2026 |
| Venue | **[PRE]** arXiv:2605.04320 (2026-05) — venue **UNVERIFIED** |
| Method / findings | Java port of the e-Otter++ line with heterogeneous prompting; reported F→P 43.6% (Claude-Sonnet-4.5) / 46.4% (GPT-5.2) on TDD-Bench-Java. |
| Relationship | Evidence that the lineage is expanding beyond Python; relevant if we later extend languages. **Numbers are secondary-sourced and were not re-derived.** |

---

## 7. Synthesis: what the literature establishes, and what it does not

**Established (do not re-claim):**

1. Issue-conditioned, fail-to-pass test generation as a task is well defined and benchmarked
   (SWT-Bench, TDD-Bench-Verified).
2. Retrieving *test-side* context matters, and can matter more than *code-side* context
   (SWT-Bench's ablation; iCoRe's `w/o Test` −33.3% on TDD-Bench).
3. Structure-aware retrieval beats pure similarity (iCoRe's function-call trees).
4. Prompt-level context variation is already used, for ensemble diversity (e-Otter++).
5. Coverage alone is an inadequate adequacy signal (MuTAP; Otter's own Table 5 shows
   non-F2P tests still at 60% coverage).
6. Contamination is a real, partly unquantified threat for this benchmark family.

**Not established (the surviving candidate gap):**

- No located work enumerates **fixtures, `conftest.py`, test helpers, project/CI config, and
  static source→test import edges** as *separate, ablatable context classes* for
  **issue-conditioned** test generation.
- No located work holds **model, context-token budget, and retry count constant** across
  those arms and reports **fail-to-pass delta per token**.
- Existing ablations are coarse (`w/o Test` / `w/o Code`), unnormalized, or are prompt
  masks used for diversity rather than measurement.

This gap is narrow and must be presented as an *empirical ablation contribution*, not as a
new system or a first-of-kind claim. See `docs/novelty_audit.md` for the formal
A–E decision record.

---

## Bibliography

Full BibTeX entries, including all URLs above, are in [`references.bib`](references.bib).

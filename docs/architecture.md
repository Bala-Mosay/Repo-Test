# RepoTest-Bridge — Architecture

## 1. Layout

```
src/repotest_bridge/
├── cli.py              Typer app: doctor|research|retrieve|generate|validate|evaluate|report
├── config.py           pydantic-settings; YAML + REPB_* env; validated on load
├── logging_setup.py    structured logging (text or JSON)
├── tasks.py            manifest models, GenerationView / EvaluationTask split, loaders
├── repository.py       read-only git + project inspection (framework, test dirs, hashing)
├── retrieval/
│   ├── types.py        ContextItem, ContextBundle, ArtifactCategory
│   ├── signals.py      BM25-ish lexical scoring, issue entity extraction (pure Python)
│   ├── extractors.py   the six artifact-class extractors
│   ├── budget.py       global + per-class token ceilings; no reallocation
│   └── strategies.py   registry: strategy name -> admitted categories
├── providers/
│   ├── base.py         Provider protocol, ChatMessage, ProviderError hierarchy
│   ├── openai_compat.py OpenAI-compatible adapter (httpx + tenacity backoff)
│   └── mock.py         deterministic offline provider for tests/demo
├── generation/
│   ├── prompts.py      versioned templates; single source of truth for prompt text
│   ├── generator.py    orchestration: context -> prompt -> provider -> parse
│   └── parsing.py      structured output -> TestPatchCandidate; path/diff validation
├── validation/
│   ├── patch.py        apply a diff to a checkout (temp), with timeouts
│   └── collect.py      syntax/AST-level pre-checks without executing repo code
├── evaluation/
│   ├── outcomes.py     the nine outcome codes + classification helpers
│   ├── runner.py       bounded subprocess execution
│   └── harness.py      paired buggy/fixed driver
├── metrics.py          primary/secondary/derived metric computation
├── reporting/          jsonl / csv / markdown writers
└── errors.py           typed exceptions surfaced as actionable CLI errors
```

## 2. Data flow

```
manifest ──► tasks.load_generation() ──► GenerationView        (gold unreachable)
                    │
                    ▼
            repository.inspect()  ──► ProjectInfo (test dirs, framework, files)
                    │
                    ▼
        retrieval.run(strategy)   ──► ContextBundle (items + scores + hashes + tokens)
                    │                     ▲ budget enforced here
                    ▼
        generation.generate()     ──► prompt (versioned) ─► Provider ─► raw text
                    │                                            │
                    ▼                                            ▼
        generation.parse()        ──► TestPatchCandidate (format/path/diff/scope checks)
                    │
                    ▼
        validation.apply()        ──► patch applies? (to a scratch checkout)
                    │
   ─ ─ ─ ─ ─ ─ ─  evaluation boundary (explicit `evaluate` only) ─ ─ ─ ─ ─
                    │
                    ▼
        evaluation.harness        ──► OutcomeCode (8-way) per version pair
                    │
                    ▼
        metrics + reporting       ──► tasks.jsonl, summary.csv, report.md
```

## 3. Key invariants

1. **Gold isolation by type.** `GenerationView` is a distinct model with an explicit
   field list. `tasks.py` never returns an object containing evaluation data to
   generation code. Enforced by `tests/unit/test_tasks.py::test_generation_view_has_no_gold_fields`.

2. **One branch point for strategies.** Everything downstream of `retrieval.run()` is
   identical across arms. A test asserts that all strategies produce bundles containing
   only their admitted categories.

3. **No reallocated budget.** `budget.py` truncates per class then globally; it never
   back-fills. Asserted by a unit test.

4. **Retrieval is offline.** `retrieval` imports nothing from `providers`. No embeddings,
   no network. `retrieve` runs without any API key.

5. **Execution is opt-in.** Only `evaluation/runner.py` launches repository code, and it
   is reachable only through the `evaluate` command. Unit tests never import it for
   execution paths.

6. **Provider is swappable by configuration only.** `OPENAI_BASE_URL` / `OPENAI_API_KEY` /
   `OPENAI_MODEL` select Groq, OpenRouter, or any compatible endpoint without touching
   source.

7. **Model output is untrusted.** All parsing happens in `generation/parsing.py`; the
   generator never assumes the model produced a valid diff.

## 4. Error handling

Typed exceptions in `errors.py` (`ConfigError`, `ManifestError`, `RepositoryError`,
`ProviderError`, `ParseError`, `ValidationError`, `ExecutionError`) are mapped by the CLI
to non-zero exit codes and actionable messages. Subprocess failures, timeouts, and
truncated output are *values*, not exceptions, wherever they are part of the measured
outcome space.

## 5. Extension points

| To add… | Do this |
|---|---|
| a new artifact class | add an extractor in `extractors.py`, a category in `types.py`, a budget entry in `configs/default.yaml`, and a `ctx_*` strategy |
| a new provider | implement `providers.base.Provider`, register it in `providers/__init__.py` |
| a new benchmark | add an adapter that emits the manifest schema; do not touch the harness |
| a new metric | add it in `metrics.py` with an explicit denominator |

## 6. Dependency posture

Runtime: `typer`, `rich`, `pydantic`, `pydantic-settings`, `PyYAML`, `httpx`, `tenacity`,
`python-dotenv`, `unidiff`. Dev: `pytest`, `pytest-cov`, `ruff`, `mypy`.

Deliberately **absent**: vector databases, embedding models, agent frameworks,
databases, web servers, and any framework that does not directly serve the experiment.

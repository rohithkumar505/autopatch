# AutoPatch

AutoPatch finds bugs in a code project, explains their root cause, writes tests that prove
them, generates a focused fix, and validates that fix in an isolated sandbox. Nothing changes in
the project until you accept the patch, and every version can be restored.

It works on any file. **Universal file intelligence** identifies every file by its content (file
signatures, encoding, structure), not by its extension, and checks it with a format-specific
inspector. Formats covered: JSON/JSONC, YAML, TOML, XML/SVG, INI, .env, CSV, Python, other source
code, PNG, JPEG, GIF, PDF, ZIP/Office/JAR, GZIP, TAR and SQLite. Each issue gets a repairability
status: fixable, partially fixable, requires human intervention, not fixable, or unsupported, with
the reason, options, limitations and confidence. Deep code analysis and repair are available for
Python; other languages get structure, encoding and secret checks, and each report says so.

**AutoPatch Core**, the built-in engine (no API key, nothing leaves the machine):
- Detects bugs with its own analyzers: input reaching `eval`/shell/SQL (taint tracking),
  division by empty or zero values, missing range checks, sorts that run the wrong way for what the
  function promises, off-by-one thresholds, shared mutable defaults, unguarded indexing, overwritten
  loop accumulators, modules that run `main()` on import, and syntax errors.
- Proves them by running the code in the sandbox with edge inputs and attack payloads.
- Repairs them with minimal fixes, falling back to a search over small mutations, all validated by tests.
- Claude and local models (Ollama) remain optional engines in Settings.

## Run it

Needs Python 3.12+, Node 20+ and Git.

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
npm --prefix frontend install
npm --prefix frontend run build
.venv\Scripts\python -m autopatch.main
```

Open http://127.0.0.1:8000. Then:

1. **Settings → Test sandbox → Set up sandbox** (one time, about 5 seconds).
2. **Settings → AI provider**: paste an Anthropic API key, or point AutoPatch at a local Ollama model.
3. **Projects**: import a folder, files, a zip, a local path or a Git URL. `samples/marks_calculator`
   has seven deliberately planted bugs; the list is in `samples/ground_truth/` (kept outside the
   sample so the scan cannot read it).

For frontend development, run `npm --prefix frontend run dev` (port 5173, proxies `/api` to 8000).

## Workflow

Upload → Scan → Detect → Root cause → Explain → Generate patch → Review → Run tests → Validate →
Accept or reject → History.

- **Scan**: Python's parser (syntax errors, structure, imports, call graph), Ruff, Pylint, then an AI
  review that confirms or refines the static findings and finds logic bugs the tools cannot see.
  Findings that several sources report are merged. Ranking = severity × confidence × impact, where
  impact comes from the call graph (how much code depends on it, whether any test covers it).
- **Fix**: the AI writes tests *from the bug report*, before any fix exists: `reproduce`, `normal`,
  `invalid`, `minimum`, `maximum` and `edge` cases. They run on the original code; a failing
  reproduce test **confirms** the bug. Then the AI writes a patch, which is applied to a copy, and
  every test runs again. If anything fails, the failures go back to the AI for another attempt.
- **Validated** means a reproduce test failed before the patch and passes after it, every
  generated test passes, and no test that passed before fails after. Anything less is shown as
  *partially validated* or *failed validation*. A failed patch can only be applied after an
  explicit confirmation.
- **History**: each project is a private Git repository in `data/projects/<id>/repo`. The upload is
  the first commit; each accepted patch or restore is a new commit. Versions can be browsed, diffed,
  downloaded, restored, and individual patches reverted.

## Security model

Uploaded code is never imported or run inside the AutoPatch server process. Static analysis reads
source text only. Ruff runs with `--isolated`, and Pylint runs with AutoPatch's own rcfile, so a
project's configuration cannot load plugins or run init hooks.

Tests run in the strongest sandbox available:

| Sandbox | Isolation |
|---|---|
| Docker (when installed) | `--network none`, read-only root, all capabilities dropped, unprivileged user, memory/CPU/PID limits |
| Windows AppContainer | No network (internet, DNS and localhost blocked). No file access except the run folder and AutoPatch's private Python copy. Job Object limits on memory, process count and CPU time. UI and clipboard restrictions. Killed on timeout |
| Local process | Job Object limits and a Python-level network block only. Off unless enabled in Settings |

The AppContainer setup proves its claims with a self-test that tries to read a file outside the
sandbox, write outside it, and open an internet connection. Setup fails if any of those succeed.
Each run uses a fresh throwaway copy of the project, and runs are serialized.

## Layout

```
backend/autopatch/
  analysis/   language analyzers (base interface + Python: AST, Ruff, Pylint), ranking
  ai/         providers (Anthropic, Ollama), schemas for structured outputs, prompts
  testing/    sandbox backends, Windows AppContainer launcher, private runtime, pytest runner
  pipeline/   scan, fix/validate, review and history workflows
  core/       git workspace, safe imports, background jobs, settings
  api/        FastAPI routes
frontend/src/ React + TypeScript + Tailwind UI (9 sections), Monaco editor and diff views
samples/      demo project with planted bugs
```

Adding a language means implementing `LanguageAnalyzer` (structure, static checks, tools) and a
test runner for it. The rest of the pipeline is language-neutral.

## Tests

```powershell
.venv\Scripts\python -m pytest
```

The suite runs real code in the real sandbox. Only the AI provider is replaced, by a scripted test
double that lives in `backend/tests/test_pipeline.py` and is never used by the application.

## Current limits

- Only Python is analyzed.
- Tests run with the standard library, pytest and hypothesis. A project's third-party dependencies
  (`requirements.txt`) are detected and shown, but not installed into the sandbox yet, so tests that
  import them fail there.
- The Docker and Ollama backends are implemented, but they have not been exercised on the
  development machine because neither is installed there.

# AutoPatch → AI IDE — Phase 1 Architecture

**Status:** Phase 1 complete · **Phase 2 IDE workspace shipped** (editable tabs, file CRUD, search, git status, Problems, real sandbox Run)  
**Date:** 2026-11-20  
**Constraint:** Preserve all existing AutoPatch capabilities; extend, do not replace.

### UX locked in this phase (product direction)

| Surface | Behavior |
|---------|----------|
| **App home** | **Dashboard** at `/` and `#/dashboard` (brand click → Dashboard) |
| **Open IDE** | Opens a **separate full-bleed workspace** (`#/ide`), not a padded tool page |
| **Left / center** | Main IDE: file explorer + Monaco (same as other IDEs) |
| **Right** | **Agent panel** with an explicit **show/hide switch** (Cursor-style). Chat/agents are Phase 3 — no mock replies |
| **Activity bar** | Dashboard → IDE → AutoPatch tools → Settings |

---

## 1. Repository architecture report

### Stack (keep)

| Layer | Technology | Notes |
|-------|------------|--------|
| Frontend | React 19 + Vite 8 + TypeScript + Tailwind 4 | Hash routing (`lib.tsx`); no React Router |
| Editor | `monaco-editor` + `@monaco-editor/react` | Local workers (`monaco.ts`); **read-only** today |
| Backend | FastAPI + Uvicorn | Serves `/api` + `frontend/dist` |
| DB | SQLite via SQLModel | WAL; light `ALTER` migrations in `db.py` |
| Jobs | In-process (`core/jobs.py`) | Polled by UI (`useJob`) |
| Git | Private per-project repos under `data/projects/<id>/repo` | Upload = first commit; accept/restore = new commits |
| AI | `builtin` (AutoPatch Core), Anthropic, Ollama, `none` | Selected in Settings |
| Execution | Sandbox: Docker / Windows AppContainer / local | **Test runs only** — not interactive IDE terminal/run yet |

### Layout

```
VINOD_PROJECT/
  backend/autopatch/
    api/           FastAPI routers: projects, bugs, system
    ai/            Anthropic + Ollama providers + Engine
    analysis/      LanguageAnalyzer (Python AST, Ruff, Pylint)
    core/          files, jobs, settings, workspace (git)
    core_engine/   Offline detect + repair (no API key)
    fileintel/     Universal file identify / inspect
    pipeline/      scan, fix, patching, history, projects, formatfix
    testing/       sandbox + pytest runner + private runtime
    db.py          SQLModel tables
    engines.py     builtin | anthropic | ollama | none
    main.py        App + static mount
  frontend/src/
    App.tsx        Shell: title / activity / status; IDE = full-bleed
    api.ts         Typed /api client
    lib.tsx        Hash route + AppProvider (default section: ide)
    components/ui  Design system + CodeView / DiffView / JobLog
    ide/           IdeWorkspace (Phase 1 shell)
    pages/         AutoPatch tool pages (unchanged roles)
  data/            DB, projects, sandbox, uploads, runtime
  samples/         Demo projects with planted bugs
  docs/            This architecture doc
```

### Runtime model

1. Import project → copy into private git `Workspace` (original commit).  
2. Scan / analyze / fix run as **jobs**; patches wait for human accept.  
3. Accept commits into workspace; restore rewinds git.  
4. Uploaded code is **never imported** into the AutoPatch server process; tests run only in the sandbox.

---

## 2. Existing functionality inventory

### Frontend sections

| Section id | Page | Capability |
|------------|------|------------|
| `ide` | `ide/IdeWorkspace.tsx` | Full-bleed IDE: tree + read-only Monaco + outline/deps/issues + **right Agent switch** |
| `projects` | Projects | Import folder/files/zip/path/git; rename/delete/download; **Open IDE** |
| `scanner` | Scanner | Start scan, job log, history, findings table |
| `analysis` | Analysis | Root cause, dismiss/reopen, trigger fix |
| `autofix` | AutoFix | Diff review, accept/reject/revalidate/revert |
| `tests` | TestCenter | Sandbox pytest runs |
| `files` | FileReports | Per-file intelligence / repairability |
| `history` | HistoryPage | Versions, diffs, restore, events |
| `dashboard` | Dashboard | Aggregate stats |
| `settings` | SettingsPage | AI, analysis toggles, limits, sandbox prepare/self-test |

Legacy `#/explorer` normalizes to `#/ide`.

### Backend APIs (preserve all)

**Projects** (`/api/projects…`): list/get/patch/delete, upload, upload-zip, import-path, clone, tree, file, structure, download, scans, file-reports  

**Bugs** (`/api…`): start scan, findings, analyze, fix, dismiss/reopen, patches accept/reject/revalidate/revert, test-runs  

**System** (`/api…`): health, status, dashboard, versions/diff/restore, events, jobs, settings, test-ai, sandbox prepare/self-test, languages  

### Database tables

`Project`, `Scan`, `Finding`, `FileReport`, `Patch`, `TestRun` (`testrun`), `Event`, `Job`, `Setting`

### Authentication

**None.** Single-user local app bound to `127.0.0.1`. API keys live in Settings (masked in public API). No login, JWT, OAuth, or multi-tenant isolation.

### AI integrations

| Provider | Role |
|----------|------|
| **AutoPatch Core** (`builtin`) | Local detectors, probes, codegen, mutation search — default |
| **Anthropic** | Structured generate via streaming Messages API |
| **Ollama** | Local OpenAI-style chat + JSON schema validation |
| **none** | Deep analysis off |

### Detection & repair (do not remove)

**Detect:** fileintel → Python AST structure → Ruff/Pylint → CoreEngine or AI review → merge/rank → Findings + FileReports  

**Repair:** root cause → write tests → run on original (confirm) → write patch → sandbox revalidate → bounded retries → Patch awaiting review → accept commits via Workspace  

### Storage

| Path | Purpose |
|------|---------|
| `data/autopatch.db` | Metadata |
| `data/projects/<id>/repo` | Private git workspace |
| `data/uploads` | Staging |
| `data/sandbox` | Throwaway test copies |
| `data/runtime` | Private Python for sandbox |

### Design system reuse

`Button`, `Card`, `PageHeader`, badges, `Tabs`, `Modal`, `JobLog`, `CodeView`, `DiffView`, theme tokens in `index.css`, activity-bar shell in `App.tsx`.

---

## 3. Missing functionality report (for Cursor-like IDE)

| Capability | Today | Gap |
|------------|-------|-----|
| Separate IDE surface | **Phase 1: full-bleed `#/ide`** | Done for shell |
| Agent on the **right** with switch | **Phase 1: panel + toggle** | Chat / streaming / tools = Phase 3 |
| Writable editing | Monaco `readOnly` | Edit + save + dirty state |
| File mutations | Read tree/file only | Create / rename / delete / mkdir APIs + UI |
| Multi-tab editor | Single path state | Tab models, close/reorder |
| Integrated terminal | JobLog / test `<pre>` | PTY + WebSocket + xterm (new dep when Phase 4 ships) |
| App run / debug | Sandbox pytest only | Run configs, stdout, DAP-lite later |
| AI coding assistant | Form jobs (analyze/fix) | Streaming chat + apply edits in Agent panel |
| Multi-agent orchestration | Single engine job | Agent roles + shared workspace tools |
| Live git UX | History page | IDE git pane on same Workspace |
| FS watch | Manual reload | Optional watch / poll |
| Auth / multi-user | Absent | Optional later; not required for local IDE v1 |

---

## 4. Proposed IDE architecture

### Principle

**Opening IDE opens a dedicated workspace.** AutoPatch tools stay as sibling activity-bar destinations (and as Agent quick links). Agent lives on the **right**, toggled — not on the left.

```
┌──────────────────────────────────────────────────────────────────┐
│ App title bar: brand · project · AI/sandbox chips · theme        │
├────┬────────────┬─────────────────────────────┬──────────────────┤
│ A  │ Explorer   │ Editor (Monaco)             │ Agent (switch)   │
│ c  │ file tree  │ path bar · read-only today  │ Phase 3: chat    │
│ t  │            │ Outline / Deps / Issues     │ + tool actions   │
│ i  │            │ (bottom of center column)   │                  │
│ v  ├────────────┴─────────────────────────────┴──────────────────┤
│ i  │ Later: Terminal | Problems | Run (bottom panel)             │
│ t  │                                                             │
│ y  │ Icons: IDE · Projects · Scan · Analysis · AutoFix · …       │
└────┴─────────────────────────────────────────────────────────────┘
```

### Backend extensions (additive — later phases)

| Area | New / extended | Reuses |
|------|----------------|--------|
| Files | `PUT/POST/DELETE` file & folder under Workspace | `Workspace`, `safe_relpath`, git commit helpers |
| Terminal | WebSocket PTY scoped to project root | Sandbox policy inspiration; **not** identical to test sandbox |
| Run | `POST …/run` with command allowlist | Sandbox or limited local process |
| AI chat | `POST …/chat` (+ stream) with tools: read/edit/scan/fix | `get_engine`, existing pipelines |
| Agents | Job kinds: plan / implement / verify | `jobs.submit`, Core + optional LLM |
| Debug | Phase 6+ DAP adapter | — |

### Frontend modules

```
frontend/src/
  ide/
    IdeWorkspace.tsx     # Phase 1: full-bleed layout + Agent switch
    EditorTabs.tsx       # Phase 2
    FileTree.tsx         # Phase 2 extract
    TerminalPanel.tsx    # Phase 4
    AiChatPanel.tsx      # Phase 3 (replaces Agent placeholder)
  pages/                 # keep all AutoPatch tool pages
```

Monaco: keep `CodeView` read-only for Analysis snippets; only the IDE editor becomes writable in Phase 2.

---

## 5. Frontend & backend integration plan

1. **Phase 1 (done):** Analysis docs; `#/ide` default; full-bleed IDE shell; left tree+editor; **right Agent switch**; Projects **Open IDE**; no new APIs/deps.  
2. **Phase 2:** Tabs; `PUT` file save; create/rename/delete; draft git commits for manual edits.  
3. **Phase 3:** Wire Agent panel chat to existing engines + edit/scan/fix tools; Problems from Findings.  
4. **Phase 4:** Terminal (xterm + PTY); Run configurations.  
5. **Phase 5:** Multi-agent orchestration jobs; autonomous detect→fix→validate from IDE.  
6. **Phase 6:** Debug UI; richer git pane; polish; optional auth if hosted.

**API compatibility:** Keep every existing route. New routes under `/api/projects/{id}/…` or `/api/ide/…`. `api.ts` only grows.

**No stack swap:** FastAPI, React, Monaco, SQLite, hash routing. PTY needs WebSocket — Uvicorn already supports.

---

## 6. Database changes required

### Phase 1

**None.**

### Later (additive)

| Change | Phase | Purpose |
|--------|-------|---------|
| Open tabs / layout | 2 | Prefer `localStorage` first; table only if sync needed |
| `ChatThread`, `ChatMessage` | 3 | Persist Agent history |
| `AgentRun` (+ Job link) | 5 | Multi-agent audit trail |
| `TerminalSession` metadata | 4 | Optional audit; PTY ephemeral |
| Manual edit Events | 2 | `Event.kind=ide_edit` on Workspace commits |

Prefer **Events + Jobs** before new tables when possible.

---

## 7. Security considerations

| Risk | Mitigation |
|------|------------|
| Path traversal on write/terminal | Continue `safe_relpath`; reject `..` and absolute paths |
| Interactive terminal escape | Project-root cwd; optional allowlist; never default unrestricted host shell |
| Running untrusted project code | Keep strong sandbox for AutoPatch tests; IDE Run must show isolation level |
| AI applying edits | Diff preview / accept gate (same as Patch accept) for autonomous fixes |
| API open on LAN | No auth — bind `127.0.0.1` (current); document risk if exposed |
| Secrets in Settings | Keep masking; never log API keys |
| Supply chain | Minimize new deps; add xterm/PTY only when Terminal ships |

**Do not weaken:** never `import` user project into the AutoPatch process.

---

## 8. Implementation sequence (all phases)

| Phase | Goal | Ships |
|-------|------|-------|
| **1** | Analysis + dedicated IDE shell | This doc; `#/ide` full-bleed; left IDE; right Agent switch; Open IDE; read-only editor |
| **2** | Real editing | Tabs, file CRUD APIs, save, git commit policy |
| **3** | AI Agent in IDE | Chat UI in right panel, streaming, tools → read/edit/scan/fix |
| **4** | Terminal + Run | PTY WebSocket, xterm, run configs |
| **5** | Agents + autonomy | Multi-agent jobs from IDE |
| **6** | Debug + polish | DAP-lite, git pane, optional auth |

### Phase 1 explicit non-goals

- No writable Monaco  
- No terminal / debug / fake agent chat  
- No DB migrations  
- No new npm/pip dependencies  
- No deletion of Dashboard or AutoPatch tool pages  

---

## Reusable vs build

**Reuse heavily:** App shell, activity bar, theme, `api.ts`, Workspace git, scan/fix pipelines, CoreEngine, sandbox tests, CodeView decorations, Job polling, Outline/Deps/Issues data.  

**Build new (later):** write APIs, tabs, chat, terminal, multi-agent UI.  

**Phase 1 adapted:** Explorer → `IdeWorkspace` (full-bleed; Agent on right; intelligence tabs under editor).

---

## Phase 1 code changes (this phase only)

| File | Change |
|------|--------|
| `docs/PHASE1_IDE_ARCHITECTURE.md` | Full deliverables 1–8 + UX lock |
| `frontend/src/ide/IdeWorkspace.tsx` | New full-bleed IDE + Agent switch |
| `frontend/src/pages/Explorer.tsx` | Re-exports IdeWorkspace |
| `frontend/src/App.tsx` | IDE section renders without padded tool layout |
| `frontend/src/pages/Projects.tsx` | Button label **Open IDE** |
| `frontend/src/lib.tsx` | Already: default/`explorer` → `ide` |
| `frontend/src/components/ui.tsx` | `CodeView` fills height when `height="100%"` |

No backend or database changes in Phase 1.

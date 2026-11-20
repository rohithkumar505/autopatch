import {
  Bug, GitBranch, Play, RefreshCw, Search, Sparkles, Wrench,
} from "lucide-react";
import { useState } from "react";
import { api, type IdeProblem, type PatchSummary, type SearchHit } from "../api";
import { Button, Empty, ErrorNote, SeverityBadge, Spinner, inputCls } from "../components/ui";
import { navigate, useApp, useLoad } from "../lib";
import { ExplorerPanel } from "./ExplorerPanel";
import type { LeftView } from "./state";

export function SidePanelBody({
  view, projectId, selected, onOpenFile, onTreeChanged, onPathMoved,
}: {
  view: LeftView;
  projectId: number;
  selected: string;
  onOpenFile: (path: string, line?: number) => void;
  onTreeChanged: () => void;
  onPathMoved?: (from: string, to: string) => void;
}) {
  switch (view) {
    case "explorer":
      return (
        <ExplorerPanel
          projectId={projectId}
          selected={selected}
          onOpenFile={onOpenFile}
          onTreeChanged={onTreeChanged}
          onPathMoved={onPathMoved}
        />
      );
    case "search":
      return <SearchPanel projectId={projectId} onOpenFile={onOpenFile} />;
    case "git":
      return <GitPanel projectId={projectId} onOpenFile={onOpenFile} />;
    case "run":
      return <RunPanel projectId={projectId} />;
    case "extensions":
      return <ExtensionsPanel />;
    case "agents":
      return <AgentsPanel />;
    case "autopatch":
      return <AutoPatchSidePanel projectId={projectId} onOpenFile={onOpenFile} />;
    default:
      return null;
  }
}

function SearchPanel({ projectId, onOpenFile }: { projectId: number; onOpenFile: (p: string, line?: number) => void }) {
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [truncated, setTruncated] = useState(false);

  const search = async () => {
    if (!q.trim()) return;
    setBusy(true);
    setError("");
    try {
      const res = await api.search(projectId, q.trim());
      setHits(res.hits);
      setTruncated(res.truncated);
    } catch (e) {
      setError((e as Error).message);
      setHits([]);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-border px-2 py-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted">Search</div>
      <div className="flex gap-1 border-b border-border p-2">
        <input className={inputCls} placeholder="Search in project…" value={q}
          onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && search()} />
        <Button kind="primary" icon={<Search className="size-3.5" />} onClick={search} disabled={busy}>Go</Button>
      </div>
      <div className="min-h-0 flex-1 overflow-auto p-2">
        {error && <ErrorNote>{error}</ErrorNote>}
        {busy && <Spinner />}
        {!busy && !hits.length && !error && <p className="text-[12px] text-muted">Searches real project files via the AutoPatch API.</p>}
        {truncated && <p className="mb-2 text-[11px] text-amber-600">Results truncated.</p>}
        <ul className="space-y-1">
          {hits.map((h, i) => (
            <li key={`${h.path}:${h.line}:${i}`}>
              <button className="w-full rounded px-1.5 py-1 text-left hover:bg-hover" onClick={() => onOpenFile(h.path, h.line || undefined)}>
                <div className="truncate font-mono text-[12px] text-accent">{h.path}{h.line ? `:${h.line}` : ""}</div>
                <div className="truncate text-[12px] text-muted">{h.preview}</div>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

function GitPanel({ projectId, onOpenFile }: { projectId: number; onOpenFile: (p: string) => void }) {
  const status = useLoad(() => api.gitStatus(projectId), [projectId]);
  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center border-b border-border px-2 py-1.5">
        <span className="flex-1 text-[11px] font-semibold uppercase tracking-wide text-muted">Source Control</span>
        <button className="rounded p-1 text-muted hover:bg-hover" onClick={() => status.reload()}><RefreshCw className="size-3.5" /></button>
      </div>
      <div className="min-h-0 flex-1 overflow-auto p-2 text-[13px]">
        {status.error ? <ErrorNote>{status.error}</ErrorNote> : !status.data ? <Spinner /> : (
          <>
            <div className="mb-3 flex items-center gap-1.5 text-muted">
              <GitBranch className="size-3.5" />
              <span className="font-mono text-[12px]">{status.data.head.slice(0, 8)}</span>
            </div>
            <div className="mb-1 font-medium">Working tree</div>
            {status.data.changes.length === 0 ? (
              <p className="mb-4 text-[12px] text-muted">No uncommitted changes.</p>
            ) : (
              <ul className="mb-4 space-y-0.5">
                {status.data.changes.map((c) => (
                  <li key={c.path}>
                    <button className="flex w-full gap-2 rounded px-1 py-0.5 text-left hover:bg-hover" onClick={() => onOpenFile(c.path)}>
                      <span className="font-mono text-[11px] text-amber-600">{c.status}</span>
                      <span className="truncate font-mono text-[12px]">{c.path}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
            <div className="mb-1 font-medium">History</div>
            <ul className="space-y-1">
              {status.data.versions.map((v) => (
                <li key={v.sha} className="rounded bg-panel2 px-2 py-1 ring-1 ring-border">
                  <div className="font-mono text-[11px] text-muted">{v.short}</div>
                  <div className="truncate text-[12px]">{v.message}</div>
                </li>
              ))}
            </ul>
            <Button className="mt-3" onClick={() => navigate("history")}>Open Change History</Button>
          </>
        )}
      </div>
    </div>
  );
}

function RunPanel({ projectId }: { projectId: number }) {
  const { toast, status } = useApp();
  const [jobId, setJobId] = useState<string | null>(null);
  const runs = useLoad(() => api.testRuns(projectId), [projectId]);

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-border px-2 py-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted">Run and Debug</div>
      <div className="space-y-3 overflow-auto p-3 text-[13px]">
        <p className="text-muted">Runs use AutoPatch’s real sandbox (same as Test Center). Interactive debugger arrives in a later phase — no fake DAP session.</p>
        {!status?.sandbox.ready && <ErrorNote>Sandbox not ready. Prepare it in Settings.</ErrorNote>}
        <Button kind="primary" icon={<Play className="size-3.5" />} onClick={async () => {
          try {
            const r = await api.runTests(projectId);
            setJobId(r.job_id);
            toast("success", "Test run started");
            runs.reload();
          } catch (e) {
            toast("error", (e as Error).message);
          }
        }} disabled={!status?.sandbox.ready}>
          Run project tests
        </Button>
        {jobId && <p className="font-mono text-[11px] text-muted">Job {jobId}</p>}
        <div className="font-medium">Recent runs</div>
        {!runs.data ? <Spinner /> : runs.data.length === 0 ? <Empty title="No runs yet" /> : (
          <ul className="space-y-1">
            {runs.data.slice(0, 8).map((r) => (
              <li key={r.id} className="rounded bg-panel2 px-2 py-1 ring-1 ring-border">
                <div className="flex justify-between gap-2">
                  <span className="font-medium">{r.status}</span>
                  <span className="text-[11px] text-muted">{r.passed}✓ {r.failed}✗</span>
                </div>
                <div className="text-[11px] text-muted">{new Date(r.created_at).toLocaleString()} · {r.sandbox}</div>
              </li>
            ))}
          </ul>
        )}
        <Button onClick={() => navigate("tests")}>Open Test Center</Button>
        <div className="rounded border border-border bg-panel2 p-2 text-[12px] text-muted">
          <strong className="text-fg">Debug</strong> — breakpoints / step-through need a DAP adapter (Phase 6). Use Run tests + Problems for now.
        </div>
      </div>
    </div>
  );
}

function ExtensionsPanel() {
  const langs = useLoad(() => api.languages(), []);
  const sandbox = useLoad(() => api.sandbox(), []);
  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-border px-2 py-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted">Extensions</div>
      <div className="min-h-0 flex-1 space-y-3 overflow-auto p-3 text-[13px]">
        <p className="text-muted">Built-in analyzers and sandboxes from AutoPatch — not a marketplace mock.</p>
        <div className="font-medium">Languages</div>
        {!langs.data ? <Spinner /> : langs.data.map((l) => (
          <div key={l.language} className="rounded bg-panel2 p-2 ring-1 ring-border">
            <div className="font-semibold">{l.language}</div>
            <div className="text-[12px] text-muted">Tests: {l.test_framework || "—"} · {l.extensions.join(", ")}</div>
            <ul className="mt-1 text-[12px]">
              {l.tools.map((t) => (
                <li key={t.name} className={t.available ? "text-emerald-600 dark:text-emerald-400" : "text-muted"}>
                  {t.name} {t.available ? `(${t.version || "ok"})` : "(unavailable)"} — {t.purpose}
                </li>
              ))}
            </ul>
          </div>
        ))}
        <div className="font-medium">Sandbox backends</div>
        {!sandbox.data ? <Spinner /> : sandbox.data.backends.map((b) => (
          <div key={b.name} className="rounded bg-panel2 p-2 ring-1 ring-border">
            <div className="font-semibold">{b.label}</div>
            <div className="text-[12px] text-muted">{b.ready ? "Ready" : b.available ? "Needs prepare" : "Unavailable"} · {b.isolation}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

function AgentsPanel() {
  const { status } = useApp();
  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-border px-2 py-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted">AI Agents</div>
      <div className="space-y-3 overflow-auto p-3 text-[13px]">
        <p className="text-muted">Agent orchestration (plan / implement / verify jobs) ships in Phase 5. Today this panel shows the real configured engine.</p>
        <div className="rounded bg-panel2 p-3 ring-1 ring-border">
          <div className="flex items-center gap-2 font-semibold"><Sparkles className="size-4 text-accent" />Active engine</div>
          <div className="mt-1 text-[12px]">
            {status?.ai.configured
              ? `${status.ai.provider}${status.ai.model ? ` · ${status.ai.model}` : ""}`
              : "Not configured"}
          </div>
          <p className="mt-2 text-[12px] text-muted">{status?.ai.message}</p>
        </div>
        <Button kind="primary" onClick={() => navigate("settings")}>Configure AI</Button>
        <Button onClick={() => navigate("analysis")}>Open AI Analysis</Button>
        <Button onClick={() => navigate("autofix")}>Open AutoFix</Button>
      </div>
    </div>
  );
}

function AutoPatchSidePanel({ projectId, onOpenFile }: { projectId: number; onOpenFile: (p: string, line?: number) => void }) {
  const { toast, status } = useApp();
  const patches = useLoad(() => api.patches(projectId), [projectId]);
  const problems = useLoad(() => api.problems(projectId), [projectId]);

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-border px-2 py-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted">AutoPatch</div>
      <div className="min-h-0 flex-1 space-y-3 overflow-auto p-3 text-[13px]">
        <Button kind="primary" icon={<Bug className="size-3.5" />} onClick={async () => {
          try {
            await api.startScan(projectId, { ruff: true, pylint: true, ai: true });
            toast("success", "Scan started");
            problems.reload();
            patches.reload();
          } catch (e) {
            toast("error", (e as Error).message);
          }
        }}>
          Start scan
        </Button>
        <Button icon={<Wrench className="size-3.5" />} onClick={() => navigate("scanner")}>Bug Scanner</Button>
        <div className="font-medium">Open problems ({problems.data?.problems.length ?? "…"})</div>
        {!problems.data ? <Spinner /> : problems.data.problems.slice(0, 12).map((p: IdeProblem) => (
          <button key={p.id} className="flex w-full flex-col rounded bg-panel2 px-2 py-1.5 text-left ring-1 ring-border hover:bg-hover"
            onClick={() => onOpenFile(p.file, p.line)}>
            <div className="flex items-center gap-2"><SeverityBadge severity={p.severity} /><span className="truncate font-medium">{p.title}</span></div>
            <span className="font-mono text-[11px] text-muted">{p.file}:{p.line}</span>
          </button>
        ))}
        <div className="font-medium">Pending patches ({patches.data?.filter((p: PatchSummary) => p.status === "ready").length ?? "…"})</div>
        <Button onClick={() => navigate("autofix")}>Review in AutoFix</Button>
        {!status?.ai.configured && <ErrorNote>AI not configured — Core engine may still run if selected in Settings.</ErrorNote>}
      </div>
    </div>
  );
}

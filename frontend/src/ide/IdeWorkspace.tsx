/**
 * Phase 2 professional IDE workspace — Cursor / VS Code style.
 * Wired to real AutoPatch APIs (files, search, git, findings, tests, jobs).
 * No fake terminal output, fake AI chat, or fabricated file trees.
 */
import {
  AlignLeft, Bot, Bug, Columns2, Command, FlaskConical, FolderTree, GitBranch, LayoutDashboard, PanelBottom,
  PanelRight, Play, Puzzle, Save, Search, Settings as SettingsIcon, Sparkles, Terminal as TerminalIcon,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type FileContent, type Job } from "../api";
import { Button, Empty, ErrorNote, JobLog, NeedProject, SeverityBadge, Spinner, cx, inputCls } from "../components/ui";
import { navigate, useApp, useJob, useLoad, useRoute } from "../lib";
import { EditableEditor, type EditorMarker } from "./EditableEditor";
import { SidePanelBody } from "./SidePanels";
import { LEFT_VIEWS, loadLayout, saveLayout, type BottomTab, type IdeLayoutState, type LeftView } from "./state";

const LEFT_ICONS: Record<LeftView, typeof FolderTree> = {
  explorer: FolderTree,
  search: Search,
  git: GitBranch,
  run: Play,
  extensions: Puzzle,
  agents: Sparkles,
  autopatch: Bug,
};

export default function IdeWorkspace() {
  return (
    <NeedProject>
      <div className="flex h-full min-h-0 flex-col bg-bg">
        <IdeInner />
      </div>
    </NeedProject>
  );
}

function IdeInner() {
  const { active, projects, setActive, status, theme, setTheme, toast } = useApp();
  const route = useRoute();
  const pid = active!.id;

  const [layout, setLayout] = useState<IdeLayoutState>(() => loadLayout(pid));
  const [buffers, setBuffers] = useState<Record<string, { original: string; draft: string; language: string; findings: FileContent["findings"] }>>({});
  const [previews, setPreviews] = useState<Record<string, { kind: "image" | "binary" | "folder"; mime?: string; image_base64?: string; size: number }>>({});
  const [loadingPath, setLoadingPath] = useState("");
  const [fileError, setFileError] = useState("");
  const [reveal, setReveal] = useState<{ path: string; line: number } | null>(null);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [globalQ, setGlobalQ] = useState("");
  const [treeEpoch, setTreeEpoch] = useState(0);
  const [testJobId, setTestJobId] = useState<string | null>(null);
  const [scanJobId, setScanJobId] = useState<string | null>(null);
  const [outputLines, setOutputLines] = useState<string[]>([]);
  const drag = useRef<"left" | "right" | "bottom" | null>(null);

  const problems = useLoad(() => api.problems(pid), [pid, treeEpoch]);
  const jobs = useLoad(() => api.activeJobs(pid), [pid]);
  const testRuns = useLoad(() => api.testRuns(pid), [pid]);
  const events = useLoad(() => api.events(pid), [pid]);

  useEffect(() => {
    setLayout(loadLayout(pid));
    setBuffers({});
  }, [pid]);

  useEffect(() => {
    saveLayout(pid, layout);
  }, [pid, layout]);

  const patchLayout = useCallback((partial: Partial<IdeLayoutState>) => {
    setLayout((prev) => ({ ...prev, ...partial }));
  }, []);

  const openFile = useCallback(async (path: string, line?: number) => {
    if (!path) return;
    setFileError("");
    if (line) setReveal({ path, line });
    setLayout((prev) => {
      const tabs = prev.tabs.includes(path) ? prev.tabs : [...prev.tabs, path];
      return { ...prev, tabs, activePath: path };
    });
    if (buffers[path] || previews[path]) return;
    setLoadingPath(path);
    try {
      const st = await api.fsStat(pid, path);
      if (st.type === "dir") {
        setPreviews((p) => ({ ...p, [path]: { kind: "folder", size: st.children ?? 0 } }));
        return;
      }
      if (st.preview === "image" && st.image_base64) {
        setPreviews((p) => ({ ...p, [path]: { kind: "image", mime: st.mime, image_base64: st.image_base64, size: st.size } }));
        return;
      }
      if (st.binary || st.preview === "binary") {
        setPreviews((p) => ({ ...p, [path]: { kind: "binary", mime: st.mime, size: st.size } }));
        return;
      }
      const f = await api.file(pid, path);
      setBuffers((b) => ({
        ...b,
        [path]: { original: f.content, draft: f.content, language: f.monaco_language || st.monaco_language || "plaintext", findings: f.findings },
      }));
    } catch (e) {
      setFileError((e as Error).message);
    } finally {
      setLoadingPath("");
    }
  }, [pid, buffers, previews]);

  const onPathMoved = useCallback((from: string, to: string) => {
    setBuffers((b) => {
      const next = { ...b };
      if (next[from]) {
        next[to] = next[from];
        delete next[from];
      }
      // Remap nested paths under a moved folder.
      for (const key of Object.keys(next)) {
        if (key.startsWith(from + "/")) {
          const nk = to + key.slice(from.length);
          next[nk] = next[key];
          delete next[key];
        }
      }
      return next;
    });
    setPreviews((p) => {
      const next = { ...p };
      if (next[from]) {
        next[to] = next[from];
        delete next[from];
      }
      return next;
    });
    setLayout((prev) => ({
      ...prev,
      tabs: prev.tabs.map((t) => (t === from ? to : t.startsWith(from + "/") ? to + t.slice(from.length) : t)),
      activePath: prev.activePath === from ? to : prev.activePath.startsWith(from + "/") ? to + prev.activePath.slice(from.length) : prev.activePath,
      splitPath: prev.splitPath === from ? to : prev.splitPath.startsWith(from + "/") ? to + prev.splitPath.slice(from.length) : prev.splitPath,
    }));
  }, []);

  // Deep link ?file=&line=
  useEffect(() => {
    const f = route.query.get("file");
    if (f) openFile(f, Number(route.query.get("line") || 0) || undefined);
  }, [route.query, pid]); // eslint-disable-line react-hooks/exhaustive-deps

  // Open first file if none
  useEffect(() => {
    if (layout.activePath || layout.tabs.length) return;
    api.fsTree(pid).then((t) => {
      const first = firstFile(t.root);
      if (first) openFile(first);
    }).catch(() => undefined);
  }, [pid]); // eslint-disable-line react-hooks/exhaustive-deps

  const dirty = useMemo(() => {
    const d: Record<string, boolean> = {};
    for (const [p, b] of Object.entries(buffers)) d[p] = b.draft !== b.original;
    return d;
  }, [buffers]);

  const savePath = useCallback(async (path: string, quiet = false) => {
    const buf = buffers[path];
    if (!path || !buf || buf.draft === buf.original) return;
    try {
      // Quiet auto-save writes the workspace file; manual save also creates a git commit.
      await api.saveFile(pid, path, buf.draft, !quiet, `IDE: update ${path}`);
      setBuffers((b) => ({ ...b, [path]: { ...b[path], original: b[path].draft } }));
      if (!quiet) toast("success", `Saved ${path}`);
      setTreeEpoch((n) => n + 1);
    } catch (e) {
      toast("error", (e as Error).message);
    }
  }, [buffers, pid, toast]);

  const saveActive = useCallback(async () => {
    await savePath(layout.activePath);
  }, [layout.activePath, savePath]);

  const saveAll = useCallback(async () => {
    for (const [path, buf] of Object.entries(buffers)) {
      if (buf.draft !== buf.original) await savePath(path, true);
    }
    toast("success", "All files saved");
  }, [buffers, savePath, toast]);

  const formatActive = useCallback(async () => {
    const path = layout.activePath;
    const buf = buffers[path];
    if (!path || !buf) return;
    try {
      const res = await api.formatFile(pid, path, buf.draft, false);
      setBuffers((b) => ({
        ...b,
        [path]: { ...b[path], draft: res.content, language: res.monaco_language || b[path].language },
      }));
      toast("success", res.changed ? `Formatted with ${res.tool}` : "Already formatted");
    } catch (e) {
      toast("error", (e as Error).message);
    }
  }, [layout.activePath, buffers, pid, toast]);

  const goToDefinition = useCallback(async (line: number, column: number, word: string) => {
    const path = layout.activePath;
    if (!path) return;
    try {
      const res = await api.gotoDefinition(pid, path, line, column, word);
      const def = res.definitions[0];
      if (!def) {
        toast("info", res.word ? `No definition for “${res.word}”` : "No symbol under cursor");
        return;
      }
      await openFile(def.file, def.line);
      toast("success", `${def.qualname} → ${def.file}:${def.line}`);
    } catch (e) {
      toast("error", (e as Error).message);
    }
  }, [layout.activePath, pid, toast, openFile]);

  /** Apply disk/AI/AutoPatch content into the open editor (real workspace file). */
  const applyEditorContent = useCallback((path: string, content: string, language?: string, original?: string) => {
    setPreviews((p) => {
      const next = { ...p };
      delete next[path];
      return next;
    });
    setBuffers((b) => {
      const prev = b[path];
      if (prev && prev.draft !== prev.original) {
        if (!confirm(`${path} has unsaved edits. Replace with updated content?`)) return b;
      }
      const base = original ?? content;
      return {
        ...b,
        [path]: {
          original: base,
          draft: content,
          language: language || prev?.language || "plaintext",
          findings: prev?.findings ?? [],
        },
      };
    });
    setLayout((prev) => ({
      ...prev,
      tabs: prev.tabs.includes(path) ? prev.tabs : [...prev.tabs, path],
      activePath: path,
    }));
  }, []);

  const reloadFromWorkspace = useCallback(async (path: string) => {
    const f = await api.file(pid, path);
    if (f.binary) return;
    applyEditorContent(path, f.content, f.monaco_language);
  }, [pid, applyEditorContent]);

  const closeTab = (path: string) => {
    if (dirty[path] && !confirm(`${path} has unsaved changes. Close anyway?`)) return;
    setLayout((prev) => {
      const tabs = prev.tabs.filter((t) => t !== path);
      const activePath = prev.activePath === path ? (tabs[tabs.length - 1] || "") : prev.activePath;
      const splitPath = prev.splitPath === path ? "" : prev.splitPath;
      return { ...prev, tabs, activePath, splitPath };
    });
    setBuffers((b) => {
      const next = { ...b };
      delete next[path];
      return next;
    });
    setPreviews((p) => {
      const next = { ...p };
      delete next[path];
      return next;
    });
  };

  const setLeftView = (view: LeftView) => {
    setLayout((prev) => ({
      ...prev,
      leftView: view,
      leftOpen: prev.leftView === view ? !prev.leftOpen : true,
    }));
  };

  // Auto-save dirty buffers to the real workspace
  useEffect(() => {
    if (!layout.autoSave) return;
    const timer = window.setTimeout(() => {
      for (const [path, buf] of Object.entries(buffers)) {
        if (buf.draft !== buf.original) void savePath(path, true);
      }
    }, 1500);
    return () => window.clearTimeout(timer);
  }, [buffers, layout.autoSave, savePath]);

  // Protect unsaved changes on browser close / refresh
  useEffect(() => {
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      if (Object.values(buffers).some((b) => b.draft !== b.original)) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [buffers]);

  // Keyboard shortcuts
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const meta = e.metaKey || e.ctrlKey;
      if (meta && e.key.toLowerCase() === "s") {
        e.preventDefault();
        if (e.shiftKey) void saveAll();
        else void saveActive();
      }
      if (meta && e.shiftKey && e.key.toLowerCase() === "i") {
        e.preventDefault();
        void formatActive();
      }
      if (meta && e.key.toLowerCase() === "p") {
        e.preventDefault();
        setPaletteOpen(true);
      }
      if (meta && e.key.toLowerCase() === "b") {
        e.preventDefault();
        patchLayout({ leftOpen: !layout.leftOpen });
      }
      if (meta && e.key === "j") {
        e.preventDefault();
        patchLayout({ bottomOpen: !layout.bottomOpen });
      }
      if (meta && e.key === "`") {
        e.preventDefault();
        patchLayout({ bottomOpen: true, bottomTab: "terminal" });
      }
      if (meta && e.shiftKey && e.key.toLowerCase() === "e") {
        e.preventDefault();
        setLeftView("explorer");
      }
      if (meta && e.shiftKey && e.key.toLowerCase() === "f") {
        e.preventDefault();
        setLeftView("search");
      }
      if (meta && e.shiftKey && e.key.toLowerCase() === "g") {
        e.preventDefault();
        setLeftView("git");
      }
      if (meta && e.key.toLowerCase() === "\\") {
        e.preventDefault();
        patchLayout({ split: !layout.split, splitPath: layout.split ? "" : layout.activePath });
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [layout, saveActive, saveAll, formatActive, patchLayout]);

  // Resize handlers
  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      if (!drag.current) return;
      if (drag.current === "left") patchLayout({ leftWidth: Math.min(480, Math.max(180, e.clientX - 48) ) });
      if (drag.current === "right") patchLayout({ rightWidth: Math.min(480, Math.max(220, window.innerWidth - e.clientX) ) });
      if (drag.current === "bottom") patchLayout({ bottomHeight: Math.min(480, Math.max(120, window.innerHeight - e.clientY - 28) ) });
    };
    const onUp = () => { drag.current = null; };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, [patchLayout]);

  const runTests = async () => {
    try {
      const r = await api.runTests(pid);
      setTestJobId(r.job_id);
      patchLayout({ bottomOpen: true, bottomTab: "tests" });
      toast("success", "Test run started");
      setOutputLines((lines) => [...lines, `[tests] job ${r.job_id} started`]);
    } catch (e) {
      toast("error", (e as Error).message);
    }
  };

  const startScan = async () => {
    try {
      const r = await api.startScan(pid, { ruff: true, pylint: true, ai: true });
      setScanJobId(r.job_id);
      patchLayout({ bottomOpen: true, bottomTab: "logs" });
      toast("success", "Scan started");
      setOutputLines((lines) => [...lines, `[scan] job ${r.job_id} started`]);
    } catch (e) {
      toast("error", (e as Error).message);
    }
  };

  useJob(testJobId, () => {
    testRuns.reload();
    setOutputLines((lines) => [...lines, "[tests] finished"]);
    setTestJobId(null);
  });
  useJob(scanJobId, () => {
    problems.reload();
    jobs.reload();
    setOutputLines((lines) => [...lines, "[scan/fix] finished"]);
    setScanJobId(null);
    setTreeEpoch((n) => n + 1);
  });

  const activeBuf = layout.activePath ? buffers[layout.activePath] : null;
  const activePreview = layout.activePath ? previews[layout.activePath] : null;
  const splitBuf = layout.split && layout.splitPath ? buffers[layout.splitPath] : null;
  const highlights = (activeBuf?.findings ?? []).map((f) => ({
    start: f.line_start, end: f.line_end, severity: f.severity as "critical" | "high" | "medium" | "low",
    message: `**${f.title}** (${f.severity})`,
  }));
  const editorMarkers: EditorMarker[] = useMemo(() => {
    const path = layout.activePath;
    if (!path || !problems.data) return [];
    return problems.data.problems
      .filter((p) => p.file === path)
      .map((p) => ({
        line: p.line,
        endLine: p.end_line || p.line,
        message: `${p.title} (${p.severity})`,
        severity: (p.severity === "critical" || p.severity === "high" ? "error" : p.severity === "medium" ? "warning" : "info") as EditorMarker["severity"],
      }));
  }, [layout.activePath, problems.data]);

  return (
    <>
      {/* IDE top bar */}
      <header className="flex h-10 shrink-0 items-center gap-2 border-b border-border bg-panel px-2">
        <button className="rounded-md px-2 py-1 text-[12px] text-muted hover:bg-hover hover:text-fg" onClick={() => navigate("dashboard")} title="Back to Dashboard">
          <LayoutDashboard className="size-4" />
        </button>
        <select
          className="max-w-[180px] truncate rounded border border-border bg-panel2 px-2 py-1 text-[12px]"
          value={pid}
          onChange={(e) => setActive(Number(e.target.value))}
          aria-label="Project"
        >
          {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        <div className="relative min-w-0 flex-1 max-w-md">
          <Search className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-muted" />
          <input
            className={cx(inputCls, "pl-7")}
            placeholder="Search files (Enter) · Ctrl+P commands"
            value={globalQ}
            onChange={(e) => setGlobalQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && globalQ.trim()) {
                setLeftView("search");
                // hand off via opening search — user types again; also try open path
                openFile(globalQ.trim());
              }
            }}
          />
        </div>
        <button className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-[12px] hover:bg-hover" onClick={() => setPaletteOpen(true)} title="Command palette (Ctrl+P)">
          <Command className="size-3.5" /> Commands
        </button>
        <button className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-[12px] hover:bg-hover" onClick={runTests} title="Run tests"><Play className="size-3.5" /> Run</button>
        <button className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-[12px] hover:bg-hover" onClick={() => { setLeftView("run"); patchLayout({ bottomOpen: true, bottomTab: "debug" }); }} title="Run and Debug"><Bug className="size-3.5" /> Debug</button>
        <button className="rounded-md border border-border p-1.5 hover:bg-hover" onClick={() => patchLayout({ bottomOpen: true, bottomTab: "terminal" })} title="Terminal"><TerminalIcon className="size-3.5" /></button>
        <button className={cx("rounded-md border p-1.5 hover:bg-hover", layout.rightOpen ? "border-accent/40 bg-accent/10" : "border-border")} onClick={() => patchLayout({ rightOpen: !layout.rightOpen })} title="AI assistant"><Bot className="size-3.5" /></button>
        <button className="rounded-md border border-border p-1.5 hover:bg-hover" onClick={() => navigate("settings")} title="Settings"><SettingsIcon className="size-3.5" /></button>
        <button className="rounded-md border border-border px-2 py-1 text-[12px] hover:bg-hover" onClick={() => setTheme(theme === "dark" ? "light" : "dark")}>
          {theme === "dark" ? "Light" : "Dark"}
        </button>
        <span className="hidden truncate text-[11px] text-muted lg:inline">{status?.ai.configured ? status.ai.model ?? status.ai.provider : "AI off"}</span>
      </header>

      <div className="flex min-h-0 flex-1">
        {/* IDE activity bar */}
        <nav className="flex w-12 shrink-0 flex-col items-center gap-0.5 border-r border-border bg-activity py-2" aria-label="IDE activity">
          {LEFT_VIEWS.map((v) => {
            const Icon = LEFT_ICONS[v.id];
            const on = layout.leftOpen && layout.leftView === v.id;
            return (
              <button key={v.id} title={`${v.label} (${v.shortcut})`} aria-label={v.label} aria-current={on ? "page" : undefined}
                onClick={() => setLeftView(v.id)}
                className={cx("relative grid size-10 place-items-center rounded-md", on ? "text-fg" : "text-muted hover:text-fg")}>
                {on && <span className="absolute bottom-2 left-0 top-2 w-0.5 rounded bg-accent" />}
                <Icon className="size-5" />
              </button>
            );
          })}
          <div className="mt-auto flex flex-col gap-0.5">
            <button title="Toggle bottom panel" className="grid size-10 place-items-center text-muted hover:text-fg" onClick={() => patchLayout({ bottomOpen: !layout.bottomOpen })}>
              <PanelBottom className="size-5" />
            </button>
            <button title="Toggle AI sidebar" className="grid size-10 place-items-center text-muted hover:text-fg" onClick={() => patchLayout({ rightOpen: !layout.rightOpen })}>
              <PanelRight className="size-5" />
            </button>
          </div>
        </nav>

        {/* Left side panel */}
        {layout.leftOpen && (
          <>
            <aside style={{ width: layout.leftWidth }} className="flex shrink-0 flex-col border-r border-border bg-panel">
              <SidePanelBody
                view={layout.leftView}
                projectId={pid}
                selected={layout.activePath}
                onOpenFile={(p, line) => openFile(p, line)}
                onTreeChanged={() => setTreeEpoch((n) => n + 1)}
                onPathMoved={onPathMoved}
              />
            </aside>
            <div className="w-1 shrink-0 cursor-col-resize hover:bg-accent/40" onMouseDown={() => { drag.current = "left"; }} />
          </>
        )}

        {/* Center: editors + bottom */}
        <div className="flex min-w-0 flex-1 flex-col">
          {/* Tabs */}
          <div className="flex h-9 shrink-0 items-center gap-0 overflow-x-auto border-b border-border bg-panel2">
            {layout.tabs.length === 0 && <span className="px-3 text-[12px] text-muted">No files open</span>}
            {layout.tabs.map((t) => (
              <div key={t} className={cx("group flex h-full items-center gap-1 border-r border-border px-2 text-[12px]", t === layout.activePath ? "bg-bg text-fg" : "text-muted hover:bg-hover")}>
                <button className="max-w-[160px] truncate" onClick={() => patchLayout({ activePath: t })} title={t}>
                  {dirty[t] ? "● " : ""}{t.split("/").pop()}
                </button>
                <button className="rounded p-0.5 opacity-0 hover:bg-panel group-hover:opacity-100" onClick={() => closeTab(t)} aria-label={`Close ${t}`}><X className="size-3" /></button>
              </div>
            ))}
            <div className="ml-auto flex shrink-0 items-center gap-1 px-1">
              <label className="mr-1 flex items-center gap-1 text-[11px] text-muted" title="Auto-save to workspace">
                <input type="checkbox" checked={!!layout.autoSave} onChange={(e) => patchLayout({ autoSave: e.target.checked })} />
                Auto-save
              </label>
              <button className="rounded p-1 text-muted hover:bg-hover hover:text-fg" title="Format document (Ctrl+Shift+I)" onClick={() => void formatActive()}>
                <AlignLeft className="size-4" />
              </button>
              <button className="rounded p-1 text-muted hover:bg-hover hover:text-fg" title="Split editor (Ctrl+\\)" onClick={() => patchLayout({ split: !layout.split, splitPath: layout.split ? "" : layout.activePath })}>
                <Columns2 className="size-4" />
              </button>
              <button className="rounded p-1 text-muted hover:bg-hover hover:text-fg" title="Save (Ctrl+S)" onClick={() => void saveActive()}>
                <Save className="size-4" />
              </button>
              {dirty[layout.activePath] && (
                <Button kind="primary" className="mr-1" onClick={() => void saveActive()}>Save</Button>
              )}
            </div>
          </div>

          <div className={cx("min-h-0 flex-1", layout.split ? "grid grid-cols-2" : "flex flex-col")}>
            <div className="min-h-0 min-w-0 flex-1 border-r border-border">
              {fileError && <div className="p-2"><ErrorNote>{fileError}</ErrorNote></div>}
              {loadingPath === layout.activePath && <Spinner />}
              {activePreview && layout.activePath ? (
                <div className="grid h-full place-items-center overflow-auto p-6">
                  {activePreview.kind === "image" && activePreview.image_base64 ? (
                    <img
                      alt={layout.activePath}
                      className="max-h-full max-w-full rounded border border-border"
                      src={`data:${activePreview.mime || "image/png"};base64,${activePreview.image_base64}`}
                    />
                  ) : activePreview.kind === "folder" ? (
                    <Empty title="Folder" action={<Button onClick={() => setLeftView("explorer")}>Show in Explorer</Button>}>
                      {layout.activePath} · {activePreview.size} entr{activePreview.size === 1 ? "y" : "ies"}
                    </Empty>
                  ) : (
                    <Empty title="Binary file">
                      {layout.activePath} ({(activePreview.size / 1024).toFixed(1)} KB)
                      {activePreview.mime ? ` · ${activePreview.mime}` : ""}. Download from Explorer context menu.
                    </Empty>
                  )}
                </div>
              ) : activeBuf && layout.activePath ? (
                <EditableEditor
                  projectId={pid}
                  path={layout.activePath}
                  value={activeBuf.draft}
                  language={activeBuf.language}
                  highlights={highlights}
                  markers={editorMarkers}
                  reveal={reveal?.path === layout.activePath ? { line: reveal.line } : undefined}
                  onChange={(v) => setBuffers((b) => ({ ...b, [layout.activePath]: { ...activeBuf, draft: v } }))}
                  onSave={saveActive}
                  onFormat={formatActive}
                  onGoToDefinition={goToDefinition}
                />
              ) : !loadingPath && !fileError ? (
                <div className="grid h-full place-items-center p-6">
                  <Empty title="Open a file" action={<Button kind="primary" onClick={() => setLeftView("explorer")}>Open Explorer</Button>}>
                    Use the Explorer or Search panel. Ctrl+P for commands.
                  </Empty>
                </div>
              ) : null}
            </div>
            {layout.split && (
              <div className="min-h-0 min-w-0">
                {splitBuf && layout.splitPath ? (
                  <EditableEditor
                    projectId={pid}
                    path={layout.splitPath}
                    value={splitBuf.draft}
                    language={splitBuf.language}
                    onChange={(v) => setBuffers((b) => ({ ...b, [layout.splitPath]: { ...splitBuf, draft: v } }))}
                    onSave={saveActive}
                    onFormat={formatActive}
                    onGoToDefinition={goToDefinition}
                  />
                ) : (
                  <div className="grid h-full place-items-center text-[12px] text-muted">Click a tab, then Split to compare</div>
                )}
              </div>
            )}
          </div>

          {/* Bottom panel */}
          {layout.bottomOpen && (
            <>
              <div className="h-1 shrink-0 cursor-row-resize hover:bg-accent/40" onMouseDown={() => { drag.current = "bottom"; }} />
              <BottomPanel
                height={layout.bottomHeight}
                tab={layout.bottomTab}
                onTab={(bottomTab) => patchLayout({ bottomTab })}
                onClose={() => patchLayout({ bottomOpen: false })}
                problems={problems.data?.problems ?? []}
                problemsLoading={!problems.data && !problems.error}
                problemsError={problems.error}
                onOpenProblem={(file, line) => { openFile(file, line); }}
                onScan={startScan}
                testJobId={testJobId}
                scanJobId={scanJobId}
                testRuns={testRuns.data ?? []}
                outputLines={outputLines}
                events={events.data ?? []}
                activeJobs={jobs.data ?? []}
                onRunTests={runTests}
              />
            </>
          )}
        </div>

        {/* Right AI sidebar */}
        {layout.rightOpen && (
          <>
            <div className="w-1 shrink-0 cursor-col-resize hover:bg-accent/40" onMouseDown={() => { drag.current = "right"; }} />
            <aside style={{ width: layout.rightWidth }} className="flex shrink-0 flex-col border-l border-border bg-panel">
              <RightAssistant
                projectId={pid}
                activePath={layout.activePath}
                problems={problems.data?.problems ?? []}
                onOpenFile={openFile}
                onScan={startScan}
                onApplyPatchToEditor={(path, after, before) => applyEditorContent(path, after, undefined, before)}
                onAnalyze={async (id) => {
                  try {
                    const r = await api.analyze(id);
                    toast("success", "Analysis job started");
                    setScanJobId(r.job_id);
                    patchLayout({ bottomOpen: true, bottomTab: "logs" });
                  } catch (e) {
                    toast("error", (e as Error).message);
                  }
                }}
                onFix={async (id) => {
                  try {
                    const r = await api.fix(id);
                    toast("success", "Fix job started");
                    setScanJobId(r.job_id);
                    patchLayout({ bottomOpen: true, bottomTab: "logs" });
                  } catch (e) {
                    toast("error", (e as Error).message);
                  }
                }}
              />
            </aside>
          </>
        )}
      </div>

      {paletteOpen && (
        <CommandPalette
          onClose={() => setPaletteOpen(false)}
          actions={[
            { label: "Save file", run: () => { void saveActive(); setPaletteOpen(false); } },
            { label: "Save all", run: () => { void saveAll(); setPaletteOpen(false); } },
            { label: "Format document", run: () => { void formatActive(); setPaletteOpen(false); } },
            { label: "Toggle auto-save", run: () => { patchLayout({ autoSave: !layout.autoSave }); setPaletteOpen(false); } },
            { label: "Reload file from workspace", run: () => { if (layout.activePath) void reloadFromWorkspace(layout.activePath); setPaletteOpen(false); } },
            { label: "Toggle Explorer", run: () => { setLeftView("explorer"); setPaletteOpen(false); } },
            { label: "Search in files", run: () => { setLeftView("search"); setPaletteOpen(false); } },
            { label: "Source Control", run: () => { setLeftView("git"); setPaletteOpen(false); } },
            { label: "Run and Debug", run: () => { setLeftView("run"); setPaletteOpen(false); } },
            { label: "Extensions", run: () => { setLeftView("extensions"); setPaletteOpen(false); } },
            { label: "AI Agents", run: () => { setLeftView("agents"); setPaletteOpen(false); } },
            { label: "AutoPatch panel", run: () => { setLeftView("autopatch"); setPaletteOpen(false); } },
            { label: "Run project tests", run: () => { runTests(); setPaletteOpen(false); } },
            { label: "Start AutoPatch scan", run: () => { startScan(); setPaletteOpen(false); } },
            { label: "Toggle AI sidebar", run: () => { patchLayout({ rightOpen: !layout.rightOpen }); setPaletteOpen(false); } },
            { label: "Toggle bottom panel", run: () => { patchLayout({ bottomOpen: !layout.bottomOpen }); setPaletteOpen(false); } },
            { label: "Split editor", run: () => { patchLayout({ split: !layout.split, splitPath: layout.activePath }); setPaletteOpen(false); } },
            { label: "Open Settings", run: () => { navigate("settings"); setPaletteOpen(false); } },
            { label: "Back to Dashboard", run: () => { navigate("dashboard"); setPaletteOpen(false); } },
            ...layout.tabs.map((t) => ({ label: `Focus ${t}`, run: () => { patchLayout({ activePath: t }); setPaletteOpen(false); } })),
          ]}
        />
      )}
    </>
  );
}

function RightAssistant({
  projectId, activePath, problems, onOpenFile, onScan, onAnalyze, onFix, onApplyPatchToEditor,
}: {
  projectId: number;
  activePath: string;
  problems: { id: number; title: string; file: string; line: number; severity: "critical" | "high" | "medium" | "low" }[];
  onOpenFile: (p: string, line?: number) => void;
  onScan: () => void;
  onAnalyze: (id: number) => void;
  onFix: (id: number) => void;
  onApplyPatchToEditor?: (path: string, after: string, before?: string) => void;
}) {
  const { status, active } = useApp();
  const fileProblems = problems.filter((p) => p.file === activePath);
  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 border-b border-border px-3 py-2">
        <Bot className="size-4 text-accent" />
        <span className="text-[13px] font-semibold">AI Assistant</span>
      </div>
      <div className="min-h-0 flex-1 space-y-3 overflow-auto p-3 text-[13px]">
        <div className="rounded bg-panel2 p-2 ring-1 ring-border">
          <div className="text-[11px] uppercase text-muted">Project context</div>
          <div className="font-medium">{active?.name}</div>
          <div className="font-mono text-[11px] text-muted">{activePath || "No file focused"}</div>
          <div className="mt-1 text-[12px] text-muted">
            Engine: {status?.ai.configured ? `${status.ai.provider}${status.ai.model ? ` · ${status.ai.model}` : ""}` : "not configured"}
          </div>
        </div>
        <p className="text-[12px] text-muted">
          Free-form chat streams land in Phase 3. This sidebar drives <strong>real</strong> AutoPatch analyze/fix jobs and surfacing suggestions from scan findings — no mock replies.
        </p>
        <Button kind="primary" onClick={onScan}>Scan project</Button>
        <div className="font-medium">Suggestions in this file</div>
        {fileProblems.length === 0 ? (
          <p className="text-[12px] text-muted">No open findings on this file. Run a scan or pick a problem below.</p>
        ) : fileProblems.map((p) => (
          <div key={p.id} className="rounded bg-panel2 p-2 ring-1 ring-border">
            <button className="text-left font-medium hover:underline" onClick={() => onOpenFile(p.file, p.line)}>{p.title}</button>
            <div className="mt-1 flex flex-wrap gap-1">
              <Button onClick={() => onAnalyze(p.id)}>Analyze</Button>
              <Button kind="primary" onClick={() => onFix(p.id)}>Generate fix</Button>
              <Button onClick={async () => {
                try {
                  const patches = await api.patches(projectId);
                  const ready = patches.find((x) => x.finding_id === p.id && (x.status === "ready" || x.status === "accepted"));
                  if (!ready) {
                    onFix(p.id);
                    return;
                  }
                  const full = await api.patch(ready.id);
                  onApplyPatchToEditor?.(full.file, full.after_content, full.before_content);
                  onOpenFile(full.file, p.line);
                } catch (e) {
                  /* fall through to generate */
                  onFix(p.id);
                }
              }}>Open fix in editor</Button>
            </div>
          </div>
        ))}
        <div className="font-medium">Agent activity</div>
        <Button onClick={() => navigate(`analysis`)}>AI Analysis</Button>
        <Button onClick={() => navigate("autofix")}>AutoFix queue</Button>
        <Button onClick={() => navigate("settings")}>AI settings</Button>
        <p className="text-[11px] text-muted">Project id {projectId}</p>
      </div>
    </div>
  );
}

function BottomPanel({
  height, tab, onTab, onClose, problems, problemsLoading, problemsError, onOpenProblem, onScan,
  testJobId, scanJobId, testRuns, outputLines, events, activeJobs, onRunTests,
}: {
  height: number;
  tab: BottomTab;
  onTab: (t: BottomTab) => void;
  onClose: () => void;
  problems: { id: number; title: string; file: string; line: number; severity: "critical" | "high" | "medium" | "low" }[];
  problemsLoading: boolean;
  problemsError: string;
  onOpenProblem: (file: string, line: number) => void;
  onScan: () => void;
  testJobId: string | null;
  scanJobId: string | null;
  testRuns: { id: number; status: string; passed: number; failed: number; errors: number; output?: string; created_at: string; sandbox: string }[];
  outputLines: string[];
  events: { id: number; kind: string; message: string; created_at: string }[];
  activeJobs: Job[];
  onRunTests: () => void;
}) {
  const tabs: { id: BottomTab; label: string }[] = [
    { id: "terminal", label: "Terminal" },
    { id: "problems", label: `Problems (${problems.length})` },
    { id: "output", label: "Output" },
    { id: "debug", label: "Debug Console" },
    { id: "tests", label: "Test Results" },
    { id: "logs", label: "AutoPatch Logs" },
  ];
  const latest = testRuns[0];
  const testJob = useLoad(() => (testJobId ? api.job(testJobId) : Promise.resolve(null)), [testJobId]);
  const scanJob = useLoad(() => (scanJobId ? api.job(scanJobId) : Promise.resolve(null)), [scanJobId]);

  return (
    <div style={{ height }} className="flex shrink-0 flex-col border-t border-border bg-panel">
      <div className="flex h-8 shrink-0 items-center gap-1 border-b border-border px-1">
        {tabs.map((t) => (
          <button key={t.id} onClick={() => onTab(t.id)}
            className={cx("rounded px-2 py-1 text-[12px]", tab === t.id ? "bg-bg text-fg" : "text-muted hover:text-fg")}>
            {t.label}
          </button>
        ))}
        <button className="ml-auto rounded p-1 text-muted hover:bg-hover" onClick={onClose} aria-label="Close panel"><X className="size-3.5" /></button>
      </div>
      <div className="min-h-0 flex-1 overflow-auto p-2 text-[13px]">
        {tab === "terminal" && (
          <div className="space-y-2">
            <p className="text-[12px] text-muted">
              Interactive PTY shell is not enabled (security). This panel runs <strong>real</strong> AutoPatch sandbox test jobs — same engine as Test Center.
            </p>
            <Button kind="primary" icon={<FlaskConical className="size-3.5" />} onClick={onRunTests}>Run pytest in sandbox</Button>
            {testJob.data && <JobLog job={testJob.data} />}
            {latest?.output && (
              <pre className="mt-2 max-h-40 overflow-auto rounded bg-black/80 p-2 font-mono text-[11px] text-emerald-300 whitespace-pre-wrap">{latest.output}</pre>
            )}
          </div>
        )}
        {tab === "problems" && (
          <div>
            {problemsError && <ErrorNote>{problemsError}</ErrorNote>}
            {problemsLoading && <Spinner />}
            {!problemsLoading && problems.length === 0 && (
              <Empty title="No problems" action={<Button kind="primary" onClick={onScan}>Run scan</Button>}>
                Problems come from open AutoPatch findings after a scan.
              </Empty>
            )}
            <ul className="divide-y divide-border">
              {problems.map((p) => (
                <li key={p.id}>
                  <button className="flex w-full items-center gap-2 px-1 py-1.5 text-left hover:bg-hover" onClick={() => onOpenProblem(p.file, p.line)}>
                    <SeverityBadge severity={p.severity} />
                    <span className="min-w-0 flex-1 truncate">{p.title}</span>
                    <span className="font-mono text-[11px] text-muted">{p.file}:{p.line}</span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
        {tab === "output" && (
          <pre className="font-mono text-[12px] text-muted whitespace-pre-wrap">
            {outputLines.length ? outputLines.join("\n") : "IDE output will appear here when you run scans or tests."}
            {activeJobs.length > 0 && `\n\nActive jobs:\n${activeJobs.map((j) => `${j.id} ${j.kind} ${j.status}`).join("\n")}`}
          </pre>
        )}
        {tab === "debug" && (
          <div className="space-y-2 text-[12px] text-muted">
            <p>Debug console is reserved for a future DAP adapter. Use <strong>Run and Debug</strong> to execute real sandbox tests and inspect failures in Test Results.</p>
            <Button onClick={onRunTests}>Run tests now</Button>
          </div>
        )}
        {tab === "tests" && (
          <div className="space-y-2">
            {testJob.data && <JobLog job={testJob.data} />}
            {!latest ? <Empty title="No test runs" /> : (
              <div>
                <div className="mb-1 font-medium">{latest.status} · {latest.passed} passed · {latest.failed} failed · {latest.errors} errors · {latest.sandbox}</div>
                {latest.output && <pre className="max-h-48 overflow-auto rounded bg-black/80 p-2 font-mono text-[11px] text-emerald-300 whitespace-pre-wrap">{latest.output}</pre>}
              </div>
            )}
          </div>
        )}
        {tab === "logs" && (
          <div className="space-y-2">
            {scanJob.data && <JobLog job={scanJob.data} />}
            <div className="font-medium">Project events</div>
            <ul className="space-y-1">
              {events.slice(0, 40).map((e) => (
                <li key={e.id} className="font-mono text-[11px] text-muted">
                  <span className="text-fg">{e.kind}</span> {e.message}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}

function CommandPalette({ onClose, actions }: { onClose: () => void; actions: { label: string; run: () => void }[] }) {
  const [q, setQ] = useState("");
  const filtered = actions.filter((a) => a.label.toLowerCase().includes(q.toLowerCase()));
  return (
    <div className="fixed inset-0 z-[80] flex items-start justify-center bg-black/40 pt-[12vh]" onClick={onClose}>
      <div className="w-[min(92vw,520px)] overflow-hidden rounded-lg border border-border bg-panel shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <input autoFocus className={cx(inputCls, "rounded-none border-0 border-b border-border")} placeholder="Type a command…" value={q} onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape") onClose();
            if (e.key === "Enter" && filtered[0]) filtered[0].run();
          }}
        />
        <ul className="max-h-80 overflow-auto py-1">
          {filtered.map((a) => (
            <li key={a.label}>
              <button className="w-full px-3 py-2 text-left text-[13px] hover:bg-hover" onClick={a.run}>{a.label}</button>
            </li>
          ))}
          {!filtered.length && <li className="px-3 py-2 text-[12px] text-muted">No commands match</li>}
        </ul>
      </div>
    </div>
  );
}

function firstFile(node: { type: string; path: string; children?: unknown[] }): string | null {
  const kids = (node.children ?? []) as { type: string; path: string; children?: unknown[]; name?: string }[];
  const py = kids.find((k) => k.type === "file" && k.path.endsWith(".py"));
  if (py) return py.path;
  for (const k of kids) {
    if (k.type === "dir") {
      const f = firstFile(k as never);
      if (f) return f;
    }
  }
  return kids.find((k) => k.type === "file")?.path ?? null;
}

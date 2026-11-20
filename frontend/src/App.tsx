import {
  Bug, Cpu, FileSearch, FlaskConical, FolderGit2, FolderTree, History, LayoutDashboard, Moon, Settings as SettingsIcon,
  ShieldAlert, ShieldCheck, Sparkles, Sun, Wrench,
} from "lucide-react";
import { lazy, Suspense } from "react";
import { cx, Spinner } from "./components/ui";
import { navigate, useApp, useRoute } from "./lib";

const Dashboard = lazy(() => import("./pages/Dashboard"));
const Projects = lazy(() => import("./pages/Projects"));
const Explorer = lazy(() => import("./pages/Explorer"));
const Scanner = lazy(() => import("./pages/Scanner"));
const FileReports = lazy(() => import("./pages/FileReports"));
const Analysis = lazy(() => import("./pages/Analysis"));
const AutoFix = lazy(() => import("./pages/AutoFix"));
const TestCenter = lazy(() => import("./pages/TestCenter"));
const HistoryPage = lazy(() => import("./pages/HistoryPage"));
const SettingsPage = lazy(() => import("./pages/SettingsPage"));

/**
 * Activity bar order:
 * Dashboard = app home; IDE opens as its own full-bleed workspace when selected;
 * AutoPatch tools follow; Settings last.
 */
const SECTIONS = [
  { id: "dashboard", label: "Dashboard", icon: LayoutDashboard, page: Dashboard },
  { id: "ide", label: "IDE", icon: FolderTree, page: Explorer },
  { id: "projects", label: "Projects", icon: FolderGit2, page: Projects },
  { id: "scanner", label: "Bug Scanner", icon: Bug, page: Scanner },
  { id: "analysis", label: "AI Analysis", icon: Sparkles, page: Analysis },
  { id: "autofix", label: "AutoFix", icon: Wrench, page: AutoFix },
  { id: "tests", label: "Test Center", icon: FlaskConical, page: TestCenter },
  { id: "files", label: "File Reports", icon: FileSearch, page: FileReports },
  { id: "history", label: "Change History", icon: History, page: HistoryPage },
  { id: "settings", label: "Settings", icon: SettingsIcon, page: SettingsPage },
] as const;

export default function App() {
  const route = useRoute();
  const { projects, active, setActive, status, toasts, theme, setTheme } = useApp();
  const section = SECTIONS.find((s) => s.id === route.section) ?? SECTIONS[0];
  const Page = section.page;

  // IDE opens as its own full-screen workspace (separate chrome). App shell stays for tools.
  if (section.id === "ide") {
    return (
      <div className="flex h-full flex-col">
        <main className="min-h-0 min-w-0 flex-1 overflow-hidden">
          <Suspense fallback={<Spinner />}>
            <Page />
          </Suspense>
        </main>
        <div className="pointer-events-none fixed bottom-4 right-4 z-[60] flex w-[min(92vw,380px)] flex-col gap-2">
          {toasts.map((t) => (
            <div
              key={t.id}
              role="status"
              className={cx(
                "pointer-events-auto rounded-md border px-3 py-2 shadow-lg",
                t.kind === "error" ? "border-red-500/50 bg-red-600 text-white" : t.kind === "success" ? "border-emerald-500/50 bg-emerald-600 text-white" : "border-border bg-panel text-fg",
              )}
            >
              {t.message}
            </div>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <header className="flex h-11 shrink-0 items-center gap-3 border-b border-border bg-panel px-3">
        <button onClick={() => navigate("dashboard")} className="flex items-center gap-2 font-semibold" title="Dashboard">
          <span className="grid size-6 place-items-center rounded-md bg-accent text-accent-fg">
            <Wrench className="size-3.5" />
          </span>
          <span className="hidden sm:inline">AutoPatch</span>
        </button>
        <div className="mx-1 h-5 w-px bg-border" />
        <label className="flex min-w-0 items-center gap-2">
          <span className="hidden text-muted md:inline">Project</span>
          <select
            value={active?.id ?? ""}
            onChange={(e) => setActive(e.target.value ? Number(e.target.value) : null)}
            className="max-w-[46vw] truncate rounded-md border border-border bg-panel2 px-2 py-1 text-[13px] sm:max-w-xs"
            aria-label="Active project"
          >
            {projects.length === 0 && <option value="">No projects yet</option>}
            {projects.map((p) => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>
        </label>
        <div className="ml-auto flex items-center gap-2">
          <button
            onClick={() => navigate("ide")}
            className="inline-flex items-center gap-1.5 rounded-md bg-accent px-3 py-1.5 text-[13px] font-semibold text-accent-fg hover:opacity-90"
            title="Open the full IDE workspace"
          >
            <FolderTree className="size-3.5" />
            Open IDE
          </button>
          <StatusChip
            ok={!!status?.ai.configured}
            icon={<Cpu className="size-3.5" />}
            text={status?.ai.configured ? status.ai.model ?? status.ai.provider : "AI not configured"}
            title={status?.ai.message ?? "AI provider"}
            onClick={() => navigate("settings")}
          />
          <StatusChip
            ok={!!status?.sandbox.ready}
            icon={status?.sandbox.ready ? <ShieldCheck className="size-3.5" /> : <ShieldAlert className="size-3.5" />}
            text={status?.sandbox.ready ? status.sandbox.label ?? "Sandbox" : "Sandbox not ready"}
            title={status?.sandbox.message ?? "Test sandbox"}
            onClick={() => navigate("settings")}
          />
          <button
            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
            className="rounded-md p-1.5 text-muted hover:bg-hover hover:text-fg"
            aria-label="Toggle colour theme"
            title="Toggle colour theme"
          >
            {theme === "dark" ? <Sun className="size-4" /> : <Moon className="size-4" />}
          </button>
        </div>
      </header>

      <div className="flex min-h-0 flex-1 flex-col-reverse md:flex-row">
        <nav className="flex shrink-0 gap-0.5 overflow-x-auto border-t border-border bg-activity px-1 py-1 md:w-12 md:flex-col md:overflow-visible md:border-r md:border-t-0 md:px-0 md:py-2" aria-label="Sections">
          {SECTIONS.map((s) => {
            const Icon = s.icon;
            const on = s.id === section.id;
            return (
              <button
                key={s.id}
                onClick={() => navigate(s.id)}
                title={s.label}
                aria-label={s.label}
                aria-current={on ? "page" : undefined}
                className={cx(
                  "group relative grid h-10 w-11 shrink-0 place-items-center rounded-md md:mx-auto md:h-11 md:w-11",
                  on ? "text-fg" : "text-muted hover:text-fg",
                )}
              >
                {on && <span className="absolute bottom-0 left-2 right-2 h-0.5 rounded bg-accent md:bottom-2 md:left-0 md:right-auto md:top-2 md:h-auto md:w-0.5" />}
                <Icon className="size-5" />
                <span className="pointer-events-none absolute left-12 z-40 hidden whitespace-nowrap rounded-md border border-border bg-panel px-2 py-1 text-[12px] text-fg shadow-lg md:group-hover:block">
                  {s.label}
                </span>
              </button>
            );
          })}
        </nav>
        <main className="min-h-0 min-w-0 flex-1 overflow-auto">
          <div className="mx-auto max-w-[1500px] p-4 md:p-6">
            <Suspense fallback={<Spinner />}>
              <Page />
            </Suspense>
          </div>
        </main>
      </div>

      <footer className="flex h-6 shrink-0 items-center gap-4 overflow-hidden bg-status px-3 text-[12px] text-white">
        <span className="truncate">{active ? active.name : "No project"}</span>
        {active?.last_scan && <span className="hidden truncate sm:inline">Last scan {new Date(active.last_scan.finished_at).toLocaleString()}</span>}
        <span className="ml-auto whitespace-nowrap">{status ? (status.active_jobs ? `${status.active_jobs} job(s) running` : "Idle") : "Server unreachable"}</span>
        <span className="hidden whitespace-nowrap sm:inline">AutoPatch {status?.version ?? ""}</span>
      </footer>

      {/* toasts */}
      <div className="pointer-events-none fixed bottom-10 right-4 z-[60] flex w-[min(92vw,380px)] flex-col gap-2">
        {toasts.map((t) => (
          <div
            key={t.id}
            role="status"
            className={cx(
              "pointer-events-auto rounded-md border px-3 py-2 shadow-lg",
              t.kind === "error" ? "border-red-500/50 bg-red-600 text-white" : t.kind === "success" ? "border-emerald-500/50 bg-emerald-600 text-white" : "border-border bg-panel text-fg",
            )}
          >
            {t.message}
          </div>
        ))}
      </div>
    </div>
  );
}

function StatusChip({ ok, icon, text, title, onClick }: { ok: boolean; icon: React.ReactNode; text: string; title: string; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      title={title}
      className={cx(
        "hidden max-w-[200px] items-center gap-1.5 truncate rounded-full border px-2.5 py-0.5 text-[12px] sm:flex",
        ok ? "border-emerald-500/40 text-emerald-700 dark:text-emerald-300" : "border-amber-500/50 text-amber-700 dark:text-amber-300",
      )}
    >
      {icon}
      <span className="truncate">{text}</span>
    </button>
  );
}

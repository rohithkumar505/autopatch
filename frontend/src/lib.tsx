import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { api, type AppStatus, type Job, type Project } from "./api";

// ------------------------------------------------------------------ storage (per-viewer conveniences only)

export function load<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem("autopatch." + key);
    return raw === null ? fallback : (JSON.parse(raw) as T);
  } catch {
    return fallback;
  }
}

export function save(key: string, value: unknown) {
  try {
    localStorage.setItem("autopatch." + key, JSON.stringify(value));
  } catch {
    /* storage unavailable: the app works without it */
  }
}

// ------------------------------------------------------------------ hash routing

export interface Route {
  section: string;
  param: string;
  query: URLSearchParams;
}

/** Legacy `#/explorer` deep links still open the IDE surface. Empty hash → Dashboard home. */
function normalizeSection(section: string): string {
  if (section === "explorer") return "ide";
  return section || "dashboard";
}

function parseHash(): Route {
  const raw = window.location.hash.replace(/^#\/?/, "");
  const [path, search = ""] = raw.split("?");
  const [section = "dashboard", ...rest] = path.split("/");
  return {
    section: normalizeSection(section),
    param: decodeURIComponent(rest.join("/")),
    query: new URLSearchParams(search),
  };
}

export function useRoute(): Route {
  const [route, setRoute] = useState(parseHash);
  useEffect(() => {
    const onChange = () => setRoute(parseHash());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}

export function navigate(path: string) {
  window.location.hash = "#/" + path.replace(/^\/+/, "");
}

// ------------------------------------------------------------------ data loading

export function useLoad<T>(loader: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const seq = useRef(0);
  const reload = useCallback(() => {
    const id = ++seq.current;
    setLoading(true);
    loader()
      .then((d) => {
        if (id === seq.current) {
          setData(d);
          setError("");
        }
      })
      .catch((e: Error) => id === seq.current && setError(e.message))
      .finally(() => id === seq.current && setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  useEffect(() => {
    reload();
  }, [reload]);
  return { data, error, loading, reload, setData };
}

/** Poll a background job until it finishes. Progress comes from the server as the work happens. */
export function useJob(jobId: string | null | undefined, onDone?: (job: Job) => void) {
  const [job, setJob] = useState<Job | null>(null);
  const done = useRef(onDone);
  done.current = onDone;
  useEffect(() => {
    if (!jobId) {
      setJob(null);
      return;
    }
    let stop = false;
    let timer: number;
    const tick = async () => {
      try {
        const j = await api.job(jobId);
        if (stop) return;
        setJob(j);
        if (j.status === "succeeded" || j.status === "failed") {
          done.current?.(j);
          return;
        }
      } catch {
        /* transient: keep polling */
      }
      if (!stop) timer = window.setTimeout(tick, 700);
    };
    tick();
    return () => {
      stop = true;
      window.clearTimeout(timer);
    };
  }, [jobId]);
  return job;
}

// ------------------------------------------------------------------ app context

interface Toast {
  id: number;
  kind: "success" | "error" | "info";
  message: string;
}

interface AppState {
  projects: Project[];
  projectsLoaded: boolean;
  activeId: number | null;
  active: Project | null;
  setActive: (id: number | null) => void;
  refreshProjects: () => Promise<void>;
  status: AppStatus | null;
  refreshStatus: () => void;
  toasts: Toast[];
  toast: (kind: Toast["kind"], message: string) => void;
  theme: "dark" | "light";
  setTheme: (t: "dark" | "light") => void;
}

const Ctx = createContext<AppState | null>(null);

export function useApp(): AppState {
  const v = useContext(Ctx);
  if (!v) throw new Error("AppProvider missing");
  return v;
}

export function AppProvider({ children }: { children: React.ReactNode }) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectsLoaded, setLoaded] = useState(false);
  const [activeId, setActiveId] = useState<number | null>(() => load<number | null>("activeProject", null));
  const [status, setStatus] = useState<AppStatus | null>(null);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [theme, setThemeState] = useState<"dark" | "light">(() =>
    load("theme", window.matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : "dark"),
  );

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  const refreshProjects = useCallback(async () => {
    try {
      const list = await api.projects();
      setProjects(list);
      setActiveId((cur) => (cur !== null && list.some((p) => p.id === cur) ? cur : list[0]?.id ?? null));
    } catch {
      /* the status bar shows connection problems */
    } finally {
      setLoaded(true);
    }
  }, []);

  const refreshStatus = useCallback(() => {
    api.status().then(setStatus).catch(() => setStatus(null));
  }, []);

  useEffect(() => {
    refreshProjects();
    refreshStatus();
    const t = window.setInterval(refreshStatus, 5000);
    return () => window.clearInterval(t);
  }, [refreshProjects, refreshStatus]);

  useEffect(() => {
    save("activeProject", activeId);
  }, [activeId]);

  const toast = useCallback((kind: Toast["kind"], message: string) => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, kind, message }]);
    window.setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), kind === "error" ? 8000 : 4000);
  }, []);

  const value: AppState = {
    projects,
    projectsLoaded,
    activeId,
    active: projects.find((p) => p.id === activeId) ?? null,
    setActive: setActiveId,
    refreshProjects,
    status,
    refreshStatus,
    toasts,
    toast,
    theme,
    setTheme: (t) => {
      setThemeState(t);
      save("theme", t);
    },
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

// ------------------------------------------------------------------ formatting

export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "never";
  const t = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z").getTime();
  const s = Math.round((Date.now() - t) / 1000);
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return new Date(t).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const t = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z");
  return t.toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

export const pct = (n: number) => `${Math.round(n * 100)}%`;

export const label = (s: string) => s.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());

export async function run<T>(toast: AppState["toast"], fn: () => Promise<T>, success?: string): Promise<T | undefined> {
  try {
    const r = await fn();
    if (success) toast("success", success);
    return r;
  } catch (e) {
    toast("error", (e as Error).message);
    return undefined;
  }
}

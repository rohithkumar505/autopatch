import { DiffEditor, Editor, type OnMount } from "@monaco-editor/react";
import { AlertTriangle, CheckCircle2, CircleDashed, Info, Loader2, ShieldCheck, ShieldQuestion, X, XCircle } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import type { Job, Severity, Validation, Verification } from "../api";
import { label, navigate, pct, useApp } from "../lib";

const cx = (...c: (string | false | null | undefined)[]) => c.filter(Boolean).join(" ");
export { cx };

// ------------------------------------------------------------------ basics

type BtnKind = "primary" | "secondary" | "danger" | "ghost" | "success";

export function Button({
  kind = "secondary", busy, icon, children, className, ...rest
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { kind?: BtnKind; busy?: boolean; icon?: ReactNode }) {
  const styles: Record<BtnKind, string> = {
    primary: "bg-accent text-accent-fg hover:brightness-110 border-transparent",
    success: "bg-emerald-600 text-white hover:bg-emerald-500 border-transparent",
    danger: "bg-red-600 text-white hover:bg-red-500 border-transparent",
    secondary: "bg-panel2 text-fg border-border hover:bg-hover",
    ghost: "bg-transparent text-fg border-transparent hover:bg-hover",
  };
  return (
    <button
      {...rest}
      disabled={rest.disabled || busy}
      className={cx(
        "inline-flex items-center justify-center gap-1.5 rounded-md border px-3 py-1.5 text-[13px] font-medium",
        "transition disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-accent",
        styles[kind], className,
      )}
    >
      {busy ? <Loader2 className="size-3.5 animate-spin" /> : icon}
      {children}
    </button>
  );
}

export function Card({ title, actions, children, className, padded = true }: {
  title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string; padded?: boolean;
}) {
  return (
    <section className={cx("rounded-lg border border-border bg-panel", className)}>
      {(title || actions) && (
        <header className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-2.5">
          <h3 className="text-[13px] font-semibold">{title}</h3>
          {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={padded ? "p-4" : ""}>{children}</div>
    </section>
  );
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        {subtitle && <p className="mt-0.5 text-muted">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function Spinner({ text }: { text?: string }) {
  return (
    <div className="flex items-center gap-2 p-4 text-muted">
      <Loader2 className="size-4 animate-spin" /> {text ?? "Loading..."}
    </div>
  );
}

export function Empty({ icon, title, children, action }: { icon?: ReactNode; title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-12 text-center">
      {icon && <div className="text-muted">{icon}</div>}
      <div className="text-[14px] font-semibold">{title}</div>
      {children && <div className="max-w-md text-muted">{children}</div>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

export function ErrorNote({ children }: { children: ReactNode }) {
  return (
    <div className="flex items-start gap-2 rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-red-700 dark:text-red-300">
      <XCircle className="mt-0.5 size-4 shrink-0" />
      <div className="min-w-0 break-words">{children}</div>
    </div>
  );
}

export function Notice({ kind = "info", children }: { kind?: "info" | "warning" | "success"; children: ReactNode }) {
  const style = {
    info: "border-sky-500/40 bg-sky-500/10 text-sky-800 dark:text-sky-200",
    warning: "border-amber-500/40 bg-amber-500/10 text-amber-800 dark:text-amber-200",
    success: "border-emerald-500/40 bg-emerald-500/10 text-emerald-800 dark:text-emerald-200",
  }[kind];
  const Icon = kind === "warning" ? AlertTriangle : kind === "success" ? CheckCircle2 : Info;
  return (
    <div className={cx("flex items-start gap-2 rounded-md border px-3 py-2", style)}>
      <Icon className="mt-0.5 size-4 shrink-0" />
      <div className="min-w-0">{children}</div>
    </div>
  );
}

export function Toggle({ checked, onChange, label: text, hint, disabled }: {
  checked: boolean; onChange: (v: boolean) => void; label: ReactNode; hint?: ReactNode; disabled?: boolean;
}) {
  return (
    <label className={cx("flex items-start gap-3", disabled ? "opacity-50" : "cursor-pointer")}>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={cx("relative mt-0.5 h-5 w-9 shrink-0 rounded-full transition", checked ? "bg-accent" : "bg-border")}
      >
        <span className={cx("absolute top-0.5 size-4 rounded-full bg-white shadow transition", checked ? "left-[18px]" : "left-0.5")} />
      </button>
      <span>
        <span className="block font-medium">{text}</span>
        {hint && <span className="block text-muted">{hint}</span>}
      </span>
    </label>
  );
}

export function Field({ label: text, hint, children }: { label: ReactNode; hint?: ReactNode; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block font-medium">{text}</span>
      {children}
      {hint && <span className="mt-1 block text-muted">{hint}</span>}
    </label>
  );
}

export const inputCls =
  "w-full rounded-md border border-border bg-panel2 px-2.5 py-1.5 text-[13px] text-fg placeholder:text-muted focus:border-accent focus:outline-none";

export function Tabs<T extends string>({ value, onChange, tabs }: { value: T; onChange: (v: T) => void; tabs: { id: T; label: ReactNode }[] }) {
  return (
    <div className="flex gap-1 overflow-x-auto border-b border-border" role="tablist">
      {tabs.map((t) => (
        <button
          key={t.id}
          role="tab"
          aria-selected={value === t.id}
          onClick={() => onChange(t.id)}
          className={cx(
            "whitespace-nowrap border-b-2 px-3 py-2 text-[13px]",
            value === t.id ? "border-accent font-medium text-fg" : "border-transparent text-muted hover:text-fg",
          )}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

export function Modal({ title, onClose, children, footer, wide }: { title: string; onClose: () => void; children: ReactNode; footer?: ReactNode; wide?: boolean }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onMouseDown={onClose}>
      <div
        role="dialog"
        aria-label={title}
        className={cx("max-h-[90vh] w-full overflow-auto rounded-lg border border-border bg-panel shadow-2xl", wide ? "max-w-3xl" : "max-w-lg")}
        onMouseDown={(e) => e.stopPropagation()}
      >
        <header className="flex items-center justify-between border-b border-border px-4 py-3">
          <h2 className="font-semibold">{title}</h2>
          <button onClick={onClose} className="rounded p-1 text-muted hover:bg-hover" aria-label="Close">
            <X className="size-4" />
          </button>
        </header>
        <div className="space-y-3 p-4">{children}</div>
        {footer && <footer className="flex flex-wrap justify-end gap-2 border-t border-border px-4 py-3">{footer}</footer>}
      </div>
    </div>
  );
}

export function KV({ items }: { items: [ReactNode, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1.5">
      {items.map(([k, v], i) => (
        <div key={i} className="contents">
          <dt className="text-muted">{k}</dt>
          <dd className="min-w-0 break-words">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

// ------------------------------------------------------------------ status badges

const pill = "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-semibold whitespace-nowrap";

export const severityStyle: Record<Severity, string> = {
  critical: "bg-red-500/15 text-red-700 dark:text-red-300 ring-1 ring-red-500/30",
  high: "bg-orange-500/15 text-orange-700 dark:text-orange-300 ring-1 ring-orange-500/30",
  medium: "bg-amber-500/15 text-amber-700 dark:text-amber-300 ring-1 ring-amber-500/30",
  low: "bg-slate-500/15 text-slate-700 dark:text-slate-300 ring-1 ring-slate-500/30",
};

export const severityBar: Record<Severity, string> = {
  critical: "bg-red-500", high: "bg-orange-500", medium: "bg-amber-500", low: "bg-slate-400",
};

export function SeverityBadge({ severity }: { severity: Severity }) {
  return <span className={cx(pill, severityStyle[severity])}>{label(severity)}</span>;
}

export function VerificationBadge({ v }: { v: Verification }) {
  if (v === "confirmed")
    return (
      <span className={cx(pill, "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300")} title="A test reproduced this bug, or the check is deterministic">
        <ShieldCheck className="size-3" /> Confirmed
      </span>
    );
  if (v === "not_reproduced")
    return (
      <span className={cx(pill, "bg-slate-500/15 text-slate-600 dark:text-slate-300")} title="Generated tests could not show this bug">
        <ShieldQuestion className="size-3" /> Not reproduced
      </span>
    );
  return (
    <span className={cx(pill, "bg-sky-500/15 text-sky-700 dark:text-sky-300")} title="Reported by analysis; not yet proven by a test">
      <CircleDashed className="size-3" /> Possible
    </span>
  );
}

export function ValidationBadge({ v }: { v: Validation }) {
  const map: Record<Validation, [string, string, string]> = {
    validated: ["Validated", "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300", "Bug reproduced before the fix and resolved after it, with no regressions"],
    partially_validated: ["Partially validated", "bg-amber-500/15 text-amber-700 dark:text-amber-300", "Tests pass with no regressions, but the bug was not reproduced first"],
    failed: ["Failed validation", "bg-red-500/15 text-red-700 dark:text-red-300", "Some tests fail with this patch"],
    not_run: ["Not validated", "bg-slate-500/15 text-slate-600 dark:text-slate-300", "No tests have been run"],
  };
  const [text, style, title] = map[v];
  return <span className={cx(pill, style)} title={title}>{text}</span>;
}

const statusStyles: Record<string, string> = {
  open: "bg-sky-500/15 text-sky-700 dark:text-sky-300",
  fixing: "bg-violet-500/15 text-violet-700 dark:text-violet-300",
  generating: "bg-violet-500/15 text-violet-700 dark:text-violet-300",
  patch_ready: "bg-indigo-500/15 text-indigo-700 dark:text-indigo-300",
  ready: "bg-indigo-500/15 text-indigo-700 dark:text-indigo-300",
  fixed: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  accepted: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  passed: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  patched_unverified: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  rejected: "bg-slate-500/15 text-slate-600 dark:text-slate-300",
  dismissed: "bg-slate-500/15 text-slate-600 dark:text-slate-300",
  reverted: "bg-slate-500/15 text-slate-600 dark:text-slate-300",
  skipped: "bg-slate-500/15 text-slate-600 dark:text-slate-300",
  failed: "bg-red-500/15 text-red-700 dark:text-red-300",
  error: "bg-red-500/15 text-red-700 dark:text-red-300",
  crashed: "bg-red-500/15 text-red-700 dark:text-red-300",
  timeout: "bg-red-500/15 text-red-700 dark:text-red-300",
  no_tests: "bg-slate-500/15 text-slate-600 dark:text-slate-300",
  running: "bg-violet-500/15 text-violet-700 dark:text-violet-300",
  queued: "bg-slate-500/15 text-slate-600 dark:text-slate-300",
  completed: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
};

const statusText: Record<string, string> = {
  patch_ready: "Patch ready", ready: "Awaiting review", patched_unverified: "Patched (unverified)",
  no_tests: "No tests", generating: "Generating",
};

export function StatusBadge({ status }: { status: string }) {
  return <span className={cx(pill, statusStyles[status] ?? statusStyles.queued)}>{statusText[status] ?? label(status)}</span>;
}

export function TestStatusIcon({ status }: { status: string }) {
  if (status === "passed") return <CheckCircle2 className="size-4 shrink-0 text-emerald-500" aria-label="passed" />;
  if (status === "skipped" || status === "not run") return <CircleDashed className="size-4 shrink-0 text-slate-400" aria-label={status} />;
  return <XCircle className="size-4 shrink-0 text-red-500" aria-label={status} />;
}

export function Confidence({ value }: { value: number }) {
  const color = value >= 0.85 ? "bg-emerald-500" : value >= 0.6 ? "bg-amber-500" : "bg-slate-400";
  return (
    <span className="inline-flex items-center gap-1.5" title={`Confidence ${pct(value)}`}>
      <span className="h-1.5 w-12 overflow-hidden rounded-full bg-border">
        <span className={cx("block h-full rounded-full", color)} style={{ width: pct(value) }} />
      </span>
      <span className="tabular-nums text-muted">{pct(value)}</span>
    </span>
  );
}

export function SourceChips({ sources }: { sources: string[] }) {
  return (
    <span className="inline-flex flex-wrap gap-1">
      {sources.map((s) => (
        <span key={s} className="rounded bg-panel2 px-1.5 py-0.5 font-mono text-[11px] text-muted ring-1 ring-border" title={s === "ai" ? "Found by AI review" : s.startsWith("core:") ? "Found and checked by AutoPatch Core" : s.startsWith("fileintel:") ? "Found by file intelligence" : `Reported by ${s}`}>
          {s === "ai" ? "AI" : s.startsWith("core:") ? "Core · " + s.slice(5) : s.startsWith("fileintel:") ? "File · " + s.slice(10) : s}
        </span>
      ))}
    </span>
  );
}

// ------------------------------------------------------------------ job progress (real server-side progress)

export function JobLog({ job, title }: { job: Job | null; title?: string }) {
  const end = useRef<HTMLDivElement>(null);
  // Block body on purpose: newer browsers return a Promise from scrollIntoView, and an effect
  // must not return anything except a cleanup function.
  useEffect(() => {
    end.current?.scrollIntoView({ block: "nearest" });
  }, [job?.progress.length]);
  if (!job) return null;
  const running = job.status === "queued" || job.status === "running";
  return (
    <div className="rounded-md border border-border bg-panel2">
      <div className="flex items-center justify-between border-b border-border px-3 py-1.5">
        <span className="flex items-center gap-2 font-medium">
          {running ? <Loader2 className="size-3.5 animate-spin text-accent" /> : job.status === "succeeded" ? <CheckCircle2 className="size-3.5 text-emerald-500" /> : <XCircle className="size-3.5 text-red-500" />}
          {title ?? label(job.kind)} — {running ? (job.status === "queued" ? "queued" : "running") : job.status}
        </span>
      </div>
      <div className="max-h-56 overflow-auto px-3 py-2 font-mono text-[12px] leading-5">
        {job.progress.length === 0 && <div className="text-muted">Waiting to start...</div>}
        {job.progress.map((p, i) => (
          <div key={i} className={cx(p.level === "error" ? "text-red-600 dark:text-red-400" : p.level === "warning" ? "text-amber-700 dark:text-amber-300" : "")}>
            <span className="text-muted">{new Date(p.time).toLocaleTimeString()}</span> {p.message}
          </div>
        ))}
        <div ref={end} />
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ code views (Monaco)

export interface Highlight {
  start: number;
  end: number;
  severity: Severity;
  message?: string;
}

export function CodeView({
  value, language = "python", height = 300, firstLine = 1, highlights = [], reveal, onReady,
}: {
  value: string; language?: string; height?: number | string; firstLine?: number; highlights?: Highlight[];
  reveal?: { line: number }; onReady?: (editor: Parameters<OnMount>[0]) => void;
}) {
  const { theme } = useApp();
  const editorRef = useRef<Parameters<OnMount>[0] | null>(null);
  const decorations = useRef<string[]>([]);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    const ed = editorRef.current;
    if (!ed || !mounted) return;
    decorations.current = ed.deltaDecorations(
      decorations.current,
      highlights.map((h) => ({
        range: { startLineNumber: h.start - firstLine + 1, startColumn: 1, endLineNumber: Math.max(h.start, h.end) - firstLine + 1, endColumn: 1 },
        options: {
          isWholeLine: true,
          className: `ap-line-${h.severity}`,
          glyphMarginClassName: `ap-glyph-${h.severity}`,
          hoverMessage: h.message ? { value: h.message } : undefined,
        },
      })),
    );
  }, [highlights, firstLine, mounted, value]);

  useEffect(() => {
    if (mounted && reveal && editorRef.current) {
      const target = reveal.line - firstLine + 1;
      editorRef.current.revealLineInCenter(target);
      editorRef.current.setPosition({ lineNumber: target, column: 1 });
    }
  }, [reveal, mounted, firstLine, value]);

  return (
    <div className={cx("overflow-hidden rounded-md border border-border", height === "100%" && "h-full")}>
      <Editor
        height={height}
        value={value}
        language={language}
        theme={theme === "dark" ? "vs-dark" : "vs"}
        loading={<Spinner text="Loading editor..." />}
        onMount={(ed) => {
          editorRef.current = ed;
          setMounted(true);
          onReady?.(ed);
        }}
        options={{
          readOnly: true,
          domReadOnly: true,
          minimap: { enabled: false },
          glyphMargin: highlights.length > 0,
          scrollBeyondLastLine: false,
          fontSize: 13,
          fontFamily: "'Cascadia Code', 'JetBrains Mono', Consolas, monospace",
          lineNumbers: (n: number) => String(n + firstLine - 1),
          renderLineHighlight: "none",
          automaticLayout: true,
          wordWrap: "off",
        }}
      />
    </div>
  );
}

export function DiffView({ original, modified, language = "python", height = 420, sideBySide = true }: {
  original: string; modified: string; language?: string; height?: number | string; sideBySide?: boolean;
}) {
  const { theme } = useApp();
  return (
    <div className="overflow-hidden rounded-md border border-border">
      <DiffEditor
        height={height}
        original={original}
        modified={modified}
        language={language}
        theme={theme === "dark" ? "vs-dark" : "vs"}
        loading={<Spinner text="Loading diff..." />}
        options={{
          readOnly: true,
          originalEditable: false,
          renderSideBySide: sideBySide,
          useInlineViewWhenSpaceIsLimited: true,
          minimap: { enabled: false },
          scrollBeyondLastLine: false,
          fontSize: 13,
          automaticLayout: true,
          hideUnchangedRegions: { enabled: true, contextLineCount: 4, minimumLineCount: 6 },
        }}
      />
    </div>
  );
}

/** Coloured rendering of a unified diff (for history views). */
export function UnifiedDiff({ diff }: { diff: string }) {
  if (!diff.trim()) return <div className="p-3 text-muted">No file changes in this version.</div>;
  return (
    <pre className="max-h-[480px] overflow-auto rounded-md border border-border bg-panel2 p-3 font-mono text-[12px] leading-5">
      {diff.split("\n").map((line, i) => (
        <div
          key={i}
          className={cx(
            line.startsWith("+") && !line.startsWith("+++") && "bg-emerald-500/15 text-emerald-800 dark:text-emerald-300",
            line.startsWith("-") && !line.startsWith("---") && "bg-red-500/15 text-red-800 dark:text-red-300",
            line.startsWith("@@") && "text-sky-700 dark:text-sky-300",
            (line.startsWith("diff ") || line.startsWith("+++") || line.startsWith("---") || line.startsWith("index ")) && "font-semibold text-muted",
          )}
        >
          {line || " "}
        </div>
      ))}
    </pre>
  );
}

export function NeedProject({ children }: { children: ReactNode }) {
  const { active, projectsLoaded } = useApp();
  if (!projectsLoaded) return <Spinner />;
  if (!active)
    return (
      <Empty title="No project open" action={<Button kind="primary" onClick={() => navigate("projects")}>Import a project</Button>}>
        Upload source files, a folder, a zip archive or a Git repository to get started.
      </Empty>
    );
  return <>{children}</>;
}

import { FileQuestion, FileSearch, Search, Wrench } from "lucide-react";
import { useMemo, useState } from "react";
import { api, fileApi, type FileReportRow } from "../api";
import { Button, Card, Empty, ErrorNote, NeedProject, Notice, PageHeader, SeverityBadge, Spinner, StatusBadge, cx, inputCls } from "../components/ui";
import { label, navigate, run, useApp, useLoad } from "../lib";

const REPAIR_STYLE: Record<string, string> = {
  fixable: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  partially_fixable: "bg-lime-500/15 text-lime-700 dark:text-lime-300",
  requires_human: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  not_fixable: "bg-red-500/15 text-red-700 dark:text-red-300",
  unsupported: "bg-slate-500/15 text-slate-600 dark:text-slate-300",
};
const REPAIR_TEXT: Record<string, string> = {
  fixable: "Fixable", partially_fixable: "Partially fixable", requires_human: "Requires human intervention",
  not_fixable: "Not fixable", unsupported: "Unsupported / cannot determine",
};
const STATUS_TEXT: Record<string, string> = {
  analyzed: "Analyzed", partially_analyzed: "Partially analyzed", identified_only: "Identified only",
  skipped: "Not analyzed (over limit)", unreadable: "Unreadable",
};

export function Repairability({ value }: { value: string }) {
  if (!value) return null;
  return <span className={cx("inline-flex rounded-full px-2 py-0.5 text-[11px] font-semibold whitespace-nowrap", REPAIR_STYLE[value])}>{REPAIR_TEXT[value] ?? value}</span>;
}

const size = (n: number) => (n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`);

export default function FileReports() {
  return (
    <NeedProject>
      <Inner />
    </NeedProject>
  );
}

function Inner() {
  const { active } = useApp();
  const pid = active!.id;
  const data = useLoad(() => fileApi.reports(pid), [pid, active?.last_scan?.id]);
  const [filter, setFilter] = useState<"issues" | "all" | "not_full">("issues");
  const [text, setText] = useState("");
  const [selected, setSelected] = useState<string | null>(null);

  const files = useMemo(() => (data.data?.files ?? []).filter((f) => {
    if (filter === "issues" && !f.issues.length && !f.code_findings.length) return false;
    if (filter === "not_full" && f.status === "analyzed") return false;
    return !text || `${f.path} ${f.format} ${f.category}`.toLowerCase().includes(text.toLowerCase());
  }), [data.data, filter, text]);
  const current = data.data?.files.find((f) => f.path === selected) ?? files[0];

  if (data.error) return <ErrorNote>{data.error}</ErrorNote>;
  if (!data.data) return <Spinner />;
  const { summary, import_report: imp, scan } = data.data;

  return (
    <>
      <PageHeader title="File Reports" subtitle="Every file identified by its content, inspected with a format-specific check, and assessed for repairability." />
      {!scan ? (
        <Card><Empty icon={<FileSearch className="size-8" />} title="No scan yet" action={<Button kind="primary" onClick={() => navigate("scanner")}>Open Bug Scanner</Button>}>Run a scan to build a report for every file.</Empty></Card>
      ) : (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-6">
            <Mini label="Files" value={summary.files} />
            <Mini label="With issues" value={summary.with_issues} />
            <Mini label="Fully analyzed" value={summary.by_status.analyzed ?? 0} />
            <Mini label="Partially analyzed" value={summary.by_status.partially_analyzed ?? 0} />
            <Mini label="Identified only" value={(summary.by_status.identified_only ?? 0) + (summary.by_status.skipped ?? 0)} />
            <Mini label="Not imported" value={(imp?.skipped ?? []).reduce((n, s) => n + s.files, 0)} />
          </div>
          <div className="flex flex-wrap gap-2">
            {Object.entries(summary.by_repairability).filter(([, n]) => n).map(([k, n]) => (
              <span key={k} className="flex items-center gap-1.5"><Repairability value={k} /><b className="tabular-nums">{n}</b></span>
            ))}
            {Object.entries(summary.by_category).map(([k, n]) => (
              <span key={k} className="rounded-full bg-panel2 px-2 py-0.5 text-[12px] ring-1 ring-border">{label(k)} {n}</span>
            ))}
          </div>
          {!!imp?.skipped?.length && (
            <Card title={`Not imported (${imp.skipped.length})`}>
              <ul className="max-h-48 space-y-1 overflow-auto text-[12px]">
                {imp.skipped.map((s) => (
                  <li key={s.path}><span className="font-mono">{s.path}</span> <span className="text-muted">({s.files.toLocaleString()} file{s.files === 1 ? "" : "s"}): {s.reason}</span></li>
                ))}
              </ul>
            </Card>
          )}
          <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
            <Card padded={false} title={`Files (${files.length})`} actions={<>
              <div className="relative"><Search className="absolute left-2 top-2 size-3.5 text-muted" /><input className={inputCls + " w-40 pl-7"} placeholder="Filter" value={text} onChange={(e) => setText(e.target.value)} /></div>
              <select value={filter} onChange={(e) => setFilter(e.target.value as typeof filter)} className="rounded-md border border-border bg-panel2 px-2 py-1.5" aria-label="Show">
                <option value="issues">With issues</option><option value="not_full">Not fully analyzed</option><option value="all">All files</option>
              </select>
            </>}>
              {files.length === 0 ? <Empty title="Nothing matches" /> : (
                <ul className="max-h-[64vh] divide-y divide-border overflow-auto">
                  {files.map((f) => (
                    <li key={f.path}>
                      <button onClick={() => setSelected(f.path)} className={cx("w-full px-3 py-2 text-left hover:bg-hover", current?.path === f.path && "bg-accent/10")}>
                        <div className="flex items-center gap-2">
                          <span className="min-w-0 flex-1 truncate font-mono text-[12px]">{f.path}</span>
                          {f.issues.length + f.code_findings.length > 0 && <span className="rounded-full bg-orange-500/20 px-1.5 text-[11px] font-semibold text-orange-700 dark:text-orange-300">{f.issues.length + f.code_findings.length}</span>}
                        </div>
                        <div className="mt-0.5 flex flex-wrap items-center gap-2 text-[11px] text-muted">
                          <span>{f.format}</span><span>{size(f.size)}</span><span>{STATUS_TEXT[f.status]}</span>
                          {f.repairability && <Repairability value={f.repairability} />}
                        </div>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
            {current ? <Detail key={current.path} f={current} reload={data.reload} /> : <Card><Empty icon={<FileQuestion className="size-8" />} title="Select a file" /></Card>}
          </div>
        </div>
      )}
    </>
  );
}

function Detail({ f, reload }: { f: FileReportRow; reload: () => void }) {
  const { toast } = useApp();
  return (
    <div className="space-y-4">
      <Card title={<span className="font-mono">{f.path}</span>} actions={f.category !== "binary" && f.category !== "executable" ?
        <Button onClick={() => navigate(`ide?file=${encodeURIComponent(f.path)}`)}>Open in IDE</Button> : undefined}>
        <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1.5">
          <dt className="text-muted">Detected format</dt><dd>{f.format} <span className="text-muted">({f.mime})</span></dd>
          <dt className="text-muted">Category</dt><dd>{label(f.category)}{f.language ? ` · ${f.language}` : ""}</dd>
          <dt className="text-muted">Size</dt><dd>{size(f.size)} ({f.size.toLocaleString()} bytes)</dd>
          {f.encoding && <><dt className="text-muted">Encoding</dt><dd>{f.encoding}</dd></>}
          <dt className="text-muted">Analysis</dt><dd>{STATUS_TEXT[f.status]} · {f.method}{f.reused ? " · unchanged since last scan" : ""}</dd>
          {f.extension_mismatch && <><dt className="text-muted">Identification</dt><dd className="text-amber-700 dark:text-amber-300">{f.extension_mismatch}</dd></>}
        </dl>
        {f.notes.map((n) => <div key={n} className="mt-3"><Notice>{n}</Notice></div>)}
        {f.issues.length === 0 && f.code_findings.length === 0 && f.status === "analyzed" && (
          <div className="mt-3"><Notice kind="success">No problems detected by {f.method}.</Notice></div>
        )}
      </Card>
      {f.issues.map((i, n) => (
        <Card key={n} title={<span className="flex flex-wrap items-center gap-2"><SeverityBadge severity={(i.severity === "info" ? "low" : i.severity) as "low"} />{i.title}{i.line ? <span className="text-[12px] font-normal text-muted">line {i.line}</span> : null}</span>}
          actions={<Repairability value={i.repairability} />}>
          <p>{i.message}</p>
          <dl className="mt-3 grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1.5 text-[13px]">
            <dt className="text-muted">Why this classification</dt><dd>{i.reason}</dd>
            <dt className="text-muted">Repair options</dt><dd>{i.options.join("; ") || "—"}</dd>
            {i.limitations && <><dt className="text-muted">Limitations</dt><dd>{i.limitations}</dd></>}
            {i.requirements && <><dt className="text-muted">Needs</dt><dd>{i.requirements}</dd></>}
            <dt className="text-muted">Confidence</dt><dd>{Math.round(i.confidence * 100)}%</dd>
          </dl>
          {i.finding_id && (
            <div className="mt-3 flex flex-wrap gap-2">
              {i.repair ? (
                <Button kind="primary" icon={<Wrench className="size-3.5" />} onClick={async () => {
                  const r = await run(toast, () => api.fix(i.finding_id!));
                  if (r) { reload(); navigate(`autofix/${r.patch_id}`); }
                }}>Generate repair</Button>
              ) : null}
              <Button onClick={() => navigate(`analysis/${i.finding_id}`)}>Open report</Button>
            </div>
          )}
        </Card>
      ))}
      {f.code_findings.length > 0 && (
        <Card title="Code issues (Bug Scanner)" padded={false}>
          <ul className="divide-y divide-border">
            {f.code_findings.map((c) => (
              <li key={c.id}><button className="flex w-full flex-wrap items-center gap-2 px-4 py-2 text-left hover:bg-hover" onClick={() => navigate(`analysis/${c.id}`)}>
                <SeverityBadge severity={c.severity} /><span className="flex-1">{c.title}</span><StatusBadge status={c.status} />
              </button></li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}

function Mini({ label: text, value }: { label: string; value: number }) {
  return (
    <div className="rounded-lg border border-border bg-panel p-3">
      <div className="text-[12px] text-muted">{text}</div>
      <div className="text-xl font-semibold tabular-nums">{value.toLocaleString()}</div>
    </div>
  );
}

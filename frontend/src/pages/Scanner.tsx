import { Bug, Play, Search } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api, type Finding, type Scan, type Severity } from "../api";
import {
  Button, Card, Confidence, Empty, ErrorNote, NeedProject, Notice, PageHeader, SeverityBadge, SourceChips, Spinner,
  StatusBadge, Toggle, VerificationBadge, JobLog, inputCls,
} from "../components/ui";
import { dateTime, label, load, navigate, save, useApp, useJob, useLoad } from "../lib";

export default function Scanner() {
  return (
    <NeedProject>
      <ScannerInner />
    </NeedProject>
  );
}

function ScannerInner() {
  const { active, status, refreshProjects, toast } = useApp();
  const pid = active!.id;
  const settings = useLoad(() => api.settings(), []);
  const [opts, setOpts] = useState<{ ruff: boolean; pylint: boolean; ai: boolean } | null>(null);
  const [jobId, setJobId] = useState<string | null>(() => load(`scanJob.${pid}`, null));
  const scans = useLoad(() => api.scans(pid), [pid]);
  const [scanId, setScanId] = useState<number | undefined>();
  const findings = useLoad(() => api.findings(pid, scanId), [pid, scanId]);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (settings.data && !opts)
      setOpts({ ruff: settings.data["analysis.ruff"], pylint: settings.data["analysis.pylint"], ai: settings.data["analysis.ai_scan"] });
  }, [settings.data, opts]);

  const job = useJob(jobId, (j) => {
    save(`scanJob.${pid}`, null);
    if (j.status === "succeeded") toast("success", "Scan complete");
    scans.reload();
    setScanId(undefined);
    findings.reload();
    refreshProjects();
  });

  const start = async () => {
    if (!opts) return;
    setBusy(true);
    try {
      const r = await api.startScan(pid, { ...opts, ai: opts.ai && !!status?.ai.configured });
      setJobId(r.job_id);
      save(`scanJob.${pid}`, r.job_id);
    } catch (e) {
      toast("error", (e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const running = job && (job.status === "queued" || job.status === "running");
  const scan = findings.data?.scan;

  return (
    <>
      <PageHeader title="Bug Scanner" subtitle={`Scan ${active!.name} for crashes, wrong results, edge-case failures and security weaknesses.`} />
      <div className="grid gap-4 xl:grid-cols-[340px_1fr]">
        <div className="space-y-4">
          <Card title="Scan options">
            {!opts ? <Spinner /> : (
              <div className="space-y-3">
                <Toggle checked={opts.ruff} onChange={(v) => setOpts({ ...opts, ruff: v })} label="Ruff" hint="Undefined names, bug patterns, Bandit security rules" />
                <Toggle checked={opts.pylint} onChange={(v) => setOpts({ ...opts, pylint: v })} label="Pylint" hint="Wrong call signatures, unsafe constructs" />
                <Toggle
                  checked={opts.ai && !!status?.ai.configured}
                  disabled={!status?.ai.configured}
                  onChange={(v) => setOpts({ ...opts, ai: v })}
                  label="Deep analysis"
                  hint={status?.ai.configured ? `Logic, security and edge-case bugs, each proven by running the code, using ${status.ai.model}` : "Choose an engine in Settings to enable"}
                />
                <p className="text-[12px] text-muted">Every file is also identified by its content and checked by format (JSON, YAML, images, archives, databases and more). Syntax, structure and the call graph are always analyzed.</p>
                <Button kind="primary" className="w-full" icon={<Play className="size-3.5" />} busy={busy || !!running} onClick={start}>
                  {running ? "Scanning..." : "Start scan"}
                </Button>
              </div>
            )}
          </Card>
          {job && <JobLog job={job} title="Scan" />}
          <Card title="Scan history" padded={false}>
            {!scans.data ? <Spinner /> : scans.data.length === 0 ? (
              <p className="p-4 text-muted">No scans yet.</p>
            ) : (
              <ul className="max-h-72 divide-y divide-border overflow-auto">
                {scans.data.map((s) => (
                  <li key={s.id}>
                    <button
                      disabled={s.status !== "completed"}
                      onClick={() => setScanId(s.id)}
                      className={"flex w-full items-center gap-2 px-4 py-2 text-left hover:bg-hover disabled:cursor-default " + (scan?.id === s.id ? "bg-accent/10" : "")}
                    >
                      <span className="font-medium">#{s.id}</span>
                      <StatusBadge status={s.status} />
                      <span className="text-[12px] text-muted">{dateTime(s.started_at)}</span>
                      <span className="ml-auto font-mono text-[11px] text-muted">{s.version.slice(0, 8)}</span>
                    </button>
                    {s.status === "failed" && <div className="px-4 pb-2 text-[12px] text-red-500">{s.error}</div>}
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>

        <div className="min-w-0 space-y-4">
          {findings.error && <ErrorNote>{findings.error}</ErrorNote>}
          {scan && <ScanSummary scan={scan} />}
          {findings.data && !scan ? (
            <Card><Empty icon={<Bug className="size-8" />} title="Not scanned yet">Choose the scan options and start a scan.</Empty></Card>
          ) : !findings.data ? <Spinner /> : (
            <FindingsTable findings={findings.data.findings} />
          )}
        </div>
      </div>
    </>
  );
}

function ScanSummary({ scan }: { scan: Scan }) {
  const st = scan.stats;
  const usage = st.ai?.usage;
  return (
    <Card title={`Scan #${scan.id} · ${dateTime(scan.finished_at || scan.started_at)}`}>
      <p className="mb-3">{scan.summary}</p>
      <div className="flex flex-wrap gap-x-6 gap-y-1 text-[12px] text-muted">
        <span>{st.analyzed_files ?? 0} Python files analyzed of {st.files ?? 0}</span>
        <span>{st.symbols ?? 0} functions and classes</span>
        {Object.entries(st.tools ?? {}).map(([t, v]) => (
          <span key={t}>{t}: {v.status === "ok" ? `${v.issues} issue(s)` : v.error ? `failed (${v.error.slice(0, 80)})` : v.status}</span>
        ))}
        <span>
          Deep analysis: {st.ai?.used ? `${st.ai.findings} finding(s) from ${st.ai.model}` : st.ai?.reason ? `skipped (${st.ai.reason})` : "off"}
          {usage && ` · ${(usage.input_tokens + usage.cache_read_tokens + usage.cache_write_tokens).toLocaleString()} input / ${usage.output_tokens.toLocaleString()} output tokens`}
        </span>
        <span className="font-mono">version {scan.version.slice(0, 8)}</span>
      </div>
      {!!st.unsupported?.length && (
        <div className="mt-3"><Notice kind="warning">Also found {st.unsupported.join(", ")} files. They are not analyzed yet; AutoPatch currently analyzes Python.</Notice></div>
      )}
    </Card>
  );
}

const SEVERITIES: Severity[] = ["critical", "high", "medium", "low"];

function FindingsTable({ findings }: { findings: Finding[] }) {
  const [sev, setSev] = useState<Set<Severity>>(new Set(SEVERITIES));
  const [status, setStatus] = useState<"active" | "all" | "resolved">("active");
  const [text, setText] = useState("");
  const shown = useMemo(
    () =>
      findings.filter((f) => {
        if (!sev.has(f.severity)) return false;
        const resolved = f.status === "fixed" || f.status === "dismissed" || f.status === "rejected";
        if (status === "active" && resolved) return false;
        if (status === "resolved" && !resolved) return false;
        const t = text.toLowerCase();
        return !t || `${f.title} ${f.file} ${f.function} ${f.category}`.toLowerCase().includes(t);
      }),
    [findings, sev, status, text],
  );

  return (
    <Card
      title={`Findings (${shown.length} of ${findings.length})`}
      padded={false}
      actions={
        <>
          <div className="relative">
            <Search className="absolute left-2 top-2 size-3.5 text-muted" />
            <input className={inputCls + " w-44 pl-7"} placeholder="Filter" value={text} onChange={(e) => setText(e.target.value)} />
          </div>
          <select value={status} onChange={(e) => setStatus(e.target.value as typeof status)} className="rounded-md border border-border bg-panel2 px-2 py-1.5" aria-label="Status filter">
            <option value="active">Active</option>
            <option value="resolved">Resolved</option>
            <option value="all">All</option>
          </select>
        </>
      }
    >
      <div className="flex flex-wrap gap-1.5 border-b border-border px-4 py-2">
        {SEVERITIES.map((s) => {
          const n = findings.filter((f) => f.severity === s).length;
          const on = sev.has(s);
          return (
            <button
              key={s}
              aria-pressed={on}
              onClick={() => { const next = new Set(sev); if (on) next.delete(s); else next.add(s); setSev(next); }}
              className={"rounded-full px-2.5 py-0.5 text-[12px] ring-1 " + (on ? "bg-accent/15 ring-accent/40" : "text-muted ring-border")}
            >
              {label(s)} {n}
            </button>
          );
        })}
      </div>
      {shown.length === 0 ? (
        <Empty title={findings.length ? "No findings match the filters" : "No issues found"}>
          {findings.length ? undefined : "The enabled checks found nothing. Enable the AI review to look for logic bugs."}
        </Empty>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[820px] text-left">
            <thead className="text-[12px] text-muted">
              <tr className="border-b border-border">
                <th className="px-3 py-2 font-medium">#</th>
                <th className="px-3 py-2 font-medium">Severity</th>
                <th className="px-3 py-2 font-medium">Issue</th>
                <th className="px-3 py-2 font-medium">Location</th>
                <th className="px-3 py-2 font-medium">Confidence</th>
                <th className="px-3 py-2 font-medium">Evidence</th>
                <th className="px-3 py-2 font-medium">Status</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((f, i) => (
                <tr key={f.id} onClick={() => navigate(`analysis/${f.id}`)} className="cursor-pointer border-b border-border last:border-0 hover:bg-hover">
                  <td className="px-3 py-2 tabular-nums text-muted" title={`Priority score ${f.priority}`}>{i + 1}</td>
                  <td className="px-3 py-2"><SeverityBadge severity={f.severity} /></td>
                  <td className="px-3 py-2">
                    <div className="font-medium">{f.title}</div>
                    <div className="text-[12px] text-muted">{label(f.category)}</div>
                  </td>
                  <td className="px-3 py-2 font-mono text-[12px]">
                    <div>{f.file}:{f.line_start}</div>
                    <div className="text-muted">{f.function}</div>
                  </td>
                  <td className="px-3 py-2"><Confidence value={f.confidence} /></td>
                  <td className="space-y-1 px-3 py-2"><VerificationBadge v={f.verification} /><div><SourceChips sources={f.sources} /></div></td>
                  <td className="px-3 py-2"><StatusBadge status={f.status} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

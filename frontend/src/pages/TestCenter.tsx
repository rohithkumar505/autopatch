import { FlaskConical, Play, ShieldAlert, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { api, type TestRun } from "../api";
import { Button, Card, Empty, ErrorNote, JobLog, NeedProject, Notice, PageHeader, Spinner, StatusBadge, TestStatusIcon, cx } from "../components/ui";
import { dateTime, label, load, navigate, save, useApp, useJob, useLoad } from "../lib";

const KIND_LABEL: Record<string, string> = {
  project: "Project tests",
  original: "Before fix (original code)",
  patched: "After fix (patched code)",
};

export default function TestCenter() {
  return (
    <NeedProject>
      <TestCenterInner />
    </NeedProject>
  );
}

function TestCenterInner() {
  const { active, toast } = useApp();
  const pid = active!.id;
  const runs = useLoad(() => api.testRuns(pid), [pid]);
  const versions = useLoad(() => api.versions(pid), [pid]);
  const sandbox = useLoad(() => api.sandbox(), []);
  const [version, setVersion] = useState("");
  const [selected, setSelected] = useState<number | null>(null);
  const [jobId, setJobId] = useState<string | null>(() => load(`testJob.${pid}`, null));
  const job = useJob(jobId, (j) => {
    save(`testJob.${pid}`, null);
    runs.reload();
    const id = j.result?.test_run_id as number | undefined;
    if (id) setSelected(id);
  });
  const running = job && (job.status === "queued" || job.status === "running");
  const activeBackend = sandbox.data?.backends.find((b) => b.name === sandbox.data?.active);
  const shownId = selected ?? runs.data?.[0]?.id ?? null;

  return (
    <>
      <PageHeader title="Test Center" subtitle="Run the project's tests in the isolated sandbox and review every test run, including the before/after runs behind each patch." />
      <div className="grid gap-4 xl:grid-cols-[360px_1fr]">
        <div className="space-y-4">
          <Card title="Run project tests">
            <div className="space-y-3">
              <label className="block">
                <span className="mb-1 block font-medium">Version</span>
                <select value={version} onChange={(e) => setVersion(e.target.value)} className="w-full rounded-md border border-border bg-panel2 px-2 py-1.5">
                  <option value="">Current version</option>
                  {(versions.data ?? []).map((v) => <option key={v.sha} value={v.sha}>{v.short} · {v.message.slice(0, 50)}</option>)}
                </select>
              </label>
              <Button kind="primary" className="w-full" icon={<Play className="size-3.5" />} busy={!!running} disabled={!sandbox.data?.active}
                onClick={async () => {
                  try {
                    const r = await api.runTests(pid, version || undefined);
                    setJobId(r.job_id);
                    save(`testJob.${pid}`, r.job_id);
                  } catch (e) { toast("error", (e as Error).message); }
                }}>
                {running ? "Running tests..." : "Run tests"}
              </Button>
            </div>
          </Card>
          {job && <JobLog job={job} title="Test run" />}
          <Card title="Sandbox">
            {!sandbox.data ? <Spinner /> : activeBackend ? (
              <div className="space-y-2">
                <div className="flex items-center gap-2 font-medium">
                  {activeBackend.isolation === "strong" ? <ShieldCheck className="size-4 text-emerald-500" /> : <ShieldAlert className="size-4 text-amber-500" />}
                  {activeBackend.label} <span className="text-[12px] text-muted">({activeBackend.isolation} isolation)</span>
                </div>
                <ul className="list-disc space-y-1 pl-5 text-[12px] text-muted">
                  {activeBackend.guarantees.map((g) => <li key={g}>{g}</li>)}
                </ul>
              </div>
            ) : (
              <Notice kind="warning">{sandbox.data.error} <button className="underline" onClick={() => navigate("settings")}>Open Settings</button></Notice>
            )}
          </Card>
        </div>

        <div className="min-w-0 space-y-4">
          <Card title="Test runs" padded={false}>
            {runs.error ? <ErrorNote>{runs.error}</ErrorNote> : !runs.data ? <Spinner /> : runs.data.length === 0 ? (
              <Empty icon={<FlaskConical className="size-8" />} title="No test runs yet">Run the project's tests, or generate a fix (which runs tests before and after).</Empty>
            ) : (
              <div className="max-h-[360px] overflow-auto">
                <table className="w-full min-w-[680px] text-left">
                  <thead className="sticky top-0 bg-panel text-[12px] text-muted">
                    <tr className="border-b border-border">
                      <th className="px-3 py-2 font-medium">When</th>
                      <th className="px-3 py-2 font-medium">Run</th>
                      <th className="px-3 py-2 font-medium">Status</th>
                      <th className="px-3 py-2 font-medium">Passed</th>
                      <th className="px-3 py-2 font-medium">Failed</th>
                      <th className="px-3 py-2 font-medium">Errors</th>
                      <th className="px-3 py-2 font-medium">Time</th>
                      <th className="px-3 py-2 font-medium">Version</th>
                    </tr>
                  </thead>
                  <tbody>
                    {runs.data.map((r) => (
                      <tr key={r.id} onClick={() => setSelected(r.id)} className={cx("cursor-pointer border-b border-border last:border-0 hover:bg-hover", r.id === shownId && "bg-accent/10")}>
                        <td className="px-3 py-2 text-muted">{dateTime(r.created_at)}</td>
                        <td className="px-3 py-2">{KIND_LABEL[r.kind] ?? r.kind}{r.patch_id && <span className="text-muted"> · patch #{r.patch_id}</span>}</td>
                        <td className="px-3 py-2"><StatusBadge status={r.status} /></td>
                        <td className="px-3 py-2 tabular-nums text-emerald-600 dark:text-emerald-400">{r.passed}</td>
                        <td className="px-3 py-2 tabular-nums text-red-600 dark:text-red-400">{r.failed}</td>
                        <td className="px-3 py-2 tabular-nums">{r.errors}</td>
                        <td className="px-3 py-2 tabular-nums text-muted">{r.duration_s.toFixed(1)}s</td>
                        <td className="px-3 py-2 font-mono text-[12px] text-muted">{r.version.slice(0, 8)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
          {shownId && <RunDetail key={shownId} id={shownId} />}
        </div>
      </div>
    </>
  );
}

function RunDetail({ id }: { id: number }) {
  const { data: r, error } = useLoad(() => api.testRun(id), [id]);
  const [onlyFailing, setOnlyFailing] = useState(false);
  const [showOutput, setShowOutput] = useState(false);
  if (error) return <ErrorNote>{error}</ErrorNote>;
  if (!r) return <Spinner />;
  const tests = (r.results ?? []).filter((t) => !onlyFailing || t.status !== "passed");
  return (
    <Card
      title={`${KIND_LABEL[r.kind] ?? r.kind} · run #${r.id}`}
      actions={<>
        <label className="flex items-center gap-1.5 text-[12px]"><input type="checkbox" checked={onlyFailing} onChange={(e) => setOnlyFailing(e.target.checked)} />Only failures</label>
        <Button kind="ghost" onClick={() => setShowOutput(!showOutput)}>{showOutput ? "Hide output" : "Show raw output"}</Button>
        {r.patch_id && <Button onClick={() => navigate(`autofix/${r.patch_id}`)}>Open patch</Button>}
      </>}
    >
      <Summary r={r} />
      {tests.length === 0 ? (
        <p className="mt-3 text-muted">{(r.results ?? []).length ? "No failing tests." : "No individual test results were recorded (see the raw output)."}</p>
      ) : (
        <ul className="mt-3 divide-y divide-border rounded-md border border-border">
          {tests.map((t) => (
            <li key={t.id} className="px-3 py-2">
              <div className="flex items-center gap-2">
                <TestStatusIcon status={t.status} />
                <span className="min-w-0 flex-1 truncate font-mono text-[12px]" title={t.id}>{t.id}</span>
                {t.kind && <span className="rounded bg-panel2 px-1.5 text-[11px] ring-1 ring-border">{label(t.kind)}</span>}
              </div>
              {t.status !== "passed" && t.message && <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap rounded bg-panel2 p-2 font-mono text-[12px]">{t.message}</pre>}
            </li>
          ))}
        </ul>
      )}
      {showOutput && <pre className="mt-3 max-h-80 overflow-auto whitespace-pre-wrap rounded-md border border-border bg-panel2 p-3 font-mono text-[12px]">{r.output || "(no output)"}</pre>}
    </Card>
  );
}

function Summary({ r }: { r: TestRun }) {
  return (
    <div className="flex flex-wrap gap-x-6 gap-y-1 text-[13px]">
      <StatusBadge status={r.status} />
      <span><b className="tabular-nums">{r.passed}</b> passed</span>
      <span><b className="tabular-nums">{r.failed}</b> failed</span>
      <span><b className="tabular-nums">{r.errors}</b> errors</span>
      <span><b className="tabular-nums">{r.skipped}</b> skipped</span>
      <span className="text-muted">{r.duration_s.toFixed(1)}s in the {r.sandbox} sandbox</span>
      <span className="font-mono text-[12px] text-muted">version {r.version.slice(0, 8)}</span>
    </div>
  );
}

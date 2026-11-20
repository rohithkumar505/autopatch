import { Ban, FileSearch, RotateCcw, Sparkles, Wrench } from "lucide-react";
import { useState } from "react";
import { api, type Finding } from "../api";
import {
  Button, Card, CodeView, Confidence, Empty, ErrorNote, JobLog, Modal, NeedProject, Notice, PageHeader, SeverityBadge,
  SourceChips, Spinner, StatusBadge, ValidationBadge, VerificationBadge, cx, inputCls,
} from "../components/ui";
import { label, navigate, run, useApp, useJob, useLoad, useRoute } from "../lib";

export default function Analysis() {
  return (
    <NeedProject>
      <AnalysisInner />
    </NeedProject>
  );
}

function AnalysisInner() {
  const { active } = useApp();
  const route = useRoute();
  const pid = active!.id;
  const list = useLoad(() => api.findings(pid), [pid]);
  const selected = Number(route.param) || list.data?.findings[0]?.id;

  return (
    <>
      <PageHeader title="AI Analysis" subtitle="Detailed bug reports: what goes wrong, why, what triggers it, and how sure AutoPatch is." />
      <div className="grid gap-4 lg:grid-cols-[300px_1fr]">
        <Card padded={false} className="max-h-[80vh] overflow-auto">
          {!list.data ? <Spinner /> : list.data.findings.length === 0 ? (
            <Empty title="No findings">Run a scan from the Bug Scanner first.</Empty>
          ) : (
            <ul className="divide-y divide-border">
              {list.data.findings.map((f) => (
                <li key={f.id}>
                  <button
                    onClick={() => navigate(`analysis/${f.id}`)}
                    className={cx("w-full px-3 py-2 text-left hover:bg-hover", f.id === selected && "bg-accent/10")}
                  >
                    <div className="flex items-center gap-1.5">
                      <SeverityBadge severity={f.severity} />
                      {f.verification === "confirmed" && <VerificationBadge v="confirmed" />}
                      <StatusBadge status={f.status} />
                    </div>
                    <div className="mt-1 line-clamp-2 font-medium">{f.title}</div>
                    <div className="font-mono text-[11px] text-muted">{f.file}:{f.line_start}</div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <div className="min-w-0">
          {selected ? <Report key={selected} id={selected} onChange={list.reload} /> : list.data && <Card><Empty icon={<FileSearch className="size-8" />} title="Select a finding" /></Card>}
        </div>
      </div>
    </>
  );
}

function Report({ id, onChange }: { id: number; onChange: () => void }) {
  const { status, toast } = useApp();
  const { data: f, error, reload } = useLoad(() => api.finding(id), [id]);
  const [jobId, setJobId] = useState<string | null>(null);
  const [dismissing, setDismissing] = useState(false);
  const [note, setNote] = useState("");
  const job = useJob(jobId, (j) => {
    if (j.status === "succeeded") toast("success", "Root-cause analysis complete");
    reload();
    onChange();
  });

  if (error) return <ErrorNote>{error}</ErrorNote>;
  if (!f) return <Spinner />;
  const analyzing = job && (job.status === "queued" || job.status === "running");
  const latest = f.patches?.[0];
  const generating = latest?.status === "generating";

  const startFix = async () => {
    const r = await run(toast, () => api.fix(f.id));
    if (r) navigate(`autofix/${r.patch_id}`);
  };

  return (
    <div className="space-y-4">
      <Card>
        <div className="flex flex-wrap items-start gap-3">
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <SeverityBadge severity={f.severity} />
              <VerificationBadge v={f.verification} />
              <StatusBadge status={f.status} />
              <span className="text-[12px] text-muted">{label(f.category)}</span>
            </div>
            <h2 className="mt-2 text-lg font-semibold">{f.title}</h2>
            <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px] text-muted">
              <button className="font-mono text-accent hover:underline" onClick={() => navigate(`ide?file=${encodeURIComponent(f.file)}&line=${f.line_start}`)}>
                {f.file}:{f.line_start}{f.line_end !== f.line_start ? `–${f.line_end}` : ""}
              </button>
              <span>in <span className="font-mono">{f.function}</span></span>
              <span className="flex items-center gap-1">Confidence <Confidence value={f.confidence} /></span>
              <span className="flex items-center gap-1">Reported by <SourceChips sources={f.sources} /></span>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button
              icon={<Sparkles className="size-3.5" />}
              busy={!!analyzing}
              disabled={!status?.ai.configured}
              title={status?.ai.configured ? "Trace the root cause with the AI" : "Configure an AI provider in Settings"}
              onClick={async () => { const r = await run(toast, () => api.analyze(f.id)); if (r) setJobId(r.job_id); }}
            >
              {f.analyzed ? "Re-analyze" : "Analyze root cause"}
            </Button>
            {latest && latest.status !== "rejected" && latest.status !== "failed" ? (
              <Button kind="primary" icon={<Wrench className="size-3.5" />} onClick={() => navigate(`autofix/${latest.id}`)}>
                {generating ? "View fix in progress" : "View patch"}
              </Button>
            ) : (
              <Button
                kind="primary"
                icon={<Wrench className="size-3.5" />}
                disabled={!status?.ai.configured || !status?.sandbox.ready || f.status === "dismissed"}
                title={!status?.ai.configured ? "Configure an AI provider in Settings" : !status?.sandbox.ready ? "Set up the test sandbox in Settings" : "Generate and validate a fix"}
                onClick={startFix}
              >
                Generate fix
              </Button>
            )}
            {f.status === "dismissed" ? (
              <Button icon={<RotateCcw className="size-3.5" />} onClick={async () => { if (await run(toast, () => api.reopen(f.id))) { reload(); onChange(); } }}>Reopen</Button>
            ) : (
              <Button kind="ghost" icon={<Ban className="size-3.5" />} disabled={f.status === "fixing"} onClick={() => setDismissing(true)}>Dismiss</Button>
            )}
          </div>
        </div>
        {(!status?.ai.configured || !status?.sandbox.ready) && f.status !== "fixed" && (
          <div className="mt-3">
            <Notice kind="warning">
              {!status?.ai.configured && "Analysis and fixes need an engine (AutoPatch Core is built in). "}
              {!status?.sandbox.ready && "Validating fixes needs the test sandbox. "}
              <button className="underline" onClick={() => navigate("settings")}>Open Settings</button>
            </Notice>
          </div>
        )}
        {job && <div className="mt-3"><JobLog job={job} title="Root-cause analysis" /></div>}
      </Card>

      {!f.analyzed && (
        <Notice>
          This was reported by static analysis{f.sources.includes("ai") ? "" : " only"}. Run the root-cause analysis for a traced
          explanation, or generate a fix (which runs it first).
        </Notice>
      )}

      <Card title="Problematic code">
        {f.code_snippet ? (
          <CodeView value={f.code_snippet} firstLine={f.snippet_start || 1} height={Math.min(420, 22 + 19 * f.code_snippet.split("\n").length)}
            highlights={[{ start: f.line_start, end: f.line_end, severity: f.severity }]} />
        ) : <p className="text-muted">No snippet available.</p>}
      </Card>

      <div className="grid gap-4 xl:grid-cols-2">
        <Section title="What goes wrong" text={f.description} />
        <Section title="Root cause" text={f.root_cause} empty="Not analyzed yet." />
        <Section title="What triggers it" text={f.trigger} empty="Not analyzed yet." />
        <Section title="Expected behavior" text={f.expected_behavior} empty="Not analyzed yet." />
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card title="Related code and impact">
          {f.related.length > 0 && (
            <ul className="mb-3 list-disc space-y-1 pl-5">
              {f.related.map((r, i) => <li key={i} className="break-words">{r}</li>)}
            </ul>
          )}
          <p className="whitespace-pre-wrap">{f.impact || <span className="text-muted">No call-graph information.</span>}</p>
        </Card>
        <Card title="Severity and confidence">
          <SeverityExplain f={f} />
        </Card>
      </div>

      {f.patches && f.patches.length > 0 && (
        <Card title="Patches for this finding" padded={false}>
          <ul className="divide-y divide-border">
            {f.patches.map((p) => (
              <li key={p.id} className="flex flex-wrap items-center gap-2 px-4 py-2">
                <button className="font-medium hover:underline" onClick={() => navigate(`autofix/${p.id}`)}>Patch #{p.id}</button>
                <StatusBadge status={p.status} />
                {p.status !== "generating" && p.status !== "failed" && <ValidationBadge v={p.validation} />}
                {p.error && <span className="text-[12px] text-red-500">{p.error}</span>}
              </li>
            ))}
          </ul>
        </Card>
      )}

      {dismissing && (
        <Modal
          title="Dismiss this finding?"
          onClose={() => setDismissing(false)}
          footer={<>
            <Button onClick={() => setDismissing(false)}>Cancel</Button>
            <Button kind="primary" onClick={async () => { if (await run(toast, () => api.dismiss(f.id, note), "Dismissed")) { reload(); onChange(); } setDismissing(false); }}>Dismiss</Button>
          </>}
        >
          <p>Dismissed findings stay in the history and can be reopened.</p>
          <textarea className={inputCls} rows={3} placeholder="Reason (optional), e.g. intended behavior" value={note} onChange={(e) => setNote(e.target.value)} />
        </Modal>
      )}
    </div>
  );
}

function Section({ title, text, empty }: { title: string; text: string; empty?: string }) {
  return (
    <Card title={title}>
      {text ? <p className="whitespace-pre-wrap leading-relaxed">{text}</p> : <p className="text-muted">{empty ?? "—"}</p>}
    </Card>
  );
}

function SeverityExplain({ f }: { f: Finding }) {
  const conf =
    f.verification === "confirmed"
      ? "A test written from this report failed on the original code (or the check is deterministic), so the bug is proven."
      : f.verification === "not_reproduced"
        ? "Generated tests could not demonstrate this bug, so treat it as unproven."
        : f.sources.length > 1
          ? "More than one independent check reported it, which raises confidence, but no test has proven it yet."
          : "Reported by one check and not yet proven by a test.";
  return (
    <div className="space-y-2">
      <p>{f.severity_reason || "No severity explanation recorded."}</p>
      <p className="text-muted">{conf}</p>
      <p className="text-[12px] text-muted">Priority score {f.priority} (severity × confidence × impact from the call graph).</p>
    </div>
  );
}

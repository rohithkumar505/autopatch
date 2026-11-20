import { Check, Columns2, FlaskConical, RefreshCw, Rows2, Undo2, Wrench, X } from "lucide-react";
import { Fragment, useState } from "react";
import { api, type Patch } from "../api";
import {
  Button, Card, CodeView, DiffView, Empty, ErrorNote, JobLog, KV, Modal, NeedProject, Notice, PageHeader, SeverityBadge,
  Spinner, StatusBadge, TestStatusIcon, Toggle, ValidationBadge, VerificationBadge, cx, inputCls,
} from "../components/ui";
import { dateTime, label, navigate, run, timeAgo, useApp, useJob, useLoad, useRoute } from "../lib";

export default function AutoFix() {
  return (
    <NeedProject>
      <AutoFixInner />
    </NeedProject>
  );
}

function AutoFixInner() {
  const { active } = useApp();
  const route = useRoute();
  const pid = active!.id;
  const patches = useLoad(() => api.patches(pid), [pid]);
  const selected = Number(route.param) || patches.data?.find((p) => p.status === "ready" || p.status === "generating")?.id || patches.data?.[0]?.id;

  return (
    <>
      <PageHeader title="AutoFix" subtitle="Review proposed patches: the change, why it works, and the test evidence. Nothing is applied until you accept it." />
      <div className="grid gap-4 lg:grid-cols-[300px_1fr]">
        <Card title="Patches" padded={false} className="max-h-[82vh] overflow-auto">
          {!patches.data ? <Spinner /> : patches.data.length === 0 ? (
            <Empty icon={<Wrench className="size-8" />} title="No patches yet" action={<Button onClick={() => navigate("analysis")}>Open AI Analysis</Button>}>
              Pick a finding in AI Analysis and choose Generate fix.
            </Empty>
          ) : (
            <ul className="divide-y divide-border">
              {patches.data.map((p) => (
                <li key={p.id}>
                  <button onClick={() => navigate(`autofix/${p.id}`)} className={cx("w-full px-3 py-2 text-left hover:bg-hover", p.id === selected && "bg-accent/10")}>
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="font-semibold">#{p.id}</span>
                      <StatusBadge status={p.status} />
                      {p.status !== "generating" && p.status !== "failed" && <ValidationBadge v={p.validation} />}
                      {p.stale && <span className="text-[11px] font-semibold text-amber-600">stale</span>}
                    </div>
                    <div className="mt-1 line-clamp-2">{p.finding_title}</div>
                    <div className="text-[11px] text-muted">{p.file} · {timeAgo(p.created_at)}</div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <div className="min-w-0">
          {selected ? <PatchView key={selected} id={selected} onChange={patches.reload} /> : null}
        </div>
      </div>
    </>
  );
}

function PatchView({ id, onChange }: { id: number; onChange: () => void }) {
  const { toast, refreshProjects } = useApp();
  const { data: p, error, reload } = useLoad(() => api.patch(id), [id]);
  const [sideBySide, setSideBySide] = useState(true);
  const [revalidateJob, setRevalidateJob] = useState<string | null>(null);
  const [dialog, setDialog] = useState<"accept" | "reject" | "revert" | null>(null);
  const genJob = useJob(p?.status === "generating" ? p.job_id : null, () => { reload(); onChange(); });
  const revJob = useJob(revalidateJob, (j) => {
    toast(j.status === "succeeded" ? "success" : "error", j.status === "succeeded" ? "Re-validation finished" : j.error);
    reload();
    onChange();
  });

  if (error) return <ErrorNote>{error}</ErrorNote>;
  if (!p) return <Spinner />;
  const f = p.finding;
  const d = p.validation_detail;
  const done = () => { reload(); onChange(); refreshProjects(); };

  return (
    <div className="space-y-4">
      <Card>
        <div className="flex flex-wrap items-start gap-3">
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-semibold">Patch #{p.id}</span>
              <StatusBadge status={p.status} />
              {p.status !== "generating" && p.status !== "failed" && <ValidationBadge v={p.validation} />}
              <SeverityBadge severity={f.severity} />
              <VerificationBadge v={f.verification} />
            </div>
            <button className="mt-2 text-left text-lg font-semibold hover:underline" onClick={() => navigate(`analysis/${f.id}`)}>{f.title}</button>
            <div className="text-[12px] text-muted">
              {p.file} · created {dateTime(p.created_at)}
              {p.base_version && <> · based on version <span className="font-mono">{p.base_version.slice(0, 8)}</span></>}
              {p.decided_at && <> · {p.status} {dateTime(p.decided_at)}</>}
            </div>
            {p.decision_note && <div className="mt-1 text-[12px]">Note: {p.decision_note}</div>}
          </div>
          <div className="flex flex-wrap gap-2">
            {p.status === "ready" && !p.stale && (
              <>
                <Button kind="success" icon={<Check className="size-3.5" />} onClick={() => setDialog("accept")}>Accept</Button>
                <Button kind="danger" icon={<X className="size-3.5" />} onClick={() => setDialog("reject")}>Reject</Button>
              </>
            )}
            {p.status === "ready" && p.stale && (
              <Button kind="primary" icon={<RefreshCw className="size-3.5" />} busy={!!revJob && revJob.status !== "succeeded" && revJob.status !== "failed"}
                onClick={async () => { const r = await run(toast, () => api.revalidate(p.id)); if (r) setRevalidateJob(r.job_id); }}>
                Re-validate on current version
              </Button>
            )}
            {p.status === "failed" && <Button kind="danger" icon={<X className="size-3.5" />} onClick={() => setDialog("reject")}>Discard</Button>}
            {p.status === "accepted" && <Button icon={<Undo2 className="size-3.5" />} onClick={() => setDialog("revert")}>Revert</Button>}
            {(p.status === "rejected" || p.status === "failed" || p.status === "reverted") && (
              <Button kind="primary" icon={<Wrench className="size-3.5" />} onClick={async () => { const r = await run(toast, () => api.fix(f.id)); if (r) { onChange(); navigate(`autofix/${r.patch_id}`); } }}>
                Generate a new fix
              </Button>
            )}
          </div>
        </div>
        {p.stale && p.status === "ready" && (
          <div className="mt-3"><Notice kind="warning">The project changed after this patch was validated. Re-validate it on the current version before accepting; this re-applies the same change and re-runs the same tests.</Notice></div>
        )}
        {p.error && <div className="mt-3"><ErrorNote>{p.error}</ErrorNote></div>}
        {genJob && <div className="mt-3"><JobLog job={genJob} title="Generating and validating the fix" /></div>}
        {revJob && <div className="mt-3"><JobLog job={revJob} title="Re-validation" /></div>}
      </Card>

      {p.status === "generating" ? null : p.after_content ? (
        <>
          <ValidationSummary p={p} />
          <Card
            title={`Before and after: ${p.file}`}
            actions={
              <Button kind="ghost" icon={sideBySide ? <Rows2 className="size-3.5" /> : <Columns2 className="size-3.5" />} onClick={() => setSideBySide(!sideBySide)}>
                {sideBySide ? "Inline view" : "Side by side"}
              </Button>
            }
          >
            <DiffView original={p.before_content} modified={p.after_content} sideBySide={sideBySide} height={Math.min(560, 140 + 19 * (p.diff.split("\n").length))} />
            {d.scope && (
              <p className={cx("mt-2 text-[12px]", d.scope.outside_function.length ? "text-amber-600 dark:text-amber-400" : "text-muted")}>
                {d.scope.lines_changed} line(s) changed
                {d.scope.function ? ` in ${d.scope.function} (lines ${d.scope.function_lines?.[0]}–${d.scope.function_lines?.[1]})` : ""}.
                {d.scope.outside_function.length > 0 && ` Also changes lines outside that function: ${d.scope.outside_function.map(([a, b]) => (a === b ? a : `${a}–${b}`)).join(", ")}.`}
              </p>
            )}
          </Card>
          <div className="grid gap-4 xl:grid-cols-2">
            <Card title="Why this fix works"><p className="whitespace-pre-wrap leading-relaxed">{p.explanation}</p></Card>
            <Card title="What changes for callers"><p className="whitespace-pre-wrap leading-relaxed">{p.behavior_change}</p></Card>
          </div>
          <GeneratedTests p={p} />
          {d.attempts && d.attempts.length > 0 && (
            <Card title="Attempts">
              <ol className="space-y-1">
                {d.attempts.map((a) => (
                  <li key={a.attempt} className="flex flex-wrap gap-2">
                    <span className="font-medium">Attempt {a.attempt}:</span>
                    <span>{label(a.result)}</span>
                    <span className="text-muted">{a.detail}</span>
                  </li>
                ))}
              </ol>
              {d.ai_usage && (
                <p className="mt-2 text-[12px] text-muted">
                  {d.ai_usage.length} AI call(s), {d.ai_usage.reduce((n, u) => n + u.input_tokens + u.cache_read_tokens + u.cache_write_tokens, 0).toLocaleString()} input and{" "}
                  {d.ai_usage.reduce((n, u) => n + u.output_tokens, 0).toLocaleString()} output tokens
                  ({d.ai_usage.reduce((n, u) => n + u.cache_read_tokens, 0).toLocaleString()} read from cache).
                </p>
              )}
            </Card>
          )}
        </>
      ) : p.status === "failed" ? null : <Spinner />}

      {dialog === "accept" && <AcceptDialog p={p} onClose={() => setDialog(null)} onDone={done} />}
      {dialog === "reject" && (
        <NoteDialog title={p.status === "failed" ? "Discard this patch?" : "Reject this patch?"} action="Reject" kind="danger"
          text="The rejection is recorded in the change history. The finding stays open, so you can generate a new fix."
          onClose={() => setDialog(null)}
          onSubmit={async (note) => { if (await run(toast, () => api.reject(p.id, note), "Patch rejected")) done(); }} />
      )}
      {dialog === "revert" && (
        <NoteDialog title="Revert this patch?" action="Revert" kind="primary"
          text="Creates a new version that undoes this change. The accepted version stays in the history and can be restored."
          onClose={() => setDialog(null)}
          onSubmit={async (note) => { if (await run(toast, () => api.revert(p.id, note), "Patch reverted")) done(); }} />
      )}
    </div>
  );
}

function ValidationSummary({ p }: { p: Patch }) {
  const d = p.validation_detail;
  const ex = d.existing;
  const repro = d.reproduce_tests?.length ?? 0;
  const rows: [React.ReactNode, React.ReactNode][] = [
    ["Bug reproduced first", repro === 0 ? "No reproduce test" : d.reproduced?.length ? `Yes: ${d.reproduced.length} of ${repro} reproduce test(s) failed on the original code` : "No: the reproduce test passed on the original code"],
    ["Bug resolved", d.reproduced?.length ? (d.still_failing?.length ? `No: ${d.still_failing.length} reproduce test(s) still fail` : "Yes: every reproduce test passes with the patch") : "Not provable without a reproduction"],
    ["Existing tests", ex ? `${ex.before.passed}/${ex.before.total} passing before → ${ex.after.passed}/${ex.after.total} after` + (ex.failing_before.length ? ` (${ex.failing_before.length} already failing before)` : "") : "—"],
    ["Regressions", d.regressions?.length ? <span className="text-red-600 dark:text-red-400">{d.regressions.join(", ")}</span> : "None"],
    ["Generated tests after the patch", d.generated_failing?.length ? <span className="text-red-600 dark:text-red-400">{d.generated_failing.length} failing</span> : p.test_cases.length ? `All ${p.test_cases.length} pass` : "None generated"],
    ["Sandbox", d.sandbox ?? "—"],
  ];
  const kind = p.validation === "validated" ? "success" : p.validation === "failed" ? "warning" : "info";
  const headline =
    p.validation === "validated" ? "Validated: the bug was reproduced by a test before the patch and is resolved after it, with no regressions."
      : p.validation === "partially_validated" ? "Partially validated: all tests pass and nothing regressed, but the bug was not reproduced first, so the fix is not proven."
        : p.validation === "failed" ? "Failed validation: some tests fail with this patch. It is not considered a fix."
          : "Not validated.";
  return (
    <Card title="Validation">
      <Notice kind={kind}>{headline}</Notice>
      <div className="mt-3"><KV items={rows} /></div>
    </Card>
  );
}

function GeneratedTests({ p }: { p: Patch }) {
  const [showCode, setShowCode] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  if (!p.test_cases.length && !p.test_code) return null;
  return (
    <Card
      title={<span className="flex items-center gap-2"><FlaskConical className="size-4" />Generated tests ({p.test_file})</span>}
      actions={<Button kind="ghost" onClick={() => setShowCode(!showCode)}>{showCode ? "Hide code" : "Show code"}</Button>}
      padded={false}
    >
      <div className="overflow-x-auto">
        <table className="w-full min-w-[640px] text-left">
          <thead className="text-[12px] text-muted">
            <tr className="border-b border-border">
              <th className="px-4 py-2 font-medium">Test</th>
              <th className="px-4 py-2 font-medium">Kind</th>
              <th className="px-4 py-2 font-medium">Original code</th>
              <th className="px-4 py-2 font-medium">Patched code</th>
            </tr>
          </thead>
          <tbody>
            {p.test_cases.map((c) => (
              <Fragment key={c.id}>
                <tr className="cursor-pointer border-b border-border hover:bg-hover" onClick={() => setOpen(open === c.id ? null : c.id)}>
                  <td className="px-4 py-2">
                    <div className="font-mono text-[12px]">{c.name}</div>
                    <div className="text-[12px] text-muted">{c.description}</div>
                  </td>
                  <td className="px-4 py-2">{label(c.kind || "other")}</td>
                  <td className="px-4 py-2"><span className="flex items-center gap-1.5"><TestStatusIcon status={c.before} />{c.before}</span></td>
                  <td className="px-4 py-2"><span className="flex items-center gap-1.5"><TestStatusIcon status={c.after} />{c.after}</span></td>
                </tr>
                {open === c.id && (c.message_before || c.message_after) && (
                  <tr className="border-b border-border bg-panel2">
                    <td colSpan={4} className="px-4 py-2">
                      {c.message_before && <><div className="text-[12px] font-medium">On the original code</div><pre className="max-h-48 overflow-auto whitespace-pre-wrap font-mono text-[12px]">{c.message_before}</pre></>}
                      {c.message_after && <><div className="mt-2 text-[12px] font-medium">On the patched code</div><pre className="max-h-48 overflow-auto whitespace-pre-wrap font-mono text-[12px]">{c.message_after}</pre></>}
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>
      {showCode && <div className="p-3"><CodeView value={p.test_code} height={Math.min(520, 40 + 19 * p.test_code.split("\n").length)} /></div>}
    </Card>
  );
}

function AcceptDialog({ p, onClose, onDone }: { p: Patch; onClose: () => void; onDone: () => void }) {
  const { toast } = useApp();
  const [includeTests, setIncludeTests] = useState(!!p.test_code);
  const [confirmUnvalidated, setConfirm] = useState(false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const failed = p.validation === "failed";
  return (
    <Modal
      title="Accept this patch?"
      onClose={onClose}
      footer={<>
        <Button onClick={onClose}>Cancel</Button>
        <Button kind="success" busy={busy} disabled={failed && !confirmUnvalidated}
          onClick={async () => {
            setBusy(true);
            const r = await run(toast, () => api.accept(p.id, { include_tests: includeTests, force: failed && confirmUnvalidated, note }), "Patch applied as a new version");
            setBusy(false);
            if (r) { onClose(); onDone(); }
          }}>
          Apply patch
        </Button>
      </>}
    >
      <p>AutoPatch writes the change to its copy of <span className="font-mono">{p.file}</span> and records it as a new version. Earlier versions stay available in Change History.</p>
      {p.test_code && <Toggle checked={includeTests} onChange={setIncludeTests} label="Also add the generated tests to the project" hint={p.test_file} />}
      {failed && (
        <>
          <Notice kind="warning">This patch failed validation. Applying it is not recommended and the finding will be marked as patched but unverified.</Notice>
          <Toggle checked={confirmUnvalidated} onChange={setConfirm} label="I understand; apply it anyway" />
        </>
      )}
      <textarea className={inputCls} rows={2} placeholder="Note for the history (optional)" value={note} onChange={(e) => setNote(e.target.value)} />
    </Modal>
  );
}

function NoteDialog({ title, text, action, kind, onClose, onSubmit }: {
  title: string; text: string; action: string; kind: "danger" | "primary"; onClose: () => void; onSubmit: (note: string) => Promise<void>;
}) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <Modal title={title} onClose={onClose} footer={<>
      <Button onClick={onClose}>Cancel</Button>
      <Button kind={kind} busy={busy} onClick={async () => { setBusy(true); await onSubmit(note); setBusy(false); onClose(); }}>{action}</Button>
    </>}>
      <p>{text}</p>
      <textarea className={inputCls} rows={3} placeholder="Reason (optional)" value={note} onChange={(e) => setNote(e.target.value)} />
    </Modal>
  );
}

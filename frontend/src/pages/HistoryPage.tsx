import { Download, FolderTree, GitCommitHorizontal, History as HistoryIcon, RotateCcw, Undo2 } from "lucide-react";
import { useState } from "react";
import { api, type Version } from "../api";
import { Button, Card, Empty, ErrorNote, Modal, NeedProject, PageHeader, Spinner, StatusBadge, Tabs, UnifiedDiff, ValidationBadge, cx, inputCls } from "../components/ui";
import { dateTime, navigate, run, timeAgo, useApp, useLoad } from "../lib";

export default function HistoryPage() {
  return (
    <NeedProject>
      <HistoryInner />
    </NeedProject>
  );
}

function HistoryInner() {
  const { active, toast, refreshProjects } = useApp();
  const pid = active!.id;
  const [tab, setTab] = useState<"versions" | "decisions" | "activity">("versions");
  const versions = useLoad(() => api.versions(pid), [pid]);
  const patches = useLoad(() => api.patches(pid), [pid]);
  const events = useLoad(() => api.events(pid), [pid]);
  const [selected, setSelected] = useState<string | null>(null);
  const [restoring, setRestoring] = useState<Version | null>(null);
  const [reverting, setReverting] = useState<Version | null>(null);
  const shown = selected ?? versions.data?.[0]?.sha ?? null;
  const diff = useLoad(() => (shown ? api.versionDiff(pid, shown) : Promise.resolve(null)), [pid, shown]);
  const reloadAll = () => { versions.reload(); patches.reload(); events.reload(); refreshProjects(); };

  return (
    <>
      <PageHeader title="Change History" subtitle="Every version of the project, every patch decision and every test run is recorded here. Any version can be restored." />
      <Tabs value={tab} onChange={setTab} tabs={[
        { id: "versions", label: `Versions (${versions.data?.length ?? "…"})` },
        { id: "decisions", label: `Patch decisions (${patches.data?.filter((p) => p.status !== "generating").length ?? "…"})` },
        { id: "activity", label: "Activity log" },
      ]} />
      <div className="mt-4">
        {tab === "versions" && (
          <div className="grid gap-4 xl:grid-cols-[440px_1fr]">
            <Card padded={false} className="max-h-[78vh] overflow-auto">
              {versions.error ? <ErrorNote>{versions.error}</ErrorNote> : !versions.data ? <Spinner /> : (
                <ol className="relative">
                  {versions.data.map((v, i) => (
                    <li key={v.sha} className={cx("relative border-b border-border last:border-0", v.sha === shown && "bg-accent/10")}>
                      <button onClick={() => setSelected(v.sha)} className="flex w-full gap-3 px-4 py-3 text-left hover:bg-hover">
                        <GitCommitHorizontal className={cx("mt-0.5 size-4 shrink-0", v.is_head ? "text-accent" : "text-muted")} />
                        <div className="min-w-0 flex-1">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="font-medium">{v.message}</span>
                            {v.is_head && <span className="rounded bg-accent/15 px-1.5 text-[11px] font-semibold text-accent">current</span>}
                            {i === versions.data!.length - 1 && <span className="rounded bg-panel2 px-1.5 text-[11px] ring-1 ring-border">original upload</span>}
                          </div>
                          <div className="mt-0.5 flex flex-wrap gap-x-3 text-[12px] text-muted">
                            <span className="font-mono">{v.short}</span>
                            <span>{dateTime(v.date)}</span>
                            {v.files_changed.length > 0 && <span>{v.files_changed.length} file(s): {v.files_changed.slice(0, 3).join(", ")}</span>}
                          </div>
                          {v.patch && (
                            <div className="mt-1 flex items-center gap-1.5">
                              <span className="text-[12px]">Patch #{v.patch.id}</span>
                              <StatusBadge status={v.patch.status} />
                              <ValidationBadge v={v.patch.validation} />
                            </div>
                          )}
                        </div>
                      </button>
                    </li>
                  ))}
                </ol>
              )}
            </Card>
            <div className="min-w-0 space-y-3">
              {shown && versions.data && (() => {
                const v = versions.data.find((x) => x.sha === shown)!;
                if (!v) return null;
                return (
                  <Card
                    title={<span className="font-mono">{v.short}</span>}
                    actions={<>
                      <Button icon={<FolderTree className="size-3.5" />} onClick={() => navigate(`ide?version=${v.sha}`)}>Open in IDE</Button>
                      <a href={api.downloadUrl(pid, v.sha)} className="inline-flex items-center gap-1.5 rounded-md border border-border bg-panel2 px-3 py-1.5 hover:bg-hover"><Download className="size-3.5" />Download</a>
                      {v.patch?.status === "accepted" && <Button icon={<Undo2 className="size-3.5" />} onClick={() => setReverting(v)}>Revert this patch</Button>}
                      {!v.is_head && <Button kind="primary" icon={<RotateCcw className="size-3.5" />} onClick={() => setRestoring(v)}>Restore this version</Button>}
                    </>}
                  >
                    <p className="mb-3">{v.message}</p>
                    {diff.data ? <UnifiedDiff diff={diff.data.diff} /> : <Spinner />}
                  </Card>
                );
              })()}
            </div>
          </div>
        )}

        {tab === "decisions" && (
          <Card padded={false}>
            {!patches.data ? <Spinner /> : patches.data.length === 0 ? <Empty icon={<HistoryIcon className="size-8" />} title="No patches yet" /> : (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[760px] text-left">
                  <thead className="text-[12px] text-muted">
                    <tr className="border-b border-border">
                      <th className="px-4 py-2 font-medium">Patch</th>
                      <th className="px-4 py-2 font-medium">Finding</th>
                      <th className="px-4 py-2 font-medium">Decision</th>
                      <th className="px-4 py-2 font-medium">Validation</th>
                      <th className="px-4 py-2 font-medium">When</th>
                      <th className="px-4 py-2 font-medium">Note</th>
                    </tr>
                  </thead>
                  <tbody>
                    {patches.data.map((p) => (
                      <tr key={p.id} className="cursor-pointer border-b border-border last:border-0 hover:bg-hover" onClick={() => navigate(`autofix/${p.id}`)}>
                        <td className="px-4 py-2 font-medium">#{p.id}</td>
                        <td className="px-4 py-2">{p.finding_title}<div className="font-mono text-[11px] text-muted">{p.file}</div></td>
                        <td className="px-4 py-2"><StatusBadge status={p.status} /></td>
                        <td className="px-4 py-2">{p.status !== "generating" && <ValidationBadge v={p.validation} />}</td>
                        <td className="px-4 py-2 text-muted">{timeAgo(p.decided_at || p.created_at)}</td>
                        <td className="px-4 py-2 text-muted">{p.decision_note || p.error}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        )}

        {tab === "activity" && (
          <Card padded={false}>
            {!events.data ? <Spinner /> : events.data.length === 0 ? <Empty title="No activity yet" /> : (
              <ul className="divide-y divide-border">
                {events.data.map((e) => (
                  <li key={e.id} className="flex flex-wrap items-start gap-3 px-4 py-2">
                    <span className="w-36 shrink-0 text-[12px] text-muted">{dateTime(e.created_at)}</span>
                    <span className="rounded bg-panel2 px-1.5 text-[11px] ring-1 ring-border">{e.kind.replace(/_/g, " ")}</span>
                    <span className="min-w-0 flex-1">{e.message}</span>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        )}
      </div>

      {restoring && (
        <ConfirmNote
          title={`Restore version ${restoring.short}?`}
          text="The project's files become identical to this version. This is saved as a new version, so nothing is lost: you can return to the current version the same way."
          action="Restore"
          onClose={() => setRestoring(null)}
          onSubmit={async (note) => { const r = await run(toast, () => api.restore(pid, restoring.sha, note)); if (r) { toast("success", r.changed ? `Restored ${restoring.short}` : "Already identical to that version"); setSelected(null); reloadAll(); } }}
        />
      )}
      {reverting?.patch && (
        <ConfirmNote
          title={`Revert patch #${reverting.patch.id}?`}
          text="Creates a new version that undoes only this patch, keeping later changes. If later changes touch the same lines, restore an earlier version instead."
          action="Revert"
          onClose={() => setReverting(null)}
          onSubmit={async (note) => { if (await run(toast, () => api.revert(reverting.patch!.id, note), "Patch reverted")) { setSelected(null); reloadAll(); } }}
        />
      )}
    </>
  );
}

function ConfirmNote({ title, text, action, onClose, onSubmit }: { title: string; text: string; action: string; onClose: () => void; onSubmit: (note: string) => Promise<void> }) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <Modal title={title} onClose={onClose} footer={<>
      <Button onClick={onClose}>Cancel</Button>
      <Button kind="primary" busy={busy} onClick={async () => { setBusy(true); await onSubmit(note); setBusy(false); onClose(); }}>{action}</Button>
    </>}>
      <p>{text}</p>
      <textarea className={inputCls} rows={2} placeholder="Note for the history (optional)" value={note} onChange={(e) => setNote(e.target.value)} />
    </Modal>
  );
}

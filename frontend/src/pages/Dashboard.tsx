import { Activity, Bug, CheckCircle2, FlaskConical, FolderGit2, GitPullRequestArrow, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { api, type Severity } from "../api";
import { Button, Card, Empty, ErrorNote, PageHeader, SeverityBadge, Spinner, Tabs, VerificationBadge, cx, severityBar } from "../components/ui";
import { label, navigate, timeAgo, useApp, useLoad } from "../lib";

export default function Dashboard() {
  const { active, projects } = useApp();
  const [scope, setScope] = useState<"project" | "all">(active ? "project" : "all");
  const projectId = scope === "project" ? active?.id : undefined;
  const { data, error, loading } = useLoad(() => api.dashboard(projectId), [projectId, active?.last_scan?.id]);

  if (!projects.length && !loading)
    return (
      <>
        <PageHeader title="Dashboard" />
        <Card>
          <Empty icon={<FolderGit2 className="size-8" />} title="Welcome to AutoPatch"
            action={
              <div className="flex flex-wrap gap-2">
                <Button kind="primary" onClick={() => navigate("projects")}>Import your first project</Button>
              </div>
            }>
            AutoPatch scans a code project for bugs, explains their root cause, writes tests that prove them, and proposes
            fixes that are validated in an isolated sandbox before you approve them. Use <b>Open IDE</b> in the top-right after importing a project.
          </Empty>
        </Card>
      </>
    );

  return (
    <>
      <PageHeader
        title="Dashboard"
        subtitle={scope === "project" && active ? `Overview of ${active.name}` : "Overview of all projects"}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            {active && <Button kind="primary" onClick={() => navigate("ide")}>Open IDE</Button>}
            <div className="w-56">
              <Tabs value={scope} onChange={setScope} tabs={[{ id: "project", label: "Active project" }, { id: "all", label: "All projects" }]} />
            </div>
          </div>
        }
      />
      {error && <ErrorNote>{error}</ErrorNote>}
      {!data ? (
        <Spinner />
      ) : (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
            <Stat icon={<Bug />} label="Bugs detected" value={data.findings.detected} sub={`${data.findings.open} open`} />
            <Stat icon={<ShieldCheck />} label="Confirmed by tests" value={data.findings.confirmed} sub={`${data.findings.possible} possible`} />
            <Stat icon={<CheckCircle2 />} label="Resolved" value={data.resolved} sub="fixed and validated" tone="good" />
            <Stat icon={<GitPullRequestArrow />} label="Pending patches" value={data.patches.pending} sub="awaiting your review" tone={data.patches.pending ? "warn" : undefined}
              onClick={() => navigate("autofix")} />
            <Stat icon={<FlaskConical />} label="Test runs" value={data.tests.runs}
              sub={data.tests.runs ? `${data.tests.tests_executed} tests executed` : "none yet"} />
            <Stat icon={<FolderGit2 />} label="Projects" value={data.projects} sub="imported" onClick={() => navigate("projects")} />
          </div>

          <div className="grid gap-4 lg:grid-cols-3">
            <Card title="Open issues by severity">
              <SeverityBars counts={data.findings.by_severity} />
              {Object.keys(data.findings.by_category).length > 0 && (
                <div className="mt-4 flex flex-wrap gap-1.5">
                  {Object.entries(data.findings.by_category).map(([c, n]) => (
                    <span key={c} className="rounded-full bg-panel2 px-2 py-0.5 text-[12px] ring-1 ring-border">
                      {label(c)} <b className="tabular-nums">{n}</b>
                    </span>
                  ))}
                </div>
              )}
            </Card>

            <Card title="Most important open issues" className="lg:col-span-2" padded={false}>
              {data.top_findings.length === 0 ? (
                <Empty title="No open issues">
                  {data.findings.detected ? "Everything found so far is resolved or dismissed." : "Run a scan from the Bug Scanner."}
                </Empty>
              ) : (
                <ul className="divide-y divide-border">
                  {data.top_findings.map((f) => (
                    <li key={f.id}>
                      <button onClick={() => navigate(`analysis/${f.id}`)} className="flex w-full flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2.5 text-left hover:bg-hover">
                        <SeverityBadge severity={f.severity} />
                        <span className="min-w-0 flex-1 truncate font-medium">{f.title}</span>
                        <VerificationBadge v={f.verification} />
                        <span className="font-mono text-[12px] text-muted">{f.file}:{f.line_start}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          </div>

          <div className="grid gap-4 lg:grid-cols-3">
            <Card title="Patches">
              <div className="grid grid-cols-2 gap-3">
                <Mini label="Validated" value={data.patches.validated} tone="good" />
                <Mini label="Failed validation" value={data.patches.failed_validation} tone={data.patches.failed_validation ? "bad" : undefined} />
                <Mini label="Accepted" value={data.patches.accepted} />
                <Mini label="Rejected" value={data.patches.rejected} />
                <Mini label="Reverted" value={data.patches.reverted} />
                <Mini label="Awaiting review" value={data.patches.pending} />
              </div>
              {data.tests.latest && (
                <div className="mt-4 rounded-md bg-panel2 p-3 ring-1 ring-border">
                  <div className="text-muted">Latest test run ({timeAgo(data.tests.latest.created_at)})</div>
                  <div className="mt-1 font-medium">
                    {data.tests.latest.passed} passed, {data.tests.latest.failed} failed, {data.tests.latest.errors} errors
                    <span className="text-muted"> · {label(data.tests.latest.kind)} · {data.tests.latest.sandbox}</span>
                  </div>
                </div>
              )}
            </Card>

            <Card title="Recent activity" className="lg:col-span-2" padded={false}>
              {data.recent_events.length === 0 ? (
                <Empty title="No activity yet" />
              ) : (
                <ul className="divide-y divide-border">
                  {data.recent_events.map((e) => (
                    <li key={e.id} className="flex items-start gap-3 px-4 py-2">
                      <Activity className="mt-0.5 size-4 shrink-0 text-muted" />
                      <span className="min-w-0 flex-1">{e.message}</span>
                      <span className="whitespace-nowrap text-[12px] text-muted">{timeAgo(e.created_at)}</span>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          </div>

          {scope === "all" && data.per_project.length > 0 && (
            <Card title="Projects" padded={false}>
              <div className="overflow-x-auto">
                <table className="w-full min-w-[520px] text-left">
                  <thead className="text-[12px] text-muted">
                    <tr className="border-b border-border">
                      <th className="px-4 py-2 font-medium">Project</th>
                      <th className="px-4 py-2 font-medium">Languages</th>
                      <th className="px-4 py-2 font-medium">Open issues</th>
                      <th className="px-4 py-2 font-medium">Critical</th>
                      <th className="px-4 py-2 font-medium">Last scan</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.per_project.map((p) => (
                      <tr key={p.id} className="border-b border-border last:border-0">
                        <td className="px-4 py-2 font-medium">{p.name}</td>
                        <td className="px-4 py-2 text-muted">{Object.keys(p.languages).join(", ") || "—"}</td>
                        <td className="px-4 py-2 tabular-nums">{p.open}</td>
                        <td className={cx("px-4 py-2 tabular-nums", p.critical > 0 && "font-semibold text-red-600 dark:text-red-400")}>{p.critical}</td>
                        <td className="px-4 py-2 text-muted">{timeAgo(p.last_scan)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          )}
        </div>
      )}
    </>
  );
}

function Stat({ icon, label: text, value, sub, tone, onClick }: {
  icon: React.ReactNode; label: string; value: number; sub: string; tone?: "good" | "warn"; onClick?: () => void;
}) {
  const Tag = onClick ? "button" : "div";
  return (
    <Tag onClick={onClick} className={cx("rounded-lg border border-border bg-panel p-3 text-left", onClick && "hover:bg-hover")}>
      <div className="flex items-center gap-2 text-muted [&_svg]:size-4">{icon}<span className="truncate">{text}</span></div>
      <div className={cx("mt-1 text-2xl font-semibold tabular-nums", tone === "good" && "text-emerald-600 dark:text-emerald-400", tone === "warn" && "text-amber-600 dark:text-amber-400")}>{value}</div>
      <div className="truncate text-[12px] text-muted">{sub}</div>
    </Tag>
  );
}

function Mini({ label: text, value, tone }: { label: string; value: number; tone?: "good" | "bad" }) {
  return (
    <div className="rounded-md bg-panel2 px-3 py-2 ring-1 ring-border">
      <div className="text-[12px] text-muted">{text}</div>
      <div className={cx("text-lg font-semibold tabular-nums", tone === "good" && "text-emerald-600 dark:text-emerald-400", tone === "bad" && "text-red-600 dark:text-red-400")}>{value}</div>
    </div>
  );
}

function SeverityBars({ counts }: { counts: Record<Severity, number> }) {
  const max = Math.max(1, ...Object.values(counts));
  return (
    <div className="space-y-2.5">
      {(["critical", "high", "medium", "low"] as Severity[]).map((s) => (
        <div key={s} className="grid grid-cols-[72px_1fr_32px] items-center gap-2">
          <span className="text-muted">{label(s)}</span>
          <span className="h-2.5 overflow-hidden rounded-full bg-panel2 ring-1 ring-border">
            <span className={cx("block h-full rounded-full", severityBar[s])} style={{ width: `${(counts[s] / max) * 100}%` }} />
          </span>
          <span className="text-right font-semibold tabular-nums">{counts[s]}</span>
        </div>
      ))}
    </div>
  );
}

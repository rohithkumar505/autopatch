import { CheckCircle2, Cpu, KeyRound, Languages, Save, ShieldAlert, ShieldCheck, TestTube2, XCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { api, type SandboxInfo, type Settings } from "../api";
import { Button, Card, ErrorNote, Field, JobLog, Notice, PageHeader, Spinner, Toggle, cx, inputCls } from "../components/ui";
import { useApp, useJob, useLoad } from "../lib";

const EFFORTS = ["low", "medium", "high", "xhigh", "max"];

export default function SettingsPage() {
  const { toast, refreshStatus } = useApp();
  const loaded = useLoad(() => api.settings(), []);
  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const [apiKey, setApiKey] = useState("");
  const [saving, setSaving] = useState(false);
  const [aiTest, setAiTest] = useState<{ ok: boolean; message: string } | null>(null);
  const [testing, setTesting] = useState(false);

  if (loaded.error) return <ErrorNote>{loaded.error}</ErrorNote>;
  if (!loaded.data) return <Spinner />;
  const s = { ...loaded.data, ...draft } as Settings;
  const set = (key: string, value: unknown) => setDraft((d) => ({ ...d, [key]: value }));
  const dirty = Object.keys(draft).length > 0 || apiKey.length > 0;

  const saveAll = async () => {
    setSaving(true);
    try {
      const changes: Record<string, unknown> = { ...draft };
      if (apiKey) changes["ai.anthropic.api_key"] = apiKey;
      const next = await api.saveSettings(changes);
      loaded.setData(next);
      setDraft({});
      setApiKey("");
      setAiTest(null);
      refreshStatus();
      toast("success", "Settings saved");
    } catch (e) {
      toast("error", (e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <PageHeader
        title="Settings"
        subtitle="AI provider, analysis options, testing and sandbox."
        actions={<>
          {dirty && <Button onClick={() => { setDraft({}); setApiKey(""); }}>Discard changes</Button>}
          <Button kind="primary" icon={<Save className="size-3.5" />} busy={saving} disabled={!dirty} onClick={saveAll}>Save settings</Button>
        </>}
      />
      <div className="grid gap-4 xl:grid-cols-2">
        <Card title={<span className="flex items-center gap-2"><Cpu className="size-4" />Analysis engine</span>}>
          <div className="space-y-4">
            <div className="grid gap-2 sm:grid-cols-2">
              {([
                ["builtin", "AutoPatch Core (built in)", "Our own engine: no key, runs locally, proves bugs by running the code"],
                ["anthropic", "Claude (optional)", "External AI; needs an Anthropic API key"],
                ["ollama", "Local model (optional)", "An open model through Ollama"],
                ["none", "Off", "Static checks and file intelligence only"],
              ] as const).map(([id, title, hint]) => (
                <button key={id} onClick={() => set("ai.provider", id)}
                  className={cx("rounded-md border p-3 text-left", s["ai.provider"] === id ? "border-accent bg-accent/10" : "border-border hover:bg-hover")}>
                  <div className="font-medium">{title}</div>
                  <div className="text-[12px] text-muted">{hint}</div>
                </button>
              ))}
            </div>
            {s["ai.provider"] === "anthropic" && (
              <>
                <Field label={<span className="flex items-center gap-1.5"><KeyRound className="size-3.5" />API key</span>}
                  hint={s["ai.anthropic.api_key"].set
                    ? `A key ending ${s["ai.anthropic.api_key"].hint} is saved. Type a new one to replace it.`
                    : s["ai.anthropic.env_key"] ? "Using ANTHROPIC_API_KEY from the environment. A key entered here takes precedence."
                      : "Create a key at console.anthropic.com. It is stored only in AutoPatch's local database."}>
                  <input type="password" autoComplete="off" className={inputCls} value={apiKey} onChange={(e) => setApiKey(e.target.value)}
                    placeholder={s["ai.anthropic.api_key"].set ? "••••••••••••" : "sk-ant-..."} />
                </Field>
                <Field label="Workspace ID (optional)"
                  hint="Only needed if Test connection says your key is not tied to a workspace. Find it in the Anthropic Console under Settings > Workspaces.">
                  <input className={inputCls} value={s["ai.anthropic.workspace_id"] ?? ""} onChange={(e) => set("ai.anthropic.workspace_id", e.target.value)} placeholder="wrkspc_..." />
                </Field>
                <Field label="Model" hint="Default: claude-opus-5-5">
                  <input className={inputCls} value={s["ai.anthropic.model"]} onChange={(e) => set("ai.anthropic.model", e.target.value)} />
                </Field>
                <Toggle checked={s["ai.anthropic.fallbacks"]} onChange={(v) => set("ai.anthropic.fallbacks", v)}
                  label="Fallback model on safety declines" hint="If the model declines a request (this can happen when analyzing security bugs), the API retries on Anthropic's recommended fallback model." />
              </>
            )}
            {s["ai.provider"] === "ollama" && (
              <>
                <Field label="Ollama URL"><input className={inputCls} value={s["ai.ollama.url"]} onChange={(e) => set("ai.ollama.url", e.target.value)} /></Field>
                <Field label="Model" hint="Must be pulled already, e.g. `ollama pull qwen2.5-coder:14b`. Local models are much weaker at multi-step fixes.">
                  <input className={inputCls} value={s["ai.ollama.model"]} onChange={(e) => set("ai.ollama.model", e.target.value)} />
                </Field>
              </>
            )}
            {s["ai.provider"] === "builtin" && (
              <Notice kind="success">AutoPatch Core needs no API key and sends nothing off this computer. It detects bugs with its own analyzers, proves them by running the code in the sandbox, and repairs them with validated fixes.</Notice>
            )}
            {s["ai.provider"] !== "none" && s["ai.provider"] !== "builtin" && (
              <>
                <div className="grid gap-3 sm:grid-cols-3">
                  <Field label="Scan effort">
                    <select className={inputCls} value={s["ai.effort.scan"]} onChange={(e) => set("ai.effort.scan", e.target.value)}>
                      {EFFORTS.map((x) => <option key={x}>{x}</option>)}
                    </select>
                  </Field>
                  <Field label="Fix effort">
                    <select className={inputCls} value={s["ai.effort.fix"]} onChange={(e) => set("ai.effort.fix", e.target.value)}>
                      {EFFORTS.map((x) => <option key={x}>{x}</option>)}
                    </select>
                  </Field>
                  <Field label="Max code sent (KB)">
                    <input type="number" min={20} max={2000} className={inputCls} value={s["ai.max_context_kb"]} onChange={(e) => set("ai.max_context_kb", Number(e.target.value))} />
                  </Field>
                </div>
                <p className="text-[12px] text-muted">Higher effort finds more and writes better fixes, but takes longer and uses more tokens.</p>
                <div className="flex flex-wrap items-center gap-3">
                  <Button busy={testing} disabled={dirty} title={dirty ? "Save first" : undefined}
                    onClick={async () => { setTesting(true); try { setAiTest(await api.testAI()); } catch (e) { setAiTest({ ok: false, message: (e as Error).message }); } finally { setTesting(false); } }}>
                    Test connection
                  </Button>
                  {dirty && <span className="text-[12px] text-muted">Save before testing.</span>}
                  {aiTest && (
                    <span className={cx("flex items-center gap-1.5", aiTest.ok ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400")}>
                      {aiTest.ok ? <CheckCircle2 className="size-4" /> : <XCircle className="size-4" />}{aiTest.message}
                    </span>
                  )}
                </div>
              </>
            )}
          </div>
        </Card>

        <div className="space-y-4">
          <Card title="Analysis">
            <div className="space-y-3">
              <Toggle checked={s["analysis.ruff"]} onChange={(v) => set("analysis.ruff", v)} label="Ruff" hint="On by default for new scans" />
              <Toggle checked={s["analysis.pylint"]} onChange={(v) => set("analysis.pylint", v)} label="Pylint" hint="On by default for new scans" />
              <Toggle checked={s["analysis.ai_scan"]} onChange={(v) => set("analysis.ai_scan", v)} label="Deep analysis" hint="On by default for new scans" />
            </div>
          </Card>
          <Card title="Limits">
            <div className="space-y-3">
              <p className="text-[12px] text-muted">0 means no limit. Anything over a limit is reported with the reason, never skipped silently.</p>
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label="Max file size (MB)"><input type="number" min={0} className={inputCls} value={Number(s["limits.max_file_mb"] ?? 0)} onChange={(e) => set("limits.max_file_mb", Number(e.target.value))} /></Field>
                <Field label="Max project size (MB)"><input type="number" min={0} className={inputCls} value={Number(s["limits.max_project_mb"] ?? 0)} onChange={(e) => set("limits.max_project_mb", Number(e.target.value))} /></Field>
                <Field label="Max number of files"><input type="number" min={0} className={inputCls} value={Number(s["limits.max_files"] ?? 0)} onChange={(e) => set("limits.max_files", Number(e.target.value))} /></Field>
                <Field label="Deep inspection up to (MB)" hint="Bigger files are identified from their header and marked as not fully analyzed.">
                  <input type="number" min={1} className={inputCls} value={Number(s["limits.analysis_max_mb"] ?? 25)} onChange={(e) => set("limits.analysis_max_mb", Number(e.target.value))} />
                </Field>
              </div>
            </div>
          </Card>
          <Card title={<span className="flex items-center gap-2"><TestTube2 className="size-4" />Testing</span>}>
            <div className="space-y-3">
              <Toggle checked={s["testing.generate_tests"]} onChange={(v) => set("testing.generate_tests", v)} label="Generate tests for each fix"
                hint="Tests that reproduce the bug are written before the fix and run on both versions. Without them a fix can only be partially validated." />
              <div className="grid gap-3 sm:grid-cols-3">
                <Field label="Timeout per run (s)"><input type="number" min={10} max={1800} className={inputCls} value={s["testing.timeout_s"]} onChange={(e) => set("testing.timeout_s", Number(e.target.value))} /></Field>
                <Field label="Memory limit (MB)"><input type="number" min={128} max={16384} className={inputCls} value={s["testing.memory_mb"]} onChange={(e) => set("testing.memory_mb", Number(e.target.value))} /></Field>
                <Field label="Fix attempts"><input type="number" min={1} max={6} className={inputCls} value={s["testing.max_fix_attempts"]} onChange={(e) => set("testing.max_fix_attempts", Number(e.target.value))} /></Field>
              </div>
              <Field label="Sandbox">
                <select className={inputCls} value={s["testing.sandbox"]} onChange={(e) => set("testing.sandbox", e.target.value)}>
                  <option value="auto">Automatic (strongest available)</option>
                  <option value="docker">Docker</option>
                  <option value="appcontainer">Windows AppContainer</option>
                  <option value="local">Local process (limited isolation)</option>
                </select>
              </Field>
              <Toggle checked={s["testing.allow_limited"]} onChange={(v) => set("testing.allow_limited", v)} label="Allow limited isolation"
                hint="Lets tests run as a restricted local process when no strong sandbox is ready. Not recommended for code you do not trust." />
            </div>
          </Card>
        </div>

        <SandboxCard />
        <LanguagesCard />
      </div>
    </>
  );
}

function SandboxCard() {
  const { toast, refreshStatus } = useApp();
  const sb = useLoad(() => api.sandbox(), []);
  const [jobId, setJobId] = useState<string | null>(null);
  const [preparing, setPreparing] = useState<string | null>(null);
  const [selfTest, setSelfTest] = useState<Record<string, { passed: boolean; checks: { check: string; ok: boolean; result: string }[] }>>({});
  const job = useJob(jobId, (j) => {
    toast(j.status === "succeeded" ? "success" : "error", j.status === "succeeded" ? "Sandbox ready" : j.error);
    setPreparing(null);
    sb.reload();
    refreshStatus();
  });
  useEffect(() => { if (!jobId) setPreparing(null); }, [jobId]);

  const local = sb.data?.backends.find((b) => b.name === "local");
  const docker = sb.data?.backends.find((b) => b.name === "docker");
  const canUseLocal = Boolean(!sb.data?.active && local?.available && local?.ready);

  return (
    <Card title="Test sandbox" className="xl:col-span-2">
      {!sb.data ? <Spinner /> : (
        <div className="space-y-3">
          {sb.data.active ? (
            <Notice kind="success">Tests currently run in: <b>{sb.data.backends.find((b) => b.name === sb.data!.active)?.label}</b></Notice>
          ) : (
            <>
              <Notice kind="warning">{sb.data.error}</Notice>
              {canUseLocal && (
                <Notice kind="warning">
                  <b>On this Mac:</b> Docker is not running ({docker?.detail ?? "unavailable"}), and AppContainer is Windows-only.
                  Turn on <b>Allow limited isolation</b> in the Testing card above and click <b>Save</b>, or start Docker Desktop and use <b>Set up sandbox</b> on the Docker card for stronger isolation.
                  <div className="mt-2">
                    <Button
                      kind="primary"
                      onClick={async () => {
                        try {
                          await api.saveSettings({ "testing.allow_limited": true });
                          toast("success", "Local sandbox enabled");
                          sb.reload();
                          refreshStatus();
                        } catch (e) {
                          toast("error", (e as Error).message);
                        }
                      }}
                    >
                      Enable local sandbox for testing
                    </Button>
                  </div>
                </Notice>
              )}
            </>
          )}
          <div className="grid gap-3 lg:grid-cols-3">
            {sb.data.backends.map((b: SandboxInfo) => (
              <div key={b.name} className={cx("rounded-md border p-3", sb.data!.active === b.name ? "border-emerald-500/50" : "border-border")}>
                <div className="flex items-center gap-2 font-medium">
                  {b.isolation === "strong" ? <ShieldCheck className="size-4 text-emerald-500" /> : <ShieldAlert className="size-4 text-amber-500" />}
                  {b.label}
                </div>
                <div className="mt-1 text-[12px]">
                  {!b.available ? <span className="text-muted">Not available: {b.detail}</span> : b.ready ? <span className="text-emerald-600 dark:text-emerald-400">{b.detail}</span> : <span className="text-amber-600 dark:text-amber-400">{b.detail}</span>}
                </div>
                <ul className="mt-2 list-disc space-y-0.5 pl-4 text-[12px] text-muted">{b.guarantees.map((g) => <li key={g}>{g}</li>)}</ul>
                {b.available && (
                  <div className="mt-3 flex flex-wrap gap-2">
                    {(!b.ready || b.name === "appcontainer") && (
                      <Button kind={b.ready ? "secondary" : "primary"} busy={preparing === b.name}
                        onClick={async () => {
                          setPreparing(b.name);
                          try { setJobId((await api.prepareSandbox(b.name)).job_id); } catch (e) { toast("error", (e as Error).message); setPreparing(null); }
                        }}>
                        {b.ready ? "Set up again" : "Set up sandbox"}
                      </Button>
                    )}
                    {b.ready && b.isolation === "strong" && (
                      <Button onClick={async () => { try { const r = await api.selfTest(b.name); setSelfTest((m) => ({ ...m, [b.name]: r })); } catch (e) { toast("error", (e as Error).message); } }}>
                        Run isolation self-test
                      </Button>
                    )}
                  </div>
                )}
                {selfTest[b.name] && (
                  <ul className="mt-2 space-y-1 text-[12px]">
                    {selfTest[b.name].checks.map((c) => (
                      <li key={c.check} className="flex items-center gap-1.5">
                        {c.ok ? <CheckCircle2 className="size-3.5 text-emerald-500" /> : <XCircle className="size-3.5 text-red-500" />}
                        {c.check}: <b>{c.result}</b>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            ))}
          </div>
          {job && <JobLog job={job} title="Sandbox setup" />}
          <p className="text-[12px] text-muted">
            Setting up the Windows sandbox copies a private Python runtime into AutoPatch's data folder and creates a
            low-privilege AppContainer identity that can read only that runtime and write only to the test folder.
            Your own Python installation and system settings are not changed.
          </p>
        </div>
      )}
    </Card>
  );
}

function LanguagesCard() {
  const langs = useLoad(() => api.languages(), []);
  return (
    <Card title={<span className="flex items-center gap-2"><Languages className="size-4" />Supported languages</span>} className="xl:col-span-2">
      {!langs.data ? <Spinner /> : (
        <div className="space-y-3">
          {langs.data.map((l) => (
            <div key={l.language}>
              <div className="font-medium">{l.language} <span className="text-[12px] font-normal text-muted">({l.extensions.join(", ")}, tests with {l.test_framework})</span></div>
              <div className="mt-2 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
                {l.tools.map((t) => (
                  <div key={t.name} className="rounded-md bg-panel2 p-2 ring-1 ring-border">
                    <div className="flex items-center gap-1.5 font-medium">
                      {t.available ? <CheckCircle2 className="size-3.5 text-emerald-500" /> : <XCircle className="size-3.5 text-red-500" />}
                      {t.name} <span className="font-normal text-muted">{t.version}</span>
                    </div>
                    <div className="text-[12px] text-muted">{t.purpose}</div>
                  </div>
                ))}
              </div>
            </div>
          ))}
          <p className="text-[12px] text-muted">
            Other languages (JavaScript, TypeScript, Java and more) are detected and shown in the IDE, but are not
            analyzed yet. Each language is a separate analyzer module, so support can be added without changing the rest.
          </p>
        </div>
      )}
    </Card>
  );
}

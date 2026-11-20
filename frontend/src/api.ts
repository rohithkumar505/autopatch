// Typed client for the AutoPatch backend API.

export type Severity = "critical" | "high" | "medium" | "low";
export type Verification = "possible" | "confirmed" | "not_reproduced";
export type FindingStatus = "open" | "fixing" | "patch_ready" | "fixed" | "patched_unverified" | "rejected" | "dismissed";
export type PatchStatus = "generating" | "ready" | "failed" | "accepted" | "rejected" | "reverted";
export type Validation = "validated" | "partially_validated" | "failed" | "not_run";

export interface Project {
  id: number;
  name: string;
  source_type: string;
  source_ref: string;
  original_version: string;
  languages: Record<string, number>;
  dependencies: string[];
  file_count: number;
  created_at: string;
  updated_at: string;
  last_scan: { id: number; finished_at: string; version: string } | null;
  open_findings: number;
  fixed_findings: number;
  pending_patches: number;
  head?: string;
  versions?: number;
}

export interface Scan {
  id: number;
  project_id: number;
  version: string;
  status: "queued" | "running" | "completed" | "failed";
  options: Record<string, boolean>;
  summary: string;
  stats: {
    files?: number;
    analyzed_files?: number;
    symbols?: number;
    languages?: Record<string, number>;
    unsupported?: string[];
    tools?: Record<string, { status: string; issues?: number; error?: string }>;
    ai?: { used: boolean; reason?: string; model?: string; findings?: number; usage?: AIUsage };
    counts?: Record<Severity, number>;
  };
  error: string;
  job_id: string;
  started_at: string;
  finished_at: string;
}

export interface AIUsage {
  provider: string;
  model: string;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  duration_s: number;
}

export interface Finding {
  id: number;
  project_id: number;
  scan_id: number;
  title: string;
  description: string;
  file: string;
  function: string;
  line_start: number;
  line_end: number;
  code_snippet?: string;
  snippet_start: number;
  category: string;
  severity: Severity;
  confidence: number;
  sources: string[];
  root_cause: string;
  trigger: string;
  expected_behavior: string;
  impact: string;
  related: string[];
  severity_reason: string;
  analyzed: boolean;
  verification: Verification;
  status: FindingStatus;
  priority: number;
  created_at: string;
  latest_patch?: { id: number; status: PatchStatus; validation: Validation } | null;
  patches?: { id: number; status: PatchStatus; validation: Validation; created_at: string; attempts: number; error: string; job_id: string }[];
}

export interface TestCase {
  id: string;
  name: string;
  file: string;
  status: "passed" | "failed" | "error" | "skipped";
  message: string;
  kind: string;
}

export interface TestRun {
  id: number;
  project_id: number;
  patch_id: number | null;
  finding_id: number | null;
  kind: "project" | "original" | "patched";
  version: string;
  status: string;
  passed: number;
  failed: number;
  errors: number;
  skipped: number;
  duration_s: number;
  results?: TestCase[];
  output?: string;
  sandbox: string;
  created_at: string;
  total?: number;
}

export interface GeneratedCaseResult {
  id: string;
  name: string;
  kind: string;
  description: string;
  before: string;
  after: string;
  message_before: string;
  message_after: string;
}

export interface ValidationDetail {
  verdict?: Validation;
  reproduced?: string[];
  reproduce_tests?: string[];
  fixed?: string[];
  still_failing?: string[];
  regressions?: string[];
  generated_failing?: string[];
  existing?: { before: { passed: number; total: number }; after: { passed: number; total: number }; failing_before: string[] };
  attempts?: { attempt: number; result: string; detail: string }[];
  scope?: {
    changed_ranges: [number, number][];
    lines_changed: number;
    function: string | null;
    function_lines: [number, number] | null;
    outside_function: [number, number][];
  };
  sandbox?: string;
  ai_usage?: AIUsage[];
  before_status?: string;
  after_status?: string;
  revalidated_at?: string;
}

export interface PatchSummary {
  id: number;
  finding_id: number;
  finding_title: string;
  severity: Severity;
  file: string;
  status: PatchStatus;
  validation: Validation;
  attempts: number;
  created_at: string;
  decided_at: string;
  decision_note: string;
  applied_version: string;
  stale: boolean;
  error: string;
  job_id: string;
}

export interface Patch {
  id: number;
  project_id: number;
  finding_id: number;
  job_id: string;
  base_version: string;
  status: PatchStatus;
  file: string;
  before_content: string;
  after_content: string;
  diff: string;
  edits: { original_code: string; patched_code: string }[];
  explanation: string;
  behavior_change: string;
  test_file: string;
  test_code: string;
  test_cases: GeneratedCaseResult[];
  attempts: number;
  validation: Validation;
  validation_detail: ValidationDetail;
  error: string;
  decision_note: string;
  applied_version: string;
  tests_added: boolean;
  created_at: string;
  decided_at: string;
  stale: boolean;
  finding: Finding;
  test_runs: TestRun[];
}

export interface JobProgress {
  time: string;
  level: "info" | "warning" | "error";
  message: string;
}

export interface Job {
  id: string;
  kind: string;
  project_id: number | null;
  status: "queued" | "running" | "succeeded" | "failed";
  progress: JobProgress[];
  result: Record<string, unknown>;
  error: string;
  created_at: string;
  updated_at: string;
}

export interface TreeNode {
  name: string;
  path: string;
  type: "dir" | "file";
  language?: string;
  findings?: number;
  children?: TreeNode[];
}

export interface FileContent {
  path: string;
  version: string;
  size: number;
  language: string;
  monaco_language: string;
  binary: boolean;
  content: string;
  findings: { id: number; title: string; severity: Severity; line_start: number; line_end: number; status: string; verification: Verification }[];
}

export interface SymbolInfo {
  qualname: string;
  name: string;
  kind: "function" | "class" | "method";
  file: string;
  line_start: number;
  line_end: number;
  params: string[];
  doc: string;
  calls: string[];
  called_by: string[];
  tested_by: string[];
}

export interface ModuleInfo {
  file: string;
  module: string;
  is_test: boolean;
  lines: number;
  imports: string[];
  imported_by: string[];
  external_imports: string[];
  symbols: string[];
  is_entry_point: boolean;
  parse_error: string;
}

export interface Structure {
  language: string;
  modules: ModuleInfo[];
  symbols: SymbolInfo[];
  declared_dependencies: string[];
  external_imports: string[];
  languages: Record<string, number>;
  syntax_errors: { file: string; line: number; message: string }[];
}

export interface Version {
  sha: string;
  short: string;
  message: string;
  date: string;
  parent: string | null;
  is_head: boolean;
  files_changed: string[];
  patch: { id: number; status: PatchStatus; finding_id: number; validation: Validation } | null;
}

export interface EventRow {
  id: number;
  project_id: number | null;
  kind: string;
  message: string;
  data: Record<string, unknown>;
  created_at: string;
}

export interface Dashboard {
  projects: number;
  findings: {
    detected: number;
    open: number;
    confirmed: number;
    possible: number;
    dismissed: number;
    by_severity: Record<Severity, number>;
    by_category: Record<string, number>;
  };
  resolved: number;
  patches: { pending: number; accepted: number; rejected: number; reverted: number; validated: number; failed_validation: number };
  tests: { runs: number; passed_runs: number; tests_executed: number; latest: TestRun | null };
  top_findings: { id: number; project_id: number; title: string; severity: Severity; confidence: number; file: string; line_start: number; verification: Verification; status: FindingStatus }[];
  per_project: { id: number; name: string; languages: Record<string, number>; open: number; critical: number; last_scan: string | null }[];
  recent_events: EventRow[];
}

export interface SandboxInfo {
  name: string;
  label: string;
  isolation: "strong" | "limited";
  available: boolean;
  ready: boolean;
  detail: string;
  guarantees: string[];
}

export interface AppStatus {
  version: string;
  ai: { configured: boolean; provider: string; model?: string; message?: string };
  sandbox: { ready: boolean; name?: string; label?: string; isolation?: string; message?: string };
  active_jobs: number;
}

export type Settings = Record<string, unknown> & {
  "ai.provider": "builtin" | "anthropic" | "ollama" | "none";
  "ai.anthropic.model": string;
  "ai.anthropic.api_key": { set: boolean; hint: string };
  "ai.anthropic.env_key": boolean;
  "ai.anthropic.fallbacks": boolean;
  "ai.anthropic.workspace_id": string;
  "ai.ollama.url": string;
  "ai.ollama.model": string;
  "ai.effort.scan": string;
  "ai.effort.fix": string;
  "ai.max_context_kb": number;
  "analysis.ruff": boolean;
  "analysis.pylint": boolean;
  "analysis.ai_scan": boolean;
  "testing.sandbox": string;
  "testing.allow_limited": boolean;
  "testing.timeout_s": number;
  "testing.memory_mb": number;
  "testing.generate_tests": boolean;
  "testing.max_fix_attempts": number;
};

export interface LanguageSupport {
  language: string;
  extensions: string[];
  test_framework: string;
  tools: { name: string; purpose: string; available: boolean; version: string }[];
}

export interface SearchHit {
  path: string;
  line: number;
  col: number;
  preview: string;
  kind: "filename" | "content";
  language?: string;
}

export interface IdeProblem {
  id: number;
  title: string;
  file: string;
  line: number;
  end_line: number;
  severity: Severity;
  category: string;
  status: FindingStatus;
  verification: Verification;
  confidence: number;
}

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function request<T>(method: string, url: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method, headers: {} };
  if (body instanceof FormData) {
    init.body = body;
  } else if (body !== undefined) {
    init.body = JSON.stringify(body);
    (init.headers as Record<string, string>)["Content-Type"] = "application/json";
  }
  let res: Response;
  try {
    res = await fetch(url, init);
  } catch {
    throw new ApiError("Cannot reach the AutoPatch server. Is it running?", 0);
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
    } catch {
      /* not JSON */
    }
    throw new ApiError(detail || `Request failed (${res.status})`, res.status);
  }
  return (await res.json()) as T;
}

const q = (params: Record<string, string | number | undefined | null>) => {
  const entries = Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== "");
  return entries.length ? "?" + new URLSearchParams(entries.map(([k, v]) => [k, String(v)])).toString() : "";
};

export const api = {
  status: () => request<AppStatus>("GET", "/api/status"),
  dashboard: (projectId?: number) => request<Dashboard>("GET", `/api/dashboard${q({ project_id: projectId })}`),

  projects: () => request<Project[]>("GET", "/api/projects"),
  project: (id: number) => request<Project>("GET", `/api/projects/${id}`),
  renameProject: (id: number, name: string) => request<Project>("PATCH", `/api/projects/${id}`, { name }),
  deleteProject: (id: number) => request<{ deleted: number }>("DELETE", `/api/projects/${id}`),
  uploadFiles: (form: FormData) => request<Project>("POST", "/api/projects/upload", form),
  uploadZip: (form: FormData) => request<Project>("POST", "/api/projects/upload-zip", form),
  importPath: (path: string, name: string) => request<Project>("POST", "/api/projects/import-path", { path, name }),
  cloneRepo: (url: string, name: string) => request<Project>("POST", "/api/projects/clone", { url, name }),
  tree: (id: number, version?: string) => request<{ version: string; files: number; root: TreeNode }>("GET", `/api/projects/${id}/tree${q({ version })}`),
  fsTree: (id: number) => request<{ version: string; files: number; dirs: number; root: TreeNode; source: string }>("GET", `/api/projects/${id}/fs/tree`),
  file: (id: number, path: string, version?: string) => request<FileContent>("GET", `/api/projects/${id}/file${q({ path, version })}`),
  saveFile: (id: number, path: string, content: string, commit = true, message = "") =>
    request<{ path: string; version: string; committed: boolean }>("PUT", `/api/projects/${id}/file`, { path, content, commit, message }),
  createFs: (id: number, path: string, kind: "file" | "dir", content = "") =>
    request<{ path: string; kind: string; version: string; committed: boolean }>("POST", `/api/projects/${id}/fs`, { path, kind, content, commit: true }),
  renameFs: (id: number, path: string, new_path: string) =>
    request<{ path: string; from: string; version: string; committed: boolean }>("POST", `/api/projects/${id}/fs/rename`, { path, new_path, commit: true }),
  moveFs: (id: number, path: string, new_path: string) =>
    request<{ path: string; from: string; version: string; committed: boolean }>("POST", `/api/projects/${id}/fs/move`, { path, new_path, commit: true }),
  copyFs: (id: number, path: string, new_path: string) =>
    request<{ path: string; from: string; version: string; committed: boolean }>("POST", `/api/projects/${id}/fs/copy`, { path, new_path, commit: true }),
  deleteFs: (id: number, path: string) =>
    request<{ deleted: string; version: string; committed: boolean }>("DELETE", `/api/projects/${id}/fs${q({ path, commit: "true" })}`),
  uploadFs: (id: number, form: FormData) =>
    request<{ files: string[]; version: string; committed: boolean }>("POST", `/api/projects/${id}/fs/upload`, form),
  downloadFsUrl: (id: number, path: string) => `/api/projects/${id}/fs/download${q({ path })}`,
  fsStat: (id: number, path: string) =>
    request<{
      path: string; type: "file" | "dir"; size: number; language: string; monaco_language: string;
      mime?: string; binary: boolean; preview: string; image_base64?: string; content?: string; children?: number;
    }>("GET", `/api/projects/${id}/fs/stat${q({ path })}`),
  search: (id: number, query: string) =>
    request<{ query: string; hits: SearchHit[]; truncated: boolean }>("GET", `/api/projects/${id}/search${q({ q: query })}`),
  gitStatus: (id: number) =>
    request<{ head: string; changes: { path: string; status: string }[]; versions: Version[] }>("GET", `/api/projects/${id}/git/status`),
  problems: (id: number) =>
    request<{ scan: { id: number; finished_at: string; version: string } | null; problems: IdeProblem[] }>("GET", `/api/projects/${id}/problems`),
  formatFile: (id: number, path: string, content: string, commit = false) =>
    request<{ path: string; content: string; changed: boolean; tool: string; monaco_language: string }>(
      "POST", `/api/projects/${id}/format`, { path, content, commit },
    ),
  gotoDefinition: (id: number, path: string, line: number, column: number, word = "") =>
    request<{ word: string; definitions: { name: string; qualname: string; kind: string; file: string; line: number; end_line: number }[] }>(
      "POST", `/api/projects/${id}/definition`, { path, line, column, word },
    ),
  completions: (id: number, path = "") =>
    request<{ items: { label: string; detail: string; kind: string; file: string; line: number; insertText: string; documentation: string }[]; path: string }>(
      "GET", `/api/projects/${id}/completions${q({ path })}`,
    ),
  structure: (id: number) => request<Structure>("GET", `/api/projects/${id}/structure`),
  downloadUrl: (id: number, version?: string) => `/api/projects/${id}/download${q({ version })}`,

  scans: (id: number) => request<Scan[]>("GET", `/api/projects/${id}/scans`),
  startScan: (id: number, options: { ruff?: boolean; pylint?: boolean; ai?: boolean }) =>
    request<{ scan: Scan; job_id: string }>("POST", `/api/projects/${id}/scans`, options),
  scan: (scanId: number) => request<Scan>("GET", `/api/scans/${scanId}`),
  findings: (id: number, scanId?: number) => request<{ scan: Scan | null; findings: Finding[] }>("GET", `/api/projects/${id}/findings${q({ scan_id: scanId })}`),
  finding: (findingId: number) => request<Finding>("GET", `/api/findings/${findingId}`),
  analyze: (findingId: number) => request<{ job_id: string }>("POST", `/api/findings/${findingId}/analyze`),
  fix: (findingId: number) => request<{ patch_id: number; job_id: string }>("POST", `/api/findings/${findingId}/fix`),
  dismiss: (findingId: number, note: string) => request<Finding>("POST", `/api/findings/${findingId}/dismiss`, { note }),
  reopen: (findingId: number) => request<Finding>("POST", `/api/findings/${findingId}/reopen`),

  patches: (id: number) => request<PatchSummary[]>("GET", `/api/projects/${id}/patches`),
  patch: (patchId: number) => request<Patch>("GET", `/api/patches/${patchId}`),
  accept: (patchId: number, body: { include_tests: boolean; force: boolean; note: string }) => request<Patch>("POST", `/api/patches/${patchId}/accept`, body),
  reject: (patchId: number, note: string) => request<Patch>("POST", `/api/patches/${patchId}/reject`, { note }),
  revalidate: (patchId: number) => request<{ job_id: string }>("POST", `/api/patches/${patchId}/revalidate`),
  revert: (patchId: number, note: string) => request<{ version: string }>("POST", `/api/patches/${patchId}/revert`, { note }),

  runTests: (id: number, version?: string) => request<{ job_id: string }>("POST", `/api/projects/${id}/test-runs`, { version }),
  testRuns: (id: number) => request<TestRun[]>("GET", `/api/projects/${id}/test-runs`),
  testRun: (runId: number) => request<TestRun>("GET", `/api/test-runs/${runId}`),

  versions: (id: number) => request<Version[]>("GET", `/api/projects/${id}/versions`),
  versionDiff: (id: number, sha: string) => request<{ sha: string; diff: string }>("GET", `/api/projects/${id}/versions/${sha}/diff`),
  restore: (id: number, version: string, note: string) => request<{ version: string | null; changed: boolean }>("POST", `/api/projects/${id}/restore`, { version, note }),
  events: (id: number) => request<EventRow[]>("GET", `/api/projects/${id}/events`),

  job: (jobId: string) => request<Job>("GET", `/api/jobs/${jobId}`),
  activeJobs: (projectId?: number) => request<Job[]>("GET", `/api/jobs${q({ project_id: projectId, active: "true" })}`),

  settings: () => request<Settings>("GET", "/api/settings"),
  saveSettings: (changes: Record<string, unknown>) => request<Settings>("PUT", "/api/settings", changes),
  testAI: () => request<{ ok: boolean; message: string }>("POST", "/api/settings/test-ai"),
  sandbox: () => request<{ active: string | null; error: string; backends: SandboxInfo[] }>("GET", "/api/sandbox"),
  prepareSandbox: (name: string) => request<{ job_id: string }>("POST", `/api/sandbox/${name}/prepare`),
  selfTest: (name: string) => request<{ passed: boolean; checks: { check: string; ok: boolean; result: string }[] }>("POST", `/api/sandbox/${name}/self-test`),
  languages: () => request<LanguageSupport[]>("GET", "/api/languages"),
};

export interface FileIssueInfo {
  code: string;
  title: string;
  severity: string;
  message: string;
  repairability: "fixable" | "partially_fixable" | "requires_human" | "not_fixable" | "unsupported";
  reason: string;
  options: string[];
  limitations: string;
  confidence: number;
  requirements: string;
  line: number;
  repair: string;
  finding_id: number | null;
}

export interface FileReportRow {
  id: number;
  path: string;
  size: number;
  format: string;
  mime: string;
  category: string;
  encoding: string;
  language: string;
  extension_mismatch: string;
  status: "analyzed" | "partially_analyzed" | "identified_only" | "skipped" | "unreadable";
  method: string;
  issues: FileIssueInfo[];
  notes: string[];
  reused: boolean;
  repairability: string;
  code_findings: { id: number; title: string; severity: Severity; status: string; verification: Verification; line_start: number }[];
}

export interface ImportReport {
  imported?: number;
  bytes?: number;
  skipped?: { path: string; files: number; reason: string }[];
}

export const fileApi = {
  reports: (id: number, scanId?: number) =>
    fetch(`/api/projects/${id}/file-reports${scanId ? `?scan_id=${scanId}` : ""}`).then(async (r) => {
      if (!r.ok) throw new ApiError((await r.json().catch(() => ({}))).detail ?? r.statusText, r.status);
      return (await r.json()) as {
        scan: Scan | null;
        files: FileReportRow[];
        summary: { files: number; with_issues: number; by_status: Record<string, number>; by_category: Record<string, number>; by_repairability: Record<string, number> };
        import_report: ImportReport;
      };
    }),
};

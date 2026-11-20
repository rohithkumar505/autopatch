import { Download, FileArchive, FileCode2, FolderInput, FolderOpen, GitBranch, Pencil, Play, Trash2, Upload } from "lucide-react";
import { useRef, useState } from "react";
import { api, type Project } from "../api";
import { Button, Card, Empty, ErrorNote, Field, Modal, PageHeader, Spinner, Tabs, inputCls } from "../components/ui";
import { navigate, run, timeAgo, useApp } from "../lib";

type Mode = "files" | "folder" | "zip" | "path" | "git";

export default function Projects() {
  const { projects, projectsLoaded, refreshProjects, setActive, toast, active } = useApp();
  const [renaming, setRenaming] = useState<Project | null>(null);
  const [deleting, setDeleting] = useState<Project | null>(null);

  const open = (p: Project, to = "ide") => {
    setActive(p.id);
    navigate(to);
  };

  return (
    <>
      <PageHeader title="Projects" subtitle="Upload, import and manage the code projects AutoPatch works on." />
      <div className="grid gap-4 xl:grid-cols-[420px_1fr]">
        <ImportPanel
          onImported={async (p) => {
            await refreshProjects();
            setActive(p.id);
            toast("success", `Imported ${p.name} (${p.file_count} files)`);
            navigate("ide");
          }}
        />
        <Card title={`Your projects (${projects.length})`} padded={false}>
          {!projectsLoaded ? (
            <Spinner />
          ) : projects.length === 0 ? (
            <Empty icon={<FolderOpen className="size-8" />} title="No projects yet">Import one with the panel on the left.</Empty>
          ) : (
            <ul className="divide-y divide-border">
              {projects.map((p) => (
                <li key={p.id} className={p.id === active?.id ? "bg-accent/5" : ""}>
                  <div className="flex flex-wrap items-start gap-3 px-4 py-3">
                    <div className="min-w-0 flex-1">
                      <button onClick={() => open(p)} className="text-[14px] font-semibold hover:underline">{p.name}</button>
                      {p.id === active?.id && <span className="ml-2 rounded bg-accent/15 px-1.5 py-0.5 text-[11px] text-accent">active</span>}
                      <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-muted">
                        <span>{p.file_count} files</span>
                        <span>{Object.entries(p.languages).map(([l, n]) => `${l} ${n}`).join(", ") || "no recognised languages"}</span>
                        <span title={p.source_ref}>from {p.source_type}</span>
                        <span>imported {timeAgo(p.created_at)}</span>
                        {p.dependencies.length > 0 && <span>depends on {p.dependencies.join(", ")}</span>}
                      </div>
                      <div className="mt-1.5 flex flex-wrap gap-3 text-[12px]">
                        <span><b className="tabular-nums">{p.open_findings}</b> open issues</span>
                        <span><b className="tabular-nums">{p.fixed_findings}</b> fixed</span>
                        <span><b className="tabular-nums">{p.pending_patches}</b> patches to review</span>
                        <span className="text-muted">last scan {timeAgo(p.last_scan?.finished_at)}</span>
                      </div>
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      <Button icon={<FolderOpen className="size-3.5" />} onClick={() => open(p)}>Open IDE</Button>
                      <Button icon={<Play className="size-3.5" />} onClick={() => open(p, "scanner")}>Scan</Button>
                      <a href={api.downloadUrl(p.id)} className="inline-flex items-center gap-1.5 rounded-md border border-border bg-panel2 px-2.5 py-1.5 hover:bg-hover" title="Download the current version as a zip">
                        <Download className="size-3.5" />
                      </a>
                      <Button kind="ghost" aria-label="Rename" title="Rename" onClick={() => setRenaming(p)} icon={<Pencil className="size-3.5" />} />
                      <Button kind="ghost" aria-label="Delete" title="Delete" onClick={() => setDeleting(p)} icon={<Trash2 className="size-3.5 text-red-500" />} />
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      {renaming && <RenameDialog project={renaming} onClose={() => setRenaming(null)} onDone={refreshProjects} />}
      {deleting && (
        <Modal
          title={`Delete ${deleting.name}?`}
          onClose={() => setDeleting(null)}
          footer={
            <>
              <Button onClick={() => setDeleting(null)}>Cancel</Button>
              <Button
                kind="danger"
                onClick={async () => {
                  const ok = await run(toast, () => api.deleteProject(deleting.id), `Deleted ${deleting.name}`);
                  setDeleting(null);
                  if (ok) refreshProjects();
                }}
              >
                Delete permanently
              </Button>
            </>
          }
        >
          <p>This removes AutoPatch's copy of the project: every version, scan, bug report, patch and test result. It cannot be undone.</p>
          <p className="text-muted">Your original files on disk are not touched. Download a zip first if you want to keep accepted changes.</p>
        </Modal>
      )}
    </>
  );
}

function ImportPanel({ onImported }: { onImported: (p: Project) => void }) {
  const [mode, setMode] = useState<Mode>("folder");
  const [name, setName] = useState("");
  const [path, setPath] = useState("");
  const [url, setUrl] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);

  const submit = async () => {
    setError("");
    setBusy(true);
    try {
      let project: Project;
      if (mode === "files" || mode === "folder") {
        const form = new FormData();
        for (const f of files) {
          form.append("files", f, f.name);
          form.append("paths", (f as File & { webkitRelativePath?: string }).webkitRelativePath || f.name);
        }
        form.append("name", name);
        project = await api.uploadFiles(form);
      } else if (mode === "zip") {
        const form = new FormData();
        form.append("file", files[0], files[0].name);
        form.append("name", name);
        project = await api.uploadZip(form);
      } else if (mode === "path") {
        project = await api.importPath(path, name);
      } else {
        project = await api.cloneRepo(url, name);
      }
      setFiles([]);
      setName("");
      if (fileInput.current) fileInput.current.value = "";
      onImported(project);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const ready = mode === "path" ? path.trim() : mode === "git" ? url.trim() : files.length > 0;
  const totalKb = Math.round(files.reduce((n, f) => n + f.size, 0) / 1024);

  return (
    <Card title="Import a project">
      <Tabs
        value={mode}
        onChange={(m) => {
          setMode(m);
          setFiles([]);
          setError("");
          if (fileInput.current) fileInput.current.value = "";
        }}
        tabs={[
          { id: "folder", label: <span className="flex items-center gap-1"><FolderInput className="size-3.5" />Folder</span> },
          { id: "files", label: <span className="flex items-center gap-1"><FileCode2 className="size-3.5" />Files</span> },
          { id: "zip", label: <span className="flex items-center gap-1"><FileArchive className="size-3.5" />Zip</span> },
          { id: "path", label: "Local path" },
          { id: "git", label: <span className="flex items-center gap-1"><GitBranch className="size-3.5" />Git</span> },
        ]}
      />
      <div className="mt-4 space-y-3">
        {(mode === "files" || mode === "folder" || mode === "zip") && (
          <label className="flex cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed border-border bg-panel2 px-4 py-8 text-center hover:border-accent">
            <Upload className="size-6 text-muted" />
            <span className="font-medium">
              {mode === "folder" ? "Choose a project folder" : mode === "zip" ? "Choose a .zip archive" : "Choose one or more source files"}
            </span>
            <span className="text-[12px] text-muted">
              {files.length ? `${files.length} file(s) selected, ${totalKb} KB` : "Caches, virtualenvs, node_modules and .git are skipped automatically"}
            </span>
            <input
              ref={fileInput}
              type="file"
              className="sr-only"
              multiple={mode !== "zip"}
              accept={mode === "zip" ? ".zip,application/zip" : undefined}
              onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
              {...(mode === "folder" ? ({ webkitdirectory: "", directory: "" } as Record<string, string>) : {})}
            />
          </label>
        )}
        {mode === "path" && (
          <Field label="Folder on this computer" hint="AutoPatch copies the folder; your original files are never modified.">
            <input className={inputCls} value={path} onChange={(e) => setPath(e.target.value)} placeholder="C:\\Users\\you\\code\\my-project" />
          </Field>
        )}
        {mode === "git" && (
          <Field label="Repository URL" hint="Clones the default branch (latest commit only).">
            <input className={inputCls} value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://github.com/owner/repo.git" />
          </Field>
        )}
        <Field label="Project name (optional)">
          <input className={inputCls} value={name} onChange={(e) => setName(e.target.value)} placeholder="Uses the folder or file name" />
        </Field>
        {error && <ErrorNote>{error}</ErrorNote>}
        <Button kind="primary" className="w-full" busy={busy} disabled={!ready} onClick={submit}>
          {busy ? (mode === "git" ? "Cloning..." : "Importing...") : "Import project"}
        </Button>
        <p className="text-[12px] text-muted">
          Each import becomes a versioned workspace. The upload is saved as the first version and is kept unchanged; every
          accepted fix becomes a new version you can roll back.
        </p>
      </div>
    </Card>
  );
}

function RenameDialog({ project, onClose, onDone }: { project: Project; onClose: () => void; onDone: () => void }) {
  const [name, setName] = useState(project.name);
  const { toast } = useApp();
  return (
    <Modal
      title="Rename project"
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            kind="primary"
            disabled={!name.trim()}
            onClick={async () => {
              if (await run(toast, () => api.renameProject(project.id, name), "Renamed")) onDone();
              onClose();
            }}
          >
            Save
          </Button>
        </>
      }
    >
      <input className={inputCls} value={name} onChange={(e) => setName(e.target.value)} autoFocus />
    </Modal>
  );
}

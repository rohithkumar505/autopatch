/**
 * Real workspace Explorer — create/rename/delete/copy/move/upload/download/DnD.
 * All operations hit AutoPatch FS APIs (no mock trees).
 */
import {
  ChevronDown, ChevronRight, ClipboardPaste, Copy, Download, File, FileCode2, FilePlus, Folder,
  FolderOpen, FolderPlus, Pencil, RefreshCw, Trash2, Upload,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, type TreeNode } from "../api";
import { Button, ErrorNote, Spinner, cx, inputCls } from "../components/ui";
import { run, useApp, useLoad } from "../lib";

type Clip = { mode: "copy" | "cut"; path: string; kind: "file" | "dir" } | null;
type Ctx = { x: number; y: number; node: TreeNode } | null;

const LANG_ICON: Record<string, string> = {
  Python: "text-amber-500",
  JavaScript: "text-yellow-500",
  TypeScript: "text-sky-500",
  JSON: "text-lime-600",
  Markdown: "text-fg",
  HTML: "text-orange-500",
  CSS: "text-pink-500",
  Go: "text-cyan-500",
  Rust: "text-orange-700",
  Java: "text-red-500",
  SQL: "text-indigo-400",
};

export function ExplorerPanel({
  projectId, selected, onOpenFile, onTreeChanged, onPathMoved,
}: {
  projectId: number;
  selected: string;
  onOpenFile: (path: string) => void;
  onTreeChanged: () => void;
  onPathMoved?: (from: string, to: string) => void;
}) {
  const { toast } = useApp();
  const tree = useLoad(() => api.fsTree(projectId), [projectId]);
  const uploadRef = useRef<HTMLInputElement>(null);
  const [filter, setFilter] = useState("");
  const [mode, setMode] = useState<"file" | "dir" | null>(null);
  const [newName, setNewName] = useState("");
  const [parentDir, setParentDir] = useState("");
  const [renaming, setRenaming] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [clipboard, setClipboard] = useState<Clip>(null);
  const [ctx, setCtx] = useState<Ctx>(null);
  const [dropTarget, setDropTarget] = useState<string | null>(null);
  const [dirtyPaths, setDirtyPaths] = useState<Set<string>>(new Set());

  const reload = useCallback(() => {
    tree.reload();
    onTreeChanged();
    api.gitStatus(projectId).then((g) => {
      setDirtyPaths(new Set(g.changes.map((c) => c.path)));
    }).catch(() => undefined);
  }, [tree, onTreeChanged, projectId]);

  useEffect(() => { reload(); }, [projectId]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const close = () => setCtx(null);
    window.addEventListener("click", close);
    return () => window.removeEventListener("click", close);
  }, []);

  const join = (dir: string, name: string) => (dir ? `${dir}/${name}` : name);

  const create = async () => {
    if (!newName.trim() || !mode) return;
    const path = join(parentDir, newName.trim().replace(/^\/+/, ""));
    const ok = await run(toast, () => api.createFs(projectId, path, mode), `Created ${path}`);
    if (ok) {
      setNewName("");
      setMode(null);
      reload();
      if (mode === "file") onOpenFile(path);
    }
  };

  const remove = async (path: string) => {
    if (!confirm(`Delete ${path}? This removes it from the project workspace.`)) return;
    const ok = await run(toast, () => api.deleteFs(projectId, path), `Deleted ${path}`);
    if (ok) reload();
  };

  const doRename = async (from: string) => {
    const name = renameValue.trim();
    if (!name || name === from.split("/").pop()) {
      setRenaming(null);
      return;
    }
    const parent = from.includes("/") ? from.slice(0, from.lastIndexOf("/")) : "";
    const to = join(parent, name);
    const ok = await run(toast, () => api.renameFs(projectId, from, to), `Renamed to ${to}`);
    if (ok) {
      onPathMoved?.(from, to);
      setRenaming(null);
      reload();
    }
  };

  const copyPath = (node: TreeNode) => {
    setClipboard({ mode: "copy", path: node.path, kind: node.type === "dir" ? "dir" : "file" });
    toast("success", `Copied ${node.path}`);
  };

  const cutPath = (node: TreeNode) => {
    setClipboard({ mode: "cut", path: node.path, kind: node.type === "dir" ? "dir" : "file" });
    toast("success", `Cut ${node.path}`);
  };

  const uniqueDest = (destDir: string, name: string, existing: Set<string>) => {
    let candidate = join(destDir, name);
    if (!existing.has(candidate)) return candidate;
    const dot = name.lastIndexOf(".");
    const stem = dot > 0 ? name.slice(0, dot) : name;
    const ext = dot > 0 ? name.slice(dot) : "";
    let i = 1;
    while (existing.has(join(destDir, `${stem} copy${i > 1 ? ` ${i}` : ""}${ext}`))) i++;
    return join(destDir, `${stem} copy${i > 1 ? ` ${i}` : ""}${ext}`);
  };

  const collectPaths = (node: TreeNode, out: Set<string>) => {
    out.add(node.path);
    (node.children ?? []).forEach((c) => collectPaths(c, out));
  };

  const pasteInto = async (destDir: string) => {
    if (!clipboard) return;
    const name = clipboard.path.split("/").pop() || "item";
    const existing = new Set<string>();
    if (tree.data) collectPaths(tree.data.root, existing);
    const dest = uniqueDest(destDir, name, existing);
    if (clipboard.mode === "copy") {
      const ok = await run(toast, () => api.copyFs(projectId, clipboard.path, dest), `Copied to ${dest}`);
      if (ok) reload();
    } else {
      const ok = await run(toast, () => api.moveFs(projectId, clipboard.path, dest), `Moved to ${dest}`);
      if (ok) {
        onPathMoved?.(clipboard.path, dest);
        setClipboard(null);
        reload();
      }
    }
  };

  const moveTo = async (src: string, destDir: string) => {
    if (src === destDir || destDir.startsWith(src + "/")) {
      toast("error", "Cannot move a folder into itself");
      return;
    }
    const name = src.split("/").pop() || src;
    const dest = join(destDir, name);
    if (dest === src) return;
    const ok = await run(toast, () => api.moveFs(projectId, src, dest), `Moved to ${dest}`);
    if (ok) {
      onPathMoved?.(src, dest);
      reload();
    }
  };

  const download = (path: string) => {
    window.open(api.downloadFsUrl(projectId, path), "_blank");
  };

  const uploadFiles = async (fileList: FileList | File[], destDir: string) => {
    const form = new FormData();
    form.append("dest_dir", destDir);
    const files = Array.from(fileList);
    for (const f of files) {
      const rel = (f as File & { webkitRelativePath?: string }).webkitRelativePath || f.name;
      form.append("files", f);
      form.append("paths", rel);
    }
    const ok = await run(toast, () => api.uploadFs(projectId, form), `Uploaded ${files.length} file(s)`);
    if (ok) reload();
  };

  const onDropFiles = async (e: React.DragEvent, destDir: string) => {
    e.preventDefault();
    e.stopPropagation();
    setDropTarget(null);
    const srcPath = e.dataTransfer.getData("text/autopatch-path");
    if (srcPath) {
      await moveTo(srcPath, destDir);
      return;
    }
    if (e.dataTransfer.files?.length) {
      await uploadFiles(e.dataTransfer.files, destDir);
    }
  };

  const filtered = (nodes: TreeNode[]): TreeNode[] => {
    if (!filter.trim()) return nodes;
    const q = filter.toLowerCase();
    const match = (n: TreeNode): TreeNode | null => {
      if (n.type === "file") return n.name.toLowerCase().includes(q) || n.path.toLowerCase().includes(q) ? n : null;
      const kids = (n.children ?? []).map(match).filter(Boolean) as TreeNode[];
      if (kids.length || n.name.toLowerCase().includes(q)) return { ...n, children: kids.length ? kids : n.children };
      return null;
    };
    return nodes.map(match).filter(Boolean) as TreeNode[];
  };

  return (
    <div
      className="flex h-full flex-col"
      onDragOver={(e) => { e.preventDefault(); setDropTarget(""); }}
      onDragLeave={() => setDropTarget(null)}
      onDrop={(e) => onDropFiles(e, "")}
    >
      <div className="flex items-center gap-0.5 border-b border-border px-1.5 py-1.5">
        <span className="flex-1 px-1 text-[11px] font-semibold uppercase tracking-wide text-muted">Explorer</span>
        <button title="New file" className="rounded p-1 text-muted hover:bg-hover hover:text-fg" onClick={() => { setParentDir(""); setMode("file"); setNewName(""); }}>
          <FilePlus className="size-3.5" />
        </button>
        <button title="New folder" className="rounded p-1 text-muted hover:bg-hover hover:text-fg" onClick={() => { setParentDir(""); setMode("dir"); setNewName(""); }}>
          <FolderPlus className="size-3.5" />
        </button>
        <button title="Upload files" className="rounded p-1 text-muted hover:bg-hover hover:text-fg" onClick={() => { setParentDir(""); uploadRef.current?.click(); }}>
          <Upload className="size-3.5" />
        </button>
        <button title="Refresh" className="rounded p-1 text-muted hover:bg-hover hover:text-fg" onClick={reload}>
          <RefreshCw className="size-3.5" />
        </button>
        <input ref={uploadRef} type="file" multiple className="hidden" onChange={(e) => {
          if (e.target.files?.length) uploadFiles(e.target.files, parentDir);
          e.target.value = "";
        }} />
      </div>

      <div className="border-b border-border p-1.5">
        <input className={inputCls} placeholder="Filter files…" value={filter} onChange={(e) => setFilter(e.target.value)} />
      </div>

      {mode && (
        <div className="flex flex-col gap-1 border-b border-border p-2">
          <div className="text-[11px] text-muted">{mode === "file" ? "New file" : "New folder"}{parentDir ? ` in ${parentDir}` : ""}</div>
          <div className="flex gap-1">
            <input className={inputCls} autoFocus placeholder="name.ext" value={newName}
              onChange={(e) => setNewName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && create()} />
            <Button kind="primary" onClick={create}>Add</Button>
            <Button onClick={() => setMode(null)}>✕</Button>
          </div>
        </div>
      )}

      {clipboard && (
        <div className="flex items-center gap-1 border-b border-border bg-accent/5 px-2 py-1 text-[11px]">
          <ClipboardPaste className="size-3" />
          <span className="min-w-0 flex-1 truncate">{clipboard.mode}: {clipboard.path}</span>
          <button className="underline" onClick={() => pasteInto("")}>Paste root</button>
          <button className="text-muted" onClick={() => setClipboard(null)}>Clear</button>
        </div>
      )}

      <div className={cx("min-h-0 flex-1 overflow-auto py-1", dropTarget === "" && "bg-accent/10")}>
        {tree.error ? <ErrorNote>{tree.error}</ErrorNote> : !tree.data ? <Spinner /> : (
          filtered(tree.data.root.children ?? []).map((n) => (
            <TreeItem
              key={n.path}
              node={n}
              depth={0}
              selected={selected}
              renaming={renaming}
              renameValue={renameValue}
              dirtyPaths={dirtyPaths}
              dropTarget={dropTarget}
              onSelect={onOpenFile}
              onContext={(e, node) => { e.preventDefault(); e.stopPropagation(); setCtx({ x: e.clientX, y: e.clientY, node }); }}
              onStartRename={(path, name) => { setRenaming(path); setRenameValue(name); }}
              onRenameValue={setRenameValue}
              onCommitRename={doRename}
              onCancelRename={() => setRenaming(null)}
              onDragStart={(path, ev) => {
                ev.dataTransfer.setData("text/autopatch-path", path);
                ev.dataTransfer.effectAllowed = "move";
              }}
              onDragOverDir={(path) => setDropTarget(path)}
              onDropDir={(path, ev) => onDropFiles(ev, path)}
            />
          ))
        )}
        {tree.data && (
          <div className="px-3 py-2 text-[11px] text-muted">
            {tree.data.files} files · {tree.data.dirs} folders · worktree
          </div>
        )}
      </div>

      {ctx && (
        <ContextMenu
          x={ctx.x}
          y={ctx.y}
          node={ctx.node}
          hasClipboard={!!clipboard}
          onClose={() => setCtx(null)}
          onOpen={() => { if (ctx.node.type === "file") onOpenFile(ctx.node.path); setCtx(null); }}
          onNewFile={() => { setParentDir(ctx.node.type === "dir" ? ctx.node.path : parentOf(ctx.node.path)); setMode("file"); setNewName(""); setCtx(null); }}
          onNewFolder={() => { setParentDir(ctx.node.type === "dir" ? ctx.node.path : parentOf(ctx.node.path)); setMode("dir"); setNewName(""); setCtx(null); }}
          onRename={() => { setRenaming(ctx.node.path); setRenameValue(ctx.node.name); setCtx(null); }}
          onCopy={() => { copyPath(ctx.node); setCtx(null); }}
          onCut={() => { cutPath(ctx.node); setCtx(null); }}
          onPaste={() => { pasteInto(ctx.node.type === "dir" ? ctx.node.path : parentOf(ctx.node.path)); setCtx(null); }}
          onDownload={() => { download(ctx.node.path); setCtx(null); }}
          onUpload={() => { setParentDir(ctx.node.type === "dir" ? ctx.node.path : parentOf(ctx.node.path)); setCtx(null); uploadRef.current?.click(); }}
          onDelete={() => { remove(ctx.node.path); setCtx(null); }}
        />
      )}
    </div>
  );
}

function parentOf(path: string) {
  return path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "";
}

function TreeItem({
  node, depth, selected, renaming, renameValue, dirtyPaths, dropTarget,
  onSelect, onContext, onStartRename, onRenameValue, onCommitRename, onCancelRename,
  onDragStart, onDragOverDir, onDropDir,
}: {
  node: TreeNode;
  depth: number;
  selected: string;
  renaming: string | null;
  renameValue: string;
  dirtyPaths: Set<string>;
  dropTarget: string | null;
  onSelect: (p: string) => void;
  onContext: (e: React.MouseEvent, node: TreeNode) => void;
  onStartRename: (path: string, name: string) => void;
  onRenameValue: (v: string) => void;
  onCommitRename: (from: string) => void;
  onCancelRename: () => void;
  onDragStart: (path: string, e: React.DragEvent) => void;
  onDragOverDir: (path: string) => void;
  onDropDir: (path: string, e: React.DragEvent) => void;
}) {
  const [open, setOpen] = useState(depth < 1 || selected === node.path || selected.startsWith(node.path + "/"));
  const pad = { paddingLeft: 8 + depth * 12 };
  const isDirty = dirtyPaths.has(node.path) || [...dirtyPaths].some((p) => p.startsWith(node.path + "/"));
  const isDrop = dropTarget === node.path;

  if (node.type === "dir") {
    return (
      <div>
        <div
          className={cx("group flex items-center hover:bg-hover", isDrop && "bg-accent/20")}
          onContextMenu={(e) => onContext(e, node)}
          draggable
          onDragStart={(e) => onDragStart(node.path, e)}
          onDragOver={(e) => { e.preventDefault(); e.stopPropagation(); onDragOverDir(node.path); }}
          onDrop={(e) => onDropDir(node.path, e)}
        >
          <button onClick={() => setOpen(!open)} style={pad} className="flex min-w-0 flex-1 items-center gap-1 py-0.5 pr-1 text-left">
            {open ? <ChevronDown className="size-3.5 shrink-0 text-muted" /> : <ChevronRight className="size-3.5 shrink-0 text-muted" />}
            {open ? <FolderOpen className="size-3.5 shrink-0 text-sky-500" /> : <Folder className="size-3.5 shrink-0 text-sky-500" />}
            {renaming === node.path ? (
              <input
                className="min-w-0 flex-1 rounded border border-accent bg-panel2 px-1 text-[12px]"
                autoFocus
                value={renameValue}
                onChange={(e) => onRenameValue(e.target.value)}
                onClick={(e) => e.stopPropagation()}
                onKeyDown={(e) => {
                  if (e.key === "Enter") onCommitRename(node.path);
                  if (e.key === "Escape") onCancelRename();
                }}
                onBlur={() => onCommitRename(node.path)}
              />
            ) : (
              <span className={cx("truncate text-[13px]", isDirty && "text-amber-600 dark:text-amber-400")}>{node.name}</span>
            )}
            {!!node.findings && <span className="ml-auto rounded-full bg-orange-500/20 px-1 text-[10px] font-semibold text-orange-700 dark:text-orange-300">{node.findings}</span>}
          </button>
        </div>
        {open && (node.children ?? []).map((c) => (
          <TreeItem
            key={c.path}
            node={c}
            depth={depth + 1}
            selected={selected}
            renaming={renaming}
            renameValue={renameValue}
            dirtyPaths={dirtyPaths}
            dropTarget={dropTarget}
            onSelect={onSelect}
            onContext={onContext}
            onStartRename={onStartRename}
            onRenameValue={onRenameValue}
            onCommitRename={onCommitRename}
            onCancelRename={onCancelRename}
            onDragStart={onDragStart}
            onDragOverDir={onDragOverDir}
            onDropDir={onDropDir}
          />
        ))}
      </div>
    );
  }

  const color = LANG_ICON[node.language || ""] || "text-muted";
  return (
    <div
      className="group flex items-center hover:bg-hover"
      onContextMenu={(e) => onContext(e, node)}
      draggable
      onDragStart={(e) => onDragStart(node.path, e)}
    >
      <button
        onClick={() => onSelect(node.path)}
        onDoubleClick={() => onStartRename(node.path, node.name)}
        style={{ paddingLeft: 24 + depth * 12 }}
        className={cx("flex min-w-0 flex-1 items-center gap-1.5 py-0.5 pr-1 text-left", node.path === selected ? "bg-accent/15" : "")}
      >
        {node.language === "Python" || node.language === "TypeScript" || node.language === "JavaScript"
          ? <FileCode2 className={cx("size-3.5 shrink-0", color)} />
          : <File className={cx("size-3.5 shrink-0", color)} />}
        {renaming === node.path ? (
          <input
            className="min-w-0 flex-1 rounded border border-accent bg-panel2 px-1 text-[12px]"
            autoFocus
            value={renameValue}
            onChange={(e) => onRenameValue(e.target.value)}
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => {
              if (e.key === "Enter") onCommitRename(node.path);
              if (e.key === "Escape") onCancelRename();
            }}
            onBlur={() => onCommitRename(node.path)}
          />
        ) : (
          <span className={cx("truncate text-[13px]", isDirty && "italic text-amber-600 dark:text-amber-400")}>
            {isDirty ? "M " : ""}{node.name}
          </span>
        )}
        {!!node.findings && <span className="ml-auto rounded-full bg-orange-500/20 px-1 text-[10px] font-semibold text-orange-700 dark:text-orange-300">{node.findings}</span>}
      </button>
    </div>
  );
}

function ContextMenu({
  x, y, node, hasClipboard, onClose, onOpen, onNewFile, onNewFolder, onRename, onCopy, onCut, onPaste, onDownload, onUpload, onDelete,
}: {
  x: number; y: number; node: TreeNode; hasClipboard: boolean;
  onClose: () => void;
  onOpen: () => void;
  onNewFile: () => void;
  onNewFolder: () => void;
  onRename: () => void;
  onCopy: () => void;
  onCut: () => void;
  onPaste: () => void;
  onDownload: () => void;
  onUpload: () => void;
  onDelete: () => void;
}) {
  const items: { label: string; icon?: React.ReactNode; action: () => void; danger?: boolean; disabled?: boolean }[] = [
    ...(node.type === "file" ? [{ label: "Open", action: onOpen }] : []),
    { label: "New File", icon: <FilePlus className="size-3.5" />, action: onNewFile },
    { label: "New Folder", icon: <FolderPlus className="size-3.5" />, action: onNewFolder },
    { label: "Rename", icon: <Pencil className="size-3.5" />, action: onRename },
    { label: "Copy", icon: <Copy className="size-3.5" />, action: onCopy },
    { label: "Cut", action: onCut },
    { label: "Paste", icon: <ClipboardPaste className="size-3.5" />, action: onPaste, disabled: !hasClipboard },
    { label: "Upload here", icon: <Upload className="size-3.5" />, action: onUpload },
    { label: "Download", icon: <Download className="size-3.5" />, action: onDownload },
    { label: "Delete", icon: <Trash2 className="size-3.5" />, action: onDelete, danger: true },
  ];
  return (
    <div
      className="fixed z-[90] min-w-[180px] rounded-md border border-border bg-panel py-1 shadow-xl"
      style={{ left: Math.min(x, window.innerWidth - 200), top: Math.min(y, window.innerHeight - 320) }}
      onClick={(e) => e.stopPropagation()}
    >
      <div className="truncate border-b border-border px-3 py-1 font-mono text-[11px] text-muted">{node.path}</div>
      {items.map((it) => (
        <button
          key={it.label}
          disabled={it.disabled}
          className={cx(
            "flex w-full items-center gap-2 px-3 py-1.5 text-left text-[13px] hover:bg-hover disabled:opacity-40",
            it.danger && "text-red-600 dark:text-red-400",
          )}
          onClick={() => { it.action(); onClose(); }}
        >
          {it.icon}
          {it.label}
        </button>
      ))}
    </div>
  );
}

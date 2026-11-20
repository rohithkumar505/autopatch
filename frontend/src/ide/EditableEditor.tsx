/**
 * Production Monaco editor surface: syntax, IntelliSense, folding, format, find/replace,
 * markers, goto line/definition hooks, minimap, undo/redo.
 */
import Editor, { type OnMount } from "@monaco-editor/react";
import type { editor as MonacoEditor, IDisposable } from "monaco-editor";
import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { Highlight } from "../components/ui";
import { Spinner, cx } from "../components/ui";
import { useApp } from "../lib";
import { monaco } from "../monaco";

export type EditorMarker = {
  line: number;
  endLine?: number;
  message: string;
  severity: "error" | "warning" | "info" | "hint";
};

export function EditableEditor({
  projectId,
  path,
  value,
  language = "python",
  readOnly = false,
  highlights = [],
  markers = [],
  reveal,
  onChange,
  onSave,
  onFormat,
  onGoToDefinition,
}: {
  projectId?: number;
  path: string;
  value: string;
  language?: string;
  readOnly?: boolean;
  highlights?: Highlight[];
  markers?: EditorMarker[];
  reveal?: { line: number; column?: number };
  onChange?: (value: string) => void;
  onSave?: () => void;
  onFormat?: () => void | Promise<void>;
  onGoToDefinition?: (line: number, column: number, word: string) => void;
}) {
  const { theme } = useApp();
  const editorRef = useRef<Parameters<OnMount>[0] | null>(null);
  const modelRef = useRef<MonacoEditor.ITextModel | null>(null);
  const decorations = useRef<string[]>([]);
  const disposables = useRef<IDisposable[]>([]);
  const [mounted, setMounted] = useState(false);
  const callbacks = useRef({ onSave, onFormat, onGoToDefinition, onChange });
  callbacks.current = { onSave, onFormat, onGoToDefinition, onChange };

  // Markers (errors / warnings) — real Monaco diagnostics
  useEffect(() => {
    if (!mounted || !path) return;
    const model = monaco.editor.getModels().find((m) => m.uri.path.endsWith("/" + path) || m.uri.toString().includes(encodeURIComponent(path)));
    const target = modelRef.current || model;
    if (!target) return;
    const sev = {
      error: monaco.MarkerSeverity.Error,
      warning: monaco.MarkerSeverity.Warning,
      info: monaco.MarkerSeverity.Info,
      hint: monaco.MarkerSeverity.Hint,
    };
    monaco.editor.setModelMarkers(
      target,
      "autopatch",
      markers.map((m) => ({
        startLineNumber: m.line,
        startColumn: 1,
        endLineNumber: m.endLine ?? m.line,
        endColumn: 1000,
        message: m.message,
        severity: sev[m.severity],
      })),
    );
  }, [markers, mounted, path, value]);

  // Finding line decorations
  useEffect(() => {
    const ed = editorRef.current;
    if (!ed || !mounted) return;
    decorations.current = ed.deltaDecorations(
      decorations.current,
      highlights.map((h) => ({
        range: { startLineNumber: h.start, startColumn: 1, endLineNumber: Math.max(h.start, h.end), endColumn: 1 },
        options: {
          isWholeLine: true,
          className: `ap-line-${h.severity}`,
          glyphMarginClassName: `ap-glyph-${h.severity}`,
          hoverMessage: h.message ? { value: h.message } : undefined,
        },
      })),
    );
  }, [highlights, mounted, value, path]);

  useEffect(() => {
    const ed = editorRef.current;
    if (!ed || !mounted || !reveal?.line) return;
    ed.revealLineInCenter(reveal.line);
    ed.setPosition({ lineNumber: reveal.line, column: reveal.column ?? 1 });
    ed.focus();
  }, [reveal, mounted, path]);

  // Python completion provider from real project structure
  useEffect(() => {
    if (!projectId || language !== "python") return;
    let cancelled = false;
    let provider: IDisposable | undefined;
    api.completions(projectId, path.endsWith(".py") ? path : "").then((res) => {
      if (cancelled) return;
      provider = monaco.languages.registerCompletionItemProvider("python", {
        triggerCharacters: [".", "_"],
        provideCompletionItems: (model, position) => {
          if (model !== modelRef.current) return { suggestions: [] };
          const word = model.getWordUntilPosition(position);
          const range = {
            startLineNumber: position.lineNumber,
            endLineNumber: position.lineNumber,
            startColumn: word.startColumn,
            endColumn: word.endColumn,
          };
          const kindMap: Record<string, monaco.languages.CompletionItemKind> = {
            function: monaco.languages.CompletionItemKind.Function,
            method: monaco.languages.CompletionItemKind.Method,
            class: monaco.languages.CompletionItemKind.Class,
          };
          return {
            suggestions: res.items.map((it) => ({
              label: it.label,
              kind: kindMap[it.kind] ?? monaco.languages.CompletionItemKind.Variable,
              insertText: it.insertText || it.label,
              detail: it.detail,
              documentation: it.documentation,
              range,
            })),
          };
        },
      });
      disposables.current.push(provider);
    }).catch(() => undefined);
    return () => {
      cancelled = true;
      provider?.dispose();
    };
  }, [projectId, path, language]);

  useEffect(() => () => {
    disposables.current.forEach((d) => d.dispose());
    disposables.current = [];
  }, [path]);

  return (
    <div className={cx("h-full overflow-hidden", readOnly && "opacity-95")}>
      <Editor
        path={path}
        height="100%"
        value={value}
        language={language}
        theme={theme === "dark" ? "vs-dark" : "vs"}
        loading={<Spinner text="Loading editor..." />}
        onChange={(v) => callbacks.current.onChange?.(v ?? "")}
        onMount={(ed, m) => {
          editorRef.current = ed;
          modelRef.current = ed.getModel();
          setMounted(true);

          ed.addCommand(m.KeyMod.CtrlCmd | m.KeyCode.KeyS, () => {
            callbacks.current.onSave?.();
          });
          ed.addCommand(m.KeyMod.CtrlCmd | m.KeyMod.Shift | m.KeyCode.KeyI, () => {
            void callbacks.current.onFormat?.();
          });
          ed.addCommand(m.KeyCode.F12, () => {
            const pos = ed.getPosition();
            if (!pos) return;
            const model = ed.getModel();
            if (!model) return;
            const word = model.getWordAtPosition(pos);
            callbacks.current.onGoToDefinition?.(pos.lineNumber, pos.column, word?.word ?? "");
          });
          ed.addCommand(m.KeyMod.CtrlCmd | m.KeyCode.KeyG, () => {
            void ed.getAction("editor.action.gotoLine")?.run();
          });
          ed.addCommand(m.KeyMod.CtrlCmd | m.KeyCode.KeyF, () => {
            void ed.getAction("actions.find")?.run();
          });
          ed.addCommand(m.KeyMod.CtrlCmd | m.KeyCode.KeyH, () => {
            void ed.getAction("editor.action.startFindReplaceAction")?.run();
          });
        }}
        options={{
          readOnly,
          domReadOnly: readOnly,
          minimap: { enabled: true, showSlider: "mouseover", maxColumn: 120 },
          glyphMargin: true,
          lineNumbers: "on",
          folding: true,
          foldingHighlight: true,
          showFoldingControls: "mouseover",
          matchBrackets: "always",
          autoClosingBrackets: "languageDefined",
          autoClosingQuotes: "languageDefined",
          autoIndent: "full",
          formatOnPaste: true,
          formatOnType: true,
          quickSuggestions: { other: true, comments: false, strings: false },
          suggestOnTriggerCharacters: true,
          wordBasedSuggestions: "currentDocument",
          parameterHints: { enabled: true },
          tabCompletion: "on",
          snippetSuggestions: "inline",
          bracketPairColorization: { enabled: true },
          guides: { bracketPairs: true, indentation: true },
          scrollBeyondLastLine: false,
          fontSize: 13,
          fontFamily: "'Cascadia Code', 'JetBrains Mono', Consolas, 'Courier New', monospace",
          fontLigatures: true,
          automaticLayout: true,
          wordWrap: "off",
          renderLineHighlight: "all",
          renderWhitespace: "selection",
          smoothScrolling: true,
          cursorBlinking: "smooth",
          cursorSmoothCaretAnimation: "on",
          padding: { top: 8 },
          find: { addExtraSpaceOnTop: false, autoFindInSelection: "never", seedSearchStringFromSelection: "always" },
          links: true,
          colorDecorators: true,
          dragAndDrop: true,
          emptySelectionClipboard: true,
          multiCursorModifier: "alt",
          accessibilitySupport: "auto",
        }}
      />
    </div>
  );
}

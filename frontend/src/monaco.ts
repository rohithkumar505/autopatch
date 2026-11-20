/**
 * Monaco bootstrap with real language workers (TS/JS, JSON, CSS, HTML) plus editor features.
 * Python IntelliSense is provided by AutoPatch structure/completion providers in the IDE.
 */
import * as monaco from "monaco-editor/editor/editor.api";
import "monaco-editor/features/register.all";
import "monaco-editor/languages/definitions/register.all";
import EditorWorker from "monaco-editor/editor/editor.worker?worker";
import JsonWorker from "monaco-editor/language/json/json.worker?worker";
import CssWorker from "monaco-editor/language/css/css.worker?worker";
import HtmlWorker from "monaco-editor/language/html/html.worker?worker";
import TsWorker from "monaco-editor/language/typescript/ts.worker?worker";
import { loader } from "@monaco-editor/react";

(self as unknown as { MonacoEnvironment: { getWorker: (_: unknown, label: string) => Worker } }).MonacoEnvironment = {
  getWorker: (_workerId, label) => {
    if (label === "json") return new JsonWorker();
    if (label === "css" || label === "scss" || label === "less") return new CssWorker();
    if (label === "html" || label === "handlebars" || label === "razor") return new HtmlWorker();
    if (label === "typescript" || label === "javascript") return new TsWorker();
    return new EditorWorker();
  },
};

// Richer TS/JS language service defaults (Monaco 0.57+). Cast keeps us compatible across typings.
try {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const langs = monaco.languages as any;
  const ts = langs.typescript;
  if (ts?.typescriptDefaults && ts?.javascriptDefaults) {
    ts.typescriptDefaults.setEagerModelSync(true);
    ts.javascriptDefaults.setEagerModelSync(true);
    const opts = {
      target: ts.ScriptTarget?.ESNext ?? 99,
      allowNonTsExtensions: true,
      moduleResolution: ts.ModuleResolutionKind?.NodeJs ?? 2,
      module: ts.ModuleKind?.ESNext ?? 99,
      jsx: ts.JsxEmit?.React ?? 2,
      allowJs: true,
      checkJs: true,
    };
    ts.typescriptDefaults.setCompilerOptions(opts);
    ts.javascriptDefaults.setCompilerOptions({
      target: opts.target,
      allowNonTsExtensions: true,
      allowJs: true,
      checkJs: true,
    });
  }
} catch {
  // Workers still provide syntax highlighting even if defaults API differs.
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
loader.config({ monaco: monaco as any });

export { monaco };

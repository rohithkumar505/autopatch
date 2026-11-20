import { load, save } from "../lib";

export type LeftView = "explorer" | "search" | "git" | "run" | "extensions" | "agents" | "autopatch";
export type BottomTab = "terminal" | "problems" | "output" | "debug" | "tests" | "logs";

export interface EditorTab {
  path: string;
  language: string;
}

export interface IdeLayoutState {
  leftView: LeftView;
  leftOpen: boolean;
  leftWidth: number;
  rightOpen: boolean;
  rightWidth: number;
  bottomOpen: boolean;
  bottomHeight: number;
  bottomTab: BottomTab;
  split: boolean;
  tabs: string[];
  activePath: string;
  splitPath: string;
  autoSave: boolean;
}

export const DEFAULT_LAYOUT: IdeLayoutState = {
  leftView: "explorer",
  leftOpen: true,
  leftWidth: 280,
  rightOpen: true,
  rightWidth: 320,
  bottomOpen: true,
  bottomHeight: 220,
  bottomTab: "problems",
  split: false,
  tabs: [],
  activePath: "",
  splitPath: "",
  autoSave: true,
};

export function loadLayout(projectId: number | null): IdeLayoutState {
  if (!projectId) return { ...DEFAULT_LAYOUT };
  return { ...DEFAULT_LAYOUT, ...load(`ide.layout.${projectId}`, {}) };
}

export function saveLayout(projectId: number, state: IdeLayoutState) {
  save(`ide.layout.${projectId}`, {
    leftView: state.leftView,
    leftOpen: state.leftOpen,
    leftWidth: state.leftWidth,
    rightOpen: state.rightOpen,
    rightWidth: state.rightWidth,
    bottomOpen: state.bottomOpen,
    bottomHeight: state.bottomHeight,
    bottomTab: state.bottomTab,
    split: state.split,
    tabs: state.tabs,
    activePath: state.activePath,
    splitPath: state.splitPath,
    autoSave: state.autoSave,
  });
}

export const LEFT_VIEWS: { id: LeftView; label: string; shortcut: string }[] = [
  { id: "explorer", label: "Explorer", shortcut: "Ctrl+Shift+E" },
  { id: "search", label: "Search", shortcut: "Ctrl+Shift+F" },
  { id: "git", label: "Source Control", shortcut: "Ctrl+Shift+G" },
  { id: "run", label: "Run and Debug", shortcut: "Ctrl+Shift+D" },
  { id: "extensions", label: "Extensions", shortcut: "Ctrl+Shift+X" },
  { id: "agents", label: "AI Agents", shortcut: "Ctrl+Shift+A" },
  { id: "autopatch", label: "AutoPatch", shortcut: "Ctrl+Shift+P" },
];

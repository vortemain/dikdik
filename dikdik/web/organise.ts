// Pure helpers for the sidebar, command menu, file tree and Markdown extras.
// No DOM access here, so every rule is unit-tested.
import type { TaskSummary } from "./contracts";

// --- conversation list --------------------------------------------------------
export type TaskSort = "recent" | "created" | "title";
export interface TaskView {
  query: string; tag: string; sort: TaskSort; sinceDays: number;
  showArchived: boolean; selected: string | null; now: number;
}
export interface OrganisedTasks {
  pinned: TaskSummary[];
  folders: { name: string; tasks: TaskSummary[] }[];
  recent: TaskSummary[];
  tags: string[];          // every tag in use, for the filter chips
  folderNames: string[];   // every folder in use, for "Move to folder"
  older: number;           // conversations hidden by the time filter
  archived: number;
}
export function taskTitle(task: TaskSummary): string {
  return (task.title || task.objective || "").trim();
}
export function organiseTasks(tasks: readonly TaskSummary[], view: TaskView): OrganisedTasks {
  const query = view.query.trim().toLowerCase();
  const cutoff = view.sinceDays > 0 ? view.now - view.sinceDays * 86400 : 0;
  let older = 0;
  const visible = tasks.filter(task => {
    if (task.id === view.selected) return true;
    if (!view.showArchived && task.archived) return false;
    if (view.tag && !(task.tags ?? []).includes(view.tag)) return false;
    if (query && !`${taskTitle(task)} ${task.folder ?? ""} ${(task.tags ?? []).join(" ")}`.toLowerCase().includes(query)) return false;
    if (cutoff && (task.updated ?? task.created ?? 0) < cutoff && !task.pinned) { older++; return false; }
    return true;
  });
  const order = (a: TaskSummary, b: TaskSummary) =>
    view.sort === "title" ? taskTitle(a).localeCompare(taskTitle(b), undefined, { sensitivity: "base" })
    : view.sort === "created" ? (b.created ?? 0) - (a.created ?? 0)
    : (b.updated ?? b.created ?? 0) - (a.updated ?? a.created ?? 0);
  visible.sort(order);
  const pinned = visible.filter(task => task.pinned);
  const filed = new Map<string, TaskSummary[]>();
  const recent: TaskSummary[] = [];
  for (const task of visible) {
    if (task.pinned) continue;
    if (task.folder) filed.set(task.folder, [...(filed.get(task.folder) ?? []), task]);
    else recent.push(task);
  }
  const folders = [...filed].sort(([a], [b]) => a.localeCompare(b, undefined, { sensitivity: "base" })).map(([name, items]) => ({ name, tasks: items }));
  const tags = [...new Set(tasks.flatMap(task => task.tags ?? []))].sort();
  const folderNames = [...new Set(tasks.map(task => task.folder ?? "").filter(Boolean))].sort();
  return { pinned, folders, recent, tags, folderNames, older, archived: tasks.filter(task => task.archived).length };
}
/** "a, B ,b" → ["a", "b"]; throws with a readable message on bad tags. */
export function parseTags(text: string): string[] {
  const tags = [...new Set(text.split(",").map(tag => tag.trim().toLowerCase()).filter(Boolean))];
  if (tags.length > 10) throw new Error("Use at most 10 tags.");
  for (const tag of tags) if (!/^[\p{L}\p{N}_][\p{L}\p{N}_ -]{0,31}$/u.test(tag)) throw new Error(`“${tag}” is not a valid tag: use letters, digits, spaces, - or _ (up to 32).`);
  return tags.sort();
}

// --- command menu --------------------------------------------------------------
/** Subsequence match score (higher is better); -1 when the query does not match. */
export function fuzzyScore(query: string, text: string): number {
  const q = query.trim().toLowerCase(), t = text.toLowerCase();
  if (!q) return 0;
  if (t.startsWith(q)) return 1000 - t.length;
  const word = t.indexOf(" " + q);
  if (word >= 0) return 800 - word;
  const inside = t.indexOf(q);
  if (inside >= 0) return 600 - inside;
  let score = 300, at = 0;
  for (const char of q) {
    const found = t.indexOf(char, at);
    if (found < 0) return -1;
    score -= found - at;
    at = found + 1;
  }
  return Math.max(1, score);
}
export function rankCommands<T extends { label: string; keywords?: string }>(query: string, items: readonly T[], limit = 50): T[] {
  if (!query.trim()) return items.slice(0, limit);
  return items.map(item => ({ item, score: Math.max(fuzzyScore(query, item.label), item.keywords ? fuzzyScore(query, item.keywords) - 50 : -1) }))
    .filter(entry => entry.score >= 0).sort((a, b) => b.score - a.score).slice(0, limit).map(entry => entry.item);
}

// --- file tree -------------------------------------------------------------------
export interface TreeRow { path: string; name: string; depth: number; dir: boolean; open: boolean }
/** Visible rows of a tree built from file paths; folders first, then files, each sorted. */
export function fileTree(paths: readonly string[], expanded: ReadonlySet<string>): TreeRow[] {
  interface Node { dirs: Map<string, Node>; files: string[] }
  const root: Node = { dirs: new Map(), files: [] };
  for (const path of paths) {
    const parts = path.split("/").filter(Boolean);
    let node = root;
    for (const part of parts.slice(0, -1)) {
      if (!node.dirs.has(part)) node.dirs.set(part, { dirs: new Map(), files: [] });
      node = node.dirs.get(part)!;
    }
    if (parts.length) node.files.push(parts[parts.length - 1]!);
  }
  const rows: TreeRow[] = [];
  const walk = (node: Node, prefix: string, depth: number) => {
    for (const name of [...node.dirs.keys()].sort((a, b) => a.localeCompare(b))) {
      const path = prefix + name, open = expanded.has(path);
      rows.push({ path, name, depth, dir: true, open });
      if (open) walk(node.dirs.get(name)!, path + "/", depth + 1);
    }
    for (const name of [...node.files].sort((a, b) => a.localeCompare(b))) rows.push({ path: prefix + name, name, depth, dir: false, open: false });
  };
  walk(root, "", 0);
  return rows;
}
/** Every parent folder of a path, so opening a file reveals it in the tree. */
export function parentFolders(path: string): string[] {
  const parts = path.split("/");
  return parts.slice(0, -1).map((_, index) => parts.slice(0, index + 1).join("/"));
}

// --- Markdown extras -------------------------------------------------------------
export type AlertKind = "note" | "tip" | "important" | "warning" | "caution";
/** `> [!NOTE]` → "note". */
export function alertKind(line: string): AlertKind | null {
  const match = /^\s*>\s*\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*$/i.exec(line);
  return match ? match[1]!.toLowerCase() as AlertKind : null;
}
function cells(line: string): string[] {
  const trimmed = line.trim().replace(/^\|/, "").replace(/\|$/, "");
  return trimmed.split(/(?<!\\)\|/).map(cell => cell.trim().replace(/\\\|/g, "|"));
}
/** A GitHub table starting at `lines[start]`, or null. */
export function parseTable(lines: readonly string[], start: number): { header: string[]; align: ("left" | "center" | "right" | "")[]; rows: string[][]; end: number } | null {
  const head = lines[start], rule = lines[start + 1];
  if (!head?.includes("|") || !rule || !/^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/.test(rule)) return null;
  const header = cells(head);
  const align = cells(rule).map(cell => cell.startsWith(":") && cell.endsWith(":") ? "center" : cell.endsWith(":") ? "right" : cell.startsWith(":") ? "left" : "") as ("left" | "center" | "right" | "")[];
  if (align.length !== header.length) return null;
  const rows: string[][] = [];
  let end = start + 2;
  for (; end < lines.length && lines[end]!.includes("|") && lines[end]!.trim(); end++) {
    const row = cells(lines[end]!);
    rows.push(header.map((_, index) => row[index] ?? ""));
  }
  return { header, align, rows, end };
}
/** A workspace path named in text (`src/app.py`, `src/app.py:12`), if it is a known file. */
export function workspacePath(token: string, files: ReadonlySet<string>): { path: string; line?: number } | null {
  const match = /^\.?\/?([\w.@\/-]+?)(?::(\d+)(?::\d+)?)?$/.exec(token.trim());
  if (!match || !files.has(match[1]!)) return null;
  return match[2] ? { path: match[1]!, line: Number(match[2]) } : { path: match[1]! };
}

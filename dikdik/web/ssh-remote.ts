/** Connect to host (ADR-009): what the Projects dialog and the title bar show for SSH connections. */

export interface SshHost { name: string; hostname: string | null; user: string | null }
export interface SshHostKey { type: string; fingerprint: string }
export type SshState = "connecting" | "host_key" | "connected" | "failed" | "closed";
export interface SshConnection {
  id: string; host: string; folder: string; state: SshState; step: string;
  error: string | null; kind: string | null; host_keys: SshHostKey[];
  python: string | null; port: number | null; created: number;
  repo?: string | null; parent?: string | null;
}
export type SshAction = "open" | "disconnect" | "trust" | "cancel" | "retry";
export interface SshView { status: string; tone: "busy" | "ok" | "warn" | "error" | "idle"; actions: SshAction[] }

const HOST = /^(?:ssh:\/\/)?(?:[A-Za-z0-9._%+-]+@)?(?:[A-Za-z0-9._-]+|\[[0-9A-Fa-f:.]+\])(?::\d{1,5})?$/;

/** The same checks the server makes, so a typo is caught before anything starts. */
export function checkHost(text: string): string | null {
  const host = text.trim();
  if (!host) return "Choose a host from your SSH config or type user@host";
  if (host.startsWith("-") || host.length > 255 || !HOST.test(host)) return "Type a host as alias, user@host or ssh://user@host:port";
  return null;
}
export function checkFolder(text: string): string | null {
  const folder = text.trim();
  if (!folder) return "Type the folder on the host to open, such as ~/projects/app";
  if (folder.length > 1000 || [...folder].some(ch => ch.charCodeAt(0) < 32)) return "That folder path is not valid";
  if (!(folder.startsWith("/") || folder === "~" || folder.startsWith("~/"))) return "Type a full path on the host, such as ~/projects/app or /srv/app";
  return null;
}

const SCP_URL = /^[A-Za-z0-9._-]+@[A-Za-z0-9.-]+:[A-Za-z0-9._/~-]+$/;
/** The same rules as Clone repository: https, ssh, git@host:path or file, without credentials. */
export function checkRepository(text: string): string | null {
  const url = text.trim();
  if (!url || url.length > 1000 || url.startsWith("-")) return "Pick a repository or paste its URL, such as https://github.com/owner/repo";
  if (SCP_URL.test(url)) return null;
  let parsed: URL;
  try { parsed = new URL(url); } catch { return "Use an https://, ssh://, git@host:path or file:// repository URL"; }
  if (!["https:", "ssh:", "file:"].includes(parsed.protocol)) return "Use an https://, ssh://, git@host:path or file:// repository URL";
  if (parsed.protocol !== "file:" && (!parsed.hostname || parsed.password || (parsed.username && parsed.protocol === "https:"))) return "Leave sign-in details out of the URL; the host uses its own Git sign-in";
  return null;
}
export function repositoryName(url: string): string {
  const tail = url.trim().replace(/\/+$/, "").replace(/:/g, "/").split("/").pop() ?? "";
  return tail.endsWith(".git") ? tail.slice(0, -4) : tail;
}
/** Where a repository opened on a host will live: the parent folder plus the repository's name. */
export function cloneTarget(url: string, parent: string): string {
  return `${(parent.trim() || "~/projects").replace(/\/+$/, "")}/${repositoryName(url)}`;
}

export function hostLabel(host: SshHost): string {
  const target = host.hostname ? `${host.user ? `${host.user}@` : ""}${host.hostname}` : host.user ? `${host.user}@${host.name}` : "";
  return target && target !== host.name ? `${host.name} — ${target}` : host.name;
}

export function isLive(connection: SshConnection): boolean {
  return connection.state === "connecting" || connection.state === "host_key" || connection.state === "connected";
}

export function connectionView(connection: SshConnection): SshView {
  switch (connection.state) {
    case "connecting": return { status: `${connection.step}…`, tone: "busy", actions: ["cancel"] };
    case "host_key": return { status: "Check the host key before connecting", tone: "warn", actions: ["trust", "cancel"] };
    case "connected": return { status: `Connected${connection.python ? ` · Python ${connection.python}` : ""}`, tone: "ok", actions: ["open", "disconnect"] };
    case "failed": return { status: connection.error ?? "The connection failed", tone: "error", actions: ["retry"] };
    default: return { status: connection.step || "Disconnected", tone: "idle", actions: ["retry"] };
  }
}

/** The title bar: a remote window names its host; this computer's window counts its live hosts. */
export function badge(session: { host: string } | null, connections: SshConnection[]): { text: string; title: string } | null {
  if (session) return { text: `SSH: ${session.host}`, title: `This window works on ${session.host} over SSH. Click to disconnect.` };
  const connected = connections.filter(connection => connection.state === "connected").length;
  const pending = connections.filter(connection => connection.state === "connecting" || connection.state === "host_key").length;
  if (!connected && !pending) return null;
  const text = connected ? `${connected} host${connected === 1 ? "" : "s"}` : "Connecting…";
  return { text, title: "Remote hosts connected over SSH. Click to manage them." };
}

/** The composer's environment pill: where this window's work runs. */
export function environmentLabel(session: { host: string } | null): { text: string; title: string; remote: boolean } {
  return session
    ? { text: `SSH: ${session.host}`, title: `Work runs on ${session.host} over SSH. Choose where to work.`, remote: true }
    : { text: "Local", title: "Work runs on this computer. Choose where to work: here or another computer over SSH.", remote: false };
}

export type EnvironmentItem =
  | { kind: "heading"; label: string }
  | { kind: "local" | "session"; label: string; detail: string; current: boolean }
  | { kind: "connection"; label: string; detail: string; connection: SshConnection }
  | { kind: "host"; label: string; detail: string; host: string }
  | { kind: "clone" | "remote-repo"; label: string; detail: string }
  | { kind: "add" | "manage" | "disconnect"; label: string };

/** The environment menu, like an editor's remote picker: this computer or the host, a repository to
 * clone there, live connections, saved hosts and Add. A clone always runs where this window works. */
export function environmentMenu(session: { host: string } | null, connections: SshConnection[], hosts: SshHost[]): EnvironmentItem[] {
  if (session) {
    return [
      { kind: "heading", label: "Environment" },
      { kind: "session", label: `SSH: ${session.host}`, detail: "This window works on the host", current: true },
      { kind: "heading", label: "Repository" },
      { kind: "clone", label: "Clone a repository…", detail: `On ${session.host}, with its Git sign-in` },
      { kind: "disconnect", label: `Disconnect from ${session.host}` },
    ];
  }
  const items: EnvironmentItem[] = [{ kind: "heading", label: "Environment" },
    { kind: "local", label: "Local", detail: "This computer", current: true },
    { kind: "heading", label: "Repository" },
    { kind: "clone", label: "Clone a repository…", detail: "On this computer, with its Git sign-in" }];
  const live = connections.filter(isLive);
  if (live.length) {
    items.push({ kind: "heading", label: "SSH connections" });
    for (const connection of live) items.push({ kind: "connection", label: `SSH: ${connection.host}`, detail: `${connection.folder} · ${connectionView(connection).status}`, connection });
  }
  const used = new Set(live.map(connection => connection.host));
  const saved = hosts.filter(host => !used.has(host.name)).slice(0, 12);
  if (saved.length) {
    items.push({ kind: "heading", label: "Saved SSH hosts" });
    for (const host of saved) {
      const target = host.hostname ? `${host.user ? `${host.user}@` : ""}${host.hostname}` : host.user ? `${host.user}@${host.name}` : "SSH host";
      items.push({ kind: "host", label: host.name, detail: target, host: host.name });
    }
  }
  items.push({ kind: "add", label: "Add SSH connection…" });
  items.push({ kind: "remote-repo", label: "Open a repository on a host…", detail: "Clone it there over SSH, then work on it" });
  if (connections.length) items.push({ kind: "manage", label: "Manage SSH connections" });
  return items;
}

/** Where "Back to this computer" may go after disconnecting: only a loopback Dikdik address. */
export function backTarget(referrer: string): string | null {
  try {
    const url = new URL(referrer);
    if (url.protocol !== "http:" || !["127.0.0.1", "localhost"].includes(url.hostname) || !url.port) return null;
    return `${url.protocol}//${url.host}/`;
  } catch { return null; }
}

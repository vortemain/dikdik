import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { backTarget, badge, checkFolder, checkHost, checkRepository, cloneTarget, connectionView, environmentLabel, environmentMenu, hostLabel, isLive, repositoryName, type SshConnection } from "../../dikdik/web/ssh-remote";

const base: SshConnection = { id: "0123456789abcdef", host: "ada@devbox", folder: "~/app", state: "connecting", step: "Checking the host",
  error: null, kind: null, host_keys: [], python: null, port: null, created: 1 };

test("hosts and folders are checked like the server checks them", () => {
  for (const good of ["devbox", "ada@10.0.0.5", "ssh://ada@host.example.com:2222", "user@[::1]"]) expect(checkHost(good)).toBeNull();
  for (const bad of ["", "-oProxyCommand=calc", "a b", "host;rm", "x".repeat(300)]) expect(checkHost(bad)).not.toBeNull();
  for (const good of ["~", "~/projects/app", "/srv/my app"]) expect(checkFolder(good)).toBeNull();
  for (const bad of ["", "projects/app", "C:\\x", "/a\nb"]) expect(checkFolder(bad)).not.toBeNull();
});

test("each state offers only the actions that make sense", () => {
  expect(connectionView(base)).toEqual({ status: "Checking the host…", tone: "busy", actions: ["cancel"] });
  expect(connectionView({ ...base, state: "host_key" }).actions).toEqual(["trust", "cancel"]);
  expect(connectionView({ ...base, state: "connected", python: "3.13.1" })).toEqual({ status: "Connected · Python 3.13.1", tone: "ok", actions: ["open", "disconnect"] });
  expect(connectionView({ ...base, state: "failed", error: "refused the SSH key sign-in" })).toMatchObject({ status: "refused the SSH key sign-in", tone: "error", actions: ["retry"] });
  expect(connectionView({ ...base, state: "closed", step: "Disconnected" }).tone).toBe("idle");
  expect([base, { ...base, state: "host_key" as const }, { ...base, state: "connected" as const }].every(isLive)).toBe(true);
  expect(isLive({ ...base, state: "failed" })).toBe(false);
});

test("the title bar names the host in a remote window and counts hosts here", () => {
  expect(badge({ host: "devbox" }, [])?.text).toBe("SSH: devbox");
  expect(badge(null, [])).toBeNull();
  expect(badge(null, [{ ...base, state: "closed" }])).toBeNull();
  expect(badge(null, [base])?.text).toBe("Connecting…");
  expect(badge(null, [{ ...base, state: "connected" }, { ...base, state: "connected" }])?.text).toBe("2 hosts");
});

test("labels and the way back stay on loopback", () => {
  expect(hostLabel({ name: "devbox", hostname: "10.0.0.5", user: "ada" })).toBe("devbox — ada@10.0.0.5");
  expect(hostLabel({ name: "devbox", hostname: null, user: null })).toBe("devbox");
  expect(backTarget("http://127.0.0.1:8787/?task=1")).toBe("http://127.0.0.1:8787/");
  expect(backTarget("http://localhost:8787/")).toBe("http://localhost:8787/");
  for (const bad of ["", "https://evil.example/", "http://127.0.0.1/", "javascript:alert(1)", "http://127.0.0.1.evil.example:80/"]) expect(backTarget(bad)).toBeNull();
});

test("repositories for a host follow the Clone repository rules", () => {
  for (const good of ["https://github.com/ada/demo", "git@github.com:ada/demo.git", "ssh://git@github.com/ada/demo.git", "file:///srv/git/demo"]) expect(checkRepository(good)).toBeNull();
  for (const bad of ["", "-upload-pack=x", "ftp://example.com/a.git", "https://user:secret@github.com/a/b", "not a url"]) expect(checkRepository(bad)).not.toBeNull();
  expect(repositoryName("git@github.com:ada/demo.git")).toBe("demo");
  expect(repositoryName("https://github.com/ada/demo/")).toBe("demo");
  expect(cloneTarget("https://github.com/ada/demo", "~/code/")).toBe("~/code/demo");
  expect(cloneTarget("https://github.com/ada/demo", "")).toBe("~/projects/demo");
});

test("the Connect dialog offers Folder or Repository", () => {
  const page = readFileSync(new URL("../../dikdik/web/index.html", import.meta.url), "utf8");
  const dialog = page.slice(page.indexOf('<dialog id="ssh-dialog"'), page.indexOf('<dialog id="info-dialog"'));
  for (const id of ['data-ssh-kind="folder"', 'data-ssh-kind="repo"', 'data-ssh-pane="repo" hidden', 'id="ssh-repo-url"', 'id="ssh-repos-list"', 'id="ssh-repo-parent"']) expect(dialog).toContain(id);
});

test("the environment pill says where work runs", () => {
  expect(environmentLabel(null)).toMatchObject({ text: "Local", remote: false });
  expect(environmentLabel({ host: "devbox" })).toMatchObject({ text: "SSH: devbox", remote: true });
});

test("the environment menu lists this computer, live connections, saved hosts and Add", () => {
  const hosts = [{ name: "devbox", hostname: "10.0.0.5", user: "ada" }, { name: "gpu", hostname: null, user: null }];
  const live = { ...base, host: "devbox", state: "connected" as const };
  const items = environmentMenu(null, [live, { ...base, host: "old", state: "failed" as const }], hosts);
  expect(items.map(item => item.kind)).toEqual(["heading", "local", "heading", "clone", "heading", "connection", "heading", "host", "add", "remote-repo", "manage"]);
  expect(items[1]).toMatchObject({ label: "Local", current: true });
  expect(items[3]).toMatchObject({ kind: "clone", detail: "On this computer, with its Git sign-in" });
  expect(items[5]).toMatchObject({ label: "SSH: devbox", detail: "~/app · Connected" });
  expect(items[7]).toMatchObject({ kind: "host", label: "gpu", detail: "SSH host", host: "gpu" });
  expect(environmentMenu(null, [], [hosts[0]!])).toEqual([
    { kind: "heading", label: "Environment" }, { kind: "local", label: "Local", detail: "This computer", current: true },
    { kind: "heading", label: "Repository" }, { kind: "clone", label: "Clone a repository…", detail: "On this computer, with its Git sign-in" },
    { kind: "heading", label: "Saved SSH hosts" }, { kind: "host", label: "devbox", detail: "ada@10.0.0.5", host: "devbox" },
    { kind: "add", label: "Add SSH connection…" },
    { kind: "remote-repo", label: "Open a repository on a host…", detail: "Clone it there over SSH, then work on it" }]);
  const remote = environmentMenu({ host: "devbox" }, [], hosts);
  expect(remote.map(item => item.kind)).toEqual(["heading", "session", "heading", "clone", "disconnect"]);
  expect(remote[3]).toMatchObject({ detail: "On devbox, with its Git sign-in" });
});

test("the composer has the environment pill and Connect to host has its own dialog", () => {
  const page = readFileSync(new URL("../../dikdik/web/index.html", import.meta.url), "utf8");
  const composer = page.slice(page.indexOf('<div class="composer-controls">'), page.indexOf('id="run-mode-pill"'));
  expect(composer).toContain('id="env-pill"');
  expect(page).toContain('id="env-menu"');
  expect(page).toContain('id="projects-connect-host"');
  const dialog = page.slice(page.indexOf('<dialog id="ssh-dialog"'), page.indexOf('<dialog id="info-dialog"'));
  expect(dialog).toContain('id="project-remote-form"');
  expect(dialog).toContain('id="projects-remote-list"');
  expect(page).toContain('id="ssh-badge"');
  const mode = readFileSync(new URL("../../dikdik/web/conversation-mode.ts", import.meta.url), "utf8");
  expect(mode).toContain('hide("env-pill", chat)');
  const app = readFileSync(new URL("../../dikdik/web/app.ts", import.meta.url), "utf8");
  expect(app).toContain('"/api/ssh/session/disconnect"');
  expect(app).toContain("opened.opener = null");
});

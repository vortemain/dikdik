import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { sidebarUpdate } from "../../dikdik/web/contracts";

const ready = { current: "0.8.0", latest: "v0.8.1", available: true, can_install: true, install: "windows", url: "https://example.test/r" };

test("nothing shows when there is no update and nothing is running", () => {
  expect(sidebarUpdate(null, { state: "idle" }, 0, false).hidden).toBe(true);
  expect(sidebarUpdate({ ...ready, available: false, can_install: false }, { state: "idle" }, 0, false).hidden).toBe(true);
});

test("an available update installs in one click, or asks first when tasks are running", () => {
  const idle = sidebarUpdate(ready, { state: "idle" }, 0, false);
  expect(idle).toMatchObject({ hidden: false, label: "Update to v0.8.1", action: "start", tone: "ready" });
  expect(sidebarUpdate(ready, { state: "idle" }, 2, false)).toMatchObject({ action: "confirm" });
  const asking = sidebarUpdate(ready, { state: "idle" }, 2, true);
  expect(asking.prompt).toBe("2 running tasks will pause and can be resumed after Dikdik restarts.");
  expect(sidebarUpdate(ready, { state: "idle" }, 1, true).prompt).toContain("1 running task will pause");
});

test("progress, verifying, installing and restarting show on the button", () => {
  expect(sidebarUpdate(ready, { state: "downloading", done: 42, total: 100, latest: "v0.8.1" }, 0, false))
    .toMatchObject({ label: "Updating 42%", progress: 42, action: "cancel", tone: "busy" });
  expect(sidebarUpdate(ready, { state: "verifying" }, 0, false)).toMatchObject({ label: "Verifying…", action: "none", progress: null });
  expect(sidebarUpdate(ready, { state: "installing" }, 0, false)).toMatchObject({ label: "Installing…", action: "none" });
  expect(sidebarUpdate(ready, { state: "restarting" }, 0, false)).toMatchObject({ label: "Restarting…", action: "none" });
});

test("a failed update offers a retry and explains why", () => {
  const failed = sidebarUpdate(ready, { state: "failed", error: "Checksum mismatch." }, 0, false);
  expect(failed).toMatchObject({ label: "Update failed · Retry", action: "retry", tone: "failed" });
  expect(failed.title).toContain("Checksum mismatch.");
});

test("python installs copy the pip command and installer-less releases open the release page", () => {
  expect(sidebarUpdate({ ...ready, can_install: false, pip_command: "python -m pip install --upgrade x" }, { state: "idle" }, 0, false))
    .toMatchObject({ action: "copy", label: "Update to v0.8.1" });
  expect(sidebarUpdate({ ...ready, can_install: false }, { state: "idle" }, 0, false)).toMatchObject({ action: "open" });
});

test("settings no longer has an update section, only the version", () => {
  const html = readFileSync(new URL("../../dikdik/web/index.html", import.meta.url), "utf8");
  expect(html).not.toContain('id="update-action"');
  expect(html).not.toContain('id="update-row"');
  expect(html).toContain('id="update-version"');
  expect(html).toContain('id="update-pop"');
});

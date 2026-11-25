import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { sidebarUpdate, updateRecommendation, updateSize } from "../../dikdik/web/contracts";

const ready = { current: "0.8.0", latest: "v0.8.1", available: true, can_install: true, install: "windows", url: "https://example.test/r", notes: "## Changes\n- faster" };

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

test("an available update is recommended once, with its size and running-task warning", () => {
  const rec = updateRecommendation({ ...ready, asset: { size: 52_428_800 } }, { state: "idle" }, 0, "", false);
  expect(rec.show).toBe(true);
  expect(rec.text).toContain("v0.8.1");
  expect(rec.text).toContain("50 MB");
  expect(rec.text).toContain("Install it from here");
  expect(updateRecommendation(ready, { state: "idle" }, 2, "", false).text).toContain("2 running tasks will pause");
});

test("a skipped, already-shown, busy or failed update stays quiet", () => {
  expect(updateRecommendation(ready, { state: "idle" }, 0, "v0.8.1", false).show).toBe(false);
  expect(updateRecommendation(ready, { state: "idle" }, 0, "", true).show).toBe(false);
  expect(updateRecommendation(ready, { state: "downloading" }, 0, "", false).show).toBe(false);
  expect(updateRecommendation({ ...ready, error: "unreachable" }, { state: "idle" }, 0, "", false).show).toBe(false);
  expect(updateRecommendation({ ...ready, available: false }, { state: "idle" }, 0, "", false).show).toBe(false);
});

test("python installs recommend copying the command and installer-less releases open the page", () => {
  expect(updateRecommendation({ ...ready, can_install: false, pip_command: "pip x" }, { state: "idle" }, 0, "", false).text)
    .toContain("Copy the upgrade command");
  expect(updateRecommendation({ ...ready, can_install: false, pip_command: "" }, { state: "idle" }, 0, "", false).text)
    .toContain("Open the release page");
});

test("sizes read in KB and MB, and an unknown size stays blank", () => {
  expect(updateSize(0)).toBe("");
  expect(updateSize(2048)).toBe("2 KB");
  expect(updateSize(5 * 1024 * 1024)).toBe("5.0 MB");
  expect(updateSize(52_428_800)).toBe("50 MB");
});

test("settings offers an in-app update panel, release notes and an automatic-check switch", () => {
  const html = readFileSync(new URL("../../dikdik/web/index.html", import.meta.url), "utf8");
  expect(html).toContain('id="update-version"');
  expect(html).toContain('id="update-pop"');
  expect(html).toContain('id="update-check-now"');
  expect(html).toContain('id="update-install"');
  expect(html).toContain('id="update-notes"');
  expect(html).toContain('id="update-checks"');
});

/** Folder navigation through the local server, independent of a desktop session. */
import type { PaneDeps } from "./models-pane";
import { folderIcon } from "./icons";

interface FolderListing {
  path: string; parent: string | null; folders: { name: string; path: string }[];
  locations: { name: string; path: string }[]; truncated: boolean;
}

export function folderPicker({ make, request }: Pick<PaneDeps, "make" | "request">): (initial?: string, title?: string) => Promise<string | null> {
  let active = false;
  return async (initial = "", title = "Choose a folder") => {
    if (active) return null;
    active = true;
    const dialog = make("dialog", "folder-picker-dialog");
    dialog.setAttribute("aria-labelledby", "folder-picker-title");
    const heading = make("h2", "", title); heading.id = "folder-picker-title";
    const control = (label: string, style: string, action: () => void) => {
      const node = make("button", style, label); node.type = "button"; node.onclick = action; return node;
    };
    const dismiss = control("×", "ghost folder-dismiss", () => finish(null)); dismiss.setAttribute("aria-label", "Close folder chooser");
    const header = make("header", "folder-picker-head"); header.append(heading, dismiss);
    const toolbar = make("form", "folder-picker-toolbar");
    const up = control("↑", "secondary small", () => { if (current?.parent) void load(current.parent); });
    up.setAttribute("aria-label", "Parent folder"); up.disabled = true;
    const address = make("input"); address.setAttribute("aria-label", "Folder path"); address.placeholder = "Enter a folder path";
    address.autocomplete = "off"; address.spellcheck = false;
    const go = make("button", "secondary small", "Go"); go.type = "submit";
    toolbar.append(up, address, go); toolbar.onsubmit = event => { event.preventDefault(); if (address.value.trim()) void load(address.value.trim()); };
    const places = make("nav", "folder-picker-places"); places.setAttribute("aria-label", "Folder locations");
    const list = make("div", "folder-picker-list"); list.setAttribute("aria-label", "Folders");
    const status = make("p", "folder-picker-status"); status.setAttribute("role", "status");
    const contents = make("div", "folder-picker-contents"), main = make("div", "folder-picker-main");
    main.append(status, list); contents.append(places, main);
    const footer = make("footer", "folder-picker-footer"), selected = make("p", "folder-picker-selected");
    const choose = control("Choose folder", "primary small", () => { if (current && !loading) finish(current.path); }); choose.disabled = true;
    footer.append(selected, control("Cancel", "secondary small", () => finish(null)), choose);
    dialog.append(header, toolbar, contents, footer); document.body.append(dialog);
    let current: FolderListing | null = null, loading = false, epoch = 0, ended = false;
    let resolve: (path: string | null) => void = () => undefined;
    const result = new Promise<string | null>(done => { resolve = done; });
    function finish(path: string | null): void {
      if (ended) return;
      ended = true; active = false; epoch++; dialog.close(); dialog.remove(); resolve(path);
    }
    dialog.addEventListener("cancel", event => { event.preventDefault(); finish(null); });
    dialog.addEventListener("close", () => { if (!ended) finish(null); });
    async function load(path: string): Promise<void> {
      const ticket = ++epoch; loading = true; choose.disabled = true; up.disabled = true;
      list.replaceChildren(); status.classList.remove("error-text"); status.textContent = "Loading folders…";
      try {
        const data = await request<FolderListing>("/api/projects/folders" + (path ? `?path=${encodeURIComponent(path)}` : ""), "GET");
        if (ticket !== epoch) return;
        current = data; address.value = data.path; selected.textContent = data.path; selected.title = data.path;
        places.replaceChildren(make("p", "", "Locations"), ...data.locations.map(location => {
          const node = control(location.name, "ghost", () => { void load(location.path); }); node.title = location.path; return node;
        }));
        list.replaceChildren(...data.folders.map(folder => {
          const node = control("", "folder-picker-item", () => { void load(folder.path); });
          node.setAttribute("aria-label", `Open ${folder.name}`);
          node.append(folderIcon(folder.path, false), make("span", "", folder.name), make("span", "folder-picker-chevron", "›")); return node;
        }));
        status.textContent = data.truncated ? "Showing the first 1,000 folders. Enter a path to open another folder." : data.folders.length ? `${data.folders.length} ${data.folders.length === 1 ? "folder" : "folders"} · Choose the current folder or open a subfolder.` : "This folder has no subfolders. You can choose it below.";
      } catch (error) {
        if (ticket !== epoch) return;
        current = null; selected.textContent = "";
        status.classList.add("error-text"); status.textContent = error instanceof Error ? error.message : "Could not open this folder";
      } finally {
        if (ticket === epoch) { loading = false; choose.disabled = !current; up.disabled = !current?.parent; }
      }
    }
    dialog.showModal(); void load(initial); return result;
  };
}

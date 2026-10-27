import { expect, test } from "bun:test";
import { launchValues, pluginCards, type Plugin, type PluginEntry } from "../../dikdik/web/plugins-pane";

const plugins: Plugin[] = [
  { name: "review-kit", scope: "installed", description: "Careful reviews", enabled: true, trusted: true, source: "github:acme/kit" },
  { name: "house", scope: "project", description: "Project rules", enabled: false, trusted: false },
];
const catalog: PluginEntry[] = [
  { marketplace: "team", marketplace_name: "Team", name: "deploy-kit", description: "Deploy helpers", version: "1.0", repo_path: "plugins/deploy-kit", installed: false },
  { marketplace: "team", marketplace_name: "Team", name: "review-kit", description: "", version: "1.2", repo_path: "plugins/review-kit", installed: true },
];
const names = (cards: ReturnType<typeof pluginCards>) => cards.map(c => c.kind === "plugin" ? c.plugin.name : `+${c.entry.name}`);

test("views and search", () => {
  expect(names(pluginCards(plugins, catalog, "all", ""))).toEqual(["review-kit", "house", "+deploy-kit"]);
  expect(names(pluginCards(plugins, catalog, "installed", ""))).toEqual(["review-kit"]);
  expect(names(pluginCards(plugins, catalog, "project", ""))).toEqual(["house"]);
  expect(names(pluginCards(plugins, catalog, "available", ""))).toEqual(["+deploy-kit"]);
  expect(names(pluginCards(plugins, catalog, "all", "acme"))).toEqual(["review-kit"]);
  expect(names(pluginCards(plugins, catalog, "all", "nothing"))).toEqual([]);
});
test("launch values: numbers default to 0, booleans to false", () => {
  const values = launchValues([{ name: "branch" }, { name: "depth", type: "number" }, { name: "strict", type: "boolean" }],
    name => ({ branch: "main", depth: "", strict: false } as Record<string, string | boolean>)[name] ?? "");
  expect(values).toEqual({ branch: "main", depth: 0, strict: false });
});

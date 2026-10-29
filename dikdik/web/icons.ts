/** Shared offline icon resolver for every place a file or provider appears. */
import { fileTheme } from "./icon-manifest";

type IconMap = Record<string, string>;
const theme = fileTheme as unknown as {
  icons: IconMap; fileNames: IconMap; fileExtensions: IconMap; languageIds: IconMap;
  folderNames: IconMap; folderNamesExpanded: IconMap;
  light: { fileNames: IconMap; fileExtensions: IconMap; languageIds: IconMap; folderNames: IconMap; folderNamesExpanded: IconMap };
  file: string; folder: string; folderExpanded: string;
};
const basename = (path: string) => path.replace(/\\/g, "/").split("/").pop()?.toLowerCase() ?? "";

export function fileSymbol(path: string, light = false, language = ""): string {
  const name = basename(path), overrides = light ? theme.light : undefined;
  let key = overrides?.fileNames[name] ?? theme.fileNames[name];
  // Longest suffix first: test.ts, d.ts, blade.php and config.js get their own symbols.
  const parts = name.split(".");
  for (let i = 1; !key && i < parts.length; i++) {
    const suffix = parts.slice(i).join(".");
    key = overrides?.fileExtensions[suffix] ?? theme.fileExtensions[suffix];
  }
  key ??= overrides?.languageIds[language] ?? theme.languageIds[language] ?? theme.file;
  return "/assets/files/" + (theme.icons[key] ?? theme.icons[theme.file]);
}

export function folderSymbol(path: string, open: boolean, light = false): string {
  const name = basename(path), map = open ? "folderNamesExpanded" : "folderNames";
  const key = (light ? theme.light[map][name] : undefined) ?? theme[map][name] ?? (open ? theme.folderExpanded : theme.folder);
  return "/assets/files/" + theme.icons[key];
}

const PROVIDERS: Record<string, string> = {
  nvidia: "nvidia",
  openrouter: "openrouter", openai: "openai", anthropic: "anthropic", google: "gemini-color", gemini: "gemini-color",
  deepseek: "deepseek-color", xai: "grok", grok: "grok", mistral: "mistral-color", groq: "groq", cerebras: "cerebras-color",
  together: "together-color", fireworks: "fireworks-color", moonshot: "moonshot", zai: "zai", qwen: "qwen-color",
  perplexity: "perplexity-color", "perplexity-orchestrator": "perplexity-color", ollama: "ollama", lmstudio: "lmstudio", azure: "azure-color",
};
const HOSTS: Record<string, string> = {
  "integrate.api.nvidia.com": "nvidia",
  "openrouter.ai": "openrouter", "api.openai.com": "openai", "api.anthropic.com": "anthropic",
  "generativelanguage.googleapis.com": "google", "api.deepseek.com": "deepseek", "api.x.ai": "xai",
  "api.mistral.ai": "mistral", "api.groq.com": "groq", "api.cerebras.ai": "cerebras",
  "api.together.xyz": "together", "api.together.ai": "together", "api.fireworks.ai": "fireworks",
  "api.moonshot.ai": "moonshot", "api.moonshot.cn": "moonshot", "api.z.ai": "zai",
  "dashscope-intl.aliyuncs.com": "qwen", "dashscope.aliyuncs.com": "qwen", "api.perplexity.ai": "perplexity",
};

/** Resolve the serving provider, not a guess from the model's marketing name. */
export function providerId(provider = "", endpoint = ""): string {
  if (endpoint) {
    try {
      const url = new URL(endpoint), host = url.hostname.toLowerCase();
      if (host === "api.perplexity.ai" && url.pathname.startsWith("/orchestrator/")) return "perplexity-orchestrator";
      if (HOSTS[host]) return HOSTS[host]!;
      if (host.endsWith(".openai.azure.com") || host.endsWith(".services.ai.azure.com")) return "azure";
      if (["localhost", "127.0.0.1", "[::1]"].includes(host)) return url.port === "11434" ? "ollama" : url.port === "1234" ? "lmstudio" : "";
      // A custom gateway is not OpenAI just because it speaks the OpenAI protocol.
      return "";
    } catch { return ""; }
  }
  return provider in PROVIDERS ? provider : "";
}

export function providerSymbol(id: string): { src: string; mono: boolean } | null {
  if (id === "nvidia") return { src: "/assets/brands/nvidia.svg", mono: false };
  const name = PROVIDERS[id];
  return name ? { src: `/assets/providers/${name}.svg`, mono: !name.endsWith("-color") } : null;
}

export function fileIcon(path: string, language = ""): HTMLImageElement {
  const node = document.createElement("img");
  const mode = document.documentElement?.dataset.theme;
  const light = mode === "light" || (mode !== "dark" && !globalThis.matchMedia?.("(prefers-color-scheme: dark)").matches);
  node.className = "file-symbol"; node.src = fileSymbol(path, light, language);
  node.dataset.file = path; node.dataset.language = language;
  node.alt = ""; node.setAttribute("aria-hidden", "true"); node.draggable = false;
  return node;
}

export function folderIcon(path: string, open: boolean): HTMLImageElement {
  const node = fileIcon(path);
  const mode = document.documentElement?.dataset.theme;
  const light = mode === "light" || (mode !== "dark" && !globalThis.matchMedia?.("(prefers-color-scheme: dark)").matches);
  node.src = folderSymbol(path, open, light); node.dataset.folder = String(open);
  return node;
}

export function refreshFileIcons(): void {
  const mode = document.documentElement?.dataset.theme;
  const light = mode === "light" || (mode !== "dark" && !globalThis.matchMedia?.("(prefers-color-scheme: dark)").matches);
  for (const node of document.querySelectorAll?.<HTMLImageElement>("img.file-symbol[data-file]") ?? []) {
    node.src = node.dataset.folder === undefined ? fileSymbol(node.dataset.file ?? "", light, node.dataset.language)
      : folderSymbol(node.dataset.file ?? "", node.dataset.folder === "true", light);
  }
}

export function providerIcon(id: string): HTMLElement {
  const symbol = providerSymbol(id);
  if (!symbol) {
    const node = document.createElement("span"); node.className = "provider-symbol provider-generic";
    node.textContent = "◇"; node.setAttribute("aria-hidden", "true"); return node;
  }
  const node = document.createElement("img");
  node.className = "provider-symbol" + (symbol.mono ? " provider-mono" : "") + (id === "nvidia" ? " provider-nvidia" : "");
  node.src = symbol.src; node.alt = ""; node.setAttribute("aria-hidden", "true"); node.draggable = false;
  return node;
}

export function vscodeLogo(): HTMLImageElement {
  const node = document.createElement("img"); node.className = "vscode-logo";
  node.src = "/assets/brands/vscode.svg"; node.alt = ""; node.setAttribute("aria-hidden", "true");
  return node;
}

export function dikdikLogo(): HTMLImageElement {
  const node = document.createElement("img"); node.className = "dikdik-logo";
  node.src = "/icon.svg"; node.alt = ""; node.setAttribute("aria-hidden", "true"); return node;
}

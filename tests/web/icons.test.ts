import { expect, test } from "bun:test";
import { existsSync } from "node:fs";
import { fileSymbol, folderSymbol, providerId, providerSymbol } from "../../dikdik/web/icons";
import { fileTheme } from "../../dikdik/web/icon-manifest";

test("real file symbols handle names, compound extensions, case and Windows paths", () => {
  for (const [file, expected] of [
    ["src/main.py", "python.svg"], ["C:\\project\\App.TSX", "react_ts.svg"],
    ["src/app.ts", "typescript.svg"], ["package.json", "nodejs.svg"],
    ["Dockerfile", "docker.svg"], ["README.md", "readme.svg"], [".gitignore", "git.svg"],
  ]) expect(fileSymbol(file!)).toEndWith(expected!);
  expect(fileSymbol("types.d.ts")).not.toBe(fileSymbol("src/app.ts"));
  expect(fileSymbol("unknown.weird")).toEndWith("file.svg");
  expect(fileSymbol("../../../secret")).toBe(fileSymbol("secret"));
  expect(folderSymbol("src", true)).not.toBe(folderSymbol("src", false));
});

test("every file theme reference resolves to a bundled asset", () => {
  for (const filename of Object.values(fileTheme.icons)) {
    expect(filename).toMatch(/^[a-zA-Z0-9_.-]+\.svg$/);
    expect(existsSync("dikdik/web/assets/files/" + filename)).toBe(true);
  }
  for (const map of [fileTheme.fileNames, fileTheme.fileExtensions, fileTheme.folderNames, fileTheme.folderNamesExpanded, fileTheme.languageIds]) {
    for (const id of Object.values(map)) expect(id in fileTheme.icons).toBe(true);
  }
});

test("all catalog providers have real local logos; custom gateways stay neutral", () => {
  const providers = "openrouter openai anthropic google deepseek xai mistral groq cerebras together fireworks moonshot zai qwen perplexity ollama lmstudio azure".split(" ");
  for (const provider of providers) {
    const symbol = providerSymbol(provider)!;
    expect(symbol.src).toStartWith("/assets/providers/");
    expect(existsSync("dikdik/web" + symbol.src)).toBe(true);
  }
  expect(providerSymbol("custom-provider")).toBeNull();
  expect(providerSymbol("xai")?.src).toBe("/assets/providers/grok.svg");
  expect(providerSymbol("grok")?.src).toBe("/assets/providers/grok.svg");
  expect(providerSymbol("nvidia")).toEqual({ src: "/assets/brands/nvidia.svg", mono: false });
  expect(providerId("openai", "https://integrate.api.nvidia.com/v1")).toBe("nvidia");
  expect(providerId("openai", "https://openrouter.ai/api/v1")).toBe("openrouter");
  expect(providerId("openai", "https://api.groq.com/openai/v1")).toBe("groq");
  expect(providerId("openai", "https://company-gateway.example/v1")).toBe("");
  expect(providerId("openai", "https://api.openai.com.attacker.example/v1")).toBe("");
  expect(providerId("local", "http://localhost:11434/v1")).toBe("ollama");
  expect(providerId("local", "http://127.0.0.1:1234/v1")).toBe("lmstudio");
});

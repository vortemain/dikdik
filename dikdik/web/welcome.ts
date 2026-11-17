// Welcome screen: adaptive greeting rotation and context-aware suggestions.
//
// The greeting cycles through French and English phrases every few seconds.
// The suggestions adapt to the workspace — what files exist, what languages
// are in use, whether there are data tables to analyze — so each project
// gets relevant starting points instead of generic ones.
//
// Pure: takes the project facts and renders into the #welcome-suggestions
// container. No app state, no API calls.

import { make, icon } from "./dom";

export interface ProjectFacts {
  name: string;
  files: string[];
  hasData: boolean;    // CSV, TSV, JSON, XLSX
  hasTests: boolean;   // test files or python_tests
  hasReadme: boolean;
  hasMarkdown: boolean;
  hasPython: boolean;
  hasTypeScript: boolean;
  hasWeb: boolean;     // HTML, CSS, JS/TS frontend
  hasGit: boolean;
  fileCount: number;
}

export interface Suggestion {
  prompt: string;
  label: string;
  iconId: string;     // SVG symbol id from the icon sprite
  hint?: string;      // small sub-label
}

// Greetings rotate in French and English. Warm but not cheesy.
const GREETINGS = [
  "Hello! What can I do for you?",
  "Bonjour ! Comment puis-je vous aider ?",
  "Hi! What are we working on today?",
  "Salut ! On s'y met ?",
  "Hey — ask me anything.",
  "Bienvenue ! Posez votre question.",
  "Ready when you are.",
  "À votre service. Que puis-je faire ?",
];

/** Start the greeting rotation. Returns a cancel function. */
export function startGreeting(element: HTMLElement): () => void {
  let index = 0;
  const rotate = () => {
    index = (index + 1) % GREETINGS.length;
    element.classList.add("greeting-out");
    setTimeout(() => {
      element.textContent = GREETINGS[index]!;
      element.classList.remove("greeting-out");
      element.classList.add("greeting-in");
      setTimeout(() => element.classList.remove("greeting-in"), 600);
    }, 300);
  };
  const timer = setInterval(rotate, 4500);
  return () => clearInterval(timer);
}

/** Pick the most relevant suggestions for this project. */
export function suggestionsFor(facts: ProjectFacts): Suggestion[] {
  const tips: Suggestion[] = [];

  // Always: a friendly research starter
  tips.push({
    prompt: "Research the latest best practices for this kind of project and give me a summary.",
    label: "Research best practices",
    iconId: "search",
    hint: "web + sources",
  });

  // Data files → analysis
  if (facts.hasData) {
    tips.push({
      prompt: "Find the data files in this workspace and analyze them. Give me statistics, distributions and any interesting patterns.",
      label: "Analyze my data",
      iconId: "chart",
      hint: "stats · correlations",
    });
  }

  // Tests → run or understand tests
  if (facts.hasTests) {
    tips.push({
      prompt: "What tests are in this project? Summarize what they cover and suggest any gaps.",
      label: "Review my tests",
      iconId: "check",
      hint: "coverage gaps",
    });
  }

  // Markdown / docs → documentation
  if (facts.hasMarkdown || facts.hasReadme) {
    tips.push({
      prompt: "Read the documentation in this workspace and summarize what this project does, how it's structured, and what I should know.",
      label: "Explain this project",
      iconId: "book",
      hint: "docs · structure",
    });
  }

  // Python project
  if (facts.hasPython && !facts.hasData) {
    tips.push({
      prompt: "What does the Python code in this project do? Walk me through the main modules and their relationships.",
      label: "Walk through the code",
      iconId: "file",
      hint: "architecture",
    });
  }

  // Web project → preview
  if (facts.hasWeb) {
    tips.push({
      prompt: "What frontend framework and build setup does this project use? Summarize the tech stack and how to run it locally.",
      label: "Understand the frontend",
      iconId: "globe",
      hint: "tech stack",
    });
  }

  // Always: explore files
  if (facts.fileCount > 0) {
    tips.push({
      prompt: `Give me a quick overview of the ${facts.fileCount} files in this workspace. What are the main directories and what do they contain?`,
      label: "Explore my files",
      iconId: "folder-open",
      hint: `${facts.fileCount} files`,
    });
  }

  // Always: a creative / open-ended prompt
  tips.push({
    prompt: "What's something interesting or non-obvious about this project that I might not know?",
    label: "Surprise me",
    iconId: "sparkle",
    hint: "discoveries",
  });

  // Cap at 6, prioritizing the most specific ones
  return tips.slice(0, 6);
}

/** Render suggestions into a container. Each is a pill with an icon + label + hint. */
export function renderSuggestions(container: HTMLElement, suggestions: Suggestion[], onPick: (prompt: string) => void): void {
  container.replaceChildren();
  for (const tip of suggestions) {
    const button = make("button", "suggestion");
    button.type = "button";
    button.dataset.prompt = tip.prompt;
    button.append(icon(tip.iconId));
    const label = make("span", "suggestion-label", tip.label);
    button.append(label);
    if (tip.hint) {
      const hint = make("span", "suggestion-hint", tip.hint);
      button.append(hint);
    }
    button.addEventListener("click", () => onPick(tip.prompt));
    container.append(button);
  }
}

/** Detect project facts from the file list returned by /api/project. */
export function factsFromFiles(name: string, files: string[], pythonTests: string[]): ProjectFacts {
  const lower = files.map(f => f.toLowerCase());
  const has = (suffix: string) => lower.some(f => f.endsWith(suffix));
  const hasAny = (suffixes: string[]) => suffixes.some(s => lower.some(f => f.endsWith(s)));
  return {
    name,
    files,
    hasData: hasAny([".csv", ".tsv", ".xlsx", ".xlsm"]) || lower.some(f => f.endsWith(".json") && !f.includes("package") && !f.includes("tsconfig") && !f.includes("config")),
    hasTests: pythonTests.length > 0 || lower.some(f => f.includes("test") || f.includes(".spec.") || f.endsWith(".test.ts") || f.endsWith(".test.tsx") || f.endsWith(".test.js")),
    hasReadme: lower.some(f => f.startsWith("readme")),
    hasMarkdown: hasAny([".md", ".markdown"]),
    hasPython: has(".py"),
    hasTypeScript: hasAny([".ts", ".tsx"]),
    hasWeb: hasAny([".html", ".css", ".jsx"]) || lower.some(f => f.endsWith(".tsx")),
    hasGit: true,  // the workspace is a git repo (Dikdik requires it)
    fileCount: files.length,
  };
}
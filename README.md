<div align="center">
  <a href="https://github.com/vortemain/dikdik">
    <img src="./dikdik/web/icon.svg" alt="Dikdik logo" width="104" height="104" />
  </a>

  <h1>Dikdik</h1>
  <p><strong>Small name. Serious intelligence.</strong></p>
  <p>An AI workspace that runs on your computer.<br />Chat and research with sources, analyse documents and data, and build software with an agent — using the models you choose.</p>

  <p>
    <a href="https://github.com/vortemain/dikdik/actions/workflows/release.yml"><img src="https://github.com/vortemain/dikdik/actions/workflows/release.yml/badge.svg?branch=main" alt="Windows and Linux checks" /></a>
    <a href="https://github.com/vortemain/dikdik/releases/latest"><img src="https://img.shields.io/github/v/release/vortemain/dikdik?style=flat-square&amp;color=1F3D31&amp;label=release" alt="Latest release" /></a>
    <a href="pyproject.toml"><img src="https://img.shields.io/badge/Python-3.13%2B-1F3D31?style=flat-square" alt="Python 3.13 or newer" /></a>
    <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-1F3D31?style=flat-square" alt="MIT license" /></a>
  </p>

  <p>
    <a href="#quick-start">Quick start</a> &nbsp;·&nbsp;
    <a href="#chat">Chat</a> &nbsp;·&nbsp;
    <a href="#documents-data-and-images">Documents &amp; data</a> &nbsp;·&nbsp;
    <a href="#code">Code</a> &nbsp;·&nbsp;
    <a href="#documentation">Documentation</a> &nbsp;·&nbsp;
    <a href="https://github.com/vortemain/dikdik/releases/latest">Downloads</a>
  </p>
</div>

---

Dikdik has two ways to work, side by side in the sidebar:

- **Chat** — a general-purpose assistant for any question: research, science, studies, writing, business and everyday topics. It searches the web and reads sources before answering, reads your Word, PDF and Excel files in full, runs real statistics, and writes finished documents. A chat needs no project: just start typing.
- **Code** — an agent that works inside a project folder: it plans, edits files, runs commands and tests, previews apps in a browser, and shows every change for review, with checkpoints and Undo.

**Web research with no setup · Word/PDF/Excel in, Word/Excel out · Research-grade statistics · Works with text-only models · 35 bundled skills · Your choice of models**

> [!NOTE]
> **Dikdik is a developer preview.** Get the newest build from the [latest release](https://github.com/vortemain/dikdik/releases/latest) and see [Security](SECURITY.md) for operating boundaries.

## Quick start

### 1. Install

Download the **Windows setup `.exe`**, **macOS `.dmg`**, or **Linux amd64 `.deb`** from the
[latest release](https://github.com/vortemain/dikdik/releases/latest). Desktop bundles include
Python and Chromium. Windows requires WebView2; Linux packages target Ubuntu 24.04 or newer
compatible Debian systems. These are unsigned developer previews; macOS builds are not notarized.
See [desktop installation](docs/DESKTOP.md) for platform requirements.

For the Python command line and browser interface (**Python 3.13 or newer**), download the wheel
and `SHA256SUMS.txt` from the same release, verify the checksum, then install:

```bash
python -m pip install ./dikdik-0.5.2-py3-none-any.whl
dikdik --version
dikdik /path/to/a-project-folder
```

Dikdik opens at **http://127.0.0.1:8787** and signs you in automatically. Use `--no-browser`
to start without opening a tab, and **Ctrl+C** to stop the server. For the desktop window from a
wheel, install `"dikdik-0.5.2-py3-none-any.whl[desktop]"` and run `dikdik-desktop`. From a source
checkout, `python -m pip install -e ".[desktop]"`, then double-click **Dikdik.pyw** on Windows.
[Desktop setup →](docs/DESKTOP.md) · [Download verification →](docs/RELEASING.md)

Nothing else is required: no Docker, Node.js or Rust to run it, and no extra packages for
documents, statistics, OCR or web search.

### 2. Connect a model

Open **Settings → Models**, add a provider connection and choose a model. Dikdik works with
OpenRouter, NVIDIA, Nebius, OpenAI, Anthropic, Google and any OpenAI-compatible endpoint,
including local servers such as Ollama and LM Studio. Switch models per conversation from the
composer. Your provider receives only the context you send; charges depend on your account.

### 3. Start

- **Chat:** pick the **Chat** tab, press **New chat** and ask anything — attach a thesis, a survey
  export, a PDF or a screenshot if it helps.
- **Code:** pick the **Code** tab, choose or create a project folder, review the trust prompt,
  and give the agent an objective. Start in **Plan only** to explore before changing anything.

## Chat

A chat belongs to no project. It has its own private folder for the files you attach and the
files it creates, and it is listed from every project.

| | What it does |
| --- | --- |
| **Researches before answering** | Questions about companies, people, products, recent events, statistics, or health, legal and financial specifics are searched with several queries; the best sources are read, cross-checked and cited. |
| **Web search with no key** | Uses DuckDuckGo (then Wikipedia) out of the box; a connected Tavily key is used first. Pages are read with `web_fetch`. |
| **Thinks like an expert** | Step-by-step reasoning for science and maths, academic conventions for studies and theses, honest limits for medical, legal and financial questions. Never invents sources. |
| **Delivers finished work** | Writes Word reports with headings, tables, captions, a table of contents, page numbers and native, editable charts; Excel workbooks; Markdown and CSV. Each file appears as a card with Preview, Download, Open in app and Show in folder. |
| **Keeps context** | Files attached earlier stay available after a pause, a stop or a follow-up. Pause and resume a long task exactly where it stopped. |
| **Runs analyses on request** | Turn on *Run commands* to let it run Python scripts in the chat's folder, each with your approval. |

## Documents, data and images

| Tool | Handles |
| --- | --- |
| `read_document` | Word (.docx, .odt, .rtf), PDF (including scanned PDFs, read with OCR), Excel (.xlsx, .xls, .ods, .csv), PowerPoint (.pptx, .odp), EPUB and HTML — in full, paged, with an outline and phrase search. |
| `statistics` | Frequencies, descriptives, Likert scales with composite scores and Cronbach's alpha, crosstabs with chi-square, Cramér's V and Fisher's exact test, t-test and Mann-Whitney, ANOVA and Kruskal-Wallis, Pearson and Spearman correlation, linear and logistic regression. Answers such as "4 = Agree" are coded automatically. Results match SciPy and statsmodels. |
| `create_document` | Word documents from Markdown with native charts, Excel workbooks, and text formats. |
| `read_image` | Images, pictures inside documents, and scanned pages, as text. |

**Previews inside Dikdik.** Word documents open as formatted pages with their pictures and charts;
spreadsheets and CSV files in a spreadsheet view that fits phones, tablets and desktops (frozen
headers, column letters, a cell bar for long values, search, sheet tabs); slides, PDFs and images
in their own viewers.

**Images for every model.** Text in images is read on your computer with the OCR built into
Windows (or Tesseract on macOS/Linux). If one of your models can see images, it describes charts,
tables and screenshots for the conversation's model (Settings → General → *Read images with*).
A model that refuses images receives that text instead, so text-only models still work with them.

## Code

| Capability | What you can do |
| --- | --- |
| **Build with an agent** | Discover files, search code, make targeted edits, create files, run commands and tests, and work through failures. |
| **Plan and continue** | Plan-only conversations, checklists, pause and resume from the exact step, branch conversations. |
| **Review changes** | Diffs, check results, a durable terminal history, checkpoints, and Undo for a turn. |
| **Browse and preview** | A live browser panel, app previews at desktop and mobile widths, and interactive testing of pages. |
| **Work with files and Git** | Browse source and documents, preview Markdown and Office files, commit, push and open pull requests. |
| **Set up the computer** | Detect and install missing toolchains (about 40, with winget, Homebrew, apt and others). |
| **Extend the agent** | Skills on demand, plugins, MCP servers, agent profiles, and lifecycle hooks. |
| **Automate work** | Daily, interval, cron or Git-revision triggers with explicit approvals and run history. |
| **Integrate** | A Python client and an authenticated OpenAI-compatible gateway. |

### Permission modes

| Mode | How it works |
| --- | --- |
| **Ask before commands** | The agent edits files in your project and asks before commands, web visits and MCP calls. |
| **Auto** (default) | The agent works and verifies in your trusted project without per-tool prompts. Permission rules still apply. |
| **Plan only** | Explore the repository and prepare a plan with read-only tools. |
| **Classic** | Work on an isolated candidate with syntax checks only, and review before applying. |

Results distinguish **passing tests**, **passing static checks** and **unavailable verification**;
a syntax check or a missing test tool is never reported as a passed test suite.
[Verification and recovery →](docs/MODEL_RELIABILITY.md)

## Skills and hooks

Dikdik ships **35 skills** — planning, debugging, research, data analysis, document creation,
implementation, review, testing, security, UI and delivery — loaded on demand. A central hook
engine exposes six lifecycle events (`task.started`, `context.building`, `tool.before`,
`tool.after`, `tool.failed`, `task.completed`) with priorities, allow/block decisions, timeouts
and traces. Plugins can bundle skills, hooks, agents and MCP configuration; their MCP servers start
disabled until you trust them. [Skills and hooks →](docs/AGENT_SKILLS_AND_HOOKS.md)

## Models and connections

- **Hosted providers:** any supported provider or a custom OpenAI-compatible endpoint, with a live model directory for Nebius and others.
- **Local models:** a local compatible server with a tool-capable model.
- **Keys:** new connection keys stay in memory for up to eight hours and are cleared on shutdown; profiles store model settings only.
- **Recovery:** bounded retries, response validation, and automatic context compaction that keeps requirements, plans and findings.
- **Usage:** reported tokens and costs when the provider supplies them.

<details>
<summary><strong>Environment-variable configuration</strong></summary>

When no saved profile is configured, these server-side variables provide a fallback:

| Variable | Purpose |
| --- | --- |
| `DIKDIK_MODEL_URL` | Exact HTTPS chat-completions endpoint; loopback HTTP is supported for local servers. |
| `DIKDIK_MODEL` | Provider model ID. Agent work requires function-tool support. |
| `DIKDIK_API_KEY` | Provider credential, when required by the endpoint. |

Keep credentials out of source files and committed configuration.

</details>

[Models →](docs/MODEL_DISCOVERY.md) · [Nebius and Tavily →](docs/NEBIUS_TAVILY.md) · [Reliability →](docs/MODEL_RELIABILITY.md)

## Documentation

| Guide | Covers |
| --- | --- |
| [Skills and lifecycle hooks](docs/AGENT_SKILLS_AND_HOOKS.md) | Skill catalog, hook rules, plugin trust and execution traces. |
| [Planning](docs/PLANNING.md) | Plan conversations, checklists and building a reviewed plan. |
| [Models](docs/MODEL_DISCOVERY.md) | Connections, model discovery, profiles and provider setup. |
| [Nebius and Tavily](docs/NEBIUS_TAVILY.md) | Nebius Token Factory models and Tavily web research. |
| [Reliability](docs/MODEL_RELIABILITY.md) | Provider errors, response recovery, context continuity and verification. |
| [Browser](docs/BROWSER.md) | Browser setup, captures, permissions and network boundaries. |
| [Conversation formatting](docs/CONVERSATION_FORMATTING.md) | Markdown, tables, charts, diagrams and design previews. |
| [Composer](docs/COMPOSER.md) | Saved drafts, attachments and conversation input. |
| [Desktop](docs/DESKTOP.md) | Desktop installers and platform requirements. |
| [Server deployment](docs/CLOUD.md) | HTTPS, remote access and project boundaries. |
| [Releases](docs/RELEASING.md) | Versioning, builds, checksums and release automation. |
| [Security](SECURITY.md) | Permissions, execution boundaries and deployment considerations. |

## Development

Requires **Python 3.13+** and **Bun 1.4.0** for frontend work. The compiled browser bundle is committed.

```bash
git clone https://github.com/vortemain/dikdik.git
cd dikdik
python -m pip install -e .
bun install --frozen-lockfile

python -m unittest discover -s tests -v
bun run typecheck:web
bun run test:web
bun run build:web
```

Start Dikdik against a separate folder with `dikdik /path/to/a-project-folder`; it refuses to use
its own program folder as a project.

<details>
<summary><strong>Optional tools</strong></summary>

| Tool | Needed for |
| --- | --- |
| **Git** | Cloning and Git operations. |
| **GitHub CLI / GitLab CLI** | Their account and repository integrations. |
| **Edge or Chrome** | Local browser captures when using the host browser. |
| **Tesseract** | OCR on macOS and Linux (Windows has OCR built in). |
| **Rust** | Building the optional native repository indexer (`python scripts/build_native.py`). |

</details>

See [CHANGELOG.md](CHANGELOG.md) for release history and [GitHub Issues](https://github.com/vortemain/dikdik/issues) to report a problem or suggest an improvement.

## License

Original Dikdik code is available under the **[MIT License](LICENSE)**. Bundled dependencies, fonts
and product marks keep their own terms; see [LICENSE-STATUS.md](LICENSE-STATUS.md) and the
[asset notices](dikdik/web/assets/NOTICES.md).

---

<div align="center">
  <sub>Small name. Serious intelligence.</sub><br />
  <a href="https://github.com/vortemain/dikdik/releases/latest">Get Dikdik</a> · <a href="https://github.com/vortemain/dikdik/issues">Share feedback</a> · <a href="LICENSE">MIT licensed</a>
</div>

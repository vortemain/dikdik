<div align="center">
  <a href="https://github.com/vortemain/dikdik">
    <img src="./dikdik/web/icon.svg" alt="Dikdik logo" width="104" height="104" />
  </a>

  <h1>Dikdik</h1>
  <p><strong>Small name. Serious intelligence.</strong></p>
  <p>An agentic coding workspace that runs on your computer.<br />Plan work, edit files, run tools, and review changes with the models you choose.</p>

  <p>
    <a href="https://github.com/vortemain/dikdik/actions/workflows/release.yml"><img src="https://github.com/vortemain/dikdik/actions/workflows/release.yml/badge.svg?branch=main" alt="Windows and Linux checks" /></a>
    <a href="https://github.com/vortemain/dikdik/releases"><img src="https://img.shields.io/github/v/release/vortemain/dikdik?include_prereleases&amp;style=flat-square&amp;color=1F3D31&amp;label=release" alt="GitHub release" /></a>
    <a href="pyproject.toml"><img src="https://img.shields.io/badge/Python-3.13%2B-1F3D31?style=flat-square" alt="Python 3.13 or newer" /></a>
    <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-1F3D31?style=flat-square" alt="MIT license" /></a>
  </p>

  <p>
    <a href="#quick-start">Quick start</a> &nbsp;·&nbsp;
    <a href="#inside-your-workspace">Features</a> &nbsp;·&nbsp;
    <a href="#skills-and-hooks">Skills &amp; hooks</a> &nbsp;·&nbsp;
    <a href="#documentation">Documentation</a> &nbsp;·&nbsp;
    <a href="https://github.com/vortemain/dikdik/releases">Downloads</a>
  </p>
</div>

---

Dikdik brings the conversation, editor, terminal, browser, and change review into one workspace. Give the agent an objective, choose how it may act, and inspect the files, checks, and execution history as it works.

**34 bundled skills · 6 lifecycle events · Checkpoints and Undo · Your choice of models**

> [!NOTE]
> **v0.1.0 is a developer preview.** See the [release notes](https://github.com/vortemain/dikdik/releases/tag/v0.1.0) for downloads and validation, and [Security](SECURITY.md) for operating boundaries.

## Quick start

### 1. Install a release

Requires **Python 3.13 or newer**. Download the wheel and `SHA256SUMS.txt` from [GitHub Releases](https://github.com/vortemain/dikdik/releases), verify its checksum, then install:

```bash
python -m pip install ./dikdik-0.1.0-py3-none-any.whl
dikdik --version
dikdik /path/to/your-project
```

Replace the project path with your own folder. On Windows, for example:

```powershell
dikdik C:\Projects\my-app
```

Dikdik opens its browser interface at **http://127.0.0.1:8787** and signs you in automatically. Use `--no-browser` to start without opening a tab, and **Ctrl+C** to stop the server.

The wheel includes the web interface, fonts, icons, and bundled skills. Node.js, Bun, and Rust are not required to run it. On Windows, pip also installs `tzdata` for named time zones. [Download verification and release details →](docs/RELEASING.md)

### 2. Connect a model

Open **Settings → Models**, add a provider connection, and choose a model. Dikdik supports providers including OpenRouter, NVIDIA, OpenAI, Anthropic, Google, and compatible local servers such as Ollama and LM Studio.

Use the model picker in the composer to switch profiles. Your selected provider receives the context you authorize; provider charges depend on your account and model.

### 3. Open a project and start a conversation

Choose an existing folder, create a project, or clone a repository from the project picker. Review the project trust prompt, then choose a permission mode.

Start in **Plan only** to explore a repository before making changes:

> Inspect this project, explain how it works, and propose a plan for the next change.

Switch to **Ask before commands** when you are ready to implement. Review the agent's steps, terminal output, checks, and diffs as it works.

## Inside your workspace

| Capability | What you can do |
| --- | --- |
| **Code with an agent** | Discover files, search code, make targeted edits, create files, run commands, and work through failures. |
| **Plan and continue** | Keep checklists, pause or stop work, resume saved progress, and branch conversations. |
| **Review changes** | Inspect diffs and check results, keep a durable terminal history, and restore a turn with Undo. |
| **Choose your models** | Save connections and profiles, tune model settings, route requests, and inspect provider-reported usage. |
| **Extend the agent** | Load skills on demand, review plugins, connect MCP tools, and configure agent profiles. |
| **Work with files and Git** | Browse source and images, preview Markdown, inspect repository status, and commit, push, or open pull requests. |
| **Browse and preview** | Capture public web pages, inspect browsing history, and open isolated HTML/CSS previews. |
| **Automate work** | Configure daily, interval, cron, or Git-revision triggers with explicit approvals and run history. |
| **Build integrations** | Use the Python client or authenticated OpenAI-compatible gateway for programmatic access. |

## Workspace modes

| Mode | How it works |
| --- | --- |
| **Ask before commands** | The agent edits files in your project and asks before commands, web visits, and MCP calls. |
| **Auto** | The agent works in your project and asks before actions classified as high risk. |
| **Plan only** | Explore the repository and prepare a plan with read-only tools. |
| **Classic** | Work on an isolated candidate, verify with Docker when approved, and review before applying changes. |

Local modes run approved commands with your computer's tools and permissions. Checkpoints record a turn's changes; **Undo** restores them unless you have edited those files afterward.

Result details distinguish **passing tests**, **passing static checks**, and **unavailable verification**. A syntax check or missing test tool is never reported as a passed test suite. See [verification behavior and recovery](docs/MODEL_RELIABILITY.md).

## Skills and hooks

Dikdik ships with **34 distinct skills** covering planning, debugging, research, implementation, review, testing, security, data work, UI, and delivery. Browse the catalog in Settings and load the workflow you need on demand.

The central hook engine exposes six typed lifecycle events:

```text
task.started       context.building
tool.before        tool.after
tool.failed        task.completed
```

Hooks support priority ordering, explicit allow/block decisions, timeouts, structured outputs, and correlated execution traces. Plugin hooks cannot grant permissions or override core security denials and required approvals.

Plugins can bundle skills, hook rules, agent definitions, and MCP configuration. Review a plugin's contents before trusting it; its MCP servers start disabled.

[Explore the skill catalog and hook configuration →](docs/AGENT_SKILLS_AND_HOOKS.md)

## Models and connections

Connections hold credentials; profiles select a model and its settings. You can reuse a connection across profiles and select a different profile for each conversation.

- **Hosted providers:** configure a supported provider or a custom OpenAI-compatible endpoint.
- **Local models:** connect a local compatible server and choose a tool-capable model for agent work.
- **Recovery:** bounded retries and response validation handle supported transient and malformed-response failures.
- **Context:** automatic compaction retains requirements, plans, findings, and current change state.
- **Usage:** inspect reported tokens and costs when the provider supplies them.

<details>
<summary><strong>Environment-variable configuration</strong></summary>

When no saved profile is configured, these server-side variables provide a fallback:

| Variable | Purpose |
| --- | --- |
| `DIKDIK_MODEL_URL` | Exact HTTPS chat-completions endpoint; loopback HTTP is supported for local servers. |
| `DIKDIK_MODEL` | Provider model ID. Agent work requires function-tool support. |
| `DIKDIK_API_KEY` | Provider credential, when required by the endpoint. |

Set credentials in your environment or the application's encrypted key store. Keep them out of source files and committed configuration.

</details>

[Model discovery and configuration →](docs/MODEL_DISCOVERY.md) · [Failure diagnosis and reliability →](docs/MODEL_RELIABILITY.md)

## Documentation

| Guide | Covers |
| --- | --- |
| [Skills and lifecycle hooks](docs/AGENT_SKILLS_AND_HOOKS.md) | Skill catalog, hook rules, plugin trust, and execution traces. |
| [Planning](docs/PLANNING.md) | Plan conversations, checklists, and building a reviewed plan. |
| [Models](docs/MODEL_DISCOVERY.md) | Connections, model discovery, profiles, and provider setup. |
| [Reliability](docs/MODEL_RELIABILITY.md) | Provider errors, response recovery, context continuity, and verification. |
| [Browser](docs/BROWSER.md) | Browser setup, captures, permissions, and network boundaries. |
| [Conversation formatting](docs/CONVERSATION_FORMATTING.md) | Markdown, tables, charts, and isolated design previews. |
| [Composer](docs/COMPOSER.md) | Saved drafts, attachments, and conversation input. |
| [Server deployment](docs/CLOUD.md) | HTTPS, remote access, and project boundaries. |
| [Releases](docs/RELEASING.md) | Versioning, builds, checksums, and release automation. |
| [Security](SECURITY.md) | Permissions, execution boundaries, and deployment considerations. |

## Development

Requires **Python 3.13+** and **Bun 1.4.0** for frontend development. The compiled browser bundle is committed to the repository.

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

Start Dikdik against a separate project folder with `dikdik /path/to/your-project`. The application prevents its own installed program folder from becoming a project.

<details>
<summary><strong>Optional tools and runtime components</strong></summary>

| Tool | Needed for |
| --- | --- |
| **Git** | Repository cloning and Git operations. |
| **GitHub CLI / GitLab CLI** | Their respective account and repository integrations. |
| **Docker** | Approved container execution and Classic-mode test isolation. Images are configured separately. |
| **Edge or Chrome** | Local browser captures when using the host browser. |
| **Rust** | Building the optional native repository indexer. |

To build the optional Rust accelerator from a source checkout:

```bash
python scripts/build_native.py
```

Dikdik uses the Python indexer when the accelerator is absent or incompatible. The portable release wheel includes the Python implementation.

</details>

See [CHANGELOG.md](CHANGELOG.md) for release history and [GitHub Issues](https://github.com/vortemain/dikdik/issues) to report a problem or suggest an improvement.

## License

Original Dikdik code is available under the **[MIT License](LICENSE)**.

Bundled dependencies, fonts, and product marks retain their own terms. Their notices and provenance are recorded in [LICENSE-STATUS.md](LICENSE-STATUS.md) and [asset notices](dikdik/web/assets/NOTICES.md).

---

<div align="center">
  <sub>Small name. Serious intelligence.</sub><br />
  <a href="https://github.com/vortemain/dikdik/releases">Get Dikdik</a> · <a href="https://github.com/vortemain/dikdik/issues">Share feedback</a> · <a href="LICENSE">MIT licensed</a>
</div>

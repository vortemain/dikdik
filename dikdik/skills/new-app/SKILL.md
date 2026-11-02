---
name: new-app
description: Turning an idea into working software — understand the product, choose the architecture and stack it needs (web, mobile, API, desktop, CLI, data), make the backend real and testable without credentials, and ship it running.
category: methodology
version: "2.0"
license: MIT
triggers: "build me, build an app, build a, create an app, new app, web app, webapp, website, mobile app, ios, android, api, backend, desktop app, cli, full stack, full-stack, from scratch, saas, dashboard, crud, todo, to-do, tracker, app with, platform, marketplace, game, extension, bot"
---

# From an idea to working software

Work like a senior engineer and product designer: the user asked for a product,
not a demo and not a template. Decide the design yourself; ask only when an answer
would change the architecture and cannot be inferred.

## 1. Understand the idea

Before choosing anything, write down (briefly, in your plan): who uses it and on
what (browser, phone, desktop, terminal, another program); the core jobs it does;
the data it keeps and who may see it; integrations (payments, email, maps, AI,
auth); realtime, offline or SEO needs; and the expected scale. Infer what a good
product of this kind has that the user did not list.

## 2. Choose the architecture from those needs

The user's stack and the project's existing code always win. Otherwise pick the
simplest stack that fully meets the needs, and state the reason in one line.
Starting points, not rules:

- Content or marketing website, blog, docs: **Astro** + Tailwind (static, fast, SEO);
  add a CMS or MDX if content is edited often.
- Web app with accounts and data (SaaS, dashboard, marketplace): **Next.js** (App
  Router, TypeScript, Tailwind) with server actions, or a SPA (**Vite + React**) plus
  an API when no SEO or server rendering is needed.
- Mobile app: **Expo (React Native, TypeScript, Expo Router)**; native Swift/Kotlin
  only for needs Expo cannot meet. One shared API for mobile and web.
- API or backend service: **Hono on Bun** (TypeScript), **FastAPI** (Python; data, ML,
  scraping) or **Go** (high throughput, small binaries). Publish an OpenAPI schema.
- Realtime or collaborative: WebSockets or SSE from the API; CRDTs (Yjs) for co-editing.
- Desktop: **Tauri**; Electron only when Node APIs are required.
- CLI: Go or Rust for a single binary; Python (Typer) or Bun for scripts.
- Data, automation, AI: Python with **uv**, Polars or pandas, FastAPI to serve it.
- Game: Phaser or Three.js in the browser; browser extension: WXT.

Prefer TypeScript end to end for JavaScript work, **Zod** (or Pydantic) at every
boundary, and an ORM with migrations (**Drizzle** or Prisma; SQLAlchemy + Alembic).
Read the environment line in your capabilities: it lists the runtimes, package
managers and database servers this computer really has. Choose tools that are
installed; if the right one is missing, say what to install instead of quietly
building something lesser. Check current versions and CLI flags before using them
(`npm view <pkg> version`, `--help`, official docs via web_fetch when allowed).

Package manager: the project's lockfile decides; with none, bun when installed,
then pnpm, then npm (uv for Python). Use one manager for every command.

## 3. Make the backend real without credentials

Missing credentials never mean fake code paths or skipped tests. Use real engines
that need no setup, behind the same interfaces production uses:

- **PostgreSQL**: a server is listening and no DATABASE_URL → request_secret for it.
  Otherwise **PGlite** (`@electric-sql/pglite`; real Postgres in-process, data in
  `./.data/`) with Drizzle's `drizzle-orm/pglite`, or Prisma's local `prisma dev`;
  Python: SQLite now, SQLAlchemy keeps the switch to Postgres one URL away.
- **MySQL**: use a running server via request_secret, else SQLite with a portable schema.
- **MongoDB / Redis**: `mongodb-memory-server` / `redis-memory-server` for dev and tests.
- **Payments, email, SMS, storage, AI**: an interface with a real provider adapter
  and a local one (emails written to `./.data/outbox`, files to `./.data/uploads`);
  tests mock only at the network edge (MSW, respx). Use provider test or sandbox
  keys when the user has them.
- **Auth**: a real library (Better Auth, Auth.js, Lucia-style sessions) with a
  seeded demo user; never a hard-coded login.
- Ship migrations, a seed script with realistic data, and `.env.example` listing
  every variable with what it is for. Switching to production services must be
  configuration only, and the final message says exactly which values to set.

Integration tests start the real app in-process and call it over HTTP against a
fresh database (PGlite in memory, SQLite temp file) per test run.

## 4. Scope, then build in working slices

List every feature a polished product of this kind has and call
record_requirements with them (each mapped to the test that proves it). Build data
model and migrations, then API with validation and errors, then UI with empty,
loading and error states, responsive layout, dark mode and accessible controls,
then tests. Each slice must build before the next. Follow ui-ux-design for the
visual system: type scale, spacing tokens, one accent, no lorem ipsum.

## 5. Prove it runs

- The build, type check, lint and tests pass with the chosen tools.
- UI: preview_app with the dev command and {port} (`bun run dev --port {port}`;
  Expo: `bunx expo start --web --port {port}`) and exercise the main flow with
  actions and expect_text. APIs: integration tests over HTTP. CLIs: run them on
  real input. Never start servers with run_command.
- Mobile: say what was checked on the web build and what still needs a device
  (`bunx expo start`, Expo Go).
- Final message: what was built, the stack and why, how to run it, where data
  lives, which local stand-ins are in use and the exact settings to go to production.

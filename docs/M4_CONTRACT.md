# M4 contract — profiles, secrets, skills, automation (frozen)

All additions are original DIKDIK code. OpenHands stays a reference only.
Every endpoint is loopback-only and authenticated like `/api/*` today
(`authorized()` in server.py). Responses describe capability truthfully.

## Ownership walls (no cross-edits)

- Rust crate `crates/dikdik-index` + `crates/dikdik-index/benches` only (rust agent)
- Python `dikdik/profiles.py, secrets_store.py, skills.py, automation.py`
  + `tests/test_profiles.py, test_secrets.py, test_skills_automation.py` (python agent)
- Bundler `dikdik/server_routes.py` (shared: python agent adds calls only)
- Bun `dikdik/web/{panel modules as assigned}` + `tests/web/*` (ui agent)

## Endpoints (JSON; body ≤ 64KB)

Profiles (persisted in SQLite via `Store`, new table `profiles`):
- `GET /api/profiles` → `[{id,name,provider,model,endpoint,timeout_seconds,is_default}]`
  (never returns credentials)
- `POST /api/profiles` body `{name,provider,model,endpoint?,timeout_seconds?,credential_secret_ref?}`
  → 201 `{id}`; 400 on duplicate name or invalid field
- `PATCH /api/profiles/{id}` allows `{model,endpoint,timeout_seconds,is_default}`
  (credentials never patchable here; only secret reference above)
- `DELETE /api/profiles/{id}`; deleting the default requires `?confirm_default=1`,
  otherwise 409 `{error}`; other profile then promoted or none default
- Validation: `provider` in {openai, anthropic, azure, local}; `endpoint` http(s)
  loopback or one of `https://api.openai.com/v1`, `https://api.anthropic.com/v1`,
  `https://mojave.cloud/v1`; `timeout_seconds` 1..600 integer

Secrets (encrypted at rest; plaintext never returned, never logged):
- `POST /api/secrets` body `{ref, plaintext}` → 201 `{ref, set: true}`; stores
  ciphertext only; duplicate ref overwrites atomically
- `GET /api/secrets` → `[{ref, set_at}]` (no values, no ciphertext fields)
- `DELETE /api/secrets/{ref}`; unknown → 404
- Backing store: file `<data>/secrets.bin` with per-record AES-GCM-style
  authenticated encryption keyed by DPAPI-unwrapped local key on Windows;
  on non-Windows, key = file with 0600 + warning. Zero external runtime deps:
  implement with stdlib (DPAPI via ctypes; AES alternative: stdlib `hashlib`-
  keyed BLAKE2b stream would be non-standard — therefore use `cryptography`?
  NO. See decision below.)
- DECISION: use stdlib-only authenticated scheme:
  `SHA-256(key||ref||counter)` stream + `hmac(key, header||data)` tag
  (encrypt-then-MAC, HMAC-SHA256), key derived from user key material
  (DPAPI blob on Windows, os.random keyfile 0600 elsewhere). Documented as a
  reviewed original construction, not claimed to be an audited cipher.

Skills (text is data, never permission overrides):
- `GET /api/skills` → `[{id,name,scope:'personal'|'project',enabled,path}]`
- `PUT /api/skills/{id}` body `{enabled: bool}`
- Skills read from `<data>/skills/*.md` (personal) and `<workspace>/.dikdik/skills/*.md`
- Skill content surfaces only as quoted context text in agent messages,
  prefixed by an explicit untrusted-data marker; profile/permission keys cannot
  be changed from skill text (enforced in agent.py by whitelist)

Automation (in-process scheduler; each run = one durable task submission):
- `GET /api/automations` → `[{id,name,schedule,workspace_path,objective,enabled,
  last_state,next_run,failures}]`
- `POST /api/automations` body `{name,schedule,objective,enabled?}`
  - schedule: `{interval_seconds >= 60}` only (no cron in M4)
- `PUT /api/automations/{id}` `{enabled}` only toggle; `DELETE` stops + removes
- Runs log into the task table; `GET /api/automations/{id}/runs` → task summaries
- Retries: max 3 consecutive failures → automation disabled + event `automation_disabled`
- Scheduler starts with engine, persists in automations table, catches up
  missed runs once after restart (idempotency key = automation_id + due time)

Header contract updates (Bun `contracts.ts`):
- Add global types above matching Python responses exactly, optional fields
  absent when not requested. Additional response fields are additive and
  non-breaking; unknown fields must be ignored by UI code.

## Status labels (mandatory)

- Contract-tested only ≠ verified-live. UI labels: "tested: contract" until a
  live run record exists in docs/VERIFICATION.md.

# Sponsored gateway deployment (Dokploy)

Operator runbook for `dikdik.sponsored_gateway` — the invite-only sponsored inference
service described in [NEBIUS_TAVILY.md](NEBIUS_TAVILY.md). This deploys **one gateway
instance** as a second service on the same Dokploy server that hosts the
[dikdik release hub](https://dikdik.vorte.dev). The website itself stays
secret-free: it links only to the public gateway URL. All credentials live in
Dokploy's server-side service environment.

## Deployment steps

1. **Nebius account.** Create a funded Token Factory service account key, note the
   exact model ID of a Nemotron function-tools model from *your* account's catalog,
   and set a spending cap on the account.
2. **Invitations.** Generate one token per user (and optionally a separate set
   for a prioritized second pool) in an administrator terminal:
   ```
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```
   Keep them out of chat, tickets and screenshots; deliver each user theirs
   privately.
3. **Storage directory** (on the Dokploy host) for the usage counters:
   ```
   mkdir -p /srv/dikdik-gateway-data && sudo chown 10001:10001 /srv/dikdik-gateway-data
   ```
4. **Dokploy service.** Type **Docker**, repository `vortemain/dikdik`, build from the
   committed `Dockerfile`, **command**:
   ```
   python -m dikdik.sponsored_gateway
   ```
   Expose port **8788**. Bind-mount the storage directory from step 3 to `/data`.
5. **Environment variables** (service env, never committed):

   | Variable | Value for the container |
   | --- | --- |
   | `NEBIUS_API_KEY` | Your funded service key |
   | `NEBIUS_MODEL` | Exact model ID from your account's catalog |
   | `DIKDIK_GATEWAY_INVITATIONS` | Comma-separated public invitation tokens |
   | `DIKDIK_GATEWAY_JUDGE_INVITATIONS` | Optional comma-separated tokens for the second pool |
   | `DIKDIK_GATEWAY_JUDGE_DAILY` | Requests per token in the second pool per UTC day (default 150) |
   | `DIKDIK_GATEWAY_JUDGE_GLOBAL_DAILY` | Second-pool requests per UTC day (default 600) |
   | `DIKDIK_GATEWAY_USAGE_DB` | `/data/gateway-usage.sqlite3` (persists counters across restarts) |
   | `DIKDIK_GATEWAY_HOST` | `0.0.0.0` inside the container — HTTPS terminates at Dokploy's reverse proxy; leave unset for a plain 127.0.0.1 host deployment |
   | `DIKDIK_GATEWAY_PORT` | `8788` |

   The `NEBIUS_API_KEY` is supplied through the hosting platform's service
   environment and is not committed to the repository or shipped in the image.
6. **Domain.** Attach a subdomain (for example `gw.vorte.dev`) to the service so
   Dokploy terminates HTTPS in front of it. Public users get only
   `https://gw.vorte.dev/v1`; they authenticate with their own invitation as the
   session key (Custom provider, per
   [NEBIUS_TAVILY.md](NEBIUS_TAVILY.md)).
   The second invitation pool draws from its own daily allowance and never
   consumes the public 300/day budget.
7. **Verify** from outside (positive and negative paths):
   ```
   curl -sS https://gw.vorte.dev/healthz
   curl -sS -H "Authorization: Bearer <a-user-invitation>" https://gw.vorte.dev/v1/models
   curl -sS https://gw.vorte.dev/v1/models      # expect 401
   ```
   Do not paste a real invitation into any shared log or ticket.

## Operating notes

- The second invitation pool (`DIKDIK_GATEWAY_JUDGE_*`) uses a separate allowance
  in the same SQLite file; it never consumes the public 300/day budget. Raising
  the two variables increases cost, so do it deliberately and in small steps.
- Nebius failures also consume an allowance (requests, not dollars). Keep a second
  funded connection documented for users whose allowance is exhausted; there is no
  automatic switch to any user's billable personal key.
- Apply connection/rate limits at the reverse proxy and a spending cap at Nebius;
  both are the real budget controls. Restarting the service preserves counters via
  the mounted SQLite file; deleting it resets everyone's day counters.
- The service logs no prompts and persists only invitation hashes and daily counts.
- The gateway is **not** deployed or funded by installing Dikdik, and allowance
  degrees of freedom are finite. Do not describe it as unlimited free access; the keyless
  always-available option remains Ollama or LM Studio. Any future public onboarding
  (account issuance, invitations, multi-replica quota) is out of scope for this pilot.

## Troubleshooting: variables are set but the gateway says they are missing

The process only reads its own environment. If Dokploy knows the variables but the
container does not, Dokploy's *Container Environment* was not applied to the stack:

1. **Dokploy Application (Docker build type):** set the variables in the
   application's **Environment** tab and **Redeploy** — variables are injected when
   the container is created, and redeploys are required after edits.
2. **Dokploy Docker Compose:** a compose stack does **not** automatically receive
   the resource environment; declare and interpolate it on the service:

   ```yaml
   services:
     gateway:
       build: { context: ., dockerfile: Dockerfile }   # repository Dockerfile
       command: python -m dikdik.sponsored_gateway
       environment:
         # Values come from Dokploy's resource environment; keep them out of the file.
         - NEBIUS_API_KEY=${NEBIUS_API_KEY}
         - NEBIUS_MODEL=${NEBIUS_MODEL}
         - DIKDIK_GATEWAY_INVITATIONS=${DIKDIK_GATEWAY_INVITATIONS}
         - DIKDIK_GATEWAY_JUDGE_INVITATIONS=${DIKDIK_GATEWAY_JUDGE_INVITATIONS}
         - DIKDIK_GATEWAY_USAGE_DB=/data/gateway-usage.sqlite3
         - DIKDIK_GATEWAY_HOST=0.0.0.0
         - DIKDIK_GATEWAY_PORT=8788
       volumes:
         - gateway-data:/data
       restart: unless-stopped
   volumes:
     gateway-data:
   ```

3. **Verify inside the container** (names only, never secret contents):
   ```
   docker exec -it <gateway-container> python -m dikdik.sponsored_gateway --check-env
   ```
   It lists each variable as `set (n chars)` or `MISSING` and exits 1 while the
   required three are absent. `docker logs` shows the startup traceback with the
   specific missing variable names.
4. Check for quoting mistakes: values with spaces must be quoted in the Dokploy
   form, and a typo such as `dikdik_gateway_invitations` (wrong case) silently
   reads as missing because every variable name is case-sensitive.

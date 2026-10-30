# Nebius, Tavily and sponsored access

Dikdik connects to NVIDIA Nemotron on Nebius for inference and to Tavily for web
research. Provider keys live in the running process; nothing about the model
requires committing a key to the repository or bundling one in the application.

## Run the integration

1. Install the current build and open **Settings → Models**.
2. Select **Nebius Token Factory**, obtain a Token Factory key from your own account, and connect. The key is held in the running Dikdik process, not written to disk.
3. Load the live model directory. Select an available NVIDIA Nemotron model that supports function tools and choose **Use**. Model identifiers are provider-specific; do not copy an NVIDIA API identifier into Nebius without checking its directory.
4. Enter your Tavily key in **Tavily web research** on the same screen.
5. Start an agent task with external model access, execution and web browsing approved. The search tool does not require the optional browser runtime, but respects the task's web permission and the agent profile's browser permission.
6. Ask Dikdik to research public documentation, implement a small integration in a separate repository, cite the sources, and run that repository's tests.

Example objective:

> Research the current public documentation for this project's missing integration using Tavily. Explain the sources, implement the smallest working integration, and run the project's tests. Do not include private source code or secrets in web queries.

For a server configured through its environment, `NEBIUS_API_KEY` plus `NEBIUS_MODEL` supplies the model when no saved model profile is selected. Set the exact model ID from your account's catalog. Explicit `DIKDIK_MODEL_*` configuration takes priority over this fallback. Tavily is connected through the session form.

## Credentials and switching

- New provider connection keys and Tavily form keys use process memory and are cleared when the engine closes; re-enter a key after restarting. A running request may already hold a credential copy, so disconnect cannot recall an in-flight request.
- Connection names, endpoints and model profiles remain on disk. Choose another profile using the existing model selector when a provider reaches its allowance. Switching is explicit and may bill the selected provider account.
- Replacing or disconnecting a legacy saved connection deletes its encrypted credential record; existing saved keys are not silently deleted during an upgrade. The general encrypted secrets feature still exists for explicitly configured integrations and sandbox secrets.
- Background automations need an available credential in the running service; session expiry or a restart requires reconnecting.
- Search queries and model context go to the selected providers. Web results remain untrusted observations.

## Sponsored default access pilot

`python -m dikdik.sponsored_gateway` runs an optional **invite-only** inference service. It proxies a fixed model to Nebius; it is separate from Dikdik's existing workspace/task gateway. Never bundle a Nebius key or a shared invitation in a downloadable app.

Service configuration (inject actual credentials through the hosting platform; do not commit them):

| Environment variable | Purpose |
| --- | --- |
| `NEBIUS_API_KEY` | Your funded service credential, held in the service environment |
| `NEBIUS_MODEL` | Exact available NVIDIA Nemotron model ID |
| `DIKDIK_GATEWAY_INVITATIONS` | Comma-separated, independently generated per-user invitation tokens, each at least 32 characters |
| `DIKDIK_GATEWAY_USAGE_DB` | Private SQLite usage-counter path; default `gateway-usage.sqlite3` |
| `DIKDIK_GATEWAY_USER_DAILY` | Requests per invitation per UTC day; default 30 |
| `DIKDIK_GATEWAY_GLOBAL_DAILY` | Requests across all invitations per UTC day; default 300 |
| `DIKDIK_GATEWAY_PORT` | Loopback port; default 8788 |
| `DIKDIK_GATEWAY_HOST` | Bind address; default `127.0.0.1`. Set `0.0.0.0` only inside a container fronted by the host's HTTPS reverse proxy |
| `DIKDIK_GATEWAY_JUDGE_INVITATIONS` | Optional comma-separated tokens for a second invitation pool (each at least 32 characters), never overlapping the public list |
| `DIKDIK_GATEWAY_JUDGE_DAILY` | Requests per token in the second pool per UTC day; default 150 |
| `DIKDIK_GATEWAY_JUDGE_GLOBAL_DAILY` | Requests across the second pool per UTC day; default 600 |

Generate invitation tokens with `python -c "import secrets; print(secrets.token_urlsafe(32))"` in an administrator terminal. Deliver each user their own invitation securely. Do not put invitations in repositories, recordings or application binaries.

Run one gateway instance behind an HTTPS reverse proxy on the same host. It binds to `127.0.0.1` by default (settable to `0.0.0.0` inside a container fronted by the host's reverse proxy), authenticates model listing and inference, persists only invitation hashes/counters, limits request bodies to 128 KiB and model output to 2,048 tokens, and blocks upstream redirects. Apply connection/rate limits at the reverse proxy and an account spending cap in Nebius. Request allowances are not a dollar budget; failed upstream calls also consume an allowance. Multiple replicas require shared quota coordination and are outside this pilot.

In Dikdik, add a **Custom provider** pointing to `https://YOUR-GATEWAY/v1`. Enter the user's invitation as the session key, load its single model, then choose **Use**. The user needs no Nebius key; the service owner pays. When its allowance ends, the user can choose another personal connection or a local model. There is no automatic switch to a billable personal key. Tavily continues to use the user's own session key in this pilot.

The second invitation pool (`DIKDIK_GATEWAY_JUDGE_*`) draws from its own daily allowance and never from the public 300/day budget, so prioritized users do not reduce the public allowance. See [GATEWAY_DEPLOY.md](GATEWAY_DEPLOY.md) for the deployment runbook. Deploy the gateway as an operator; installing Dikdik does not deploy or fund any gateway.

This service is not deployed or funded by installing Dikdik. Seamless public onboarding requires an account/invitation-issuance service and a deployed HTTPS gateway; those are not supplied by this pilot. Do not claim unlimited free access. A genuinely keyless local-model option already exists through Ollama or LM Studio, but requires model installation and suitable hardware.
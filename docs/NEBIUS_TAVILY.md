# Nebius, Tavily and the competition demo

## Run the integration

1. Install the current build and open **Settings → Models**.
2. Select **Nebius Token Factory**, obtain a Token Factory key from your own account, and connect. The new key is held in the running Dikdik process, not written to disk.
3. Load the live model directory. Select an available NVIDIA Nemotron model that supports function tools and choose **Use**. Model identifiers are provider-specific; do not copy an NVIDIA API identifier into Nebius without checking its directory.
4. Enter your Tavily key in **Tavily web research** on the same screen.
5. Start an agent task with external model access, execution and web browsing approved. The search tool does not require the optional browser runtime, but respects the task's web permission and the agent profile's browser permission.
6. Ask Dikdik to research public documentation, implement a small integration in a separate demo repository, cite the sources, and run that repository's tests.

Example objective:

> Research the current public documentation for this project's missing integration using Tavily. Explain the sources, implement the smallest working integration, and run the project's tests. Do not include private source code or secrets in web queries.

For a server configured through its environment, `NEBIUS_API_KEY` plus `NEBIUS_MODEL` supplies the model when no saved model profile is selected. Set the exact model ID from your account's catalog. Explicit `DIKDIK_MODEL_*` configuration takes priority over this fallback. Tavily is connected through the session form.

## Credentials and switching

- New provider connection keys and Tavily form keys use process memory. They expire on lookup after eight hours and are cleared when the engine closes. A running request/task may already hold a credential copy; disconnect cannot recall an in-flight request.
- Connection names, endpoints and model profiles remain on disk, so re-enter the key after restarting. Choose another profile using the existing model selector when a provider reaches its allowance. Switching is explicit and may bill the selected provider account.
- Replacing or disconnecting a legacy saved connection deletes its encrypted credential record. Existing saved keys are not silently deleted during an upgrade. The general encrypted secrets feature still exists for explicitly configured integrations and sandbox secrets.
- The app does not deliberately persist these session keys or export them to command environments. Python and the OS cannot guarantee that transient values never appear in swap or crash dumps. Do not promise absolute memory erasure.
- Background automations need an available credential in the running service. Session expiry/restart requires reconnecting.
- Search queries and model context go to the selected providers. Memory-only keys do not imply local inference or zero provider data retention. Web results remain untrusted observations.

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
| `DIKDIK_GATEWAY_JUDGE_INVITATIONS` | Optional comma-separated judge tokens (each at least 32 characters), never overlapping the public list |
| `DIKDIK_GATEWAY_JUDGE_DAILY` | Requests per judge invitation per UTC day; default 150 |
| `DIKDIK_GATEWAY_JUDGE_GLOBAL_DAILY` | Judge requests across all judge invitations per UTC day; default 600 |

Generate invitation tokens with `python -c "import secrets; print(secrets.token_urlsafe(32))"` in an administrator terminal. Deliver each user their own invitation securely. Do not put invitations in repositories, demo videos or application binaries.

Run one gateway instance behind an HTTPS reverse proxy on the same host. It binds to `127.0.0.1` by default (settable to `0.0.0.0` inside a container fronted by the host's reverse proxy), authenticates model listing and inference, persists only invitation hashes/counters, limits request bodies to 128 KiB and model output to 2,048 tokens, and blocks upstream redirects. Apply connection/rate limits at the reverse proxy and an account spending cap in Nebius. Request allowances are not a dollar budget; failed upstream calls also consume an allowance. Multiple replicas require shared quota coordination and are outside this pilot.

In Dikdik, add a **Custom provider** pointing to `https://YOUR-GATEWAY/v1`. Enter the user's invitation as the session key, load its single model, then choose **Use**. The user needs no Nebius key; the service owner pays. When its allowance ends, the user can choose another personal connection or a local model. There is no automatic switch to a billable personal key. Tavily continues to use the user's own session key in this pilot.

Judge access is arranged through optional judge invitation tokens in a separate allowance pool, free of charge during the judging period and never drawing from the public allowance. See [GATEWAY_DEPLOY.md](GATEWAY_DEPLOY.md) for the deployment runbook. Deploy the gateway as an operator; installing Dikdik does not deploy or fund any gateway.

This service is not deployed or funded by installing Dikdik. Seamless public onboarding requires an account/invitation-issuance service and a deployed HTTPS gateway; those are not supplied by this pilot. Do not claim unlimited free access. A genuinely keyless local-model option already exists through Ollama or LM Studio, but requires model installation and suitable hardware.

## Record and submit

Rules checked October 30, 2026: <https://nebiusglobalaihackathon.devpost.com/rules>.

Use the Coding and Agentic Engineering track. Show a genuine Nebius inference call with an NVIDIA open source model and a Tavily runtime call. Provider selection alone or mocked tests are not evidence of live sponsor use.

Suggested video, under three minutes:

1. **0:00–0:20:** Identify the developer problem and the demo repository's failing/missing behavior.
2. **0:20–1:00:** Show Tavily research and actual source URLs. Keep credential forms out of the recording.
3. **1:00–2:15:** Show Nemotron on Nebius implementing the change, followed by real test output and the diff.
4. **2:15–2:50:** Show the working behavior and explain the measured result, permissions and session-only credentials.

Provide the public repository with its open source license, reproducible setup instructions, a working test build or demo URL, the public YouTube video and a clear statement of improvements made during the submission period. Verify third-party licensing using `LICENSE-STATUS.md`. Arrange judge access free of charge without the ordinary public allowance through the judging period; do not publish provider credentials to achieve that.

The listed submission deadline is October 30, 2026 at 10:00 a.m. Pacific / 8:00 p.m. Nairobi. No implementation can guarantee a judging outcome. Real provider calls, final model availability, credentials/credits, hosting, video recording and Devpost submission still require your accounts and live verification.

# Model discovery and credential settings

Updated 2026-10-28. Model directories are fetched on demand; suggested IDs are
labelled as suggestions rather than presented as a complete directory.

## Provider endpoints

Most directories require a provider key. “Public” describes anonymous model
metadata access, not anonymous inference. A directory can contain models that
the account cannot use for chat; known non-chat capabilities disable selection.

| Provider | Directory | Authentication / pagination |
|---|---|---|
| NVIDIA | `https://integrate.api.nvidia.com/v1/models` | Public directory; Bearer key required for inference |
| OpenRouter | `https://openrouter.ai/api/v1/models` | Public; optional Bearer |
| Anthropic | `https://api.anthropic.com/v1/models` | `x-api-key`, version; `after_id` / `has_more` |
| OpenAI | `https://api.openai.com/v1/models` | Bearer |
| Google Gemini | `https://generativelanguage.googleapis.com/v1beta/models` | `x-goog-api-key`; `pageToken` |
| DeepSeek | `https://api.deepseek.com/v1/models` | Bearer |
| xAI | `https://api.x.ai/v1/models` | Bearer |
| Mistral | `https://api.mistral.ai/v1/models` | Bearer |
| Groq | `https://api.groq.com/openai/v1/models` | Bearer |
| Cerebras | `https://api.cerebras.ai/public/v1/models` or keyed `/v1/models` | Public / Bearer |
| Together AI | `https://api.together.xyz/v1/models` | Bearer |
| Fireworks | `https://api.fireworks.ai/v1/accounts/fireworks/models` | Bearer; `pageSize=200`, `pageToken` |
| Moonshot | `https://api.moonshot.ai/v1/models` | Bearer |
| Z.ai | `https://api.z.ai/api/paas/v4/models` | Bearer; compatible schema, authenticated availability unverified |
| Qwen | `https://dashscope-intl.aliyuncs.com/compatible-mode/v1/models` | Bearer; region/account dependent |
| Perplexity Sonar | Legacy endpoint has no model directory | Manual model IDs |
| Perplexity Orchestrator | `https://api.perplexity.ai/orchestrator/v1/models` | Bearer; separate from legacy Sonar |
| Ollama | `http://127.0.0.1:11434/v1/models` | Local installed models, optional key |
| LM Studio | `http://127.0.0.1:1234/v1/models` | Local visible models, optional key |
| Azure OpenAI | Configured resource base + `/models` | `api-key`; requires resource URL |
| Custom compatible providers | Registered base + `/models` | Bearer if configured |

The discovery adapter follows native tokens and compatible `has_more` / `after`
pagination. It deduplicates IDs, normalizes context and prices, and refuses to
label an incomplete list complete. Limits: 100 pages, 20,000 entries, 16 MB of
metadata, 30 seconds total with bounded reads. Provider pagination URLs and HTTP
redirects never receive credentials. Errors contain no response bodies or keys.

Primary references: [Anthropic](https://platform.claude.com/docs/en/api/models/list),
[Gemini](https://ai.google.dev/api/models),
[Fireworks](https://docs.fireworks.ai/api-reference/list-models),
[Cerebras](https://inference-docs.cerebras.ai/api-reference/models/public-models),
[Azure](https://learn.microsoft.com/en-us/rest/api/aifoundry/azureopenai/models),
[Perplexity Orchestrator](https://docs.perplexity.ai/api-reference/gateway-models-get).

## UI behavior

NVIDIA is the default setup provider, with
`nvidia/nemotron-3.5-lightning-30b-a3b` as the default model preset. NVIDIA's
[model page](https://build.nvidia.com/nvidia/nemotron-3.5-lightning-30b-a3b)
lists its free developer endpoint as available (checked 2026-10-28). It requires
an NVIDIA Developer account and API key; NVIDIA limits and availability apply.
The catalog is public, so browsing does not require a key. Connecting through
"Connect & use default" saves the key and selects the preset as the global
default. Existing profiles are not replaced automatically merely by upgrading.
With no configured profiles or DIKDIK model environment, `NVIDIA_API_KEY` or
`NVIDIA_NIM_API_KEY` selects this preset automatically. Explicit DIKDIK model
environment variables still take precedence.

Anonymous live discovery returned 85 models and included the default preset.
That directory does not guarantee all listed models have free chat inference.
Authenticated inference was not run: no actual NVIDIA key was available. The UI
setup flow was tested with synthetic credentials and fixture discovery only.

The chat picker separates saved models from providers, searches full returned
directories, and renders 50 additional rows per click. Settings renders 100 at a
time. Neither silently truncates the catalog. Public catalog entries without a
connection lead to the provider setup page. Selecting a connected model for chat
does not replace an existing default. Status and logos follow the chosen profile.
Model overrides apply to the next messages in the current tab, not persisted per
conversation. No-key local connections work without creating a dummy secret.

API keys settings separates provider credentials from general saved keys. Secret
values remain write-only and masked. Rename, replace, restore, sandbox exposure
and two-step deletion are available inside Manage; adding a duplicate name is
rejected in the UI. Provider Manage opens the matching model provider.

## Verification

Contract tests cover native Anthropic/Gemini/Fireworks paging, all compatible
provider adapters, Azure authentication, Cerebras public access, 2,401 entries,
malformed pagination, HTTP errors, unsafe metadata and credential isolation.
Local HTTP integration tests cover connected discovery, model selection, long
profile names, no-key local selection and preserving defaults.

Anonymous live probes returned OpenRouter 464 models and Cerebras 2 models on
2026-10-28. Authenticated cloud directories were not tested with real keys.
Later OpenRouter requests timed out, confirming the retry/error view; counts are
observations, not guarantees of current availability. Browser checks used a
disposable project and synthetic keys: saved-model search/selection, provider
browsing, light/dark layouts, and key add/rename/replace all exercised without
inference or modifying the user's credentials.

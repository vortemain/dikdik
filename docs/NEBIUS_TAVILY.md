# Nebius and Tavily

Dikdik connects to NVIDIA Nemotron on Nebius for inference and to Tavily for web
research. Nebius works like every other provider: you paste your own key once and Dikdik
saves it encrypted on this computer. Nothing requires committing a key to the repository
or bundling one in the application.

## Run the integration

1. Install the current build and open **Settings → Models**.
2. Select **Nebius Token Factory**, obtain a Token Factory key from your own account, and connect. The key is saved encrypted in Dikdik's data folder (DPAPI on Windows), like every provider key, and stays connected after a restart.
3. Load the live model directory. Select an available NVIDIA Nemotron model that supports function tools and choose **Use**. Model identifiers are provider-specific; do not copy an NVIDIA API identifier into Nebius without checking its directory.
4. Enter your Tavily key in **Tavily web research** on the same screen.
5. Start an agent task with external model access, execution and web browsing approved. The search tool does not require the optional browser runtime, but respects the task's web permission and the agent profile's browser permission.
6. Ask Dikdik to research public documentation, implement a small integration in a separate repository, cite the sources, and run that repository's tests.

Example objective:

> Research the current public documentation for this project's missing integration using Tavily. Explain the sources, implement the smallest working integration, and run the project's tests. Do not include private source code or secrets in web queries.

For a server configured through its environment, `NEBIUS_API_KEY` plus `NEBIUS_MODEL` supplies the model when no saved model profile is selected. Set the exact model ID from your account's catalog. Explicit `DIKDIK_MODEL_*` configuration takes priority over this fallback. Tavily is connected through the session form.

## Credentials and switching

- Provider connection keys (Nebius included) are saved encrypted and survive restarts; **Disconnect key** deletes the saved record. The Tavily form key stays in process memory (up to 8 hours) and is re-entered after a restart. A running request may already hold a credential copy, so disconnect cannot recall an in-flight request.
- Connection names, endpoints and model profiles remain on disk. Choose another profile using the existing model selector when a provider reaches its allowance. Switching is explicit and may bill the selected provider account.
- Replacing a key keeps the previous value sealed so a bad rotation can be undone; disconnecting deletes the record.
- Background automations use the saved key, so they keep working after a restart.
- Search queries and model context go to the selected providers. Web results remain untrusted observations.

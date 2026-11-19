// Browser bundle of Mermaid built against Dikdik's own (patched) KaTeX instead of the
// copy embedded in Mermaid's prebuilt dist. Run: bun run build:mermaid
import mermaid from "mermaid";

(globalThis as unknown as { mermaid: typeof mermaid }).mermaid = mermaid;

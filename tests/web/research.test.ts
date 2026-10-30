import { expect, test } from "bun:test";
import { eventsToChat, observationText } from "../../dikdik/web/contracts";

test("Tavily sources keep their URLs and bounded excerpts in chat and transcript text", () => {
  const observation = { provider: "Tavily", untrusted: true, results: [
    { title: "Public docs", url: "https://example.org/docs", content: "x".repeat(5000) },
    { title: "Unsafe", url: "javascript:alert(1)", content: "omit this source" },
  ] };
  const body = observationText("web_search", observation);
  expect(body).toContain("untrusted web content");
  expect(body).toContain("https://example.org/docs");
  expect(body).not.toContain("javascript:");
  expect(body).not.toContain("omit this source");
  expect(body.length).toBeLessThan(1600);
  const messages = eventsToChat([{ seq: 1, task_id: "task", time: 1, kind: "agent_tool",
    data: { phase: "end", id: "search-1", name: "web_search", observation } }], "Research docs");
  expect(messages[0]?.body).toBe(body);
  expect(messages[0]?.details).toEqual(observation);
});

test("Tavily failures and empty results remain clear", () => {
  expect(observationText("web_search", { status: "failed", error: "Allowance exhausted" })).toBe("Error: Allowance exhausted");
  expect(observationText("web_search", { results: [] })).toBe("No matching sources.");
  expect(observationText("web_search", { results: [null, 12, {}] })).toBe("No matching sources.");
});

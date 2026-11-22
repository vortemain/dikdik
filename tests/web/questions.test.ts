import { expect, test } from "bun:test";
import { eventToMessages, openQuestions, questionAnswer, type TaskEvent } from "../../dikdik/web/contracts";

let seq = 0;
const ev = (kind: string, data: Record<string, unknown> = {}): TaskEvent => ({ seq: ++seq, task_id: "t", time: seq, kind, data });

test("a clarifying question becomes a card with its choices", () => {
  const [card] = eventToMessages(ev("question_requested", { id: "q1", question: "Which citation style?", choices: ["APA 7", "Harvard"] }), "");
  expect(card?.kind).toBe("question_requested");
  expect(card?.body).toBe("Which citation style?");
  expect(card?.details).toEqual({ id: "q1", choices: ["APA 7", "Harvard"], sensitive: false, name: "" });
});

test("a sensitive question is marked so the answer field is masked", () => {
  const [card] = eventToMessages(ev("question_requested", { id: "q2", question: "Database password?", sensitive: true, name: "DB_PASSWORD" }), "");
  expect(card?.details).toEqual({ id: "q2", choices: [], sensitive: true, name: "DB_PASSWORD" });
  expect(card?.title).toBe("The assistant needs a secret value");
});

test("an answered question shows what was sent, with sealed values named, never shown", () => {
  const [answer] = eventToMessages(ev("question_answered", { id: "q3", answer: "Use $ANSWER_SECRET_1", secrets: ["ANSWER_SECRET_1"] }), "");
  expect(answer?.title).toBe("Answered · sealed ANSWER_SECRET_1");
  expect(answer?.body).toBe("Use $ANSWER_SECRET_1");
});

test("open questions close when answered or when the task ends", () => {
  const events = [ev("question_requested", { id: "a" }), ev("question_requested", { id: "b" }), ev("question_answered", { id: "a" })];
  expect(openQuestions(events)).toEqual(["b"]);
  expect(openQuestions([...events, ev("completed")])).toEqual([]);
});

test("the answer body keeps a secret flag only when the user asks for it or the question is sensitive", () => {
  expect(questionAnswer("q", " APA 7 ", false, false)).toEqual({ id: "q", answer: "APA 7" });
  expect(questionAnswer("q", "hunter2-long", true, false)).toEqual({ id: "q", answer: "hunter2-long", secret: true });
  expect(questionAnswer("q", "hunter2-long", false, true)).toEqual({ id: "q", answer: "hunter2-long", secret: true });
  expect(questionAnswer("q", "", false, false)).toEqual({ id: "q", answer: "" });
});

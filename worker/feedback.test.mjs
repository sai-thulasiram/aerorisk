import { test } from "node:test";
import assert from "node:assert/strict";
import { cleanFeedback } from "./feedback.js";

test("accepts a rating alone", () => assert.equal(cleanFeedback({ rating: "up", page: "/" }).value.rating, "up"));
test("accepts a message alone and trims it", () => assert.equal(cleanFeedback({ message: "  hi  " }).value.message, "hi"));
test("rejects empty", () => assert.ok(cleanFeedback({}).error));
test("honeypot is treated as spam", () => assert.ok(cleanFeedback({ message: "x", website: "http://spam" }).spam));
test("bad email rejected", () => assert.ok(cleanFeedback({ message: "x", email: "nope" }).error));
test("unknown kind becomes other; long message capped", () => {
  const r = cleanFeedback({ message: "a".repeat(5000), kind: "<script>" }).value;
  assert.equal(r.kind, "other"); assert.equal(r.message.length, 1500);
});
test("page must be a path", () => assert.equal(cleanFeedback({ rating: "down", page: "https://evil" }).value.page, null));

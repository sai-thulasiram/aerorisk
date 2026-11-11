import { test } from "node:test";
import assert from "node:assert/strict";
import { forwardSend, UMAMI_SEND } from "./umami.js";

const req = (init) => new Request("https://myluckyseat.in/u/api/send", init);

test("forwards tracker posts to Umami Cloud with UA, IP and cache token", async () => {
  const r = await forwardSend(req({ method: "POST", body: '{"type":"event","payload":{}}',
    headers: { "user-agent": "Mozilla/5.0 Test", "cf-connecting-ip": "203.0.113.9", "x-umami-cache": "tok", cookie: "secret=1" } }));
  assert.equal(r.request.url, UMAMI_SEND);
  assert.equal(r.request.headers.get("user-agent"), "Mozilla/5.0 Test");
  assert.equal(r.request.headers.get("x-forwarded-for"), "203.0.113.9");
  assert.equal(r.request.headers.get("x-umami-cache"), "tok");
  assert.equal(r.request.headers.get("cookie"), null);
  assert.equal(await r.request.text(), '{"type":"event","payload":{}}');
});

test("rejects other methods and oversized bodies", async () => {
  assert.equal((await forwardSend(req({ method: "GET" }))).status, 405);
  assert.equal((await forwardSend(req({ method: "POST", body: "x".repeat(20000) }))).status, 413);
});

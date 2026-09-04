import assert from "node:assert/strict";

const base = process.env.BASE_URL ?? "http://localhost:3010";
const [home, batch, item] = await Promise.all([
  fetch(base).then((response) => response.text()),
  fetch(`${base}/batches/1`).then((response) => response.text()),
  fetch(`${base}/batches/1/items/ABC-123`).then((response) => response.text()),
]);

assert.doesNotMatch(home, /class="assistant-float"/);
assert.match(batch, /class="assistant-float"/);
assert.match(batch, /Ask NYRA/);
assert.match(item, /Item ABC-123/);
console.log("floating assistant is present and route-scoped");

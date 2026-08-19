/**
 * The SSE reader, against chunk splits a real network produces and localhost
 * never does.
 *
 * `node --test src/api.test.js` — no test framework, no extra dependency.
 *
 * This exists because the bug it guards is invisible in development. Against
 * `npm run dev` every frame arrives whole, so parsing each chunk as one message
 * passes every manual test you will think to run; deployed, CloudFront and Lambda
 * streaming split the same bytes differently and events vanish. The cases below
 * are the splits that matter: mid-JSON, mid-terminator, and several frames in one
 * read.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { chat } from "./api.js";

/** A fetch whose body arrives in exactly the chunks you specify. */
function respondWith(chunks, { ok = true, status = 200, body = {} } = {}) {
    globalThis.fetch = async () => ({
        ok,
        status,
        json: async () => body,
        body: {
            getReader() {
                const encoder = new TextEncoder();
                let i = 0;
                return {
                    read: async () =>
                        i < chunks.length
                            ? { done: false, value: encoder.encode(chunks[i++]) }
                            : { done: true, value: undefined },
                };
            },
        },
    });
}

async function collect(chunks) {
    respondWith(chunks);
    const out = [];
    for await (const event of chat({ prompt: "hi", token: "t" })) out.push(event);
    return out;
}

const FRAMES = [
    'data: {"type":"session","sessionId":"s"}\n\n',
    'data: {"type":"status","tool":"ask_screening_agent","text":"Screening"}\n\n',
    'data: {"type":"done","text":"answer"}\n\n',
];
const EXPECTED = [
    { type: "session", sessionId: "s" },
    { type: "status", tool: "ask_screening_agent", text: "Screening" },
    { type: "done", text: "answer" },
];

test("one frame per chunk", async () => {
    assert.deepEqual(await collect(FRAMES), EXPECTED);
});

test("every frame in a single chunk", async () => {
    assert.deepEqual(await collect([FRAMES.join("")]), EXPECTED);
});

test("a chunk boundary in the middle of the JSON", async () => {
    const whole = FRAMES.join("");
    const cut = whole.indexOf('"status"') + 3;
    assert.deepEqual(await collect([whole.slice(0, cut), whole.slice(cut)]), EXPECTED);
});

test("a chunk boundary between the two terminating newlines", async () => {
    // The nastiest split: a naive parser sees a complete-looking frame with no
    // terminator, and either drops it or fuses it onto the next one.
    const whole = FRAMES.join("");
    const cut = whole.indexOf("\n\n") + 1;
    assert.deepEqual(await collect([whole.slice(0, cut), whole.slice(cut)]), EXPECTED);
});

test("one byte at a time", async () => {
    assert.deepEqual(await collect([...FRAMES.join("")]), EXPECTED);
});

test("a multi-line note survives, because only the terminator splits frames", async () => {
    const note = "Hi Priya,\n\nWe have a role.";
    const events = await collect([`data: ${JSON.stringify({ type: "done", text: note })}\n\n`]);
    assert.equal(events[0].text, note);
});

test("a keepalive frame is skipped rather than throwing", async () => {
    // Killing a working stream over a heartbeat is a worse failure than ignoring
    // one, so the parser swallows anything that is not JSON.
    const events = await collect([": keepalive\n\n", FRAMES[2]]);
    assert.deepEqual(events, [EXPECTED[2]]);
});

test("a trailing partial frame is dropped, not half-parsed", async () => {
    const events = await collect([FRAMES[0], 'data: {"type":"tok']);
    assert.deepEqual(events, [EXPECTED[0]]);
});

test("an error response throws with the server's message", async () => {
    respondWith([], { ok: false, status: 401, body: { error: "not authorised: expired" } });
    await assert.rejects(
        async () => {
            for await (const _ of chat({ prompt: "hi", token: "t" })) break;
        },
        /not authorised: expired/,
    );
});

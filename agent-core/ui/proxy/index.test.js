/**
 * The event shapes, against real fixtures from both gateways.
 *
 * `npm install && npm test` in this directory.
 *
 * This is the code most likely to be wrong after a topology change and least
 * likely to announce it. API Gateway REST and Lambda function URLs disagree on
 * where the path and the method live, and reading the wrong one yields
 * `undefined` rather than an error — so the handler answers 405 or routes to
 * nothing, and the log says only that a POST was not a POST.
 *
 * `awslambda` is a global the Lambda runtime injects, so it is stubbed before the
 * module is imported. Only `normalise` is exercised here; the invoke path needs
 * AWS and belongs in the local end-to-end run.
 */

import { test } from "node:test";
import assert from "node:assert/strict";

globalThis.awslambda = {
    streamifyResponse: (fn) => fn,
    HttpResponseStream: { from: (stream) => stream },
};

const { normalise } = await import("./index.mjs");

// Trimmed to the fields normalise reads. Shapes per the AWS payload-format docs.
const REST = {
    path: "/api/chat",
    httpMethod: "POST",
    headers: { Authorization: "eyJraWQ", "Content-Type": "application/json" },
    body: '{"prompt":"hi"}',
};

const FUNCTION_URL = {
    rawPath: "/api/chat",
    requestContext: { http: { method: "POST" } },
    headers: { authorization: "eyJraWQ", "content-type": "application/json" },
    body: '{"prompt":"hi"}',
};

test("REST proxy event: path and httpMethod", () => {
    const { path, method } = normalise(REST);
    assert.equal(path, "/api/chat");
    assert.equal(method, "POST");
});

test("function URL event: rawPath and requestContext.http.method", () => {
    const { path, method } = normalise(FUNCTION_URL);
    assert.equal(path, "/api/chat");
    assert.equal(method, "POST");
});

test("both gateways yield an identical normalised request", () => {
    assert.deepEqual(normalise(REST), normalise(FUNCTION_URL));
});

test("headers are lower-cased, because REST preserves the client's case", () => {
    // The bug this prevents: `headers.authorization` is undefined on a REST event
    // where the client sent `Authorization`, so a valid token reads as a missing
    // one and every request is 401.
    assert.equal(normalise(REST).headers.authorization, "eyJraWQ");
    assert.equal(normalise(FUNCTION_URL).headers.authorization, "eyJraWQ");
});

test("an unrecognised event does not throw", () => {
    // It must degrade to a 405/400 answer rather than a 500 with a stack trace,
    // so an unexpected invoke source is diagnosable from the response.
    const { path, method, headers } = normalise({});
    assert.equal(path, "");
    assert.equal(method, "GET");
    assert.deepEqual(headers, {});
});

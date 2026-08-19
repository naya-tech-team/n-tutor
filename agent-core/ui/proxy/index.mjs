/**
 * The chat proxy: the only thing between the browser and the supervisor runtime.
 *
 * It exists because of two hard constraints that between them rule out every
 * simpler design:
 *
 *   1. A browser cannot call InvokeAgentRuntime. It is a SigV4-signed AWS API
 *      call, not a bearer-token endpoint, and AWS service endpoints send no CORS
 *      headers — so even handing the browser temporary credentials from a Cognito
 *      identity pool fails at the preflight. Something server-side must make the
 *      call.
 *
 *   2. That something cannot be Python. Lambda response streaming works on
 *      Node.js managed runtimes and custom runtimes only; the Python managed
 *      runtime has no equivalent of `streamifyResponse`. The rest of this repo is
 *      Python, and this file is Node for exactly one reason: a full pipeline run
 *      is three remote delegations, and buffering it into a single JSON reply
 *      means a browser showing nothing for two minutes.
 *
 * **Zero dependencies.** The JWT is verified with node:crypto (`createPublicKey`
 * reads a JWK directly), Cognito login is a plain fetch — InitiateAuth on a public
 * client takes no signature — and the runtime is invoked over plain HTTPS with the
 * caller's bearer token rather than through the AWS SDK.
 *
 * That last one is not a preference. The supervisor uses CUSTOM_JWT inbound auth,
 * and the SDK only speaks SigV4; AWS documents that an OAuth-configured agent cannot
 * be called with the SDK at all. A happy side effect is that this function needs no
 * AWS credentials, so its execution role grants nothing but logs.
 *
 * The supervisor is the only runtime configured that way — it is the only one a
 * person calls. The four behind it take SigV4 with their own execution roles, so
 * the token this file relays goes exactly one hop and no further.
 *
 * Both routes arrive through CloudFront as /api/*, which is why there is no CORS
 * handling anywhere in this file: the page and the API are the same origin.
 *
 * In front of that is API Gateway REST with `ResponseTransferMode: STREAM`, which
 * only became possible in November 2025 — before that, API Gateway buffered every
 * Lambda response and fixed the integration timeout at 29 seconds, so a
 * two-minute pipeline could not be exposed through it at all.
 *
 * The output format API Gateway requires for streaming is a JSON metadata prelude,
 * then eight null bytes, then the payload. `HttpResponseStream.from()` emits
 * exactly that, which is why nothing below writes a delimiter by hand and why the
 * same handler works unchanged behind a function URL.
 */

import { createPublicKey, verify as verifySignature } from "node:crypto";
import { randomUUID } from "node:crypto";

const REGION = process.env.AWS_REGION;
const AGENT_RUNTIME_ARN = process.env.AGENT_RUNTIME_ARN;
const USER_POOL_ID = process.env.COGNITO_USER_POOL_ID;
const CLIENT_ID = process.env.COGNITO_CLIENT_ID;

const ISSUER = `https://cognito-idp.${REGION}.amazonaws.com/${USER_POOL_ID}`;
const IDP_ENDPOINT = `https://cognito-idp.${REGION}.amazonaws.com/`;

/**
 * The runtime's invocations URL.
 *
 * The ARN must be encoded whole — colons AND slashes — which is why this is
 * `encodeURIComponent` and not a template with the ARN dropped in. A half-encoded
 * ARN gives a 404 that reads like the runtime is down.
 *
 * No `qualifier`: without one the call targets the DEFAULT endpoint, which is the
 * only endpoint this stack has.
 */
const RUNTIME_URL =
    `https://bedrock-agentcore.${REGION}.amazonaws.com` +
    `/runtimes/${encodeURIComponent(AGENT_RUNTIME_ARN || "")}/invocations`;

// --- JWT verification --------------------------------------------------------

/**
 * Cognito's signing keys. Cached for the life of the container.
 *
 * Cached by `kid`, and a miss refetches once. Caching the whole document without
 * that escape hatch means a pool key rotation takes every warm container down
 * until it happens to recycle.
 */
let keyCache = null;

async function signingKey(kid) {
    if (keyCache?.[kid]) return keyCache[kid];

    const response = await fetch(`${ISSUER}/.well-known/jwks.json`);
    if (!response.ok) throw new Error(`jwks fetch failed: ${response.status}`);
    const { keys } = await response.json();

    keyCache = Object.fromEntries(
        keys.map((jwk) => [jwk.kid, createPublicKey({ key: jwk, format: "jwk" })]),
    );
    if (!keyCache[kid]) throw new Error("token signed by an unknown key");
    return keyCache[kid];
}

function b64url(segment) {
    return Buffer.from(segment.replace(/-/g, "+").replace(/_/g, "/"), "base64");
}

/**
 * Verify a Cognito **ID** token and return its claims.
 *
 * ID, not access, and the reason is API Gateway rather than preference. A
 * COGNITO_USER_POOLS authorizer with no `authorizationScopes` configured is the
 * identity-claims path, which is the ID token; the access token is the
 * custom-scopes path and needs a resource server and matching scopes on the
 * method before it is reliably accepted. One token type reaches both the
 * authorizer at the edge and this check, so there is nothing to reconcile.
 *
 * The audience claim differs between the two, which is the trap if you ever
 * switch back: an ID token carries `aud`, an access token carries `client_id`.
 * Checking the wrong one passes silently against a token from the right pool.
 *
 * Checking the signature is the part everyone remembers. The four claim checks
 * after it are the part that matters: a valid signature only proves Cognito
 * issued the token, not that it was issued by *this* pool, for *this* app, as an
 * identity rather than an API credential, and recently.
 */
async function verifyToken(token) {
    const [rawHeader, rawPayload, rawSignature] = (token || "").split(".");
    if (!rawHeader || !rawPayload || !rawSignature) throw new Error("malformed token");

    const header = JSON.parse(b64url(rawHeader).toString());
    if (header.alg !== "RS256") throw new Error(`unexpected alg ${header.alg}`);

    const key = await signingKey(header.kid);
    const signed = Buffer.from(`${rawHeader}.${rawPayload}`);
    if (!verifySignature("RSA-SHA256", signed, key, b64url(rawSignature))) {
        throw new Error("bad signature");
    }

    const claims = JSON.parse(b64url(rawPayload).toString());
    if (claims.iss !== ISSUER) throw new Error("wrong issuer");
    if (claims.token_use !== "id") throw new Error("not an id token");
    if (claims.aud !== CLIENT_ID) throw new Error("wrong client");
    if (claims.exp * 1000 < Date.now()) throw new Error("expired");
    return claims;
}

// --- session ids -------------------------------------------------------------

const SESSION_RE = /^[a-zA-Z0-9][a-zA-Z0-9_-]{32,255}$/;

/**
 * InvokeAgentRuntime rejects a runtimeSessionId shorter than **33 characters**.
 *
 * That is the whole reason this function exists. The obvious thing to send is the
 * requisition id, and `J2001` fails with a ValidationException that names a
 * length constraint you did not know about. The browser keeps one id per chat
 * thread so follow-up questions land in the same memory session; anything that
 * does not satisfy the constraint is replaced rather than passed through, because
 * a rejected id would fail the whole turn.
 */
function safeSessionId(candidate) {
    return SESSION_RE.test(candidate || "") ? candidate : `chat-${randomUUID()}${randomUUID()}`.slice(0, 64);
}

// --- routes ------------------------------------------------------------------

async function login(body) {
    const response = await fetch(IDP_ENDPOINT, {
        method: "POST",
        headers: {
            "Content-Type": "application/x-amz-json-1.1",
            "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth",
        },
        body: JSON.stringify({
            AuthFlow: "USER_PASSWORD_AUTH",
            ClientId: CLIENT_ID,
            AuthParameters: { USERNAME: body.username, PASSWORD: body.password },
        }),
    });

    const payload = await response.json();
    if (!response.ok) {
        // Cognito's own message is the useful one ("Incorrect username or
        // password."), so it is passed through rather than flattened to "login
        // failed" — but only the message, never the raw error type.
        return { status: 401, body: { error: payload.message || "login failed" } };
    }
    if (payload.ChallengeName) {
        return {
            status: 401,
            body: {
                error:
                    `Cognito wants to complete '${payload.ChallengeName}' first. The ` +
                    `terraform user is created with a permanent password to avoid this; ` +
                    `a user made by hand in the console usually needs one password change.`,
            },
        };
    }
    return {
        status: 200,
        body: {
            // IdToken, not AccessToken — see verifyToken. This is the one the
            // API Gateway authorizer in front of /api/chat will accept.
            token: payload.AuthenticationResult.IdToken,
            expiresIn: payload.AuthenticationResult.ExpiresIn,
        },
    };
}

/**
 * Relay the runtime's SSE straight through to the browser, byte for byte.
 *
 * No parsing, no re-framing. The supervisor already yields exactly the event
 * objects the UI renders, and bedrock_agentcore has already serialised them as
 * `data: {...}\n\n`. Anything this function did to them would be a second place
 * for the contract to drift.
 */
async function chat(body, token, responseStream) {
    const sessionId = safeSessionId(body.sessionId);

    // Plain HTTPS with the caller's own token — deliberately NOT the AWS SDK.
    //
    // The supervisor uses CUSTOM_JWT inbound auth, and a JWT-configured agent
    // cannot be invoked with SigV4. The SDK only speaks SigV4, so using it produces:
    //
    //   Authorization method mismatch. The agent is configured for a different
    //   authorization method than what was used in your request.
    //
    // AWS documents this directly: "If you're integrating your agent with OAuth,
    // you can't use the AWS SDK to call InvokeAgentRuntime. Instead, make a HTTPS
    // request." So this is that request — which also means the proxy needs no
    // AWS credentials and no SDK dependency at all.
    //
    // The token forwarded here is the user's, unchanged, all the way from the
    // browser — and it stops here. AgentCore consumes `Authorization` at its edge,
    // so the supervisor's container never sees it and cannot pass it on; every hop
    // beyond this one is SigV4 with an execution role. Nothing in this stack mints
    // a service token, and no container holds a long-lived credential.
    const upstream = await fetch(RUNTIME_URL, {
        method: "POST",
        headers: {
            Authorization: `Bearer ${token}`,
            "Content-Type": "application/json",
            Accept: "text/event-stream",
            "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": sessionId,
        },
        body: JSON.stringify({ prompt: body.prompt || "", job_id: body.jobId || "" }),
    });

    if (!upstream.ok) {
        const detail = await upstream.text().catch(() => "");
        throw new Error(`runtime ${upstream.status}: ${detail.slice(0, 400)}`);
    }

    // Tell the browser which session this turn used, so the next turn can reuse
    // it. It is the first frame rather than a header because the UI reads the
    // body as a stream and never sees the headers of a fetch it did not fail.
    responseStream.write(`data: ${JSON.stringify({ type: "session", sessionId })}\n\n`);

    for await (const chunk of upstream.body) {
        responseStream.write(chunk);
    }
}

// --- handler -----------------------------------------------------------------

function json(responseStream, status, body) {
    const stream = awslambda.HttpResponseStream.from(responseStream, {
        statusCode: status,
        headers: { "Content-Type": "application/json" },
    });
    stream.write(JSON.stringify(body));
    stream.end();
}

/**
 * One request, whichever gateway delivered it.
 *
 * API Gateway REST proxy integrations and Lambda function URLs disagree on the
 * two fields this handler actually reads, and neither errors when you read the
 * other one's — you get `undefined`, then a 405 or a 400 that says nothing about
 * event shapes:
 *
 *              REST (proxy)          function URL (payload v2)
 *   path       event.path            event.rawPath
 *   method     event.httpMethod      event.requestContext.http.method
 *
 * Headers differ too: REST preserves the case the client sent, function URLs
 * lower-case them. Hence the case-insensitive lookup below rather than
 * `headers.authorization`.
 */
export function normalise(event) {
    const headers = Object.fromEntries(
        Object.entries(event.headers || {}).map(([k, v]) => [k.toLowerCase(), v]),
    );
    return {
        path: event.path || event.rawPath || "",
        method: event.httpMethod || event.requestContext?.http?.method || "GET",
        headers,
    };
}

export const handler = awslambda.streamifyResponse(async (event, responseStream) => {
    const { path, method, headers } = normalise(event);

    let body = {};
    try {
        const raw = event.isBase64Encoded
            ? Buffer.from(event.body || "", "base64").toString()
            : event.body || "{}";
        body = JSON.parse(raw || "{}");
    } catch {
        return json(responseStream, 400, { error: "body must be JSON" });
    }

    if (method !== "POST") return json(responseStream, 405, { error: "POST only" });

    if (path.endsWith("/login")) {
        const { status, body: out } = await login(body);
        return json(responseStream, status, out);
    }

    // 400, not 404, and that is not fussiness. CloudFront rewrites 404 across the
    // whole distribution to /index.html for the SPA fallback, so a 404 here would
    // reach the browser as an HTML page and fail in fetch() as a JSON parse error
    // several layers from the actual mistake.
    if (!path.endsWith("/chat")) return json(responseStream, 400, { error: "no such route" });

    // Verified here as well as by the Cognito authorizer at the edge. That is not
    // redundant belt-and-braces: scripts/ui_server.py has no API Gateway in front
    // of it, and a deploy that accidentally drops the authorizer should fail
    // closed rather than serve the supervisor to anyone who finds the URL.
    const bearer = (headers.authorization || "").replace(/^Bearer\s+/i, "");
    try {
        await verifyToken(bearer);
    } catch (error) {
        return json(responseStream, 401, { error: `not authorised: ${error.message}` });
    }

    // Headers are committed here, before the first agent byte arrives. Anything
    // that fails after this point cannot become a 500 — the browser already has
    // a 200 — so it has to arrive as an SSE error frame instead.
    const stream = awslambda.HttpResponseStream.from(responseStream, {
        statusCode: 200,
        headers: {
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
        },
    });

    try {
        await chat(body, bearer, stream);
    } catch (error) {
        stream.write(`data: ${JSON.stringify({ type: "error", text: String(error.message || error) })}\n\n`);
    } finally {
        stream.end();
    }
});

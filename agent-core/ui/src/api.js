/**
 * Talking to the proxy. Two calls, one of which is a stream.
 *
 * Both are same-origin `/api/*`: deployed that is a second CloudFront behaviour
 * pointing at the proxy Lambda, and in `npm run dev` it is Vite's proxy pointing
 * at scripts/ui_server.py. Neither the origin nor any AWS endpoint appears in
 * this file, and no AWS credential ever reaches the browser.
 */

const STORAGE_KEY = "hr-chat-token";

export function storedToken() {
    try {
        const raw = localStorage.getItem(STORAGE_KEY);
        if (!raw) return null;
        const token = JSON.parse(raw);
        // Expiry is checked here as well as server-side, so a stale tab shows the
        // login form instead of a 401 on the user's first message.
        return token.expiresAt > Date.now() ? token : null;
    } catch {
        return null;
    }
}

export function forgetToken() {
    localStorage.removeItem(STORAGE_KEY);
}

export async function login(username, password) {
    const response = await fetch("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || "login failed");

    const token = {
        // A Cognito **ID** token. It goes to the API Gateway Cognito authorizer,
        // which — with no authorizationScopes configured — is the identity-claims
        // path and wants exactly this. See ui/proxy/index.mjs:verifyToken.
        value: body.token,
        // A minute of slack, so a token that expires mid-request is treated as
        // already expired rather than sent and rejected.
        expiresAt: Date.now() + (body.expiresIn - 60) * 1000,
    };
    localStorage.setItem(STORAGE_KEY, JSON.stringify(token));
    return token;
}

/**
 * Send one message and yield the supervisor's events as they arrive.
 *
 * `EventSource` — the obvious API for this — is not usable: it only does GET and
 * cannot set an Authorization header. So this is `fetch` plus a manual reader,
 * which is fine, but it moves the SSE framing into our hands:
 *
 * **Chunk boundaries are not frame boundaries.** A single `read()` can deliver
 * half a frame, or three and a half. Parsing each chunk as if it were one message
 * works perfectly against a fast local server and drops events in production,
 * where the payloads are bigger and the network splits them differently. Hence
 * the buffer, and splitting on the blank line that actually terminates a frame.
 */
export async function* chat({ prompt, jobId, sessionId, token, signal }) {
    const response = await fetch("/api/chat", {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
            // The bare token, with no "Bearer " prefix. API Gateway's Cognito
            // authorizer is documented only as "include the token in the
            // Authorization header", and the bare form is the one that reliably
            // passes; a prefix is a 401 with nothing in it to say why. The proxy
            // strips an optional prefix anyway, so this works in both worlds.
            Authorization: token,
        },
        body: JSON.stringify({ prompt, jobId, sessionId }),
        signal,
    });

    if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.error || `request failed (${response.status})`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });

        let split;
        while ((split = buffer.indexOf("\n\n")) !== -1) {
            const frame = buffer.slice(0, split);
            buffer = buffer.slice(split + 2);

            const line = frame.split("\n").find((l) => l.startsWith("data:"));
            if (!line) continue;
            try {
                yield JSON.parse(line.slice(5).trim());
            } catch {
                // A keepalive or a comment frame. Skipping is correct; throwing
                // here would kill a working stream over a heartbeat.
            }
        }
    }
}

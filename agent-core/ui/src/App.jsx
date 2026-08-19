import { useEffect, useRef, useState } from "react";
import Login from "./Login";
import { chat, forgetToken, storedToken } from "./api";

const SUGGESTIONS = [
    "Find the best candidate for J2001 and draft a note to them",
    "Who is on the bench in Bengaluru?",
    "Fill J2003 — who have we got?",
    "Which open requisitions have no strong candidates?",
];

let nextId = 0;

export default function App() {
    const [token, setToken] = useState(storedToken);
    const [messages, setMessages] = useState([]);
    const [draft, setDraft] = useState("");
    const [busy, setBusy] = useState(false);

    // One id for the whole conversation. It becomes the AgentCore session id, so
    // it is what makes a follow-up question land in the same memory session as
    // the answer it is following up on. "New chat" throwing it away is the point.
    const sessionId = useRef(null);
    const transcript = useRef(null);

    useEffect(() => {
        transcript.current?.scrollTo({ top: transcript.current.scrollHeight, behavior: "smooth" });
    }, [messages]);

    if (!token) return <Login onToken={setToken} />;

    /** Apply one streamed event to the in-flight agent message. */
    function reduce(message, event) {
        switch (event.type) {
            case "status": {
                // Grouped by tool, not appended one row per call.
                //
                // The supervisor really does call the same agent several times in
                // one run — a local qwen2.5:7b asked for J2001 hit Screening four
                // times and Compliance four times. Every one of those is a real
                // delegation and the stream is right to report it, but nine rows
                // reading "Screening Agent — ranking candidates" looks like a
                // rendering bug rather than a model that went round twice. One row
                // per agent with a count says the same thing and reads as intended.
                //
                // Nothing overlaps: the supervisor awaits each delegation, so a new
                // status means the previous one returned.
                const idle = message.statuses.map((s) => ({ ...s, running: false }));
                const seen = idle.find((s) => s.tool === event.tool);
                return {
                    ...message,
                    statuses: seen
                        ? idle.map((s) =>
                              s.tool === event.tool
                                  ? { ...s, count: s.count + 1, running: true }
                                  : s,
                          )
                        : [...idle, { tool: event.tool, text: event.text, count: 1, running: true }],
                };
            }
            case "token":
                return { ...message, text: message.text + event.text };
            case "done":
                // The authoritative answer replaces the streamed buffer rather
                // than appending to it — the buffer also contains whatever the
                // model said to itself between delegations.
                return {
                    ...message,
                    text: event.text || message.text,
                    statuses: message.statuses.map((s) => ({ ...s, running: false })),
                    streaming: false,
                };
            case "error":
                return { ...message, error: event.text, streaming: false };
            default:
                return message;
        }
    }

    async function send(prompt) {
        const text = prompt.trim();
        if (!text || busy) return;

        const agentId = ++nextId;
        setDraft("");
        setBusy(true);
        setMessages((prev) => [
            ...prev,
            { id: ++nextId, role: "user", text },
            { id: agentId, role: "agent", text: "", statuses: [], error: null, streaming: true },
        ]);

        const update = (fn) =>
            setMessages((prev) => prev.map((m) => (m.id === agentId ? fn(m) : m)));

        try {
            const stream = chat({
                prompt: text,
                sessionId: sessionId.current,
                token: token.value,
            });
            for await (const event of stream) {
                if (event.type === "session") {
                    sessionId.current = event.sessionId;
                    continue;
                }
                update((m) => reduce(m, event));
            }
            // A stream that ends without a `done` frame is a dropped connection,
            // not a finished answer. Without this the bubble keeps its typing dots
            // for ever and the UI looks like it is still thinking.
            update((m) =>
                m.streaming
                    ? { ...m, streaming: false, error: m.error || "the connection closed early" }
                    : m,
            );
        } catch (failure) {
            if (failure.message.includes("not authorised")) {
                forgetToken();
                setToken(null);
            }
            update((m) => ({ ...m, error: failure.message, streaming: false }));
        } finally {
            setBusy(false);
        }
    }

    function newChat() {
        sessionId.current = null;
        setMessages([]);
    }

    return (
        <div className="app">
            <header>
                <div>
                    <strong>HR Hiring Desk</strong>
                    <span className="muted"> — supervisor, screening, outreach, compliance</span>
                </div>
                <div className="actions">
                    <button className="ghost" onClick={newChat} disabled={busy || !messages.length}>
                        New chat
                    </button>
                    <button
                        className="ghost"
                        onClick={() => {
                            forgetToken();
                            setToken(null);
                        }}
                    >
                        Sign out
                    </button>
                </div>
            </header>

            <main ref={transcript}>
                {messages.length === 0 && (
                    <div className="empty">
                        <p className="muted">
                            Every answer comes back from a delegation — the supervisor holds no HR
                            data of its own. A full run asks three remote agents in turn, so give it
                            a minute.
                        </p>
                        <div className="chips">
                            {SUGGESTIONS.map((s) => (
                                <button key={s} className="chip" onClick={() => send(s)}>
                                    {s}
                                </button>
                            ))}
                        </div>
                    </div>
                )}

                {messages.map((message) => (
                    <Message key={message.id} message={message} />
                ))}
            </main>

            <form
                className="composer"
                onSubmit={(e) => {
                    e.preventDefault();
                    send(draft);
                }}
            >
                <input
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    placeholder="Ask about a requisition, the bench, or a candidate…"
                    disabled={busy}
                />
                <button type="submit" disabled={busy || !draft.trim()}>
                    {busy ? "Working…" : "Send"}
                </button>
            </form>
        </div>
    );
}

function Message({ message }) {
    if (message.role === "user") {
        return (
            <div className="row user">
                <div className="bubble">{message.text}</div>
            </div>
        );
    }

    return (
        <div className="row agent">
            <div className="bubble">
                {message.statuses.length > 0 && (
                    <ul className="steps">
                        {message.statuses.map((step) => (
                            <li key={step.tool} className={step.running ? "running" : "done"}>
                                <span className="tick">{step.running ? "•" : "✓"}</span>
                                {step.text}
                                {step.count > 1 && <span className="count">×{step.count}</span>}
                            </li>
                        ))}
                    </ul>
                )}

                {message.text && <div className="answer">{message.text}</div>}

                {message.streaming && !message.text && (
                    <div className="dots">
                        <span />
                        <span />
                        <span />
                    </div>
                )}

                {message.error && <p className="error">{message.error}</p>}
            </div>
        </div>
    );
}

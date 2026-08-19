import { useState } from "react";
import { login } from "./api";

/**
 * The Cognito user pool from 03_gateway, reused. There is no sign-up, no
 * password reset and no hosted UI: this is the one machine user terraform
 * creates, and adding a second is `aws cognito-idp admin-create-user`.
 *
 * The password is posted to our own /api/login, not to Cognito from here. Same
 * origin, no AWS SDK in the bundle, and the browser never learns the pool id.
 */
export default function Login({ onToken }) {
    const [username, setUsername] = useState("hr-agent");
    const [password, setPassword] = useState("");
    const [error, setError] = useState(null);
    const [busy, setBusy] = useState(false);

    async function submit(event) {
        event.preventDefault();
        setBusy(true);
        setError(null);
        try {
            onToken(await login(username, password));
        } catch (failure) {
            setError(failure.message);
        } finally {
            setBusy(false);
        }
    }

    return (
        <div className="centre">
            <form className="card login" onSubmit={submit}>
                <h1>HR Hiring Desk</h1>
                <p className="muted">Sign in with the Cognito user terraform created.</p>

                <label>
                    User
                    <input
                        value={username}
                        onChange={(e) => setUsername(e.target.value)}
                        autoComplete="username"
                    />
                </label>

                <label>
                    Password
                    <input
                        type="password"
                        value={password}
                        onChange={(e) => setPassword(e.target.value)}
                        autoComplete="current-password"
                    />
                </label>

                {error && <p className="error">{error}</p>}

                <button type="submit" disabled={busy || !password}>
                    {busy ? "Signing in…" : "Sign in"}
                </button>
            </form>
        </div>
    );
}

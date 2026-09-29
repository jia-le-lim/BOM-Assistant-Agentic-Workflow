"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";

const REMEMBERED_ACCOUNT = "bom.login-account";

export function LoginForm({ nextPath }: { nextPath: string }) {
  const [step, setStep] = useState<"account" | "password">("account");
  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(false);
  const [visible, setVisible] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [help, setHelp] = useState(false);
  const accountInput = useRef<HTMLInputElement>(null);
  const passwordInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      try {
        const saved = localStorage.getItem(REMEMBERED_ACCOUNT);
        if (saved) { setIdentifier(saved); setRemember(true); }
      } catch { /* Sign-in also works with browser storage disabled. */ }
      accountInput.current?.focus();
    });
    return () => cancelAnimationFrame(frame);
  }, []);

  useEffect(() => {
    if (step === "password") passwordInput.current?.focus();
    else accountInput.current?.focus();
  }, [step]);

  function changeAccount() {
    setStep("account"); setPassword(""); setVisible(false); setError(""); setHelp(false);
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setError("");
    if (step === "account") {
      const account = identifier.trim().toLowerCase();
      if (!account || /\s/.test(account) || (account.includes("@") && !/^[^@]+@[^@]+\.[^@]+$/.test(account))) {
        setError("Enter your email address or pilot username.");
        return;
      }
      setIdentifier(account); setStep("password");
      return;
    }
    setBusy(true);
    try {
      const response = await fetch("/api/auth/login", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ identifier, password }),
        signal: AbortSignal.timeout(20_000),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail ?? "Could not sign in. Please try again.");
      try {
        if (remember) localStorage.setItem(REMEMBERED_ACCOUNT, identifier);
        else localStorage.removeItem(REMEMBERED_ACCOUNT);
        // Avoid carrying a previous user's role selection into a new session.
        localStorage.removeItem("bom-session");
      } catch { /* The authenticated session uses an HttpOnly cookie. */ }
      setPassword("");
      window.location.assign(nextPath);
    } catch (cause) {
      setPassword(""); setVisible(false);
      setError(cause instanceof Error && cause.name === "Error" ? cause.message : "Could not connect. Please try again.");
      setBusy(false);
      requestAnimationFrame(() => passwordInput.current?.focus());
    }
  }

  return (
    <main className="auth-page">
      <div className="auth-wrap">
        <section className="auth-card" aria-labelledby="login-title">
          <div className="auth-brand" aria-label="BOM Review">
            <span className="brand-mark" aria-hidden />
          </div>
          <header className="auth-heading">
            <h1 id="login-title">{step === "account" ? "Sign in" : "Welcome back"}</h1>
            <p>{step === "account" ? "Continue to your BOM Review workspace." : "Enter your password to continue."}</p>
          </header>

          <form onSubmit={submit} className="auth-form" aria-busy={busy}>
            {step === "account" ? (
              <div className="auth-field-group">
                <label htmlFor="login-account">Email or username</label>
                <input ref={accountInput} id="login-account" name="username" className="field auth-input"
                  autoComplete="username" autoCapitalize="none" spellCheck={false} required maxLength={254}
                  placeholder="Your email or pilot username" value={identifier}
                  aria-invalid={Boolean(error)} aria-describedby={error ? "login-error" : "account-hint"}
                  onChange={(event) => { setIdentifier(event.target.value); setError(""); }} />
                <p id="account-hint" className="auth-hint">Use the account provided by your project owner.</p>
              </div>
            ) : (
              <>
                <div className="auth-account">
                  <span className="auth-avatar" aria-hidden>{identifier.slice(0, 1).toUpperCase()}</span>
                  <div className="auth-account-text"><span>Signing in as</span><strong>{identifier}</strong></div>
                  <button type="button" className="auth-text-button" onClick={changeAccount} disabled={busy}>Change</button>
                </div>
                {/* Keep the account available to password managers in the second step. */}
                <input type="text" name="username" autoComplete="username" value={identifier} readOnly hidden />
                <div className="auth-field-group">
                  <div className="auth-label-row">
                    <label htmlFor="login-password">Password</label>
                    <button type="button" className="auth-text-button" aria-expanded={help}
                      aria-controls="password-help" onClick={() => setHelp(!help)}>Forgot password?</button>
                  </div>
                  <div className="auth-password">
                    <input ref={passwordInput} id="login-password" name="password" className="field auth-input"
                      type={visible ? "text" : "password"} autoComplete="current-password" required
                      placeholder="Enter your password" value={password} disabled={busy}
                      aria-invalid={Boolean(error)} aria-describedby={error ? "login-error" : undefined}
                      onChange={(event) => { setPassword(event.target.value); setError(""); }} />
                    <button type="button" className="auth-reveal" aria-label={visible ? "Hide password" : "Show password"}
                      aria-pressed={visible} onClick={() => setVisible(!visible)} disabled={busy}>
                      <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden>
                        <path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12Z" /><circle cx="12" cy="12" r="3" />
                        {visible && <path d="m3 3 18 18" />}
                      </svg>
                    </button>
                  </div>
                </div>
                {help && <p id="password-help" className="auth-help" role="status">Contact your project owner to reset your pilot password.</p>}
              </>
            )}

            <label className="auth-remember">
              <input type="checkbox" checked={remember} onChange={(event) => setRemember(event.target.checked)} disabled={busy} />
              Remember my account on this device
            </label>
            {error && <p id="login-error" className="auth-error" role="alert">{error}</p>}
            <button className="btn btn-primary auth-submit" type="submit" disabled={busy}>
              {busy ? <><span className="auth-spinner" aria-hidden />Signing in…</> : step === "account" ? "Continue" : "Sign in"}
              {!busy && step === "account" && <span aria-hidden>→</span>}
            </button>
          </form>

          <div className="auth-divider"><span>BOM Review Assistant</span></div>
          <p className="auth-card-note">The engine calculates. You make the call.</p>
        </section>
        <p className="auth-access">Need access? <span>Contact your project owner.</span></p>
        <footer className="auth-footer">
          <svg width="13" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden>
            <rect x="5" y="10" width="14" height="11" rx="2" /><path d="M8 10V7a4 4 0 0 1 8 0v3" />
          </svg>
          Private pilot workspace
        </footer>
      </div>
    </main>
  );
}

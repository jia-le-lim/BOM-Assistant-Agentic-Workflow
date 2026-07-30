"use client";

import { useCallback, useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { Sidebar } from "./Sidebar";
import { useApi } from "@/lib/api";

/* Rail + top bar + content column.
 *
 * The rail is permanent from lg up and a dismissible drawer below it -- a
 * 240px rail on a 375px screen leaves nothing for the content it navigates. */

interface Health {
  api_version: string;
  database: string;
  llm_provider: string;
  llm_model: string;
}

const TITLES: [prefix: string, title: string][] = [
  ["/chat", "Ask"],
  ["/config", "Rules & criticality"],
  ["/batches", "Review queue"],
];

function Burger() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="1.8" strokeLinecap="round" aria-hidden>
      <path d="M4 7h16M4 12h16M4 17h16" />
    </svg>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const { call } = useApi();
  const [open, setOpen] = useState(false);
  const [health, setHealth] = useState<Health | null>(null);

  const title = TITLES.find(([p]) => path.startsWith(p))?.[1] ?? "Batches";

  useEffect(() => {
    // The chip states which model actually answered. Without it the provider is
    // invisible, and an offline EchoProvider reply looks like a real one.
    call<Health>("health").then(setHealth).catch(() => setHealth(null));
  }, [call]);

  // Escape closes the drawer -- a modal you can only leave by pointing at it is
  // a trap for keyboard users.
  const close = useCallback(() => setOpen(false), []);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") close(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, close]);

  return (
    <div className="shell">
      <aside className="rail" aria-label="Workspace">
        <Sidebar />
      </aside>

      {open && (
        <>
          <div className="drawer-veil" onClick={close} aria-hidden />
          <aside className="drawer" role="dialog" aria-modal="true" aria-label="Workspace">
            <Sidebar onNavigate={close} />
          </aside>
        </>
      )}

      <div className="shell-main">
        <header className="topbar">
          <button type="button" className="btn topbar-burger" onClick={() => setOpen(true)}
                  aria-label="Open navigation" aria-expanded={open}>
            <Burger />
          </button>
          <h1 className="topbar-title">{title}</h1>

          <span className="grow" />

          {health ? (
            <span className="chip" title={`API ${health.api_version} · ${health.database}`}>
              <span className="chip-dot" aria-hidden />
              {health.llm_provider === "echo" ? "Offline stub model" : health.llm_model}
            </span>
          ) : (
            // Silence here would read as "fine". Every page in this console is
            // backend data, so an unreachable backend is the headline.
            <span className="chip" title="No response from /health">
              <span className="chip-dot is-down" aria-hidden />
              Backend unreachable
            </span>
          )}
        </header>

        <main className="shell-content">{children}</main>
      </div>
    </div>
  );
}

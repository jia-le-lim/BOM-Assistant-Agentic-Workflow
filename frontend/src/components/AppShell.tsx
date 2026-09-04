"use client";

import { useCallback, useEffect, useState, useSyncExternalStore } from "react";
import { usePathname } from "next/navigation";
import { Sidebar } from "./Sidebar";
import { FloatingAssistant } from "./FloatingAssistant";
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

function PanelIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="M9 4v16" />
    </svg>
  );
}

const RAIL_KEY = "bom.rail";
const RAIL_EVENT = "bom:rail";

const railIsOpen = () => document.documentElement.dataset.rail !== "collapsed";

function subscribeRail(onChange: () => void) {
  window.addEventListener(RAIL_EVENT, onChange);
  return () => window.removeEventListener(RAIL_EVENT, onChange);
}

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
  // The rail's width is owned by data-rail on <html>, set by the inline script
  // in layout.tsx before first paint. React subscribes to that attribute rather
  // than holding its own copy, so the toggle's aria-expanded is right on the
  // first render after hydration without a cascading setState.
  const railOpen = useSyncExternalStore(subscribeRail, railIsOpen, () => true);
  const [health, setHealth] = useState<Health | null>(null);
  const reviewContext = path.match(/^\/batches\/(\d+)(?:\/items\/([^/]+))?/);

  const title = TITLES.find(([p]) => path.startsWith(p))?.[1] ?? "Batches";

  function toggleRail() {
    const value = railOpen ? "collapsed" : "expanded";
    document.documentElement.dataset.rail = value;
    // Private to this browser and non-critical -- a blocked or full store just
    // means the rail opens at its default width next time.
    try { localStorage.setItem(RAIL_KEY, value); } catch { /* ignore */ }
    window.dispatchEvent(new Event(RAIL_EVENT));
  }

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
          <button type="button" className="btn topbar-rail" onClick={toggleRail}
                  aria-label={railOpen ? "Collapse sidebar" : "Expand sidebar"}
                  aria-expanded={railOpen} title={railOpen ? "Collapse sidebar" : "Expand sidebar"}>
            <PanelIcon />
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
        {reviewContext && (
          <FloatingAssistant
            batchId={Number(reviewContext[1])}
            itemId={reviewContext[2] ? decodeURIComponent(reviewContext[2]) : undefined}
          />
        )}
      </div>
    </div>
  );
}

"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useId, useRef, useState } from "react";
import { useApi } from "@/lib/api";
import { useSession } from "@/lib/session";
import { groupChatSessions, subscribeChatHistoryChanged } from "@/lib/history";
import type { ChatSessionPage, ChatSessionSummary } from "@/lib/types";

/** Group destinations by the work people come here to do. */
const WORKSPACE_LINKS = [
  { href: "/", label: "Workspaces", icon: "batches" as const, hint: "Open your BOM review cycles" },
  { href: "/#new-workspace", label: "New workspace", icon: "new" as const, hint: "Create a workspace for a new BOM review cycle" },
];
const CONFIG_LINKS = [
  { href: "/config", label: "Review settings", icon: "settings" as const, hint: "Thresholds, auto-clear, criticality, and categories" },
  { href: "/config/dormant", label: "Dormant rules", icon: "rules" as const, hint: "Stocking rules for parts with no consumption" },
];
type IconName = "batches" | "upload" | "settings" | "rules" | "ask" | "new" | "search" | "history" | "panel" | "chevron" | "signout" | "close";

function Icon({ name }: { name: IconName }) {
  const props = { width: 16, height: 16, viewBox: "0 0 24 24", fill: "none",
    stroke: "currentColor", strokeWidth: 1.6, strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const, "aria-hidden": true };
  switch (name) {
    case "batches":
      return <svg {...props}><rect x="3" y="3" width="7" height="7" rx="1.5" /><rect x="14" y="3" width="7" height="7" rx="1.5" /><rect x="3" y="14" width="7" height="7" rx="1.5" /><rect x="14" y="14" width="7" height="7" rx="1.5" /></svg>;
    case "upload":
      return <svg {...props}><path d="M12 16V3m-4 4 4-4 4 4M4 14v5a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-5" /></svg>;
    case "settings":
      return <svg {...props}><path d="m9 3-.6 2.3-2 .9-2.1-.6L2 9.4 3.6 11v2L2 14.6l2.3 3.8 2.1-.6 2 .9L9 21h6l.6-2.3 2-.9 2.1.6 2.3-3.8-1.6-1.6v-2L22 9.4l-2.3-3.8-2.1.6-2-.9L15 3Z" /><circle cx="12" cy="12" r="3" /></svg>;
    case "rules":
      return <svg {...props}><path d="M4 6h6m4 0h6M4 12h12m4 0h0M4 18h2m4 0h10" /><circle cx="12" cy="6" r="2" /><circle cx="18" cy="12" r="2" /><circle cx="8" cy="18" r="2" /></svg>;
    case "ask":
      return <svg {...props}><path d="M21 11.5a8.5 8.5 0 0 1-8.5 8.5H5l-3 2V11.5A8.5 8.5 0 0 1 10.5 3h2a8.5 8.5 0 0 1 8.5 8.5Z" /><path d="M7 9h9M7 13h6" /></svg>;
    case "new":
      return <svg {...props}><rect x="3" y="3" width="18" height="18" rx="4" /><path d="M12 7v10M7 12h10" /></svg>;
    case "search":
      return <svg {...props}><circle cx="10.5" cy="10.5" r="6.5" /><path d="m20 20-5-5" /></svg>;
    case "history":
      return <svg {...props}><path d="M3 11a9 9 0 1 1 2.7 7M3 5v6h6m3-4v5l3 2" /></svg>;
    case "panel":
      return <svg {...props}><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M9 4v16" /></svg>;
    case "chevron":
      return <svg {...props}><path d="m8 10 4 4 4-4" /></svg>;
    case "signout":
      return <svg {...props}><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4m7 12 5-5-5-5M21 12H9" /></svg>;
    case "close":
      return <svg {...props}><path d="m6 6 12 12M6 18 18 6" /></svg>;
  }
}

export function Sidebar({ onNavigate, onToggleRail, railOpen = true }: {
  onNavigate?: () => void; onToggleRail?: () => void; railOpen?: boolean;
}) {
  const path = usePathname();
  const router = useRouter();
  const { user, authenticated } = useSession();
  const { call } = useApi();
  const [filter, setFilter] = useState("");
  const [historyOpen, setHistoryOpen] = useState(false);
  const [signingOut, setSigningOut] = useState(false);
  const [logoutError, setLogoutError] = useState("");
  const [history, setHistory] = useState<{
    owner: string; sessions: ChatSessionSummary[]; failed: boolean;
  } | null>(null);
  const instanceId = useId();
  const filterId = `${instanceId}-search`;
  const historyId = `${instanceId}-history`;
  const account = useRef<HTMLDetailsElement>(null);
  const accountSummary = useRef<HTMLElement>(null);


  useEffect(() => {
    let active = true;
    const load = () => {
      call<ChatSessionPage>("chat/sessions")
        .then((page) => { if (active) setHistory({ owner: user, sessions: page.sessions, failed: false }); })
        .catch(() => { if (active) setHistory({ owner: user, sessions: [], failed: true }); });
    };
    load();
    const unsubscribe = subscribeChatHistoryChanged(load);
    return () => { active = false; unsubscribe(); };
  }, [call, user]);

  useEffect(() => {
    const dismiss = (event: PointerEvent) => {
      if (account.current?.open && !account.current.contains(event.target as Node)) account.current.open = false;
    };
    document.addEventListener("pointerdown", dismiss);
    return () => document.removeEventListener("pointerdown", dismiss);
  }, []);

  const loaded = history?.owner === user ? history : null;
  const sessions = loaded?.sessions ?? [];
  const needle = filter.trim().toLowerCase();
  const groups = groupChatSessions(needle ? sessions.filter((session) => session.title.toLowerCase().includes(needle)) : sessions);
  const initials = user.split(/[\s.@_-]+/).filter(Boolean).slice(0, 2).map((part) => part[0]).join("").toUpperCase() || "U";

  function isActive(href: string) {
    if (href === "/") return path === "/" || path.startsWith("/batches/") || path.startsWith("/workspaces/");
    return path === href || path.startsWith(`${href}/`) && href !== "/config";
  }

  function navLink(link: { href: string; label: string; icon: IconName; hint: string }) {
    const active = isActive(link.href);
    return <Link key={link.href} href={link.href} title={link.hint} aria-label={link.label}
      onClick={(event) => {
        if (link.href === "/#new-workspace" && path === "/") {
          event.preventDefault();
          window.dispatchEvent(new Event("bom:new-workspace"));
        }
        onNavigate?.();
      }} aria-current={active ? "page" : undefined}
      className={`side-link${active ? " is-active" : ""}`}>
      <Icon name={link.icon} /><span className="side-label">{link.label}</span>
    </Link>;
  }

  async function signOut() {
    setSigningOut(true); setLogoutError("");
    try {
      const response = await fetch("/api/auth/logout", { method: "POST" });
      if (!response.ok) throw new Error("Sign out failed");
      try { localStorage.removeItem("bom-session"); } catch { /* optional */ }
      window.location.assign("/login");
    } catch {
      setLogoutError("Could not sign out. Please try again."); setSigningOut(false);
      accountSummary.current?.focus();
    }
  }

  return (
    <div className="side">
      <div className="side-head">
        <Link href="/" className="side-brand" onClick={onNavigate} aria-label="BOM Review home" title="BOM Review home">
          <span className="side-brand-mark" aria-hidden>B</span>
          <span className="side-brand-copy"><strong>BOM Review</strong><span>Operations workspace</span></span>
        </Link>
      </div>

      <div className="side-body">
        <nav className="side-nav" aria-label="Sections">
          <section className="side-section" aria-labelledby={`${instanceId}-workspace`}>
            <h2 id={`${instanceId}-workspace`} className="side-section-title">Workspace</h2>
            {WORKSPACE_LINKS.map(navLink)}
          </section>

          <section className="side-section" aria-labelledby={`${instanceId}-assistant`}>
            <h2 id={`${instanceId}-assistant`} className="side-section-title">Assistant</h2>
            {navLink({ href: "/chat", label: "Ask NYRA", icon: "ask", hint: "Ask about a part, explain a decision, or draft a proposal" })}
            <button type="button" className="side-link" aria-label="New conversation" title="Start a new conversation"
              onClick={() => { router.push(`/chat?new=${Date.now()}`); onNavigate?.(); }}>
              <Icon name="new" /><span className="side-label">New conversation</span>
            </button>
            <button type="button" className="side-link side-history-toggle" aria-label="Conversations" title="Search saved conversations"
              aria-expanded={historyOpen && railOpen} aria-controls={historyId}
              onClick={() => {
                if (!railOpen && onToggleRail) { onToggleRail(); setHistoryOpen(true); }
                else setHistoryOpen((current) => !current);
              }}>
              <Icon name="history" /><span className="side-label">Conversations</span>
              {loaded && !loaded.failed && <span className="side-count" aria-label={`${sessions.length} saved conversations`}>{sessions.length}</span>}
              <span className={`side-chevron${historyOpen ? " is-open" : ""}`}><Icon name="chevron" /></span>
            </button>
            <div id={historyId} hidden={!historyOpen} className="side-history">
              <div className="side-search">
                <Icon name="search" />
                <label htmlFor={filterId} className="sr-only">Search conversations</label>
                <input id={filterId} className="side-search-input" value={filter} placeholder="Search conversations"
                  onChange={(e) => setFilter(e.target.value)} />
              </div>
              <div className="side-history-list">
                {groups.length === 0 ? <p className="side-empty">
                  {!loaded ? "Loading conversations…" : loaded.failed ? "Could not load conversations."
                    : sessions.length === 0 ? "Your saved chats will appear here." : "No matching conversations."}
                </p> : groups.map((group) => (
                  <section key={group.label}>
                    <h3 className="side-group">{group.label}</h3>
                    {group.items.map((session) => (
                      <button key={session.session_id} type="button" className="side-ask" title={session.title}
                        onClick={() => { router.push(`/chat?session=${encodeURIComponent(session.session_id)}`); onNavigate?.(); }}>
                        <span className="side-ask-title">{session.title}</span>
                        <span className="side-ask-meta">{session.turn_count} {session.turn_count === 1 ? "turn" : "turns"}</span>
                      </button>
                    ))}
                  </section>
                ))}
              </div>
            </div>
          </section>

          <section className="side-section" aria-labelledby={`${instanceId}-configuration`}>
            <h2 id={`${instanceId}-configuration`} className="side-section-title">Configuration</h2>
            {CONFIG_LINKS.map(navLink)}
          </section>
        </nav>
      </div>

      <div className="side-foot">
        <button type="button" className="side-link side-collapse"
          aria-label={onToggleRail ? railOpen ? "Collapse sidebar" : "Expand sidebar" : "Close navigation"}
          aria-expanded={onToggleRail ? railOpen : undefined}
          title={onToggleRail ? railOpen ? "Collapse sidebar" : "Expand sidebar" : "Close navigation"}
          onClick={onToggleRail ?? onNavigate}>
          <Icon name={onToggleRail ? "panel" : "close"} />
          <span className="side-label">{onToggleRail ? railOpen ? "Collapse sidebar" : "Expand sidebar" : "Close navigation"}</span>
        </button>
        <details className="side-account" ref={account} onKeyDown={(event) => {
          if (event.key === "Escape" && account.current?.open) {
            event.preventDefault(); event.stopPropagation(); account.current.open = false; accountSummary.current?.focus();
          }
        }} onBlur={(event) => {
          if (event.relatedTarget && !event.currentTarget.contains(event.relatedTarget as Node) && account.current) account.current.open = false;
        }}>
          <summary className="side-user" ref={accountSummary} aria-label={`Account for ${user}`} title={`Account for ${user}`}>
            <span className="side-avatar" aria-hidden>{initials}</span>
            <span className="side-identity">
              <span className="side-username" title={user}>{user}</span>
              <span className="side-access">Administrator</span>
            </span>
            <span className="side-chevron"><Icon name="chevron" /></span>
          </summary>
          <div className="side-account-menu">
            <p className="side-account-label">Signed in as <strong>{user}</strong></p>
            <Link href="/config" className="side-link" onClick={() => { if (account.current) account.current.open = false; onNavigate?.(); }}>
              <Icon name="settings" /><span>Workspace settings</span>
            </Link>
            {authenticated && <button type="button" className="side-link side-signout" disabled={signingOut} onClick={signOut}>
              <Icon name="signout" /><span>{signingOut ? "Signing out…" : "Sign out"}</span>
            </button>}
            {logoutError && <p role="alert" className="side-note">{logoutError}</p>}
          </div>
        </details>
      </div>
    </div>
  );
}

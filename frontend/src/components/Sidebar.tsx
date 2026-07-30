"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useId, useState } from "react";
import { ROLES, useSession } from "@/lib/session";
import { clearAsks, groupAsks, useAsks } from "@/lib/history";
import type { Role } from "@/lib/types";

/* Workspace rail: identity, destinations, and what you recently asked.
 * Every entry goes somewhere real -- no decorative navigation. */

const LINKS = [
  { href: "/", label: "Batches", icon: "batches" as const,
    hint: "Uploaded extracts and their review queues" },
  { href: "/config", label: "Rules & criticality", icon: "rules" as const,
    hint: "Thresholds the engine scores with" },
  { href: "/chat", label: "Ask", icon: "ask" as const,
    hint: "Explain a decision, stage a proposal" },
];

type IconName = (typeof LINKS)[number]["icon"] | "new" | "search" | "signout";

function Icon({ name }: { name: IconName }) {
  const p = {
    width: 16, height: 16, viewBox: "0 0 24 24", fill: "none",
    stroke: "currentColor", strokeWidth: 1.7,
    strokeLinecap: "round" as const, strokeLinejoin: "round" as const,
    "aria-hidden": true,
  };
  switch (name) {
    case "batches":
      return <svg {...p}><path d="M3 7.5 12 3l9 4.5-9 4.5-9-4.5Z" /><path d="m3 12 9 4.5L21 12" /><path d="m3 16.5 9 4.5 9-4.5" /></svg>;
    case "rules":
      return <svg {...p}><path d="M4 6h16M4 12h11M4 18h16" /><circle cx="18" cy="12" r="2.2" /></svg>;
    case "ask":
      return <svg {...p}><path d="M21 12a8 8 0 0 1-8 8H5l-2 2V12a8 8 0 0 1 8-8h2a8 8 0 0 1 8 8Z" /></svg>;
    case "new":
      return <svg {...p}><path d="M12 5v14M5 12h14" /></svg>;
    case "search":
      return <svg {...p}><circle cx="11" cy="11" r="7" /><path d="m20 20-3.6-3.6" /></svg>;
    case "signout":
      return <svg {...p}><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" /><path d="m16 17 5-5-5-5M21 12H9" /></svg>;
  }
}

export function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  const path = usePathname();
  const router = useRouter();
  const { user, role, setIdentity } = useSession();
  const asks = useAsks();
  const [filter, setFilter] = useState("");
  // The rail and the drawer both mount a Sidebar below lg, so a literal id
  // would appear twice and every `for=` would point at whichever came first.
  const filterId = useId();

  const needle = filter.trim().toLowerCase();
  const groups = groupAsks(
    needle ? asks.filter((a) => a.q.toLowerCase().includes(needle)) : asks);

  function openAsk(q: string) {
    router.push(`/chat?q=${encodeURIComponent(q)}`);
    onNavigate?.();
  }

  return (
    <div className="side">
      <div className="side-head">
        <Link href="/" className="brand" onClick={onNavigate}>
          <span className="brand-mark" aria-hidden />
          <span className="brand-name">BOM Review</span>
        </Link>
      </div>

      <div className="side-top">
        <Link href="/chat?new=1" className="btn btn-primary side-new" onClick={onNavigate}>
          <Icon name="new" /> New chat
        </Link>

        <div className="side-search">
          <span aria-hidden style={{ color: "var(--text-muted)" }}><Icon name="search" /></span>
          <label htmlFor={filterId} className="sr-only">Search recent asks</label>
          <input id={filterId} className="side-search-input" value={filter}
                 placeholder="Search recent asks"
                 onChange={(e) => setFilter(e.target.value)} />
        </div>
      </div>

      <nav className="side-nav" aria-label="Sections">
        {LINKS.map((l) => {
          const active = l.href === "/" ? path === "/" : path.startsWith(l.href);
          return (
            <Link key={l.href} href={l.href} title={l.hint} onClick={onNavigate}
                  aria-current={active ? "page" : undefined}
                  className={`side-link${active ? " is-active" : ""}`}>
              <Icon name={l.icon} /> {l.label}
            </Link>
          );
        })}
      </nav>

      <div className="side-history">
        {groups.length === 0 ? (
          <p className="side-empty">
            {asks.length === 0
              ? "Questions you ask appear here."
              : "No recent ask matches that."}
          </p>
        ) : (
          groups.map((g) => (
            <section key={g.label}>
              <h2 className="side-group">{g.label}</h2>
              {g.items.map((a) => (
                <button key={a.ts} type="button" className="side-ask"
                        title={a.q} onClick={() => openAsk(a.q)}>
                  {a.q}
                </button>
              ))}
            </section>
          ))
        )}
      </div>

      <div className="side-foot">
        {asks.length > 0 && (
          <button type="button" className="side-clear" onClick={clearAsks}>
            Clear recent asks
          </button>
        )}
        <div className="side-user">
          <span className="side-avatar" aria-hidden>{user.slice(0, 1).toUpperCase()}</span>
          <label className="min-w-0 flex-1">
            <span className="sr-only">Acting as</span>
            <select className="side-role" value={role}
                    onChange={(e) => {
                      const r = e.target.value as Role;
                      setIdentity(ROLES.find((x) => x.role === r)!.user, r);
                    }}>
              {ROLES.map((r) => (
                <option key={r.role} value={r.role}>{r.user} — {r.role}</option>
              ))}
            </select>
          </label>
          <span aria-hidden style={{ color: "var(--text-muted)" }}><Icon name="signout" /></span>
        </div>
        <p className="side-note">
          Identity is a header stub for demonstrating RBAC, not authentication.
        </p>
      </div>
    </div>
  );
}

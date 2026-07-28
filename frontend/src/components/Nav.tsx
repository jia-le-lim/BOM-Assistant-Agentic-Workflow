"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ROLES, useSession } from "@/lib/session";
import type { Role } from "@/lib/types";

const LINKS = [
  { href: "/", label: "Batches" },
  { href: "/config", label: "Rules & criticality" },
  { href: "/chat", label: "Ask" },
];

export function Nav() {
  const path = usePathname();
  const { user, role, setIdentity } = useSession();

  return (
    <header style={{ background: "var(--surface-1)", borderBottom: "1px solid var(--border)" }}>
      <div className="max-w-[1400px] mx-auto px-5 py-3 flex flex-wrap items-center gap-x-6 gap-y-3">
        <Link href="/" className="font-semibold text-sm whitespace-nowrap">
          BOM Review Assistant
        </Link>
        <nav className="flex gap-4 text-sm flex-1">
          {LINKS.map((l) => {
            const active = l.href === "/" ? path === "/" : path.startsWith(l.href);
            return (
              <Link key={l.href} href={l.href}
                    style={{
                      color: active ? "var(--text-primary)" : "var(--text-secondary)",
                      borderBottom: active ? "2px solid var(--seq)" : "2px solid transparent",
                      paddingBottom: 2,
                    }}>
                {l.label}
              </Link>
            );
          })}
        </nav>

        <label className="flex items-center gap-2 text-xs">
          <span style={{ color: "var(--text-muted)" }}>Acting as</span>
          <select
            className="field"
            value={role}
            onChange={(e) => {
              const r = e.target.value as Role;
              setIdentity(ROLES.find((x) => x.role === r)!.user, r);
            }}
          >
            {ROLES.map((r) => (
              <option key={r.role} value={r.role}>{r.user} — {r.role}</option>
            ))}
          </select>
        </label>
      </div>
      <div className="max-w-[1400px] mx-auto px-5 pb-2 text-[11px]"
           style={{ color: "var(--text-muted)" }}>
        Identity is a header stub for demonstrating RBAC ({user}/{role}) — not authentication.
        Production replaces it with Intel SSO / Entra ID.
      </div>
    </header>
  );
}

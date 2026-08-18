"use client";

/**
 * Identity stub mirroring the backend's X-User / X-Role headers.
 *
 * This exists so the RBAC and two-person approval rules are *visible* -- switch
 * role and watch actions enable/disable. It is NOT security: the backend trusts
 * these headers today. Real deployment replaces both ends with Entra ID.
 */

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import type { Role } from "./types";

export const ROLES: { role: Role; user: string; blurb: string }[] = [
  { role: "engineer", user: "alice", blurb: "Review, accept, override, reject" },
  { role: "senior", user: "boss", blurb: "Approve overrides & high-risk items" },
  { role: "admin", user: "root", blurb: "Edit thresholds & confirm criticality" },
  { role: "planner", user: "pat", blurb: "View cost detail, export" },
  { role: "auditor", user: "aud", blurb: "Read-only history & audit" },
  { role: "viewer", user: "eve", blurb: "Read-only" },
];

interface Session { user: string; role: Role; setIdentity: (u: string, r: Role) => void }

const Ctx = createContext<Session>({ user: "alice", role: "engineer", setIdentity: () => {} });

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState("alice");
  const [role, setRole] = useState<Role>("engineer");

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      const s = localStorage.getItem("bom-session");
      if (s) {
        try {
          const p = JSON.parse(s);
          if (p.user && p.role) { setUser(p.user); setRole(p.role); }
        } catch { /* ignore malformed */ }
      }
    });
    return () => window.cancelAnimationFrame(frame);
  }, []);

  const setIdentity = useCallback((u: string, r: Role) => {
    setUser(u); setRole(r);
    localStorage.setItem("bom-session", JSON.stringify({ user: u, role: r }));
  }, []);

  return <Ctx.Provider value={{ user, role, setIdentity }}>{children}</Ctx.Provider>;
}

export const useSession = () => useContext(Ctx);

/** Permission mirror of backend/app/security.py -- keep in sync. */
export const can = {
  review: (r: Role) => ["engineer", "senior", "admin"].includes(r),
  approve: (r: Role) => ["senior", "admin"].includes(r),
  upload: (r: Role) => ["engineer", "senior", "admin", "it"].includes(r),
  configWrite: (r: Role) => r === "admin",
  export: (r: Role) => ["engineer", "senior", "planner", "admin", "it"].includes(r),
};

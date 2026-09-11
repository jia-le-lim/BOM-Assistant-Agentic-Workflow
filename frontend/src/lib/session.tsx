"use client";

/**
 * Pilot gateway identity plus the existing role selector.
 *
 * The gateway supplies the authenticated username and overwrites X-User before
 * requests reach the backend. Roles remain selectable for pilot testing.
 * Direct localhost development retains demo identities; production SSO is separate.
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

interface Session { user: string; role: Role; authenticated: boolean; setIdentity: (u: string, r: Role) => void }

const Ctx = createContext<Session>({ user: "alice", role: "engineer", authenticated: false, setIdentity: () => {} });

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState("alice");
  const [role, setRole] = useState<Role>("engineer");
  const [pilotUser, setPilotUser] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    const frame = window.requestAnimationFrame(() => {
      const s = localStorage.getItem("bom-session");
      if (s) {
        try {
          const p = JSON.parse(s);
          if (p.user && p.role) { setUser(p.user); setRole(p.role); }
        } catch { /* ignore malformed */ }
      }
      void fetch("/api/pilot-session", { cache: "no-store", signal: controller.signal })
        .then((response) => response.ok ? response.json() : null)
        .then((session) => {
          if (!controller.signal.aborted && typeof session?.user === "string" && session.user) {
            setPilotUser(session.user);
            setUser(session.user);
          }
        }).catch(() => { /* Local development can use the demo identities. */ });
    });
    return () => { window.cancelAnimationFrame(frame); controller.abort(); };
  }, []);

  const setIdentity = useCallback((u: string, r: Role) => {
    const identity = pilotUser ?? u;
    setUser(identity); setRole(r);
    localStorage.setItem("bom-session", JSON.stringify({ user: identity, role: r }));
  }, [pilotUser]);

  return <Ctx.Provider value={{ user, role, authenticated: pilotUser !== null, setIdentity }}>{children}</Ctx.Provider>;
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

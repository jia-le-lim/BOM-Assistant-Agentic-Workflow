"use client";

/**
 * One workspace identity with Administrator access.
 *
 * The server verifies the HttpOnly session and derives the authenticated username.
 * The authenticated session also supplies the fixed admin role.
 * Direct localhost development uses the pilot identity; production SSO is separate.
 */

import { createContext, useContext, useEffect, useState } from "react";
import type { Role } from "./types";

interface Session { user: string; role: Role; authenticated: boolean }

const Ctx = createContext<Session>({ user: "pilot", role: "admin", authenticated: false });

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [session, setSession] = useState<Session>({ user: "pilot", role: "admin", authenticated: false });

  useEffect(() => {
    const controller = new AbortController();
    const frame = window.requestAnimationFrame(() => {
      try {
        // Discard role selections saved by older versions of the pilot.
        localStorage.removeItem("bom-session");
      } catch { /* Ignore malformed or unavailable browser storage. */ }
      void fetch("/api/pilot-session", { cache: "no-store", signal: controller.signal })
        .then((response) => response.json())
        .then((session) => {
          if (!controller.signal.aborted && session?.required && !session.user && window.location.pathname !== "/login") {
            window.location.replace(`/login?next=${encodeURIComponent(window.location.pathname + window.location.search)}`);
            return;
          }
          if (!controller.signal.aborted && typeof session?.user === "string" && session.user) {
            setSession({ user: session.user, role: "admin", authenticated: Boolean(session.required) });
          }
        }).catch(() => { /* Protected APIs still require a valid server session. */ });
    });
    return () => { window.cancelAnimationFrame(frame); controller.abort(); };
  }, []);

  return <Ctx.Provider value={session}>{children}</Ctx.Provider>;
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

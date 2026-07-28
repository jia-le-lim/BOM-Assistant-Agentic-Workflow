"use client";

import { useCallback } from "react";
import { useSession } from "./session";

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

/** All calls route through /api/backend/* (the BFF proxy). */
export function useApi() {
  const { user, role } = useSession();

  const call = useCallback(
    async <T,>(path: string, init: RequestInit = {}): Promise<T> => {
      const res = await fetch(`/api/backend/${path.replace(/^\//, "")}`, {
        ...init,
        headers: { ...(init.headers ?? {}), "X-User": user, "X-Role": role },
      });
      if (!res.ok) {
        let detail = `${res.status} ${res.statusText}`;
        try {
          const j = await res.json();
          if (typeof j.detail === "string") detail = j.detail;
          else if (Array.isArray(j.detail)) detail = j.detail.map((d: {msg?: string}) => d.msg).join("; ");
        } catch { /* non-JSON error body */ }
        throw new ApiError(res.status, detail);
      }
      return res.status === 204 ? (null as T) : res.json();
    },
    [user, role],
  );

  const raw = useCallback(
    async (path: string) =>
      fetch(`/api/backend/${path.replace(/^\//, "")}`, {
        headers: { "X-User": user, "X-Role": role },
      }),
    [user, role],
  );

  return { call, raw, user, role };
}

export const fmtUsd = (n: number) =>
  n >= 1000 ? `$${Math.round(n).toLocaleString()}` : `$${n.toFixed(0)}`;

export const fmtCompact = (n: number) =>
  n >= 1_000_000 ? `$${(n / 1_000_000).toFixed(1)}M`
  : n >= 1_000 ? `$${Math.round(n / 1000)}k`
  : `$${n.toFixed(0)}`;

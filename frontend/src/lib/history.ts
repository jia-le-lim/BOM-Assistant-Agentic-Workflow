"use client";

/* Recent questions, in the browser only.
 *
 * conversation_turn on the backend is the real, audited record -- but no route
 * lists it back per user, so this is a local convenience list, not a system
 * record. The UI labels it as such.
 *
 * Exposed as an external store rather than an effect + setState: localStorage
 * IS external state, and useSyncExternalStore keeps every mounted rail in sync
 * (including across tabs, via the `storage` event) without a render pass that
 * exists only to copy data into React. */

import { useSyncExternalStore } from "react";

const KEY = "bom-recent-asks";
const CAP = 40;

export interface Ask { q: string; ts: number }

const EMPTY: Ask[] = [];                 // stable identity: a new [] each call
let raw: string | null = null;           // would re-render forever
let cache: Ask[] = EMPTY;

const listeners = new Set<() => void>();
function emit() { for (const l of listeners) l(); }

function parse(s: string | null): Ask[] {
  if (!s) return EMPTY;
  try {
    const v = JSON.parse(s);
    return Array.isArray(v)
      ? v.filter((a) => typeof a?.q === "string" && typeof a?.ts === "number")
      : EMPTY;
  } catch {
    return EMPTY;                        // malformed -> empty, never throw on render
  }
}

function snapshot(): Ask[] {
  const now = localStorage.getItem(KEY);
  if (now !== raw) { raw = now; cache = parse(now); }
  return cache;
}

function subscribe(cb: () => void): () => void {
  listeners.add(cb);
  window.addEventListener("storage", cb);
  return () => { listeners.delete(cb); window.removeEventListener("storage", cb); };
}

/** Server render has no localStorage; it gets the empty list. */
export function useAsks(): Ask[] {
  return useSyncExternalStore(subscribe, snapshot, () => EMPTY);
}

export function rememberAsk(q: string): void {
  const text = q.trim();
  if (!text) return;
  const next = [{ q: text, ts: Date.now() },
                ...snapshot().filter((a) => a.q !== text)].slice(0, CAP);
  localStorage.setItem(KEY, JSON.stringify(next));
  emit();
}

export function clearAsks(): void {
  localStorage.removeItem(KEY);
  emit();
}

/** Today / Yesterday / Previous 7 days / Older, in that order, empties dropped. */
export function groupAsks(asks: Ask[]): { label: string; items: Ask[] }[] {
  const startOfToday = new Date();
  startOfToday.setHours(0, 0, 0, 0);
  const day = 86_400_000;
  const t = startOfToday.getTime();

  const buckets: Record<string, Ask[]> = {
    Today: [], Yesterday: [], "Previous 7 days": [], Older: [],
  };
  for (const a of asks) {
    const key = a.ts >= t ? "Today"
      : a.ts >= t - day ? "Yesterday"
      : a.ts >= t - 7 * day ? "Previous 7 days"
      : "Older";
    buckets[key].push(a);
  }
  return Object.entries(buckets)
    .filter(([, items]) => items.length > 0)
    .map(([label, items]) => ({ label, items }));
}

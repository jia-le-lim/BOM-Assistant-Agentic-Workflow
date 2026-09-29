"use client";

import { useSyncExternalStore } from "react";

type ReviewView = "rows" | "split";
const KEY = "bom.review-view";
const EVENT = "bom:review-view";
let fallback: ReviewView = "rows";

function snapshot(): ReviewView {
  try { return localStorage.getItem(KEY) === "split" ? "split" : "rows"; }
  catch { return fallback; }
}

function subscribe(notify: () => void) {
  window.addEventListener("storage", notify);
  window.addEventListener(EVENT, notify);
  return () => {
    window.removeEventListener("storage", notify);
    window.removeEventListener(EVENT, notify);
  };
}

export function useReviewView() {
  const view = useSyncExternalStore(subscribe, snapshot, () => "rows" as const);
  function setView(next: ReviewView) {
    fallback = next;
    try { localStorage.setItem(KEY, next); } catch { /* Keep the choice for this session. */ }
    window.dispatchEvent(new Event(EVENT));
  }
  return [view, setView] as const;
}

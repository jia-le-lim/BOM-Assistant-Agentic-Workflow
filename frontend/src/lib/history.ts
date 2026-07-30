"use client";

import type { ChatSessionSummary } from "./types";

const CHANGE_EVENT = "bom-chat-history-updated";

export function notifyChatHistoryChanged(): void {
  window.dispatchEvent(new Event(CHANGE_EVENT));
}

export function subscribeChatHistoryChanged(listener: () => void): () => void {
  window.addEventListener(CHANGE_EVENT, listener);
  return () => window.removeEventListener(CHANGE_EVENT, listener);
}

function timestamp(value: string): number {
  const iso = value.includes("T") ? value : value.replace(" ", "T") + "Z";
  return new Date(iso).getTime();
}

export function groupChatSessions(sessions: ChatSessionSummary[]): {
  label: string;
  items: ChatSessionSummary[];
}[] {
  const startOfToday = new Date();
  startOfToday.setHours(0, 0, 0, 0);
  const day = 86_400_000;
  const t = startOfToday.getTime();

  const buckets: Record<string, ChatSessionSummary[]> = {
    Today: [], Yesterday: [], "Previous 7 days": [], Older: [],
  };
  for (const session of sessions) {
    const updatedAt = timestamp(session.updated_at);
    const key = updatedAt >= t ? "Today"
      : updatedAt >= t - day ? "Yesterday"
      : updatedAt >= t - 7 * day ? "Previous 7 days"
      : "Older";
    buckets[key].push(session);
  }
  return Object.entries(buckets)
    .filter(([, items]) => items.length > 0)
    .map(([label, items]) => ({ label, items }));
}

"use client";
/* eslint-disable @next/next/no-img-element -- Evidence is retrieved using the authenticated API and a local blob URL. */
import { useEffect, useState } from "react";
import { useApi } from "@/lib/api";

export function ReminderScreenshot({ reminderId }: { reminderId: string }) {
  const { raw } = useApi();
  const [url, setUrl] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true, objectUrl = "";
    void raw("reminders/" + reminderId + "/image").then(async (response) => {
      if (!response.ok) throw new Error("Screenshot unavailable.");
      const blob = await response.blob();
      if (active) { objectUrl = URL.createObjectURL(blob); setUrl(objectUrl); }
    }).catch((e) => { if (active) setError((e as Error).message); });
    return () => { active = false; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [reminderId, raw]);
  if (error) return <p role="alert">{error}</p>;
  return url ? <a href={url} target="_blank" rel="noreferrer" title="Open full screenshot">
    <img src={url} className="reminder-preview" alt="Original reminder screenshot" /></a> : <p role="status">Loading screenshot…</p>;
}

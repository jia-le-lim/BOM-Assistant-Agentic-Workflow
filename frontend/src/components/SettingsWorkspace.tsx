"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import "./settings.css";

export const SETTINGS_SECTIONS = [
  { id: "thresholds", label: "Thresholds" },
  { id: "autoclear", label: "Auto-clear policy" },
  { id: "review-assist", label: "Review assist" },
  { id: "criticality", label: "Machine criticality" },
  { id: "part-categories", label: "Part categories" },
] as const;

export type SettingsSection = (typeof SETTINGS_SECTIONS)[number]["id"];

export function SettingsWorkspace({ active, onSelect, changed = [], version, children }: {
  active: SettingsSection | "dormant";
  onSelect?: (section: SettingsSection) => void;
  changed?: SettingsSection[];
  version?: string;
  children: ReactNode;
}) {
  return (
    <div className="settings-workspace">
      <aside className="settings-sidebar" aria-label="Settings navigation">
        <div className="settings-identity">
          <h2>Settings</h2>
          <p>Private to your account</p>
        </div>
        <nav className="settings-nav" aria-label="Settings sections">
          {SETTINGS_SECTIONS.map(({ id, label }) => {
            const content = <><span>{label}</span>{changed.includes(id) &&
              <span className="settings-nav-dot" aria-label="Unsaved changes" />}</>;
            return onSelect ? (
              <button key={id} type="button" aria-label={label} aria-current={active === id ? "page" : undefined}
                onClick={() => onSelect(id)}>{content}</button>
            ) : (
              <Link key={id} href={`/config#${id}`}>{content}</Link>
            );
          })}
          <Link href="/config/dormant" aria-current={active === "dormant" ? "page" : undefined}>
            Dormant rules
          </Link>
        </nav>
        <div className="settings-sidebar-note">
          <span>{version ? "Active rule version" : "Stocking configuration"}</span>
          {version && <code>{version}</code>}
          <p>Saved rules apply to your workspaces on the next engine run.</p>
        </div>
      </aside>
      <div className="settings-main">{children}</div>
    </div>
  );
}

export function SettingsRow({ id, label, description, changed, children }: {
  id: string; label: string; description: string; changed?: boolean; children: ReactNode;
}) {
  return (
    <div className="settings-row">
      <div className="settings-row-copy">
        <div className="settings-row-title">
          <label htmlFor={id}>{label}</label>
          {changed && <span className="settings-changed">Changed</span>}
        </div>
        <p id={`${id}-hint`}>{description}</p>
      </div>
      <div className="settings-control">{children}</div>
    </div>
  );
}

export function SettingsActions({ dirty, status, onCancel, disabled, children }: {
  dirty: boolean; status: string; onCancel: () => void; disabled?: boolean; children: ReactNode;
}) {
  return (
    <footer className="settings-actions">
      <p className={dirty ? "settings-status is-dirty" : "settings-status"} role="status">
        <span aria-hidden>{dirty ? "" : "✓"}</span>{status}
      </p>
      <div className="settings-buttons">
        <button type="button" className="btn" onClick={onCancel} disabled={disabled || !dirty}>Cancel</button>
        {children}
      </div>
    </footer>
  );
}

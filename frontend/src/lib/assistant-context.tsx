"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef } from "react";
import { usePathname, useRouter } from "next/navigation";
import type { AssistantPageContext, PageAction, PageField, SettingsFill } from "./types";

type Form = {
  path: string;
  state: Record<string, unknown>;
  apply: (actions: SettingsFill[]) => void;
};
interface AssistantContextValue {
  capture: (detailed?: boolean) => AssistantPageContext;
  applyActions: (actions: PageAction[], snapshot: AssistantPageContext) => string;
  register: (form: Form) => () => void;
}
const Context = createContext<AssistantContextValue | null>(null);

export function pageIdentity(path: string) {
  const match = path.match(/^\/batches\/(\d+)(?:\/items\/([^/]+))?/);
  const batchId = match ? Number(match[1]) : null;
  let itemId: string | null = match?.[2] ?? null;
  try { if (itemId) itemId = decodeURIComponent(itemId); } catch { /* retain original */ }
  const title = path === "/config/dormant" ? "Dormant stocking rules"
    : path === "/config" ? "Rules & criticality"
    : itemId ? `Batch #${batchId} · Item ${itemId}`
    : batchId ? `Batch #${batchId}` : path === "/chat" ? "Ask NYRA" : "Batches";
  return { title, batch_id: batchId, item_id: itemId };
}

function visible(element: Element) {
  const box = element.getBoundingClientRect();
  return box.width > 0 && box.height > 0 && box.bottom > 56 && box.top < window.innerHeight
    && box.right > 0 && box.left < window.innerWidth
    && getComputedStyle(element).visibility !== "hidden";
}

function sectionOf(element: Element) {
  const section = element.closest("[data-assistant-section], .card, section, details");
  return (section?.getAttribute("data-assistant-section")
    || section?.querySelector("h2, h3, summary")?.textContent || "").trim().slice(0, 160);
}

function fieldOf(element: Element): PageField | null {
  if (!(element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement
        || element instanceof HTMLSelectElement)) return null;
  if (element.closest("[data-assistant-private]")
      || (element instanceof HTMLInputElement && ["password", "hidden", "file", "email"].includes(element.type))) return null;
  const label = element.getAttribute("aria-label") || element.labels?.[0]?.querySelector("span")?.textContent || element.labels?.[0]?.textContent
    || element.closest("tr")?.querySelector("td")?.textContent || element.name || element.id;
  if (!label) return null;
  const value = element instanceof HTMLInputElement && ["checkbox", "radio"].includes(element.type)
    ? String(element.checked) : element.value;
  return { label: label.trim().slice(0, 160), value: value.slice(0, 500),
           section: sectionOf(element), disabled: element.disabled };
}

function visibleText(root: Element) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const chunks: string[] = [];
  let size = 0;
  while (walker.nextNode() && size < 8000) {
    const parent = walker.currentNode.parentElement;
    if (!parent || parent.closest("script, style, option, textarea, [data-assistant-private], [aria-hidden=true]")
        || !visible(parent)) continue;
    const value = walker.currentNode.textContent?.trim();
    if (value) { chunks.push(value); size += value.length + 1; }
  }
  return chunks.join(" ").slice(0, 8000);
}

export function AssistantContextProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const form = useRef<Form | null>(null);
  const attention = useRef({ path: "", selected: "", section: "", focused: null as PageField | null });

  useEffect(() => {
    const remember = (event: Event) => {
      const target = event.target;
      if (!(target instanceof Element) || !target.closest("main.shell-content")) return;
      const path = window.location.pathname;
      if (attention.current.path !== path) attention.current = { path, selected: "", section: "", focused: null };
      attention.current.section = sectionOf(target);
      attention.current.focused = fieldOf(target);
    };
    const select = () => {
      const selection = window.getSelection();
      if (selection?.isCollapsed && document.activeElement?.closest(".assistant-float")) return;
      const parent = selection?.anchorNode?.parentElement;
      if (!parent?.closest("main.shell-content") || parent.closest("[data-assistant-private]")) return;
      attention.current = { ...attention.current, path: window.location.pathname,
                            selected: selection?.toString().slice(0, 2000) ?? "" };
    };
    document.addEventListener("focusin", remember);
    document.addEventListener("pointerdown", remember);
    document.addEventListener("input", remember);
    document.addEventListener("change", remember);
    document.addEventListener("selectionchange", select);
    return () => {
      document.removeEventListener("focusin", remember);
      document.removeEventListener("pointerdown", remember);
      document.removeEventListener("input", remember);
      document.removeEventListener("change", remember);
      document.removeEventListener("selectionchange", select);
    };
  }, []);

  const register = useCallback((next: Form) => {
    form.current = next;
    return () => { if (form.current === next) form.current = null; };
  }, []);

  const capture = useCallback((detailed = true): AssistantPageContext => {
    const root = document.querySelector("main.shell-content");
    const path = root?.getAttribute("data-assistant-path") || window.location.pathname;
    const focus = attention.current.path === path ? attention.current : null;
    const candidates = root ? [...root.querySelectorAll("[data-assistant-section], .card, section, details")]
      .filter(visible) : [];
    const sections = candidates.map(sectionOf).filter(Boolean);
    const mostVisible = candidates.reduce<{ name: string; area: number }>((best, element) => {
      const box = element.getBoundingClientRect();
      const area = (Math.min(box.bottom, window.innerHeight) - Math.max(box.top, 56)) * box.width;
      return area > best.area && sectionOf(element) ? { name: sectionOf(element), area } : best;
    }, { name: "", area: 0 });
    const fields = root && detailed ? [...root.querySelectorAll("input, select, textarea")]
      .filter(visible).map(fieldOf).filter((field): field is PageField => !!field).slice(0, 60) : [];
    const state = detailed && form.current?.path === path ? form.current.state : {};
    const detail = root?.querySelector("[data-assistant-item-id]");
    const identity = pageIdentity(path);
    const itemId = detail?.getAttribute("data-assistant-item-id") || identity.item_id;
    return { path, ...identity, item_id: itemId,
      title: itemId ? `Batch #${identity.batch_id} · Item ${itemId}` : identity.title,
      stockroom_id: detail?.getAttribute("data-assistant-stockroom-id")
        ?? new URLSearchParams(window.location.search).get("stockroom_id"),
      active_section: focus?.section && sections.includes(focus.section) ? focus.section : mostVisible.name,
      visible_sections: [...new Set(sections)].slice(0, 20),
      visible_text: root && detailed ? visibleText(root) : "", selected_text: focus?.selected || "",
      focused_field: focus?.focused || null, fields,
      form_state: JSON.parse(JSON.stringify(state)),
    };
  }, []);

  const applyActions = useCallback((actions: PageAction[], snapshot: AssistantPageContext) => {
    if (!actions.length) return "";
    const current = capture();
    if (current.path !== snapshot.path || window.location.pathname !== snapshot.path
      || current.item_id !== snapshot.item_id || current.stockroom_id !== snapshot.stockroom_id) {
      return `Draft kept out of the form because you moved from ${snapshot.title}. Return there and ask again.`;
    }
    if (JSON.stringify(current.form_state) !== JSON.stringify(snapshot.form_state)) {
      return "Your form changed while I was working. I kept your edits; ask again to update this draft.";
    }
    const fills = actions.filter((action): action is SettingsFill => action.kind === "fill_settings");
    if (fills.length) {
      if (current.form_state.busy || current.form_state.ready === false) {
        return "The settings form is loading or saving. Ask again when it is ready.";
      }
      if (fills.some((a) => a.path !== current.path) || form.current?.path !== current.path) {
        return "The settings form is unavailable. Open the settings page and ask again.";
      }
      form.current.apply(fills);
      const section = fills[0].section;
      window.requestAnimationFrame(() => {
        const target = document.querySelector(`[data-assistant-target="${section}"]`);
        if (target) {
          attention.current = { ...attention.current, path: current.path, section: sectionOf(target), focused: null };
          target.scrollIntoView({ behavior: "smooth", block: "center" });
        }
      });
      return "Filled the editable draft on this page. Review it, then use Propose or Save to submit.";
    }
    const navigation = actions.find((action) => action.kind === "navigate");
    if (navigation && /^\/(?:config(?:\/dormant)?|chat|batches\/[1-9]\d*(?:\/items\/[\w.%~-]+)?)?$/.test(navigation.path)) {
      // Moving away would discard unsaved React form state; leave the link in
      // the reply available for the user to open in that case.
      if (current.form_state.dirty) return "You have unsaved edits. Save them before opening the linked page.";
      router.push(navigation.path);
      return "Opened the requested page.";
    }
    return "";
  }, [capture, router]);
  const value = useMemo(() => ({ capture, applyActions, register }), [capture, applyActions, register]);
  return <Context.Provider value={value}>{children}</Context.Provider>;
}

export function useAssistantContext() {
  const context = useContext(Context);
  if (!context) throw new Error("AssistantContextProvider is missing");
  return context;
}

export function useAssistantForm(state: Record<string, unknown>, apply: Form["apply"]) {
  const path = usePathname();
  const { register } = useAssistantContext();
  useEffect(() => register({ path, state, apply }), [register, path, state, apply]);
}

"use client";

import { flushSync } from "react-dom";

let activeTransition: ViewTransition | undefined;
let fallbackAnimations: Animation[] = [];
let generation = 0;

function visibleMorphs(target: HTMLElement) {
  return [...target.querySelectorAll<HTMLElement>("[data-review-morph]")].filter((element) => {
    const box = element.getBoundingClientRect();
    const clip = element.closest(".review-list-scroll, .scroll-x")?.getBoundingClientRect();
    const top = Math.max(56, clip?.top ?? 0);
    const bottom = Math.min(innerHeight, clip?.bottom ?? innerHeight);
    const left = Math.max(0, clip?.left ?? 0);
    const right = Math.min(innerWidth, clip?.right ?? innerWidth);
    return box.width > 0 && box.height > 0 && box.bottom > top && box.top < bottom
      && box.right > left && box.left < right;
  });
}

function nameMorphs(target: HTMLElement) {
  for (const element of visibleMorphs(target)) {
    // Full item + stockroom identity, encoded without collisions as a CSS identifier.
    element.style.viewTransitionName = "review-morph-" + Array.from(element.dataset.reviewMorph!)
      .map((char) => char.codePointAt(0)!.toString(16)).join("-");
  }
}

function clearMorphs() {
  document.querySelectorAll<HTMLElement>("[data-review-morph]").forEach((element) => {
    element.style.removeProperty("view-transition-name");
  });
}

/** Animate snapshots of the changing review region without retaining old forms. */
export function transitionReview(kind: "layout" | "item", update: () => void, animate = true) {
  const current = ++generation;
  activeTransition?.skipTransition();
  fallbackAnimations.forEach((animation) => animation.cancel());
  fallbackAnimations = [];
  clearMorphs();
  const root = document.documentElement;
  delete root.dataset.reviewTransition;
  const target = document.querySelector<HTMLElement>(
    kind === "layout" ? ".review-layout-region" : ".review-detail",
  );

  if (!animate || !target || window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    update();
    return;
  }

  if (!document.startViewTransition) {
    const before = new Map(visibleMorphs(target)
      .filter((element) => element.dataset.reviewMorph!.startsWith("row:"))
      .map((element) => [element.dataset.reviewMorph, element.getBoundingClientRect()]));
    flushSync(update);
    if (kind === "layout") {
      // FLIP the matching rows when browser snapshots are unavailable.
      for (const element of visibleMorphs(target)) {
        const from = before.get(element.dataset.reviewMorph);
        if (!from) continue;
        const to = element.getBoundingClientRect();
        fallbackAnimations.push(element.animate([
          { transformOrigin: "top left", transform: `translate(${from.x - to.x}px, ${from.y - to.y}px) scale(${from.width / to.width}, ${from.height / to.height})` },
          { transformOrigin: "top left", transform: "none" },
        ], { duration: 560, easing: "cubic-bezier(.22, 1, .36, 1)" }));
      }
      const panel = target.querySelector(".review-detail");
      if (panel) fallbackAnimations.push(panel.animate([
        { clipPath: "inset(0 100% 0 0 round 12px)" },
        { clipPath: "inset(0 0 0 0 round 0px)" },
      ], { duration: 560, easing: "cubic-bezier(.22, 1, .36, 1)" }));
    } else {
      fallbackAnimations.push(target.animate(
        [{ opacity: .35, transform: "translateY(6px)" }, { opacity: 1, transform: "translateY(0)" }],
        { duration: 220, easing: "cubic-bezier(.22, 1, .36, 1)" },
      ));
    }
    return;
  }

  root.dataset.reviewTransition = kind;
  if (kind === "layout") nameMorphs(target);
  const transition = document.startViewTransition(() => {
    // A newer click or navigation supersedes this snapshot's pending update.
    if (current === generation && target.isConnected) {
      flushSync(update);
      if (kind === "layout") { clearMorphs(); nameMorphs(target); }
    }
  });
  activeTransition = transition;
  // A superseded snapshot rejects `ready`; its state update still runs.
  void transition.ready.catch(() => {});
  const cleanUp = () => {
    if (current !== generation) return;
    delete root.dataset.reviewTransition;
    clearMorphs();
    activeTransition = undefined;
  };
  void transition.finished.then(cleanUp, cleanUp);
}

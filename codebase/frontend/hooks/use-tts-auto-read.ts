"use client";

import { useCallback, useSyncExternalStore } from "react";

const STORAGE_KEY = "vdiab:tts-auto-read";

/**
 * One shared on/off preference for auto-reading AI replies aloud, persisted
 * in localStorage so it's remembered across both chat surfaces (patient AI
 * page and the doctor-facing AI tab) and across sessions. Defaults to on —
 * the whole point of this feature is elderly patients not having to read.
 *
 * A true module-level store (useSyncExternalStore), not per-component
 * useState — each page's own instance of this hook (used for its
 * send-message auto-play check) and the header TtsAutoReadToggle's instance
 * must observe the exact same value, or toggling it off in the header would
 * silently fail to stop auto-play (that per-component-state version was
 * shipped and caught live: the toggle's own UI flipped off but the page's
 * separate hook call kept its stale `true`, so replies kept auto-playing).
 */

let enabled = true;
let hydrated = false;
const listeners = new Set<() => void>();

function hydrate() {
  if (hydrated) return;
  hydrated = true;
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (stored !== null) enabled = stored === "1";
  } catch {
    // private browsing / storage disabled — keep the in-memory default
  }
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot() {
  hydrate();
  return enabled;
}

function getServerSnapshot() {
  return true;
}

function setEnabled(next: boolean) {
  enabled = next;
  try {
    window.localStorage.setItem(STORAGE_KEY, next ? "1" : "0");
  } catch {
    // ignore — preference just won't persist this session
  }
  listeners.forEach((listener) => listener());
}

export function useTtsAutoRead() {
  const enabledValue = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
  const toggle = useCallback(() => setEnabled(!enabled), []);
  return { enabled: enabledValue, toggle };
}

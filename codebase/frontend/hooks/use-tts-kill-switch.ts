"use client";

import { useSyncExternalStore } from "react";

/**
 * Session-wide TTS kill switch: any synthesis/playback failure (see
 * use-tts-playback.ts) trips this once, for the rest of the session, across
 * both chat surfaces — every TtsPlayButton/TtsAutoReadToggle then hides
 * itself as if NEXT_PUBLIC_TTS_API_URL were unset. Deliberately in-memory
 * only (a module-level flag, not localStorage) so a page reload gives the
 * service a fresh chance rather than staying off forever after one bad call.
 */

let killed = false;
const listeners = new Set<() => void>();

export function killTts() {
  if (killed) return;
  killed = true;
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot() {
  return killed;
}

export function useTtsKilled() {
  return useSyncExternalStore(subscribe, getSnapshot, () => false);
}

/** Non-reactive read for use inside callbacks (e.g. play() bailing out
 * before attempting a request), where a hook subscription isn't available. */
export function isTtsKilled() {
  return killed;
}

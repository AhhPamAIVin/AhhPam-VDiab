"use client";

import { Loader2, Square, Volume2 } from "lucide-react";
import type { TtsPlaybackState } from "@/hooks/use-tts-playback";
import { useTtsKilled } from "@/hooks/use-tts-kill-switch";
import { cn } from "@/lib/utils";

interface TtsPlayButtonProps {
  state: TtsPlaybackState;
  onClick: () => void;
}

const TTS_CONFIGURED = Boolean(process.env.NEXT_PUBLIC_TTS_API_URL);

/** Per-message "listen"/"stop" control. Controlled component — driven by the
 * page's single useTtsPlayback() instance (see agent-assistant-page.tsx /
 * patient-detail-page.tsx) so a manual click and auto-play share one
 * playback state and never overlap. Mirrors voice-input-button.tsx's
 * self-hiding convention: absent entirely when TTS isn't configured, and
 * also once any TTS failure has tripped the session-wide kill switch (see
 * hooks/use-tts-kill-switch.ts) — a broken service shouldn't leave every
 * message showing a dead button. */
export function TtsPlayButton({ state, onClick }: TtsPlayButtonProps) {
  const killed = useTtsKilled();
  if (!TTS_CONFIGURED || killed) return null;

  const isBusy = state === "loading" || state === "playing";

  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex h-8 w-8 shrink-0 items-center justify-center rounded-full transition",
        isBusy ? "bg-primary text-primary-foreground" : "bg-accent text-primary hover:bg-primary/15",
      )}
      aria-label={isBusy ? "Dừng đọc" : "Nghe"}
      title={isBusy ? "Dừng đọc" : "Nghe"}
    >
      {state === "loading" ? (
        <Loader2 className="h-4 w-4 animate-spin" />
      ) : state === "playing" ? (
        <Square className="h-4 w-4" />
      ) : (
        <Volume2 className="h-4 w-4" />
      )}
    </button>
  );
}

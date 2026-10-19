"use client";

import { Volume2, VolumeX } from "lucide-react";
import { useTtsAutoRead } from "@/hooks/use-tts-auto-read";
import { useTtsKilled } from "@/hooks/use-tts-kill-switch";

const TTS_CONFIGURED = Boolean(process.env.NEXT_PUBLIC_TTS_API_URL);

/** Header-level toggle for the shared "read AI replies aloud automatically"
 * preference (see hooks/use-tts-auto-read.ts) — same on/off state on both
 * the patient and doctor chat surfaces. Absent entirely when TTS isn't
 * configured, and also once any TTS failure has tripped the session-wide
 * kill switch (see hooks/use-tts-kill-switch.ts) — no point offering a
 * toggle for a service that just failed, for the rest of this session. */
export function TtsAutoReadToggle() {
  const autoRead = useTtsAutoRead();
  const killed = useTtsKilled();
  if (!TTS_CONFIGURED || killed) return null;

  return (
    <button
      type="button"
      onClick={autoRead.toggle}
      className="flex items-center gap-1.5 rounded-lg p-1.5 text-muted-foreground hover:bg-muted"
      aria-label={autoRead.enabled ? "Tắt đọc tự động" : "Bật đọc tự động"}
      title={autoRead.enabled ? "Tắt đọc tự động" : "Bật đọc tự động"}
      aria-pressed={autoRead.enabled}
    >
      {autoRead.enabled ? (
        <Volume2 className="h-4 w-4 text-primary" />
      ) : (
        <VolumeX className="h-4 w-4" />
      )}
    </button>
  );
}

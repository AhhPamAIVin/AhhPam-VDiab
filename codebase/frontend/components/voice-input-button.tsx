"use client";

import { useEffect } from "react";
import { Mic, Square, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { useVoiceTranscription } from "@/hooks/use-voice-transcription";

interface VoiceInputButtonProps {
  /** Corrected transcript judged clear enough — caller should send it as-is. */
  onFinal: (text: string) => void;
  /** Transcript was too garbled/ambiguous to safely forward — show this question instead of sending. */
  onClarification: (question: string) => void;
  disabled?: boolean;
  /** erax | whisper | qwen_omni — omit to use the STT service's configured default. */
  engine?: string;
}

const RE_ARM_DELAY_MS = 900;
const STT_CONFIGURED = Boolean(process.env.NEXT_PUBLIC_STT_WS_URL);

/** Mic button for the patient AI chat's send bar: tap to start streaming to
 * the GPU-hosted STT service, live captions appear while speaking, and the
 * corrected transcript auto-sends once the patient goes quiet. A visible
 * cancel control (and re-tapping the button itself) lets them abort
 * mid-sentence before anything is sent — see hooks/use-voice-transcription.ts
 * for the state machine and the human-in-the-loop clarification handling. */
export function VoiceInputButton({
  onFinal,
  onClarification,
  disabled,
  engine,
}: VoiceInputButtonProps) {
  const voice = useVoiceTranscription({
    onFinal,
    onClarificationNeeded: onClarification,
    engine,
  });

  // HITL: after asking the patient to repeat, listen again automatically
  // instead of making them re-tap the mic.
  useEffect(() => {
    if (voice.state !== "needs_clarification") return;
    const timer = window.setTimeout(() => void voice.start(), RE_ARM_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [voice.state, voice.start]);

  if (!STT_CONFIGURED) return null;

  const isActive =
    voice.state === "connecting" ||
    voice.state === "recording" ||
    voice.state === "processing";

  return (
    <div className="relative">
      {isActive && (
        <div className="absolute bottom-full right-0 mb-2 w-72 max-w-[80vw] rounded-2xl border border-border bg-card p-3 shadow-lg">
          <div className="mb-2 flex items-center gap-2">
            <span className="relative flex h-2.5 w-2.5">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-red-400 opacity-75" />
              <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-red-500" />
            </span>
            <span className="text-xs font-medium text-foreground">
              {voice.state === "processing" ? "Đang xử lý..." : "Đang nghe..."}
            </span>
            <button
              type="button"
              onClick={voice.cancel}
              className="ml-auto flex h-6 w-6 items-center justify-center rounded-full text-muted-foreground hover:bg-muted"
              aria-label="Hủy"
              title="Hủy"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
          <p className="min-h-[1.5em] break-words text-sm text-foreground">
            {voice.partialText || "..."}
          </p>
        </div>
      )}

      {voice.errorMessage && !isActive && (
        <p className="absolute bottom-full right-0 mb-2 w-56 rounded-lg bg-red-50 px-2 py-1 text-[11px] text-red-700">
          {voice.errorMessage}
        </p>
      )}

      <button
        type="button"
        onClick={() => (isActive ? voice.cancel() : void voice.start())}
        disabled={disabled && !isActive}
        className={cn(
          "flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-border transition disabled:opacity-40",
          isActive
            ? "border-red-200 bg-red-50 text-red-600"
            : "bg-card text-foreground hover:bg-accent",
        )}
        aria-label={isActive ? "Dừng ghi âm" : "Nói với trợ lý AI"}
        title={isActive ? "Dừng ghi âm" : "Nói với trợ lý AI"}
      >
        {isActive ? <Square className="h-4 w-4" /> : <Mic className="h-4 w-4" />}
      </button>
    </div>
  );
}

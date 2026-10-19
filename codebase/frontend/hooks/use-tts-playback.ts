"use client";

import { useCallback, useRef, useState } from "react";
import { isTtsKilled, killTts } from "@/hooks/use-tts-kill-switch";

export type TtsPlaybackState = "idle" | "loading" | "playing" | "error";

const TTS_API_URL = process.env.NEXT_PUBLIC_TTS_API_URL || "";
const TTS_TOKEN = process.env.NEXT_PUBLIC_TTS_SERVICE_TOKEN || "";

// Target chunk size for splitting a reply into sentence-level pieces before
// synthesis. Small enough that the first chunk comes back quickly (so
// playback starts almost immediately instead of waiting for the whole
// reply), large enough to avoid a flood of tiny requests for a long answer.
const TARGET_CHUNK_CHARS = 220;

function splitIntoSpeechChunks(text: string): string[] {
  const sentences = text
    .split(/(?<=[.!?])\s+|\n{2,}/)
    .map((sentence) => sentence.trim())
    .filter(Boolean);
  if (sentences.length === 0) return [text];

  const chunks: string[] = [];
  let current = "";
  for (const sentence of sentences) {
    const candidate = current ? `${current} ${sentence}` : sentence;
    if (current && candidate.length > TARGET_CHUNK_CHARS) {
      chunks.push(current);
      current = sentence;
    } else {
      current = candidate;
    }
  }
  if (current) chunks.push(current);
  return chunks;
}

/**
 * Drives the "listen" control on AI chat replies: POST reply text ->
 * standalone TTS service (codebase/tts-service) -> audio bytes -> playback.
 * One instance per page (see agent-assistant-page.tsx /
 * patient-detail-page.tsx), not per message — `playingId` tracks which
 * message's audio is currently active so calling play() on a different
 * message always stops whatever was playing first, and both auto-play and
 * every message's manual replay button share this one playback state.
 *
 * A long reply is split into sentence-sized chunks and synthesized/played
 * as a pipeline (one chunk playing while the next is being synthesized)
 * instead of one big request — so listening starts as soon as the first
 * sentence is ready rather than waiting for the entire reply.
 */
export function useTtsPlayback() {
  const [state, setState] = useState<TtsPlaybackState>("idle");
  const [playingId, setPlayingId] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const audioRef = useRef<HTMLAudioElement | null>(null);
  const objectUrlRef = useRef<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  // Rejects the in-flight chunk-playback promise so stop()/a new play() can
  // unwind an active play() call cleanly instead of leaving it suspended
  // forever waiting on an `onended`/`onerror` that will never fire.
  const cancelCurrentChunkRef = useRef<((reason: unknown) => void) | null>(null);
  // Guards against a superseded play() call's own late-arriving chunk still
  // mutating state/playing audio after a newer play()/stop() has taken over.
  const sequenceRef = useRef(0);

  const teardown = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    cancelCurrentChunkRef.current?.(new DOMException("aborted", "AbortError"));
    cancelCurrentChunkRef.current = null;
    if (audioRef.current) {
      audioRef.current.onended = null;
      audioRef.current.onerror = null;
      audioRef.current.pause();
      audioRef.current.src = "";
      audioRef.current = null;
    }
    if (objectUrlRef.current) {
      URL.revokeObjectURL(objectUrlRef.current);
      objectUrlRef.current = null;
    }
  }, []);

  const stop = useCallback(() => {
    sequenceRef.current += 1;
    teardown();
    setPlayingId(null);
    setState("idle");
  }, [teardown]);

  const play = useCallback(
    async (id: string, text: string) => {
      if (!TTS_API_URL || isTtsKilled()) return;
      teardown();
      setPlayingId(id);
      setErrorMessage(null);
      setState("loading");

      const controller = new AbortController();
      abortRef.current = controller;
      sequenceRef.current += 1;
      const sequence = sequenceRef.current;

      const fetchChunkUrl = async (chunkText: string): Promise<string> => {
        const response = await fetch(`${TTS_API_URL}/tts/synthesize`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            ...(TTS_TOKEN ? { Authorization: `Bearer ${TTS_TOKEN}` } : {}),
          },
          body: JSON.stringify({ text: chunkText }),
          signal: controller.signal,
        });
        if (!response.ok) throw new Error(`TTS request failed (${response.status})`);
        const blob = await response.blob();
        return URL.createObjectURL(blob);
      };

      try {
        const chunks = splitIntoSpeechChunks(text);
        let nextUrlPromise = fetchChunkUrl(chunks[0]);

        for (let index = 0; index < chunks.length; index++) {
          const url = await nextUrlPromise;
          if (sequenceRef.current !== sequence) {
            URL.revokeObjectURL(url);
            return;
          }
          // Start synthesizing the next sentence while this one plays, so
          // it's (usually) ready by the time playback reaches it.
          nextUrlPromise =
            index + 1 < chunks.length ? fetchChunkUrl(chunks[index + 1]) : Promise.resolve("");

          objectUrlRef.current = url;
          const audio = new Audio(url);
          audioRef.current = audio;
          setState("playing");

          await new Promise<void>((resolve, reject) => {
            cancelCurrentChunkRef.current = reject;
            audio.onended = () => {
              cancelCurrentChunkRef.current = null;
              resolve();
            };
            audio.onerror = () => {
              cancelCurrentChunkRef.current = null;
              reject(new Error("Không thể phát âm thanh."));
            };
            void audio.play().catch(reject);
          });

          URL.revokeObjectURL(url);
          objectUrlRef.current = null;
          audioRef.current = null;
        }

        if (sequenceRef.current === sequence) {
          setPlayingId(null);
          setState("idle");
        }
      } catch (reason) {
        if (reason instanceof Error && reason.name === "AbortError") return;
        teardown();
        setErrorMessage(
          reason instanceof Error ? reason.message : "Không thể tạo giọng đọc.",
        );
        setState("error");
        killTts();
      }
    },
    [teardown],
  );

  return { state, playingId, errorMessage, play, stop };
}

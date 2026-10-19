"use client";

import { useCallback, useRef, useState } from "react";

export type VoiceState =
  | "idle"
  | "connecting"
  | "recording"
  | "processing"
  | "needs_clarification"
  | "error";

type ServerEvent =
  | { type: "partial"; text: string }
  | { type: "processing" }
  | { type: "final"; text: string }
  | { type: "needs_clarification"; question: string }
  | { type: "cancelled" }
  | { type: "error"; message: string };

interface UseVoiceTranscriptionOptions {
  /** Corrected transcript judged clear enough to auto-send as-is. */
  onFinal: (text: string) => void;
  /** Transcript was too garbled/ambiguous — ask the patient to repeat instead of sending it. */
  onClarificationNeeded: (question: string) => void;
  /** erax | whisper | qwen_omni — omit to use the service's configured default. */
  engine?: string;
}

const STT_WS_URL = process.env.NEXT_PUBLIC_STT_WS_URL;
const STT_TOKEN = process.env.NEXT_PUBLIC_STT_SERVICE_TOKEN || "";

/**
 * Drives the "speak" button on the patient AI chat: mic capture -> 16kHz
 * PCM16 (via public/pcm-worklet.js) -> STT streaming service WebSocket ->
 * live partial captions -> auto-finalized transcript. This hook only talks
 * to the standalone GPU-hosted STT service (NEXT_PUBLIC_STT_WS_URL) — it
 * never touches the real backend; callers pass the resulting text into the
 * existing send path (see agent-assistant-page.tsx) themselves.
 */
export function useVoiceTranscription({
  onFinal,
  onClarificationNeeded,
  engine,
}: UseVoiceTranscriptionOptions) {
  const [state, setState] = useState<VoiceState>("idle");
  const [partialText, setPartialText] = useState("");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const workletNodeRef = useRef<AudioWorkletNode | null>(null);

  const teardownAudio = useCallback(() => {
    workletNodeRef.current?.disconnect();
    workletNodeRef.current = null;
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    if (audioContextRef.current && audioContextRef.current.state !== "closed") {
      void audioContextRef.current.close();
    }
    audioContextRef.current = null;
  }, []);

  const closeSocket = useCallback(() => {
    const ws = wsRef.current;
    wsRef.current = null;
    ws?.close();
  }, []);

  const start = useCallback(async () => {
    if (!STT_WS_URL) {
      setErrorMessage("Chưa cấu hình dịch vụ nhận diện giọng nói.");
      setState("error");
      return;
    }
    setErrorMessage(null);
    setPartialText("");
    setState("connecting");

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;

      const audioContext = new AudioContext({ sampleRate: 16000 });
      audioContextRef.current = audioContext;
      await audioContext.audioWorklet.addModule("/pcm-worklet.js");

      const url = new URL(STT_WS_URL);
      if (engine) url.searchParams.set("engine", engine);
      if (STT_TOKEN) url.searchParams.set("token", STT_TOKEN);
      const ws = new WebSocket(url.toString());
      wsRef.current = ws;

      ws.onopen = () => {
        const source = audioContext.createMediaStreamSource(stream);
        const worklet = new AudioWorkletNode(audioContext, "pcm-worklet");
        workletNodeRef.current = worklet;
        worklet.port.onmessage = (event: MessageEvent<ArrayBuffer>) => {
          if (ws.readyState === WebSocket.OPEN) ws.send(event.data);
        };
        source.connect(worklet);
        setState("recording");
      };

      ws.onmessage = (event) => {
        let payload: ServerEvent;
        try {
          payload = JSON.parse(event.data as string);
        } catch {
          return;
        }
        switch (payload.type) {
          case "partial":
            setPartialText(payload.text);
            break;
          case "processing":
            setState("processing");
            break;
          case "final":
            teardownAudio();
            closeSocket();
            setPartialText("");
            setState("idle");
            if (payload.text.trim()) onFinal(payload.text);
            break;
          case "needs_clarification":
            teardownAudio();
            closeSocket();
            setPartialText("");
            setState("needs_clarification");
            onClarificationNeeded(payload.question);
            break;
          case "cancelled":
            teardownAudio();
            closeSocket();
            setPartialText("");
            setState("idle");
            break;
          case "error":
            teardownAudio();
            closeSocket();
            setErrorMessage(payload.message);
            setState("error");
            break;
        }
      };

      ws.onerror = () => {
        teardownAudio();
        closeSocket();
        setErrorMessage("Không thể kết nối dịch vụ nhận diện giọng nói.");
        setState("error");
      };

      ws.onclose = () => {
        teardownAudio();
        wsRef.current = null;
        setState((current) =>
          current === "recording" || current === "connecting" ? "idle" : current,
        );
      };
    } catch (reason) {
      teardownAudio();
      closeSocket();
      setErrorMessage(
        reason instanceof Error ? reason.message : "Không thể truy cập micro.",
      );
      setState("error");
    }
  }, [engine, onFinal, onClarificationNeeded, teardownAudio, closeSocket]);

  const cancel = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: "cancel" }));
    }
    teardownAudio();
    closeSocket();
    setPartialText("");
    setState("idle");
  }, [teardownAudio, closeSocket]);

  return { state, partialText, errorMessage, start, cancel };
}

"use client";

import { useCallback, useEffect, useState } from "react";
import { api, type ChatMessage, type ChatSession } from "@/lib/api";

/**
 * Session list + restore/new-chat state for an AI chat page, shared between
 * agent-assistant-page.tsx (patient's own chat) and patient-detail-page.tsx
 * (a doctor's chat about one patient) — both need the same list/restore/
 * new-chat data layer, just render it into their own differently-shaped
 * message bubbles. See /Users/phamminh/.claude/plans/
 * memoized-churning-patterson.md Part 2.
 *
 * `patientId` mirrors what the page already passes to api.agentChat: omit it
 * for a patient's own self-chat (backend resolves it from the token), pass
 * it for a doctor's chat about a specific patient.
 */
export function useAgentChatSessions<TMessage>(options: {
  patientId?: string;
  mapMessage: (message: ChatMessage, index: number) => TMessage;
  welcomeMessage: TMessage;
}) {
  const { patientId, mapMessage, welcomeMessage } = options;
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [restoring, setRestoring] = useState(false);

  const loadSessions = useCallback(async () => {
    try {
      setSessions(await api.listAgentSessions(patientId));
    } catch {
      // The session list is a convenience panel, not required for chat to
      // work — a failed load just leaves it empty rather than blocking the
      // page or surfacing a second error banner alongside the chat's own.
    }
  }, [patientId]);

  // Re-fetch whenever the scope changes — critical on the doctor's page,
  // which reuses one component instance across different patientIds
  // (Next.js doesn't remount on route param change there).
  useEffect(() => {
    setSessions([]);
    setActiveSessionId(null);
    void loadSessions();
  }, [loadSessions]);

  const restoreSession = useCallback(
    async (sessionId: string): Promise<TMessage[]> => {
      setRestoring(true);
      try {
        const result = await api.getAgentSessionMessages(sessionId);
        setActiveSessionId(sessionId);
        return result.messages.length
          ? result.messages.map(mapMessage)
          : [welcomeMessage];
      } finally {
        setRestoring(false);
      }
    },
    [mapMessage, welcomeMessage],
  );

  const startNewChat = useCallback((): TMessage[] => {
    setActiveSessionId(null);
    return [welcomeMessage];
  }, [welcomeMessage]);

  // Called with AgentChatResponse.session_id after every /chat reply — the
  // first message of a fresh chat only gets a session_id back from the
  // server (agent/router.py's _load_or_create_session), so this is also
  // what makes a brand-new session appear in the list without a page reload.
  const registerSessionId = useCallback(
    (sessionId: string) => {
      setActiveSessionId((current) => {
        if (current !== sessionId) void loadSessions();
        return sessionId;
      });
    },
    [loadSessions],
  );

  return {
    sessions,
    activeSessionId,
    restoring,
    restoreSession,
    startNewChat,
    registerSessionId,
  };
}

"use client";

import { useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  Bot,
  CheckCircle2,
  ChevronRight,
  Copy,
  ExternalLink,
  Info,
  Loader2,
  MessageSquarePlus,
  MessagesSquare,
  Send,
  Save,
  Sparkles,
  ThumbsDown,
  ThumbsUp,
  User,
  X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import {
  api,
  type AgentInteraction,
  type AgentSourceCitation,
  type AgentSummaryResponse,
  type ChatMessage,
  type GlucoseTrend,
  type KeyMetric,
} from "@/lib/api";
import { ChatMarkdown } from "@/components/chat-markdown";
import { ChatAnswerCards } from "@/components/chat-answer-cards";
import { AgentSummaryContent } from "@/components/agent-summary-card";
import { AgentChatSidebar } from "@/components/agent-chat-sidebar";
import { VoiceInputButton } from "@/components/voice-input-button";
import { TtsPlayButton } from "@/components/tts-play-button";
import { TtsAutoReadToggle } from "@/components/tts-auto-read-toggle";
import { useAgentChatSessions } from "@/hooks/use-agent-chat-sessions";
import { useTtsPlayback } from "@/hooks/use-tts-playback";
import { useTtsAutoRead } from "@/hooks/use-tts-auto-read";

type Message = {
  id: string;
  role: "user" | "assistant";
  content: string;
  time: string;
  disclaimer?: string;
  sources?: AgentSourceCitation[];
  limitations?: string[];
  handoff?: boolean;
  // Set only for the "Tóm tắt sức khỏe" quick-reply — lets MessageBubble embed
  // the same structured AgentSummaryContent used on the dashboard instead of
  // a flattened text blob. Both come from a fetch already done in
  // sendMessage() below, never refetched here.
  summaryData?: AgentSummaryResponse;
  summaryKeyMetrics?: KeyMetric[];
  summaryGlucoseTrend?: GlucoseTrend | null;
  interaction?: AgentInteraction | null;
  interactions?: AgentInteraction[];
  interactionStatus?: string;
  interactionError?: string;
  interactionStatuses?: Record<string, string>;
  interactionErrors?: Record<string, string>;
};

const summaryQuestion = "Tóm tắt sức khỏe trong 7 ngày qua";
const suggestions = [
  "Chỉ số đường huyết hôm nay của tôi có ổn không?",
  summaryQuestion,
  "Tôi cần chuẩn bị gì trước lần tái khám?",
  "Giải thích thuốc Metformin",
  "Bữa ăn hôm nay có gì cần chú ý?",
];

const now = () =>
  new Date().toLocaleTimeString("vi-VN", {
    hour: "2-digit",
    minute: "2-digit",
  });

const welcomeMessage: Message = {
  id: "welcome",
  role: "assistant",
  time: now(),
  content:
    "Xin chào! Tôi có thể hỗ trợ giải thích dữ liệu sức khỏe, chuẩn bị câu hỏi tái khám và tóm tắt tình trạng gần đây của bạn.",
};

// Restored history has no per-message timestamp (ChatMessagePublic carries
// role/content only) — time is left blank rather than faked.
const mapRestoredMessage = (message: ChatMessage, index: number): Message => ({
  id: `restored-${index}`,
  role: message.role,
  content: message.content,
  time: "",
});

export function AgentAssistantPage() {
  const [messages, setMessages] = useState<Message[]>([welcomeMessage]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [progress, setProgress] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionLoadingId, setActionLoadingId] = useState<string | null>(null);
  const [mobileHistoryOpen, setMobileHistoryOpen] = useState(false);
  // Starts as the static default list; after each turn we ask the separate
  // chat_suggestions overlay (codebase/backend/agent/chat_suggestions.py) for
  // 3-4 follow-ups grounded in that turn. A failed/empty fetch just leaves
  // the previous chips in place rather than clearing them.
  const [suggestedQuestions, setSuggestedQuestions] = useState<string[]>(suggestions);
  const bottomRef = useRef<HTMLDivElement>(null);
  const tts = useTtsPlayback();
  const autoRead = useTtsAutoRead();
  const {
    sessions,
    activeSessionId,
    restoring,
    restoreSession,
    startNewChat,
    registerSessionId,
  } = useAgentChatSessions<Message>({
    mapMessage: mapRestoredMessage,
    welcomeMessage,
  });

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  useEffect(() => {
    if (!mobileHistoryOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMobileHistoryOpen(false);
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [mobileHistoryOpen]);

  const refreshSuggestions = (question: string, answer: string) => {
    if (!answer) return;
    api
      .agentChatSuggestions(question, answer)
      .then((result) => {
        if (result.suggestions.length > 0) setSuggestedQuestions(result.suggestions);
      })
      .catch(() => {
        // Keep whatever chips are already shown — a failed suggestion
        // fetch must never disrupt the chat itself.
      });
  };

  const sendMessage = async (raw: string) => {
    const content = raw.trim();
    if (!content || loading) return;
    setMessages((items) => [
      ...items,
      { id: "q-" + Date.now(), role: "user", content, time: now() },
    ]);
    setInput("");
    setLoading(true);
    setError(null);
    try {
      if (content === summaryQuestion) {
        const [result, keyMetricsResult] = await Promise.all([
          api.agentSummary(7),
          api.agentSummaryKeyMetrics(7).catch(() => ({ key_metrics: [], glucose_trend: null })),
        ]);
        const assistantMessage: Message = {
          id: "a-" + Date.now(),
          role: "assistant",
          content: result.summary,
          time: now(),
          // No disclaimer/limitations here — AgentSummaryContent already
          // renders both internally from summaryData; setting them too
          // would duplicate that box below the bubble (MessageBubble's
          // generic per-message rendering further down).
          summaryData: result,
          summaryKeyMetrics: keyMetricsResult.key_metrics,
          summaryGlucoseTrend: keyMetricsResult.glucose_trend,
        };
        setMessages((items) => [...items, assistantMessage]);
        refreshSuggestions(content, result.summary);
        if (autoRead.enabled) void tts.play(assistantMessage.id, assistantMessage.content);
      } else {
        const result = await api.agentChatStream(
          content,
          undefined,
          activeSessionId ?? undefined,
          setProgress,
        );
        registerSessionId(result.session_id);
        const assistantMessage: Message = {
          id: "a-" + Date.now(),
          role: "assistant",
          content: result.response || "AI chưa trả về nội dung.",
          time: now(),
          disclaimer: result.disclaimer,
          sources: result.sources,
          limitations: result.limitations,
          handoff: result.handoff,
          interaction: result.interaction,
          interactions:
            result.interactions?.length > 0
              ? result.interactions
              : result.interaction
                ? [result.interaction]
                : [],
        };
        setMessages((items) => [...items, assistantMessage]);
        refreshSuggestions(content, result.response);
        if (autoRead.enabled) void tts.play(assistantMessage.id, assistantMessage.content);
      }
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Không thể kết nối trợ lý AI.",
      );
    } finally {
      setLoading(false);
      setProgress(null);
    }
  };

  const handleRecordAction = async (
    messageId: string,
    interaction: AgentInteraction,
    action:
      | { action: "provide_field"; field: string; value: string }
      | { action: "confirm" | "cancel" },
  ) => {
    if (actionLoadingId) return;
    setActionLoadingId(interaction.pending_action_id);
    setMessages((items) =>
      items.map((item) =>
        item.id === messageId
          ? {
              ...item,
              interactionError: undefined,
              interactionErrors: {
                ...item.interactionErrors,
                [interaction.pending_action_id]: "",
              },
            }
          : item,
      ),
    );
    try {
      const result = await api.agentRecordAction(
        interaction.pending_action_id,
        action,
      );
      setMessages((items) =>
        items.map((item) =>
          item.id === messageId
            ? {
                ...item,
                interaction: result.interaction,
                interactions: result.interaction
                  ? (item.interactions || []).map((entry) =>
                      entry.pending_action_id === interaction.pending_action_id
                        ? result.interaction!
                        : entry,
                    )
                  : (item.interactions || []).filter(
                      (entry) => entry.pending_action_id !== interaction.pending_action_id,
                    ),
                interactionStatus: result.response,
                interactionError: undefined,
                interactionStatuses: {
                  ...item.interactionStatuses,
                  [interaction.pending_action_id]: result.response,
                },
                interactionErrors: {
                  ...item.interactionErrors,
                  [interaction.pending_action_id]: "",
                },
              }
            : item,
        ),
      );
    } catch (reason) {
      setMessages((items) =>
        items.map((item) =>
          item.id === messageId
            ? {
                ...item,
                interactionError:
                  reason instanceof Error
                    ? reason.message
                    : "Không thể cập nhật bản ghi.",
                interactionErrors: {
                  ...item.interactionErrors,
                  [interaction.pending_action_id]:
                    reason instanceof Error
                      ? reason.message
                      : "Không thể cập nhật bản ghi.",
                },
              }
            : item,
        ),
      );
    } finally {
      setActionLoadingId(null);
    }
  };

  const handleSelectSession = async (sessionId: string) => {
    if (sessionId === activeSessionId || loading) return;
    setMobileHistoryOpen(false);
    setError(null);
    setMessages(await restoreSession(sessionId));
    setSuggestedQuestions(suggestions);
  };

  const handleNewChat = () => {
    if (loading) return;
    setMobileHistoryOpen(false);
    setError(null);
    setMessages(startNewChat());
    setSuggestedQuestions(suggestions);
  };

  // The STT service already declined to auto-send this transcript (too
  // garbled/ambiguous — see codebase/stt-service/app/correction.py) — show
  // it as a local clarifying question instead of calling agentChat, so an
  // unclear turn never reaches the real consultant or pollutes the chat
  // session history.
  const handleVoiceClarification = (question: string) => {
    const assistantMessage: Message = {
      id: "clarify-" + Date.now(),
      role: "assistant",
      content: question,
      time: now(),
    };
    setMessages((items) => [...items, assistantMessage]);
    if (autoRead.enabled) void tts.play(assistantMessage.id, assistantMessage.content);
  };

  const activeSession = sessions.find(
    (session) => session.id === activeSessionId,
  );

  return (
    <div className="flex h-[calc(100dvh-10rem)] min-h-0 flex-col gap-3 md:h-[calc(100vh-8rem)] md:flex-row md:gap-4">
      <AgentChatSidebar
        sessions={sessions}
        activeSessionId={activeSessionId}
        onSelect={(id) => void handleSelectSession(id)}
        onNewChat={handleNewChat}
        disabled={loading || restoring}
        className="hidden border-r border-border pr-4 md:flex"
      />

      <div className="flex shrink-0 items-center gap-2 md:hidden">
        <button
          type="button"
          onClick={handleNewChat}
          disabled={loading || restoring}
          className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-primary text-primary-foreground shadow-sm transition active:scale-95 disabled:opacity-50"
          aria-label="Cuộc trò chuyện mới"
        >
          <MessageSquarePlus className="h-5 w-5" />
        </button>
        <button
          type="button"
          onClick={() => setMobileHistoryOpen(true)}
          disabled={loading || restoring}
          className="flex min-w-0 flex-1 items-center gap-3 rounded-xl border border-border bg-card px-3 py-2 text-left shadow-sm transition active:scale-[0.99] disabled:opacity-50"
          aria-haspopup="dialog"
          aria-expanded={mobileHistoryOpen}
        >
          <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
            <MessagesSquare className="h-4 w-4" />
          </span>
          <span className="min-w-0 flex-1">
            <span className="block text-[11px] leading-none text-muted-foreground">
              Lịch sử trò chuyện · {sessions.length}
            </span>
            <span className="mt-1 block truncate text-sm font-medium text-foreground">
              {activeSession?.title || "Cuộc trò chuyện mới"}
            </span>
          </span>
          <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
        </button>
      </div>

      {mobileHistoryOpen && (
        <div className="fixed inset-0 z-50 p-2 flex items-end md:hidden">
          <button
            type="button"
            className="absolute inset-0 bg-slate-950/45 backdrop-blur-[2px]"
            onClick={() => setMobileHistoryOpen(false)}
            aria-label="Đóng lịch sử trò chuyện"
          />
          <section
            role="dialog"
            aria-modal="true"
            aria-labelledby="mobile-chat-history-title"
            className="relative flex max-h-[78dvh] w-full flex-col overflow-hidden rounded-t-[1.75rem] border-t border-border bg-background shadow-2xl"
          >
            <div className="mx-auto mt-2 h-1 w-10 shrink-0 rounded-full bg-border" />
            <div className="flex shrink-0 items-center justify-between border-b border-border px-5 pb-4 pt-3">
              <div>
                <h2
                  id="mobile-chat-history-title"
                  className="text-base font-semibold text-foreground"
                >
                  Lịch sử trò chuyện
                </h2>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  {sessions.length > 0
                    ? `${sessions.length} cuộc trò chuyện đã lưu`
                    : "Các cuộc trò chuyện sẽ xuất hiện tại đây"}
                </p>
              </div>
              <button
                type="button"
                onClick={() => setMobileHistoryOpen(false)}
                className="flex h-9 w-9 items-center justify-center rounded-full bg-muted text-muted-foreground"
                aria-label="Đóng"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4 pb-[max(1rem,env(safe-area-inset-bottom))]">
              <AgentChatSidebar
                sessions={sessions}
                activeSessionId={activeSessionId}
                onSelect={(id) => void handleSelectSession(id)}
                onNewChat={handleNewChat}
                disabled={loading || restoring}
                className="h-auto w-full overflow-visible [&>button]:mb-4 [&>button]:border-primary/30 [&>button]:bg-primary/5 [&>div]:max-h-none [&>div]:overflow-visible"
              />
            </div>
          </section>
        </div>
      )}

      <div className="mx-auto flex min-h-0 w-full max-w-3xl flex-1 flex-col">
        <div className="mb-3 flex shrink-0 items-start justify-between gap-2 rounded-xl border border-yellow-200 bg-yellow-50 px-3 py-2.5 md:mb-4 md:rounded-2xl md:p-3">
          <div className="flex items-start gap-2">
            <Info className="mt-0.5 h-4 w-4 shrink-0 text-yellow-600" />
            <p className="text-[11px] leading-relaxed text-yellow-700 sm:text-xs">
              VDiab AI tổng hợp dữ liệu sức khỏe và{" "}
              <strong>không thay thế tư vấn của bác sĩ</strong>.
            </p>
          </div>
          <TtsAutoReadToggle />
        </div>

        <div className="mb-3 min-h-0 flex-1 space-y-3 overflow-y-auto overscroll-contain pr-1 md:mb-4 md:space-y-4">
          {messages.map((message) => (
            <MessageBubble
              key={message.id}
              message={message}
              tts={tts}
              actionBusy={
                !!actionLoadingId &&
                (message.interactions || (message.interaction ? [message.interaction] : [])).some(
                  (entry) => entry.pending_action_id === actionLoadingId,
                )
              }
              onRecordAction={(interaction, action) =>
                void handleRecordAction(message.id, interaction, action)
              }
            />
          ))}
          {loading && (
            <div className="flex items-end gap-2">
              <div className="flex h-8 w-8 items-center justify-center rounded-full bg-primary/15">
                <Bot className="h-4 w-4 text-primary" />
              </div>
              <div className="flex items-center gap-2 rounded-2xl border border-border bg-card px-4 py-3">
                <div className="flex gap-1">
                  {[0, 1, 2].map((item) => (
                    <span
                      key={item}
                      className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted-foreground"
                      style={{ animationDelay: `${item * 150}ms` }}
                    />
                  ))}
                </div>
                {progress && (
                  <span aria-live="polite" className="text-xs text-muted-foreground">
                    {progress}
                  </span>
                )}
              </div>
            </div>
          )}
          {error && (
            <div
              role="alert"
              className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700"
            >
              {error}
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        <div className="mb-3 shrink-0 md:mb-4">
          <p className="mb-2 flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
            <Sparkles className="h-3.5 w-3.5" />
            Câu hỏi gợi ý
          </p>
          <div className="grid grid-cols-2 gap-2 md:flex md:flex-wrap">
            {suggestedQuestions.map((item) => (
              <button
                key={item}
                type="button"
                onClick={() => void sendMessage(item)}
                disabled={loading}
                className="min-w-0 whitespace-normal break-words rounded-2xl border border-border bg-card px-3 py-2 text-left text-xs leading-5 text-foreground hover:border-primary/40 hover:bg-accent disabled:opacity-50 md:rounded-full"
              >
                {item}
              </button>
            ))}
          </div>
        </div>

        <form
          onSubmit={(event) => {
            event.preventDefault();
            void sendMessage(input);
          }}
          className="flex shrink-0 items-end gap-2 rounded-2xl border border-border bg-card p-2 shadow-sm"
        >
          <textarea
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={(event) => {
              if (
                event.key === "Enter" &&
                !event.shiftKey &&
                !event.nativeEvent.isComposing
              ) {
                event.preventDefault();
                void sendMessage(input);
              }
            }}
            maxLength={5000}
            rows={1}
            disabled={loading}
            placeholder="Nhập câu hỏi về sức khỏe của bạn..."
            className="max-h-28 min-w-0 flex-1 resize-none bg-transparent px-2 py-2 text-sm outline-none placeholder:text-muted-foreground disabled:opacity-60 sm:px-3"
          />
          <VoiceInputButton
            onFinal={(text) => void sendMessage(text)}
            onClarification={handleVoiceClarification}
            disabled={loading}
          />
          <button
            type="submit"
            disabled={!input.trim() || loading}
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-primary text-primary-foreground hover:bg-brand-dark disabled:opacity-40"
            aria-label="Gửi"
          >
            <Send className="h-4 w-4" />
          </button>
        </form>
      </div>
    </div>
  );
}

function MessageBubble({
  message,
  tts,
  actionBusy,
  onRecordAction,
}: {
  message: Message;
  tts: ReturnType<typeof useTtsPlayback>;
  actionBusy: boolean;
  onRecordAction: (
    interaction: AgentInteraction,
    action:
      | { action: "provide_field"; field: string; value: string }
      | { action: "confirm" | "cancel" },
  ) => void;
}) {
  const [copied, setCopied] = useState(false);
  // Frontend-only, not persisted — resets on reload/session switch. Purely a
  // lightweight "was this useful" signal for the patient's own reference.
  const [feedback, setFeedback] = useState<"like" | "dislike" | null>(null);
  if (message.role === "user")
    return (
      <div className="flex justify-end gap-2">
        <div className="max-w-[88%] sm:max-w-[82%]">
          <div className="break-words whitespace-pre-wrap rounded-2xl rounded-br-sm bg-primary px-3 py-2.5 text-sm leading-relaxed text-primary-foreground sm:px-4 sm:py-3">
            {message.content}
          </div>
          <p className="mt-1 text-right text-[11px] text-muted-foreground sm:text-xs">
            {message.time}
          </p>
        </div>
        <div className="hidden h-8 w-8 shrink-0 items-center justify-center rounded-full bg-accent sm:flex">
          <User className="h-4 w-4 text-primary" />
        </div>
      </div>
    );

  return (
    <div className="flex min-w-0 items-start gap-2">
      <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary/15 sm:h-8 sm:w-8">
        <Bot className="h-4 w-4 text-primary" />
      </div>
      <div className="min-w-0 max-w-[calc(100%-2.25rem)] space-y-2 sm:max-w-[88%]">
        <div className="break-words rounded-2xl rounded-tl-sm border border-border bg-card px-3 py-2.5 text-foreground sm:px-4 sm:py-3">
          {message.summaryData ? (
            <AgentSummaryContent
              summary={message.summaryData}
              keyMetrics={message.summaryKeyMetrics}
              glucoseTrend={message.summaryGlucoseTrend}
            />
          ) : (
            <ChatAnswerCards content={message.content} />
          )}
        </div>
        {(message.interactions || (message.interaction ? [message.interaction] : [])).map(
          (interaction) => (
            <RecordInteractionCard
              key={interaction.pending_action_id}
              interaction={interaction}
              busy={actionBusy}
              status={message.interactionStatuses?.[interaction.pending_action_id]}
              error={message.interactionErrors?.[interaction.pending_action_id]}
              onAction={(action) => onRecordAction(interaction, action)}
            />
          ),
        )}
        {!(message.interactions?.length || message.interaction) && message.interactionStatus && (
          <p className="rounded-xl border border-border bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
            {message.interactionStatus}
          </p>
        )}
        {message.handoff && (
          <p className="flex items-start gap-2 rounded-xl border border-orange-200 bg-orange-50 px-3 py-2 text-xs text-orange-700">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            Nội dung này cần được trao đổi trực tiếp với bác sĩ.
          </p>
        )}
        {message.limitations && message.limitations.length > 0 && (
          <div className="break-words rounded-xl bg-muted px-3 py-2 text-xs text-muted-foreground">
            <strong>Giới hạn dữ liệu:</strong> {message.limitations.join(" · ")}
          </div>
        )}
        {message.sources && message.sources.length > 0 && (
          <div className="min-w-0 rounded-xl border border-border px-3 py-2">
            <p className="mb-1 text-xs font-medium text-foreground">
              Nguồn tham khảo
            </p>
            {message.sources.map((source, index) =>
              source.source_url ? (
                <a
                  key={index}
                  href={source.source_url}
                  target="_blank"
                  rel="noreferrer"
                  className="flex min-w-0 items-start gap-1 break-all text-xs text-primary hover:underline"
                >
                  <span>
                    {source.title}
                    {source.organization ? ` (${source.organization})` : ""}
                    {source.section ? ` — ${source.section}` : ""}
                  </span>
                  <ExternalLink className="mt-0.5 h-3 w-3 shrink-0" />
                </a>
              ) : (
                <p
                  key={index}
                  className="break-words text-xs text-muted-foreground"
                >
                  {source.title}
                  {source.organization ? ` (${source.organization})` : ""}
                  {source.section ? ` — ${source.section}` : ""}
                </p>
              ),
            )}
          </div>
        )}
        <div className="flex items-center gap-1 px-1">
          <span className="flex-1 text-[11px] text-muted-foreground sm:text-xs">
            {message.time}
          </span>
          <button
            type="button"
            onClick={() => setFeedback((current) => (current === "like" ? null : "like"))}
            className={cn(
              "rounded-lg p-1 hover:bg-muted",
              feedback === "like" ? "text-primary" : "text-muted-foreground",
            )}
            aria-label="Câu trả lời hữu ích"
            aria-pressed={feedback === "like"}
          >
            <ThumbsUp className={cn("h-3.5 w-3.5", feedback === "like" && "fill-current")} />
          </button>
          <button
            type="button"
            onClick={() => setFeedback((current) => (current === "dislike" ? null : "dislike"))}
            className={cn(
              "rounded-lg p-1 hover:bg-muted",
              feedback === "dislike" ? "text-red-500" : "text-muted-foreground",
            )}
            aria-label="Câu trả lời chưa hữu ích"
            aria-pressed={feedback === "dislike"}
          >
            <ThumbsDown className={cn("h-3.5 w-3.5", feedback === "dislike" && "fill-current")} />
          </button>
          <button
            type="button"
            onClick={() => {
              void navigator.clipboard.writeText(message.content);
              setCopied(true);
              window.setTimeout(() => setCopied(false), 1500);
            }}
            className="rounded-lg p-1 text-muted-foreground hover:bg-muted"
            aria-label="Sao chép"
          >
            <Copy className="h-3.5 w-3.5" />
          </button>
          <TtsPlayButton
            state={tts.playingId === message.id ? tts.state : "idle"}
            onClick={() =>
              tts.playingId === message.id && tts.state !== "idle"
                ? tts.stop()
                : tts.play(message.id, message.content)
            }
          />
          {copied && <span className="text-xs text-primary">Đã sao chép</span>}
        </div>
        {feedback === "dislike" && (
          <p className="px-1 text-[11px] text-muted-foreground">Cảm ơn phản hồi — mình sẽ cố gắng trả lời tốt hơn.</p>
        )}
        {message.disclaimer && (
          <p className="break-words px-1 text-[11px] leading-relaxed text-muted-foreground">
            {message.disclaimer}
          </p>
        )}
      </div>
    </div>
  );
}

const recordTypeLabels: Record<string, string> = {
  glucose: "Đường huyết",
  blood_pressure: "Huyết áp",
  meal: "Bữa ăn",
  activity: "Vận động",
  medication_note: "Ghi chú dùng thuốc",
};

const recordFieldLabels: Record<string, string> = {
  value: "Giá trị",
  systolic: "Tâm thu",
  diastolic: "Tâm trương",
  description: "Món ăn",
  meal_type: "Loại bữa",
  activity_type: "Hoạt động",
  duration_minutes: "Thời lượng",
  medication_name: "Thuốc",
  measured_at: "Thời gian đo",
  meal_time: "Thời gian ăn",
  activity_time: "Thời gian vận động",
  meal_context: "Hoàn cảnh đo",
};

const enumLabels: Record<string, string> = {
  FASTING: "Lúc đói",
  BEFORE_MEAL: "Trước ăn",
  AFTER_MEAL: "Sau ăn",
  BEFORE_SLEEP: "Trước khi ngủ",
  RANDOM: "Khác",
  breakfast: "Bữa sáng",
  lunch: "Bữa trưa",
  dinner: "Bữa tối",
  snack: "Bữa phụ",
};

const missingFieldPrompts: Record<string, string> = {
  measured_at: "Bạn đo chỉ số này vào lúc nào?",
  meal_context: "Bạn đo đường huyết trong hoàn cảnh nào?",
  meal_type: "Đây là bữa ăn nào?",
  meal_time: "Bạn dùng bữa vào lúc nào?",
  activity_time: "Bạn thực hiện hoạt động này vào lúc nào?",
  medication_id: "Bạn đang nói tới thuốc nào?",
};

function displayDraftValue(field: string, value: unknown, draft: Record<string, unknown>) {
  if (typeof value !== "string" && typeof value !== "number") return null;
  if (field === "value") return `${value} ${String(draft.unit || "")}`.trim();
  if (field === "systolic") return `${value}/${String(draft.diastolic || "")} mmHg`;
  if (field === "duration_minutes") return `${value} phút`;
  if (field.endsWith("_at") || field.endsWith("_time")) {
    const date = new Date(String(value));
    return Number.isFinite(date.getTime()) ? date.toLocaleString("vi-VN") : String(value);
  }
  return enumLabels[String(value)] || String(value);
}

const recordTimeFields = new Set(["measured_at", "meal_time", "activity_time"]);
const vietnamTimeSlots = [6, 9, 12, 15, 18, 21];

function vietnamDateTimeParts(date = new Date()) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Ho_Chi_Minh",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(date);
  const value = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return { date: `${value.year}-${value.month}-${value.day}`, time: `${value.hour}:${value.minute}` };
}

function vietnamLocalToIso(value: string) {
  return new Date(`${value}:00+07:00`).toISOString();
}

function RecordInteractionCard({
  interaction,
  busy,
  status,
  error,
  onAction,
}: {
  interaction: AgentInteraction;
  busy: boolean;
  status?: string;
  error?: string;
  onAction: (
    action:
      | { action: "provide_field"; field: string; value: string }
      | { action: "confirm" | "cancel" },
  ) => void;
}) {
  const recordType = String(interaction.draft.record_type || "");
  const missingField = interaction.field || "";
  const missingFieldLabel = recordFieldLabels[missingField] || "Thông tin bổ sung";
  const missingFieldPrompt =
    missingFieldPrompts[missingField] || `Vui lòng bổ sung ${missingFieldLabel.toLowerCase()}.`;
  const isTimeField = recordTimeFields.has(missingField);
  const vietnamNow = vietnamDateTimeParts();
  const [selectedDateTime, setSelectedDateTime] = useState(`${vietnamNow.date}T${vietnamNow.time}`);
  const availableSlots = vietnamTimeSlots.filter(
    (hour) => `${String(hour).padStart(2, "0")}:00` <= vietnamNow.time,
  );
  const visibleFields = Object.entries(interaction.draft).filter(
    ([field, value]) =>
      value !== null &&
      value !== "" &&
      field !== "record_type" &&
      field !== "unit" &&
      field !== "diastolic" &&
      field !== "note" &&
      field !== "symptoms" &&
      field !== "text" &&
      field !== "medication_id" &&
      field !== "note_type" &&
      field !== "occurred_at" &&
      recordFieldLabels[field],
  );

  return (
    <section
      aria-label="Hoàn tất ghi chép sức khỏe"
      className="overflow-hidden rounded-2xl border border-primary/25 bg-primary/[0.035] shadow-sm"
    >
      <div className="flex items-center gap-2 border-b border-primary/15 px-3 py-2.5">
        <span className="flex h-8 w-8 items-center justify-center rounded-xl bg-primary/10 text-primary">
          {interaction.type === "record_saved" ? (
            <CheckCircle2 className="h-4 w-4" />
          ) : (
            <Save className="h-4 w-4" />
          )}
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-xs font-semibold text-foreground">
            {interaction.type === "record_saved"
              ? "Đã lưu vào hồ sơ"
              : interaction.type === "confirm_record"
                ? "Hoàn tất ghi chép · Xác nhận"
                : "Hoàn tất ghi chép · Bổ sung thông tin"}
          </p>
          <p className="text-[11px] text-muted-foreground">
            {recordTypeLabels[recordType] || "Bản ghi sức khỏe"}
          </p>
        </div>
        {interaction.type === "single_choice" && (
          <span className="shrink-0 rounded-full bg-primary/10 px-2 py-1 text-[10px] font-semibold text-primary">
            Còn 1 bước
          </span>
        )}
      </div>

      <dl className="grid gap-1.5 px-3 py-3 text-xs">
        {visibleFields.map(([field, value]) => {
          const displayed = displayDraftValue(field, value, interaction.draft);
          if (!displayed) return null;
          return (
            <div key={field} className="flex gap-3">
              <dt className="w-28 shrink-0 text-muted-foreground">{recordFieldLabels[field]}</dt>
              <dd className="min-w-0 font-medium text-foreground">{displayed}</dd>
            </div>
          );
        })}
      </dl>

      {status && interaction.type !== "single_choice" && (
        <p className="border-t border-primary/10 px-3 py-2 text-xs text-muted-foreground">
          {status}
        </p>
      )}
      {error && (
        <p role="alert" className="border-t border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
          {error}
        </p>
      )}

      {interaction.type === "single_choice" && (
        <div className="border-t border-primary/15 px-3 py-3">
          <div className="mb-3 flex gap-2 rounded-xl bg-primary/5 px-3 py-2.5 text-foreground">
            <Save className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
            <div>
              <p className="text-xs font-semibold">Thêm một thông tin để ghi chép chính xác</p>
              <p className="mt-0.5 text-xs leading-relaxed">{missingFieldPrompt}</p>
            </div>
          </div>
          {isTimeField ? (
            <div className="space-y-3">
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => {
                    const current = vietnamDateTimeParts();
                    setSelectedDateTime(`${current.date}T${current.time}`);
                  }}
                  className="rounded-full border border-primary/30 bg-background px-3 py-2 text-xs font-medium text-primary hover:bg-primary/10 disabled:opacity-50"
                >
                  Bây giờ
                </button>
                {availableSlots.map((hour) => {
                  const time = `${String(hour).padStart(2, "0")}:00`;
                  return (
                    <button
                      key={hour}
                      type="button"
                      disabled={busy}
                      onClick={() => setSelectedDateTime(`${vietnamNow.date}T${time}`)}
                      className="rounded-full border border-border bg-background px-3 py-2 text-xs font-medium text-foreground hover:bg-muted disabled:opacity-50"
                    >
                      {time}
                    </button>
                  );
                })}
              </div>
              <div className="flex flex-col gap-2 sm:flex-row">
                <input
                  type="datetime-local"
                  value={selectedDateTime}
                  max={`${vietnamNow.date}T${vietnamNow.time}`}
                  onChange={(event) => setSelectedDateTime(event.target.value)}
                  disabled={busy}
                  aria-label="Thời gian theo giờ Việt Nam"
                  className="min-w-0 flex-1 rounded-xl border border-border bg-background px-3 py-2 text-sm text-foreground"
                />
                <button
                  type="button"
                  disabled={busy || !selectedDateTime || !interaction.field}
                  onClick={() =>
                    interaction.field &&
                    onAction({
                      action: "provide_field",
                      field: interaction.field,
                      value: vietnamLocalToIso(selectedDateTime),
                    })
                  }
                  className="rounded-xl bg-primary px-4 py-2 text-xs font-semibold text-primary-foreground disabled:opacity-50"
                >
                  {busy ? "Đang cập nhật..." : "Dùng thời gian này"}
                </button>
              </div>
              <p className="text-[11px] text-muted-foreground">Múi giờ Việt Nam (UTC+7)</p>
              <button
                type="button"
                disabled={busy}
                onClick={() => onAction({ action: "cancel" })}
                className="rounded-full px-3 py-2 text-xs text-muted-foreground hover:bg-muted disabled:opacity-50"
              >
                Không lưu
              </button>
            </div>
          ) : (
          <div className="flex flex-wrap gap-2">
            {interaction.options.length > 0 ? (
              interaction.options.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  disabled={busy || !interaction.field}
                  onClick={() =>
                    interaction.field &&
                    onAction({
                      action: "provide_field",
                      field: interaction.field,
                      value: option.value,
                    })
                  }
                  className="rounded-full border border-primary/30 bg-background px-3 py-2 text-xs font-medium text-primary transition hover:bg-primary/10 disabled:opacity-50"
                >
                  {busy ? "Đang cập nhật..." : option.label}
                </button>
              ))
            ) : (
              <p className="text-xs text-muted-foreground">
                Chưa có lựa chọn phù hợp. Bạn có thể hủy bản ghi này.
              </p>
            )}
            <button
              type="button"
              disabled={busy}
              onClick={() => onAction({ action: "cancel" })}
              className="rounded-full px-3 py-2 text-xs text-muted-foreground hover:bg-muted disabled:opacity-50"
            >
              Không lưu
            </button>
          </div>
          )}
        </div>
      )}

      {interaction.type === "confirm_record" && (
        <div className="border-t border-primary/15 px-3 py-3">
          <p className="mb-2.5 text-xs text-muted-foreground">
            Thông tin đã đủ. Vui lòng kiểm tra lại trước khi lưu vào hồ sơ.
          </p>
          <div className="flex gap-2">
            <button
              type="button"
              disabled={busy}
              onClick={() => onAction({ action: "confirm" })}
              className="flex flex-1 items-center justify-center gap-2 rounded-xl bg-primary px-3 py-2.5 text-xs font-semibold text-primary-foreground disabled:opacity-50"
            >
              {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
              {busy ? "Đang lưu..." : "Xác nhận và lưu"}
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => onAction({ action: "cancel" })}
              className="rounded-xl border border-border bg-background px-4 py-2.5 text-xs font-medium text-muted-foreground disabled:opacity-50"
            >
              Không lưu
            </button>
          </div>
        </div>
      )}
    </section>
  );
}

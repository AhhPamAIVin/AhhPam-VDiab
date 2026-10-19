"use client";

import { MessageSquarePlus, MessagesSquare } from "lucide-react";
import type { ChatSession } from "@/lib/api";
import { cn } from "@/lib/utils";

function relativeTime(iso: string): string {
  const diffMinutes = Math.round((Date.now() - new Date(iso).getTime()) / 60_000);
  if (diffMinutes < 1) return "Vừa xong";
  if (diffMinutes < 60) return `${diffMinutes} phút trước`;
  const diffHours = Math.round(diffMinutes / 60);
  if (diffHours < 24) return `${diffHours} giờ trước`;
  return `${Math.round(diffHours / 24)} ngày trước`;
}

interface AgentChatSidebarProps {
  sessions: ChatSession[];
  activeSessionId: string | null;
  onSelect: (sessionId: string) => void;
  onNewChat: () => void;
  disabled?: boolean;
  className?: string;
}

/** ChatGPT-style left sidebar: "New chat" on top, past sessions stacked
 * below it, click one to restore. Shared by the patient's own AI page and
 * the doctor's per-patient AI tab (see hooks/use-agent-chat-sessions.ts). */
export function AgentChatSidebar({
  sessions,
  activeSessionId,
  onSelect,
  onNewChat,
  disabled,
  className,
}: AgentChatSidebarProps) {
  return (
    <aside className={cn("flex h-full w-64 shrink-0 flex-col overflow-hidden", className)}>
      <button
        type="button"
        onClick={onNewChat}
        disabled={disabled}
        className="mb-3 flex shrink-0 items-center gap-2 rounded-xl border border-dashed border-border px-3 py-2.5 text-sm font-medium text-foreground hover:border-primary/40 hover:bg-accent disabled:opacity-50"
      >
        <MessageSquarePlus className="h-4 w-4" />
        Cuộc trò chuyện mới
      </button>

      <div className="flex-1 space-y-1 overflow-y-auto">
        {sessions.length === 0 && (
          <p className="px-2 py-4 text-center text-xs text-muted-foreground">
            Chưa có cuộc trò chuyện nào
          </p>
        )}
        {sessions.map((session) => (
          <button
            key={session.id}
            type="button"
            onClick={() => onSelect(session.id)}
            disabled={disabled}
            title={session.title ?? "Cuộc trò chuyện"}
            className={cn(
              "flex w-full items-start gap-2 rounded-xl px-3 py-2.5 text-left text-sm transition-colors disabled:opacity-50",
              session.id === activeSessionId
                ? "bg-primary/10 text-primary"
                : "text-foreground hover:bg-accent",
            )}
          >
            <MessagesSquare className="mt-0.5 h-4 w-4 shrink-0" />
            <span className="min-w-0 flex-1">
              <span className="block truncate">{session.title || "Cuộc trò chuyện"}</span>
              <span className="block text-xs text-muted-foreground">
                {relativeTime(session.updated_at)}
              </span>
            </span>
          </button>
        ))}
      </div>
    </aside>
  );
}

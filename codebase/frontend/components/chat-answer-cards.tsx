'use client'

import { useState } from 'react'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { ChatMarkdown } from '@/components/chat-markdown'
import { cn } from '@/lib/utils'

// Below this, a reply reads fine as one block — no pagination chrome for a
// short answer just because it happens to have two paragraphs.
const SHORT_ANSWER_THRESHOLD = 320

/** Splits an AI reply into its own paragraph/list blocks using the markdown's
 * existing blank-line breaks — no backend/prompt change needed to produce
 * "sections", since a multi-paragraph answer already has this structure. */
function splitIntoBlocks(content: string): string[] {
  return content
    .split(/\n{2,}/)
    .map((block) => block.trim())
    .filter(Boolean)
}

// Each block becomes its own standalone markdown list once split apart, so
// it always renders as "1." regardless of its real position — misleading
// next to the card's own "i/N" counter. The counter already conveys
// position, so the leading marker is stripped rather than re-numbered.
function stripLeadingListMarker(text: string): string {
  return text.replace(/^\s*(?:\d+[.)]|[-*•])\s+/, '')
}

/** Renders a long free-form AI chat reply as: a short lead paragraph, a card
 * box that pages through the rest of the answer's blocks with prev/next +
 * dots, and (via the caller's existing disclaimer/limitations rendering,
 * unchanged) a persistent note at the end. Purely a frontend parsing/
 * rendering choice — works on any reply regardless of which backend path
 * produced it, no prompt change required. */
export function ChatAnswerCards({ content }: { content: string }) {
  const [index, setIndex] = useState(0)
  const blocks = splitIntoBlocks(content)

  if (blocks.length <= 1 || content.length < SHORT_ANSWER_THRESHOLD) {
    return <ChatMarkdown content={content} />
  }

  const [lead, ...cards] = blocks
  const current = Math.min(index, cards.length - 1)

  return (
    <div className="space-y-2.5">
      <ChatMarkdown content={lead} />
      <div className="rounded-xl border border-border bg-background/60 p-3">
        <div className="mb-2 flex items-center justify-between">
          <span className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">Chi tiết</span>
          <span className="text-[10.5px] font-semibold text-muted-foreground">
            {current + 1}/{cards.length}
          </span>
        </div>
        <ChatMarkdown content={stripLeadingListMarker(cards[current])} />
        <div className="mt-2.5 flex items-center justify-between border-t border-border pt-2.5">
          <button
            type="button"
            onClick={() => setIndex((value) => Math.max(0, value - 1))}
            disabled={current === 0}
            className="flex h-6 w-6 items-center justify-center rounded-full border border-border disabled:opacity-30"
            aria-label="Thẻ trước"
          >
            <ChevronLeft className="h-3.5 w-3.5 text-primary" />
          </button>
          <div className="flex items-center gap-1.5">
            {cards.map((_, dotIndex) => (
              <button
                key={dotIndex}
                type="button"
                onClick={() => setIndex(dotIndex)}
                aria-label={`Đi tới thẻ ${dotIndex + 1}`}
                className={cn('h-1.5 w-1.5 rounded-full', dotIndex === current ? 'bg-primary' : 'bg-border')}
              />
            ))}
          </div>
          <button
            type="button"
            onClick={() => setIndex((value) => Math.min(cards.length - 1, value + 1))}
            disabled={current === cards.length - 1}
            className="flex h-6 w-6 items-center justify-center rounded-full border border-border disabled:opacity-30"
            aria-label="Thẻ tiếp theo"
          >
            <ChevronRight className="h-3.5 w-3.5 text-primary" />
          </button>
        </div>
      </div>
    </div>
  )
}

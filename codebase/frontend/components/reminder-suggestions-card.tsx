'use client'

import { useEffect, useState } from 'react'
import { Clock3, Loader2 } from 'lucide-react'
import { api, type ReminderSuggestion } from '@/lib/api'
import { useToast } from '@/components/ui/toast'

const hhmm = (value: string) => value.slice(0, 5)

// Suggested new reminder times (Epic 3.3). Nothing changes until the patient
// presses "Đổi giờ"; "Giữ nguyên" stops the same suggestion for 14 days.
export function ReminderSuggestionsCard({ onChanged }: { onChanged?: () => void }) {
  const { showToast } = useToast()
  const [suggestions, setSuggestions] = useState<ReminderSuggestion[]>([])
  const [busy, setBusy] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    api.reminderSuggestions().then((list) => { if (!cancelled) setSuggestions(list) }).catch(() => {})
    return () => { cancelled = true }
  }, [])

  const decide = async (suggestion: ReminderSuggestion, accept: boolean) => {
    setBusy(suggestion.id)
    try {
      await (accept ? api.acceptReminderSuggestion(suggestion.id) : api.declineReminderSuggestion(suggestion.id))
      setSuggestions((list) => list.filter((s) => s.id !== suggestion.id))
      showToast(accept ? `Đã đổi giờ nhắc sang ${hhmm(suggestion.suggested_time)}.` : 'Đã giữ nguyên giờ nhắc.', 'success')
      if (accept) onChanged?.()
    } catch (error) {
      showToast(error instanceof Error ? error.message : 'Không lưu được lựa chọn', 'error')
    } finally {
      setBusy(null)
    }
  }

  if (suggestions.length === 0) return null
  return (
    <section className='space-y-3 rounded-2xl border border-primary/30 bg-accent/40 p-4'>
      <h3 className='flex items-center gap-2 text-sm font-semibold'><Clock3 className='h-4 w-4 text-primary' aria-hidden /> Gợi ý giờ nhắc phù hợp hơn</h3>
      {suggestions.map((suggestion) => (
        <div key={suggestion.id} className='space-y-2 rounded-xl bg-card p-3 text-sm'>
          <p>
            <strong>{suggestion.label}</strong>: {suggestion.late_count}/{suggestion.sample_count} lần gần đây bạn trả lời muộn hơn 30 phút.
            Đổi từ <strong>{hhmm(suggestion.previous_time)}</strong> sang <strong>{hhmm(suggestion.suggested_time)}</strong>?
          </p>
          <div className='flex flex-wrap gap-2'>
            <button type='button' disabled={busy !== null} onClick={() => decide(suggestion, true)} className='inline-flex items-center gap-1 rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground disabled:opacity-60'>
              {busy === suggestion.id && <Loader2 className='h-3.5 w-3.5 animate-spin' />} Đổi giờ
            </button>
            <button type='button' disabled={busy !== null} onClick={() => decide(suggestion, false)} className='rounded-lg border border-border px-3 py-1.5 text-xs disabled:opacity-60'>Giữ nguyên</button>
          </div>
        </div>
      ))}
    </section>
  )
}

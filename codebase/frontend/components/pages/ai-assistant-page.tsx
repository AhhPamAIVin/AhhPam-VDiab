'use client'

import { useState, useRef, useEffect } from 'react'
import { Bot, Send, ThumbsUp, ThumbsDown, Copy, Info, User, Sparkles } from 'lucide-react'
import { aiMessages } from '@/lib/mock-data'
import { cn } from '@/lib/utils'

type Message = {
  id: string
  role: 'user' | 'assistant'
  content: string
  time: string
}

const suggestedQuestions = [
  'Chỉ số đường huyết hôm nay của tôi có ổn không?',
  'Tóm tắt sức khỏe trong 7 ngày qua',
  'Tôi cần chuẩn bị gì trước lần tái khám?',
  'Giải thích thuốc Metformin',
  'Bữa ăn hôm nay có gì cần chú ý?',
]

const AI_RESPONSES: Record<string, string> = {
  'Chỉ số đường huyết hôm nay của tôi có ổn không?':
    'Dựa trên dữ liệu hôm nay, đường huyết buổi sáng lúc đói của bạn là **126 mg/dL** – nằm trong mục tiêu (70–140 mg/dL). Tuy nhiên, trong 3 ngày gần đây có xu hướng cao hơn một chút. Tôi khuyên bạn duy trì chế độ ăn ít carb và vận động nhẹ nhàng sau bữa ăn để giữ chỉ số ổn định.',
  'Tóm tắt sức khỏe trong 7 ngày qua':
    'Tổng hợp 7 ngày qua:\n• **Đường huyết:** Trung bình 133 mg/dL, cao nhất 165 mg/dL (31/7), 5/7 ngày trong mục tiêu.\n• **Huyết áp:** Ổn định, trung bình 126/81 mmHg.\n• **Cân nặng:** Giảm nhẹ 0.7 kg (từ 69.2 xuống 68.5 kg) – tích cực!\n• **Thuốc:** Metformin – tuân thủ tốt. Atorvastatin – cần cải thiện.\n• **Nhận xét:** Nhìn chung sức khỏe ổn định. Cần chú ý thêm đến bữa tối.',
  'Tôi cần chuẩn bị gì trước lần tái khám?':
    'Trước buổi tái khám với BS. Trần Minh (15/08/2026), bạn nên:\n1. **Nhịn ăn 8–10 tiếng** trước khi đo đường huyết lúc đói vào buổi sáng hôm khám.\n2. **Mang theo nhật ký đường huyết** của 2–4 tuần qua.\n3. **Danh sách thuốc** đang dùng và bất kỳ tác dụng phụ nào gặp phải.\n4. Các câu hỏi bạn muốn hỏi bác sĩ (bạn có thể nhờ tôi giúp chuẩn bị).',
  'Giải thích thuốc Metformin':
    '**Metformin** là thuốc hàng đầu điều trị đái tháo đường type 2.\n\n**Cơ chế:** Giảm lượng đường gan sản xuất và tăng độ nhạy insulin của tế bào.\n\n**Lưu ý khi dùng:**\n• Uống **sau bữa ăn** để giảm tác dụng phụ tiêu hóa.\n• Không nghiền hoặc nhai viên.\n• Uống đủ nước.\n\n**Không nên tự ý ngừng hoặc thay đổi liều.** Hãy trao đổi với BS. Trần Minh nếu bạn gặp khó chịu khi dùng thuốc.',
  'Bữa ăn hôm nay có gì cần chú ý?':
    'Dựa trên dữ liệu bữa ăn hôm nay:\n\n**Bữa sáng** (cháo yến mạch): Rất tốt – ít carb, nhiều chất xơ, GI thấp.\n\n**Bữa trưa** (cơm gạo lứt + cá hồi): Phù hợp – tổng carb 65g ở mức chấp nhận được.\n\n**Gợi ý cho bữa tối:** Ưu tiên rau xanh, protein nạc (cá, đậu hũ). Hạn chế cơm trắng và thức ăn chiên.\n\nLượng carb tổng ngày khoảng 107g – tốt cho mức kiểm soát đường huyết của bạn.',
}

function TypingIndicator() {
  return (
    <div className="flex items-end gap-2 mb-3">
      <div className="w-7 h-7 rounded-full bg-primary/20 flex items-center justify-center flex-shrink-0">
        <Bot className="w-4 h-4 text-primary" />
      </div>
      <div className="bg-card border border-border rounded-2xl rounded-bl-sm px-4 py-3">
        <div className="flex gap-1">
          {[0, 1, 2].map((i) => (
            <div key={i} className="w-1.5 h-1.5 bg-muted-foreground rounded-full animate-bounce" style={{ animationDelay: `${i * 0.15}s` }} />
          ))}
        </div>
      </div>
    </div>
  )
}

function MessageBubble({ msg }: { msg: Message }) {
  const [copied, setCopied] = useState(false)
  const [feedback, setFeedback] = useState<'up' | 'down' | null>(null)

  const handleCopy = () => {
    navigator.clipboard.writeText(msg.content)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  const formatContent = (text: string) => {
    return text.split('\n').map((line, i) => {
      const formatted = line
        .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
        .replace(/^•\s/, '• ')
      return <p key={i} className="text-sm leading-relaxed" dangerouslySetInnerHTML={{ __html: formatted || '&nbsp;' }} />
    })
  }

  if (msg.role === 'user') {
    return (
      <div className="flex justify-end mb-3">
        <div className="max-w-[80%]">
          <div className="bg-primary text-primary-foreground px-4 py-3 rounded-2xl rounded-br-sm">
            <p className="text-sm leading-relaxed">{msg.content}</p>
          </div>
          <p className="text-right text-xs text-muted-foreground mt-1 px-1">{msg.time}</p>
        </div>
      </div>
    )
  }

  return (
    <div className="flex items-end gap-2 mb-3">
      <div className="w-7 h-7 rounded-full bg-primary/20 flex items-center justify-center flex-shrink-0 mb-6">
        <Bot className="w-4 h-4 text-primary" />
      </div>
      <div className="max-w-[85%]">
        <div className="bg-card border border-border rounded-2xl rounded-bl-sm px-4 py-3 space-y-1">
          {formatContent(msg.content)}
        </div>
        <div className="flex items-center gap-2 mt-1.5 px-1">
          <p className="text-xs text-muted-foreground flex-1">{msg.time}</p>
          <button onClick={handleCopy} className="p-1 rounded-lg hover:bg-muted text-muted-foreground hover:text-foreground transition-colors">
            <Copy className="w-3 h-3" />
          </button>
          {copied && <span className="text-xs text-[#43B5AE]">Đã sao chép</span>}
          <button onClick={() => setFeedback('up')} className={cn('p-1 rounded-lg hover:bg-muted transition-colors', feedback === 'up' ? 'text-[#43B5AE]' : 'text-muted-foreground')}>
            <ThumbsUp className="w-3 h-3" />
          </button>
          <button onClick={() => setFeedback('down')} className={cn('p-1 rounded-lg hover:bg-muted transition-colors', feedback === 'down' ? 'text-red-500' : 'text-muted-foreground')}>
            <ThumbsDown className="w-3 h-3" />
          </button>
        </div>
      </div>
    </div>
  )
}

export function AIAssistantPage() {
  const [messages, setMessages] = useState<Message[]>(aiMessages.map((m) => ({ ...m })))
  const [input, setInput] = useState('')
  const [typing, setTyping] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, typing])

  const now = () => new Date().toLocaleTimeString('vi', { hour: '2-digit', minute: '2-digit' })

  const sendMessage = async (content: string) => {
    if (!content.trim()) return
    const userMsg: Message = { id: Date.now().toString(), role: 'user', content: content.trim(), time: now() }
    setMessages((prev) => [...prev, userMsg])
    setInput('')
    setTyping(true)
    await new Promise((r) => setTimeout(r, 1200))
    setTyping(false)
    const reply = AI_RESPONSES[content.trim()] ?? 'Cảm ơn bạn đã hỏi! Đây là một chủ đề thú vị. Dựa trên dữ liệu sức khỏe hiện tại của bạn, tôi sẽ cố gắng hỗ trợ tốt nhất có thể. Hãy đặt câu hỏi cụ thể hơn để tôi có thể đưa ra thông tin chính xác hơn nhé.'
    setMessages((prev) => [...prev, { id: (Date.now() + 1).toString(), role: 'assistant', content: reply, time: now() }])
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing && !(e.keyCode === 229)) {
      e.preventDefault()
      sendMessage(input)
    }
  }

  return (
    <div className="flex flex-col h-[calc(100vh-8rem)] max-w-3xl mx-auto">
      {/* Disclaimer */}
      <div className="flex items-center gap-2 p-3 bg-yellow-50 rounded-2xl border border-yellow-200 mb-4 flex-shrink-0">
        <Info className="w-4 h-4 text-yellow-600 flex-shrink-0" />
        <p className="text-xs text-yellow-700">VDiab AI cung cấp thông tin hỗ trợ và <strong>không thay thế tư vấn của bác sĩ</strong>.</p>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto space-y-1 mb-4">
        {messages.map((msg) => (
          <MessageBubble key={msg.id} msg={msg} />
        ))}
        {typing && <TypingIndicator />}
        <div ref={bottomRef} />
      </div>

      {/* Suggested questions */}
      {messages.length <= 1 && (
        <div className="mb-4 flex-shrink-0">
          <p className="text-xs font-medium text-muted-foreground mb-2 flex items-center gap-1.5">
            <Sparkles className="w-3.5 h-3.5" /> Câu hỏi gợi ý
          </p>
          <div className="flex flex-col gap-2">
            {suggestedQuestions.map((q) => (
              <button
                key={q}
                onClick={() => sendMessage(q)}
                className="text-left px-4 py-3 rounded-2xl border border-border bg-card hover:bg-muted hover:border-primary/30 text-sm text-foreground transition-colors"
              >
                {q}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Input */}
      <div className="flex-shrink-0 flex items-end gap-2 bg-card rounded-2xl border border-border p-2">
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Nhập câu hỏi về sức khỏe của bạn..."
          rows={1}
          className="flex-1 px-3 py-2 bg-transparent text-sm text-foreground placeholder:text-muted-foreground outline-none resize-none max-h-28"
          style={{ fieldSizing: 'content' } as React.CSSProperties}
        />
        <button
          onClick={() => sendMessage(input)}
          disabled={!input.trim() || typing}
          className="w-9 h-9 rounded-xl bg-primary text-primary-foreground flex items-center justify-center hover:bg-[#2D9D97] transition-colors disabled:opacity-40 flex-shrink-0"
          aria-label="Gửi"
        >
          <Send className="w-4 h-4" />
        </button>
      </div>
    </div>
  )
}

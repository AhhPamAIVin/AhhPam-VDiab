"use client";

import { useEffect, useId, useRef, useState } from "react";
import { AlertTriangle, Bot, ChevronDown, HelpCircle, Info, Loader2, RefreshCw, TrendingUp } from "lucide-react";
import { api, type AgentSummaryResponse, type GlucoseTrend, type KeyMetric } from "@/lib/api";
import { cn } from "@/lib/utils";

const toneStyles: Record<KeyMetric["tone"], string> = {
  good: "bg-[#D7F2EE] text-[#238D87] dark:bg-teal-900/60 dark:text-teal-200",
  warning: "bg-amber-100 text-amber-800 dark:bg-amber-900/60 dark:text-amber-200",
  critical: "bg-red-100 text-red-800 dark:bg-red-900/60 dark:text-red-200",
};

// Bolds numbers/percentages already present in the AI's own text — a display
// transform only, never adds, removes, or changes a number.
function highlightNumbers(text: string) {
  return text.split(/(\d+(?:[.,]\d+)?\s?%?)/g).map((part, index) =>
    /\d/.test(part) ? (
      <strong key={index} className="font-semibold text-foreground">
        {part}
      </strong>
    ) : (
      part
    ),
  );
}

// Splits off the first sentence for a short headline — display-only, never
// changes the AI's own text. Requires whitespace after the punctuation so a
// decimal like "202.9" doesn't get mistaken for a sentence break.
function splitFirstSentence(text: string): [string, string] {
  const match = text.match(/^(.*?[.!?])\s+([\s\S]*)$/);
  if (!match) return [text, ""];
  return [match[1], match[2]];
}

function SummaryHeadline({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  const [first, rest] = splitFirstSentence(text);

  if (!rest) return <p className="text-sm leading-relaxed text-foreground">{text}</p>;

  return (
    <div>
      <p className="text-sm leading-relaxed text-foreground">{first}</p>
      {open && <p className="mt-1.5 text-sm leading-relaxed text-foreground">{rest}</p>}
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="mt-1 flex items-center gap-1 text-xs font-semibold text-primary"
      >
        {open ? "Thu gọn" : "Xem thêm"}
        <ChevronDown className={cn("h-3 w-3 transition-transform", open && "rotate-180")} />
      </button>
    </div>
  );
}

// The agent's own trends[] always leads with a plain restatement of a number
// already shown in a stat tile (see synthesize_summary.py's _build_trends —
// unmodified, this just filters its output for display). Matched on the
// fixed phrasing those deterministic templates always produce, patient and
// doctor tone alike; a real pattern bullet (e.g. the weekday/weekend one)
// never matches any of these and passes through untouched.
function isRedundantTrend(text: string): boolean {
  return (
    text.includes("trung bình của bạn trong khoảng thời gian này là") ||
    text.includes("Đường huyết trung bình toàn kỳ:") ||
    (text.includes("vượt ngưỡng khuyến cáo") && text.includes("số lần đo")) ||
    text.includes("sau ăn (AFTER_MEAL) trung bình") ||
    text.includes("lúc đói nằm trong khoảng mục tiêu") ||
    (text.includes("lúc đói trung bình") && text.includes("trong khoảng mục tiêu"))
  );
}

// Composed from real first-half-vs-second-half numbers (summary_metrics.py),
// not LLM prose — deliberately stops at direction + a generic "trao đổi với
// bác sĩ" nudge rather than asserting any specific health outcome.
function buildGlucoseTrendBullet(trend: GlucoseTrend): string {
  if (trend.direction === "stable") {
    return `→ Đường huyết trung bình khá ổn định trong kỳ (nửa đầu ${trend.first_half_avg} mg/dL, nửa sau ${trend.second_half_avg} mg/dL).`;
  }
  const isUp = trend.direction === "up";
  const arrow = isUp ? "↑" : "↓";
  const verb = isUp ? "tăng" : "giảm";
  const preposition = isUp ? "lên" : "xuống";
  return (
    `${arrow} Đường huyết trung bình ${verb} ${Math.abs(trend.delta)} mg/dL so với nửa đầu kỳ ` +
    `(từ ${trend.first_half_avg} ${preposition} ${trend.second_half_avg} mg/dL) — nếu xu hướng này tiếp diễn, ` +
    "nên trao đổi với bác sĩ điều trị."
  );
}

function BulletGroup({
  icon, label, labelColor, items,
}: {
  icon: React.ReactNode;
  label: string;
  labelColor: string;
  items: string[];
}) {
  const [open, setOpen] = useState(false);
  const [first, ...rest] = items.filter(Boolean);
  if (!first) return null;

  return (
    <div className="border-t border-border pt-3">
      <div className="mb-1.5 flex items-center gap-1.5">
        {icon}
        <span className={cn("text-[11px] font-semibold uppercase tracking-wide", labelColor)}>{label}</span>
      </div>
      <p className="text-sm leading-relaxed text-foreground">{highlightNumbers(first)}</p>
      {open && rest.map((item, index) => (
        <p key={index} className="mt-1.5 text-sm leading-relaxed text-foreground">{highlightNumbers(item)}</p>
      ))}
      {rest.length > 0 && (
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          className="mt-1.5 flex items-center gap-1 text-xs font-semibold text-primary"
        >
          {open ? "Thu gọn" : `Xem thêm (${rest.length})`}
          <ChevronDown className={cn("h-3 w-3 transition-transform", open && "rotate-180")} />
        </button>
      )}
    </div>
  );
}

interface AgentSummaryContentProps {
  summary: AgentSummaryResponse;
  keyMetrics?: KeyMetric[];
  glucoseTrend?: GlucoseTrend | null;
  /** Extra content (e.g. "Hỏi AI" / "Xem phân tích" buttons) rendered above the disclaimer. */
  actions?: React.ReactNode;
}

/** Pure rendering, no fetching — for a caller that already has the data on
 * hand (e.g. a chat message that fetched it once for the reply). */
export function AgentSummaryContent({ summary, keyMetrics = [], glucoseTrend, actions }: AgentSummaryContentProps) {
  const trendItems = [
    ...(glucoseTrend ? [buildGlucoseTrendBullet(glucoseTrend)] : []),
    ...summary.trends.filter((item) => !isRedundantTrend(item)),
  ];

  return (
    <div className="space-y-3 text-sm">
      {keyMetrics.length > 0 && (
        <div className="grid grid-cols-3 gap-2">
          {keyMetrics.map((metric) => (
            <div key={metric.label} className={cn("rounded-xl px-2.5 py-2", toneStyles[metric.tone])}>
              <div className="text-[9.5px] font-semibold uppercase tracking-wide opacity-80">{metric.label}</div>
              <div className="mt-0.5 text-lg font-bold leading-none">
                {metric.value}
                {metric.unit && <span className="ml-0.5 text-[10px] font-semibold">{metric.unit}</span>}
              </div>
            </div>
          ))}
        </div>
      )}
      <SummaryHeadline text={summary.summary} />
      <BulletGroup
        icon={<TrendingUp className="h-3.5 w-3.5 text-primary" />}
        label="Xu hướng"
        labelColor="text-muted-foreground"
        items={trendItems}
      />
      <BulletGroup
        icon={<AlertTriangle className="h-3.5 w-3.5 text-[#F7A928]" />}
        label="Cần lưu ý"
        labelColor="text-[#B9790A]"
        items={summary.attention_points}
      />
      <BulletGroup
        icon={<HelpCircle className="h-3.5 w-3.5 text-primary" />}
        label="Câu hỏi gợi ý cho bác sĩ"
        labelColor="text-muted-foreground"
        items={summary.suggested_doctor_questions}
      />
      {summary.limitations.length > 0 && (
        <div className="rounded-xl bg-muted p-3 text-xs text-muted-foreground">
          <strong>Giới hạn dữ liệu:</strong> {summary.limitations.join(" · ")}
        </div>
      )}
      {actions}
      <div className="flex gap-2 rounded-xl bg-yellow-50 p-3">
        <Info className="h-4 w-4 shrink-0 text-yellow-600" />
        <p className="text-xs leading-relaxed text-yellow-700">{summary.disclaimer}</p>
      </div>
    </div>
  );
}

interface AgentSummaryCardProps {
  patientId?: string;
  days?: number;
  title?: string;
  subtitle?: string;
  /** Extra content (e.g. "Hỏi AI" / "Xem phân tích" buttons) rendered above the disclaimer. */
  actions?: React.ReactNode;
  /** Allows the card body to be expanded/collapsed. */
  collapsible?: boolean;
  /** Keeps the card permanently expanded on desktop. */
  mobileOnlyCollapse?: boolean;
  defaultOpen?: boolean;
}

/** Fetches both the (unmodified) AI Summary agent response and the separate,
 * additive key-metrics overlay (codebase/backend/agent/summary_metrics.py),
 * then renders them via AgentSummaryContent. Either call failing degrades
 * that part only — a key-metrics failure never blocks the prose/bullets. */
export function AgentSummaryCard({
  patientId,
  days = 7,
  title = "AI Summary",
  subtitle,
  actions,
  collapsible = false,
  mobileOnlyCollapse = false,
  defaultOpen = true,
}: AgentSummaryCardProps) {
  const [summary, setSummary] = useState<AgentSummaryResponse | null>(null);
  const [keyMetrics, setKeyMetrics] = useState<KeyMetric[]>([]);
  const [glucoseTrend, setGlucoseTrend] = useState<GlucoseTrend | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(defaultOpen);
  const contentId = useId();
  const cardRef = useRef<HTMLElement>(null);

  const toggleOpen = () => {
    if (!collapsible) return;
    if (mobileOnlyCollapse && !window.matchMedia("(max-width: 1023px)").matches) return;
    const nextOpen = !open;
    setOpen(nextOpen);
    if (nextOpen) {
      window.setTimeout(() => {
        cardRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
      }, 300);
    }
  };

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      setSummary(await api.agentSummary(days, patientId));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Không thể tạo AI Summary.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    api
      .agentSummaryKeyMetrics(days, patientId)
      .then((result) => {
        setKeyMetrics(result.key_metrics);
        setGlucoseTrend(result.glucose_trend);
      })
      .catch(() => {
        setKeyMetrics([]);
        setGlucoseTrend(null);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [patientId, days]);

  const periodLabel = days === 30 ? "1 tháng" : `${days} ngày`;

  return (
    <section ref={cardRef} className="rounded-2xl border border-border bg-card p-5">
      <div className={cn(
        "flex items-center justify-between gap-3 transition-[margin] duration-300 ease-out",
        (!collapsible || open) && "mb-4",
        mobileOnlyCollapse && "lg:mb-4",
      )}>
        <button
          type="button"
          onClick={toggleOpen}
          aria-expanded={collapsible ? open : undefined}
          aria-controls={collapsible ? contentId : undefined}
          className={cn(
            "flex min-w-0 flex-1 items-center gap-3 text-left",
            !collapsible && "cursor-default",
            mobileOnlyCollapse && "lg:cursor-default",
          )}
        >
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-accent">
            <Bot className="h-5 w-5 text-[#43B5AE]" />
          </div>
          <div>
            <h3 className="font-semibold text-foreground">{title}</h3>
            <p className="text-[11px] text-muted-foreground">
              {subtitle ?? `Tổng hợp ${periodLabel} gần nhất · Không phải chẩn đoán y khoa`}
            </p>
          </div>
          {collapsible && (
            <ChevronDown className={cn(
              "ml-auto h-5 w-5 shrink-0 text-muted-foreground transition-transform duration-300 ease-out",
              open && "rotate-180",
              mobileOnlyCollapse && "lg:hidden",
            )} />
          )}
        </button>
        <button
          type="button"
          onClick={() => void load()}
          disabled={loading}
          className="rounded-lg p-2 text-muted-foreground hover:bg-muted disabled:opacity-50"
          aria-label="Tạo lại tóm tắt"
        >
          <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />
        </button>
      </div>
      <div
        id={contentId}
        className={cn(
          collapsible && "grid transition-[grid-template-rows,opacity] duration-300 ease-out",
          collapsible && (open ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0"),
          mobileOnlyCollapse && "lg:grid-rows-[1fr] lg:opacity-100",
        )}
      >
        <div className={cn(collapsible && "overflow-hidden")}>
        {loading && (
          <div className="flex items-center justify-center gap-2 py-8 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            Đang phân tích dữ liệu...
          </div>
        )}
        {!loading && error && (
          <div role="alert" className="flex items-start gap-2 rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-700">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            {error}
          </div>
        )}
        {!loading && !error && summary && (
          <AgentSummaryContent summary={summary} keyMetrics={keyMetrics} glucoseTrend={glucoseTrend} actions={actions} />
        )}
        </div>
      </div>
    </section>
  );
}

"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useGesture } from "@use-gesture/react";
import {
  Area,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { GlucoseRecord } from "@/lib/api";
import { cn } from "@/lib/utils";

export type GlucoseRange = "24h" | "7d" | "14d" | "1m";

export const RANGE_DAYS: Record<GlucoseRange, number> = { "24h": 1, "7d": 7, "14d": 14, "1m": 30 };
export const RANGE_LABELS: Record<GlucoseRange, string> = {
  "24h": "Trong ngày",
  "7d": "7 ngày",
  "14d": "14 ngày",
  "1m": "1 tháng",
};

const DAY_MS = 24 * 60 * 60 * 1000;
// A pinch/scroll session can't zoom in past this — a narrower window just
// shows empty air between the handful of readings a real patient logs in a
// few hours.
const MIN_SPAN_MS = 3 * 60 * 60 * 1000;

function rangeWindow(range: GlucoseRange): [number, number] {
  const end = new Date();
  const start = new Date(end);
  if (range === "24h") {
    start.setTime(end.getTime() - DAY_MS);
  } else {
    // Longer ranges use full calendar days through today.
    end.setHours(23, 59, 59, 999);
    start.setTime(end.getTime());
    start.setDate(start.getDate() - RANGE_DAYS[range] + 1);
    start.setHours(0, 0, 0, 0);
  }
  return [start.getTime(), end.getTime()];
}

function recordsInWindow(records: GlucoseRecord[], start: number, end: number) {
  return records
    .filter((record) => {
      const time = new Date(record.measured_at).getTime();
      return Number.isFinite(time) && Number.isFinite(record.value) && time >= start && time <= end;
    })
    .sort((a, b) => new Date(a.measured_at).getTime() - new Date(b.measured_at).getTime());
}

export function glucoseRecordsInRange(records: GlucoseRecord[], range: GlucoseRange) {
  const [start, end] = rangeWindow(range);
  return recordsInWindow(records, start, end);
}

// Clamp a candidate [start, end] window to the given bounds (the selected
// preset's own fixed window, e.g. exactly the last 14 days) — span first
// (can't zoom out past the bounds), then position (sliding the whole window
// back inside the bounds preserves the span, same as a map not scrolling
// past the edge of its loaded tiles).
function clampWindow([start, end]: [number, number], [boundMin, boundMax]: [number, number]): [number, number] {
  const boundSpan = Math.max(boundMax - boundMin, MIN_SPAN_MS);
  const span = Math.min(Math.max(end - start, MIN_SPAN_MS), boundSpan);
  let s = start;
  let e = s + span;
  if (s < boundMin) {
    s = boundMin;
    e = s + span;
  }
  if (e > boundMax) {
    e = boundMax;
    s = e - span;
  }
  return [s, e];
}

// A zoom/pan step can otherwise land squarely inside a real gap between two
// readings — nothing wrong with the data, but an interactive chart showing
// literally nothing (no line, no dots, not even an axis label to explain
// why) reads as broken. Rather than detect and message that after the fact,
// stop it from happening: if the candidate window would contain zero
// points, extend it just enough to reach whichever actual reading (before
// or after) is closest, then re-clamp to bounds. `timestamps` only needs to
// be sorted for the nearest-neighbor scan below to be correct.
function ensureNonEmptyWindow(
  [start, end]: [number, number],
  timestamps: number[],
  bounds: [number, number],
): [number, number] {
  if (!timestamps.length) return [start, end];
  const hasPoint = timestamps.some((t) => t >= start && t <= end);
  if (hasPoint) return [start, end];

  let nearestBefore = -Infinity;
  let nearestAfter = Infinity;
  for (const t of timestamps) {
    if (t < start && t > nearestBefore) nearestBefore = t;
    if (t > end && t < nearestAfter) nearestAfter = t;
  }
  const distBefore = start - nearestBefore;
  const distAfter = nearestAfter - end;
  if (nearestBefore !== -Infinity && distBefore <= distAfter) {
    return clampWindow([nearestBefore, end], bounds);
  }
  if (nearestAfter !== Infinity) {
    return clampWindow([start, nearestAfter], bounds);
  }
  return [start, end];
}

function formatWindowLabel(start: number, end: number): string {
  const spanDays = (end - start) / DAY_MS;
  const fmt = new Intl.DateTimeFormat("vi-VN", { day: "2-digit", month: "2-digit" });
  if (spanDays <= 1.5) {
    const time = new Intl.DateTimeFormat("vi-VN", { hour: "2-digit", minute: "2-digit" });
    return `${time.format(start)} – ${time.format(end)}, ${fmt.format(end)}`;
  }
  return `${fmt.format(start)} – ${fmt.format(end)}`;
}

type ThresholdStatus = GlucoseRecord["threshold_status"];

// One point per reading, at its real x-position (time-of-day, not just
// date) — every range (24h through 1m) renders this way, relying on the
// chart's own pinch/scroll/drag zoom to manage density at wide zoom levels
// rather than pre-aggregating into daily min/max. Carries the AI-predicted
// band (predicted_min_mg_dl/predicted_max_mg_dl) per reading rather than a
// fixed target, since that band moves with meal context and history (see
// health/threshold_features.py) — a flat reference line would misrepresent it.
function perReadingData(records: GlucoseRecord[]) {
  return records.map((record) => {
    const hasBand = record.predicted_min_mg_dl != null && record.predicted_max_mg_dl != null;
    const flagged = record.threshold_status === "WATCH" || record.threshold_status === "DANGER";
    return {
      timestamp: new Date(record.measured_at).getTime(),
      value: record.value,
      status: record.threshold_status,
      predictedMin: record.predicted_min_mg_dl,
      predictedMax: record.predicted_max_mg_dl,
      band: hasBand ? [record.predicted_min_mg_dl as number, record.predicted_max_mg_dl as number] : null,
      // Two readings taken close enough together can land within a pixel of
      // each other on the time axis — whichever dot paints last would hide
      // the other's color. This duplicate series is declared after the main
      // "value" line below, so it always paints on top, guaranteeing a
      // flagged reading's dot is never covered by a neighboring plain one.
      flaggedValue: flagged ? record.value : null,
    };
  });
}

function statusFlagColor(status: ThresholdStatus): string | null {
  if (status === "DANGER") return "#DC2626";
  if (status === "WATCH") return "#D97706";
  return null;
}

function statusLabel(status: ThresholdStatus): string | null {
  if (status === "DANGER") return "Nguy hiểm";
  if (status === "WATCH") return "Cần theo dõi (AI)";
  return null;
}

// Custom dot: same size as every other point (r=DOT_RADIUS) — a flagged
// reading is distinguished only by fill color (solid amber/red instead of
// the line's own direction color), never by size. Filling solid rather than
// drawing a thin ring also means a flagged point can never visually shrink
// when a same-size unflagged point from another close-in-time reading
// happens to overlap it — same color, same size, no thin stroke to get
// obscured.
const DOT_RADIUS = 4.5; // 1.5x the original r=3

function makeThresholdDot(baseFill: string) {
  return function ThresholdDot(props: { cx?: number; cy?: number; payload?: Record<string, unknown> }) {
    const { cx, cy, payload } = props;
    if (cx == null || cy == null) return <g />;
    const flag = statusFlagColor((payload?.status as ThresholdStatus) ?? null);
    return <circle cx={cx} cy={cy} r={DOT_RADIUS} fill={flag ?? baseFill} stroke="var(--card)" strokeWidth={2} />;
  };
}

function axisDate(timestamp: number, longRange: boolean) {
  return new Intl.DateTimeFormat("vi-VN", {
    day: "2-digit",
    month: "2-digit",
    ...(longRange ? {} : { weekday: "short" as const }),
  }).format(new Date(timestamp));
}

function axisDateTime(timestamp: number) {
  return new Intl.DateTimeFormat("vi-VN", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(timestamp));
}

export function GlucoseTrendChart({
  records,
  range,
  onRangeChange,
  availableRanges,
  className,
}: {
  records: GlucoseRecord[];
  range: GlucoseRange;
  onRangeChange: (range: GlucoseRange) => void;
  availableRanges?: GlucoseRange[];
  className?: string;
}) {
  const visibleRanges = availableRanges ?? (Object.keys(RANGE_LABELS) as GlucoseRange[]);
  // The button-selected range is a fixed calendar window (e.g. 14 ngày =
  // the last 14 calendar days) — a pinch/scroll/drag session on the chart
  // below can zoom into a narrower slice of *that same window* to make
  // individual readings legible, and pan within it, but never past its
  // edges. It stays the AI summary's day count elsewhere too
  // (patient-dashboard.tsx / patient-detail-page.tsx read RANGE_DAYS[range]
  // directly), which has no reason to track exactly what sub-slice is
  // currently in view.
  //
  // Both edges are then pulled in to the actual earliest/latest reading
  // inside that calendar window, same as the old dataMin/dataMax-fit axis
  // did — a patient who hasn't logged in the last few days would otherwise
  // show a wide trailing gap of empty axis between their last real point
  // and "today" (a visual regression vs. before: the latest point would
  // look like it had "drifted" left off the edge instead of anchoring it).
  const presetWindow = useMemo<[number, number]>(() => {
    const [calStart, calEnd] = rangeWindow(range);
    const times = records
      .map((record) => new Date(record.measured_at).getTime())
      .filter((time) => Number.isFinite(time) && time >= calStart && time <= calEnd);
    if (!times.length) return [calStart, calEnd];
    return [Math.max(calStart, Math.min(...times)), Math.min(calEnd, Math.max(...times))];
  }, [range, records]);
  // `data` covers the whole *preset* window (e.g. all 14 days), not just the
  // currently panned/zoomed-into slice, and only changes when the preset or
  // the underlying records do — never on every pointer-move. XAxis below
  // then does the actual windowing via its own `domain`. This keeps the
  // array Recharts receives referentially stable across a drag/pinch
  // session, so each frame is a cheap re-scale + clip instead of Recharts
  // re-deriving its internal layout from a brand new array every time —
  // that per-frame array churn (plus each series re-animating into place)
  // is what made dragging feel stuttery before.
  const presetRecords = useMemo(() => recordsInWindow(records, presetWindow[0], presetWindow[1]), [records, presetWindow]);
  const data = useMemo(() => perReadingData(presetRecords), [presetRecords]);
  const dataTimestamps = useMemo(() => data.map((point) => point.timestamp), [data]);
  const dataTimestampsRef = useRef(dataTimestamps);
  dataTimestampsRef.current = dataTimestamps;

  const [manualWindow, setManualWindow] = useState<[number, number] | null>(null);
  const [windowStart, windowEnd] = manualWindow ?? presetWindow;
  // Gesture handlers below read/write this on every single pointer-move
  // event (many per frame on a fast trackpad) so each increment is computed
  // against the true latest window, not a stale one from before the last
  // commit — see commitWindow.
  const windowRef = useRef<[number, number]>([windowStart, windowEnd]);
  windowRef.current = [windowStart, windowEnd];

  const containerRef = useRef<HTMLDivElement>(null);
  const pinchStartSpan = useRef(0);
  const rafIdRef = useRef<number | null>(null);

  // Recharts re-renders (and, per-series, re-animates) the whole SVG on
  // every setManualWindow — doing that once per raw pointer-move event
  // (which can fire faster than the display refreshes) is what made
  // dragging feel stuttery. windowRef is updated synchronously so gesture
  // math always sees the latest value, but the React state write — the
  // expensive part — is coalesced to at most once per animation frame.
  // ensureNonEmptyWindow keeps a zoom/pan step from ever landing on a
  // window with zero readings in it (see its own docstring).
  const commitWindow = useCallback(
    (next: [number, number]) => {
      const adjusted = ensureNonEmptyWindow(next, dataTimestampsRef.current, presetWindow);
      windowRef.current = adjusted;
      if (rafIdRef.current != null) return;
      rafIdRef.current = requestAnimationFrame(() => {
        rafIdRef.current = null;
        setManualWindow(windowRef.current);
      });
    },
    [presetWindow],
  );

  useEffect(() => {
    return () => {
      if (rafIdRef.current != null) cancelAnimationFrame(rafIdRef.current);
    };
  }, []);

  const fractionAtClientX = useCallback((clientX: number) => {
    const rect = containerRef.current?.getBoundingClientRect();
    if (!rect || rect.width === 0) return 0.5;
    return Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
  }, []);

  const zoomAtFraction = useCallback(
    (factor: number, fraction: number) => {
      const [start, end] = windowRef.current;
      const span = end - start;
      const anchor = start + span * fraction;
      const newSpan = span * factor;
      const newStart = anchor - fraction * newSpan;
      commitWindow(clampWindow([newStart, newStart + newSpan], presetWindow));
    },
    [presetWindow, commitWindow],
  );

  const panByFraction = useCallback(
    (deltaFraction: number) => {
      const [start, end] = windowRef.current;
      const shift = (end - start) * deltaFraction;
      commitWindow(clampWindow([start + shift, end + shift], presetWindow));
    },
    [presetWindow, commitWindow],
  );

  useGesture(
    {
      onWheel: ({ event, delta: [dx, dy] }) => {
        if (!dx && !dy) return;
        event.preventDefault();
        // A trackpad's two-finger swipe arrives here too (as a wheel event,
        // never a drag) — horizontal-dominant motion pans, same as dragging;
        // vertical-dominant zooms, same as a mouse wheel/pinch.
        if (Math.abs(dx) > Math.abs(dy)) {
          const width = containerRef.current?.getBoundingClientRect().width || 1;
          panByFraction(dx / width);
        } else {
          zoomAtFraction(dy > 0 ? 1.15 : 1 / 1.15, fractionAtClientX(event.clientX));
        }
      },
      onDrag: ({ event, delta: [dx], pinching }) => {
        if (pinching) return;
        event.preventDefault();
        const width = containerRef.current?.getBoundingClientRect().width || 1;
        panByFraction(-dx / width);
      },
      onPinchStart: () => {
        pinchStartSpan.current = windowRef.current[1] - windowRef.current[0];
      },
      onPinch: ({ event, offset: [scale], origin: [ox] }) => {
        event.preventDefault();
        const fraction = fractionAtClientX(ox);
        const [start, end] = windowRef.current;
        const anchor = start + (end - start) * fraction;
        const newSpan = pinchStartSpan.current / scale;
        const newStart = anchor - fraction * newSpan;
        commitWindow(clampWindow([newStart, newStart + newSpan], presetWindow));
      },
    },
    { target: containerRef, eventOptions: { passive: false } },
  );

  // Cancels any pending rAF commit first — otherwise a gesture settling right
  // as the user clicks reset/a preset button could re-apply its stale value
  // a frame after this call, undoing it.
  const clearManualWindow = useCallback(() => {
    if (rafIdRef.current != null) {
      cancelAnimationFrame(rafIdRef.current);
      rafIdRef.current = null;
    }
    setManualWindow(null);
  }, []);
  const isZoomed = manualWindow !== null;

  const spanDays = (windowEnd - windowStart) / DAY_MS;
  const isIntraday = spanDays <= 1.5;
  // Ticks stay legible at every zoom level by dropping the time-of-day once
  // the visible window is wide enough that showing it on every tick would
  // just be noise — the data itself is always per-reading regardless.
  const compactTicks = spanDays > 3;
  const longRange = spanDays > 16;

  // Y-axis bounds come from the same stable, whole-preset data — fixed for
  // the duration of a pan/zoom session rather than tightening to whatever's
  // currently visible, which avoids the vertical rescale-jitter that would
  // otherwise accompany every horizontal pan/zoom frame.
  const values = data.flatMap((point) => [point.value, point.predictedMin, point.predictedMax]).filter((v): v is number => v != null);
  const lower = values.length ? Math.max(0, Math.floor((Math.min(...values) - 15) / 10) * 10) : 60;
  const upper = values.length ? Math.ceil((Math.max(...values) + 15) / 10) * 10 : 180;

  // The actually-visible subset, for the "N lần đo" footer count — cheap
  // (filtering an already-computed array), so fine to recompute every
  // pan/zoom frame even though `data` itself isn't. Never empty while
  // `data` isn't, thanks to ensureNonEmptyWindow above.
  const filtered = useMemo(
    () => data.filter((point) => point.timestamp >= windowStart && point.timestamp <= windowEnd),
    [data, windowStart, windowEnd],
  );

  // The rendered X-axis domain only — a hair wider than [windowStart,
  // windowEnd] so a point sitting exactly at either edge (routine, since
  // presetWindow itself is pulled in to the earliest/latest actual reading)
  // draws its full dot instead of getting sliced in half by Recharts'
  // plot-area clip path. windowStart/windowEnd themselves stay exact for
  // filtering, the footer count, and pan/zoom clamping — this padding is
  // cosmetic only.
  const xDomainPad = Math.max((windowEnd - windowStart) * 0.02, 5 * 60 * 1000);
  const xDomain: [number, number] = [windowStart - xDomainPad, windowEnd + xDomainPad];

  return (
    <div className={cn("space-y-4", className)}>
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h3 className="font-semibold text-foreground">Xu hướng đường huyết</h3>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {isIntraday ? "Từng lần đo trong 24 giờ gần nhất" : "Từng lần đo theo thời điểm trong ngày"} · dải màu là ngưỡng cá nhân AI dự đoán, thay đổi theo bối cảnh
          </p>
          <p className="mt-0.5 text-[11px] text-muted-foreground/70">Cuộn hoặc chụm để phóng to/thu nhỏ · kéo để xem thời gian khác</p>
        </div>
        <div className="flex items-center gap-2">
          {isZoomed && (
            <button
              type="button"
              onClick={clearManualWindow}
              className="rounded-lg px-2.5 py-1.5 text-xs font-medium text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
            >
              Đặt lại
            </button>
          )}
          <div className="flex w-fit gap-1 rounded-xl bg-muted p-1" aria-label="Khoảng thời gian">
            {visibleRanges.map((item) => (
              <button
                key={item}
                type="button"
                aria-pressed={!isZoomed && range === item}
                onClick={() => {
                  clearManualWindow();
                  onRangeChange(item);
                }}
                className={cn(
                  "rounded-lg px-3 py-1.5 text-xs font-medium transition-colors",
                  !isZoomed && range === item
                    ? "bg-card text-foreground shadow-sm"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                {RANGE_LABELS[item]}
              </button>
            ))}
          </div>
        </div>
      </div>

      {data.length ? (
        <>
          <div
            ref={containerRef}
            className="touch-none select-none rounded-xl cursor-grab active:cursor-grabbing"
            title="Cuộn hoặc chụm để phóng to/thu nhỏ, kéo để xem thời gian khác"
          >
          <ResponsiveContainer width="100%" height={260}>
            <LineChart data={data} margin={{ top: 8, right: 12, left: 0, bottom: 4 }}>
              <CartesianGrid vertical={false} strokeDasharray="3 3" stroke="var(--border)" />
              <XAxis
                dataKey="timestamp"
                type="number"
                scale="time"
                domain={xDomain}
                allowDataOverflow
                tickFormatter={(value) => (compactTicks ? axisDate(value, longRange) : axisDateTime(value))}
                minTickGap={isIntraday ? 28 : compactTicks ? 36 : 48}
                tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
                tickLine={false}
                axisLine={false}
              />
              <YAxis
                domain={[lower, upper]}
                width={42}
                tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
                tickLine={false}
                axisLine={false}
                label={{ value: "mg/dL", angle: -90, position: "insideLeft", fontSize: 10, fill: "var(--muted-foreground)" }}
              />
              <Tooltip
                labelFormatter={(value) =>
                  new Date(Number(value)).toLocaleString("vi-VN", {
                    weekday: "short", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
                  })
                }
                formatter={(value, name, item) => {
                  if (name !== "Đường huyết") return [null, null];
                  const point = item.payload as { status: ThresholdStatus; predictedMin: number | null; predictedMax: number | null };
                  const status = statusLabel(point.status);
                  const band = point.predictedMin != null && point.predictedMax != null
                    ? `dải cá nhân ${point.predictedMin.toFixed(1)}-${point.predictedMax.toFixed(1)} mg/dL`
                    : null;
                  const suffix = [status, band].filter(Boolean).join(" · ");
                  return [`${value} mg/dL${suffix ? ` · ${suffix}` : ""}`, name];
                }}
                contentStyle={{ borderRadius: 12, border: "1px solid var(--border)", fontSize: 12 }}
              />
              {/* connectNulls=true here (unlike the value line below): a
                 DANGER reading genuinely has no AI band — the model is
                 never called for it, by design — so bridging that single
                 gap with a straight interpolated segment is a visual
                 smoothing choice, not a claim that a band was computed
                 there. The DANGER reading itself still shows its true
                 "no band" state via its red-ringed dot and tooltip. */}
              <Area
                type="linear"
                dataKey="band"
                name="Dải cá nhân"
                stroke="none"
                fill="#43B5AE"
                fillOpacity={0.2}
                connectNulls={true}
                activeDot={false}
                legendType="none"
                isAnimationActive={false}
              />
              <Line
                type="linear"
                dataKey="predictedMin"
                name="Ngưỡng dưới (AI)"
                stroke="#43B5AE"
                strokeWidth={1.5}
                strokeDasharray="4 4"
                dot={false}
                connectNulls={true}
                legendType="none"
                isAnimationActive={false}
              />
              <Line
                type="linear"
                dataKey="predictedMax"
                name="Ngưỡng trên (AI)"
                stroke="#43B5AE"
                strokeWidth={1.5}
                strokeDasharray="4 4"
                dot={false}
                connectNulls={true}
                legendType="none"
                isAnimationActive={false}
              />
              <Line
                type="linear"
                dataKey="value"
                name="Đường huyết"
                stroke="#2563EB"
                strokeWidth={2.25}
                connectNulls={false}
                dot={makeThresholdDot("#2563EB")}
                activeDot={{ r: 5 }}
                isAnimationActive={false}
              />
              {/* No visible line (stroke=none) — same values as "Đường
                 huyết" above at flagged points only, redeclared after it
                 so its dots always paint on top. See flaggedValue's
                 comment in perReadingData. */}
              <Line
                type="linear"
                dataKey="flaggedValue"
                name="_flagged"
                stroke="none"
                connectNulls={false}
                dot={makeThresholdDot("#2563EB")}
                activeDot={false}
                legendType="none"
                isAnimationActive={false}
              />
            </LineChart>
          </ResponsiveContainer>
          </div>
          <div className="flex flex-wrap gap-x-4 gap-y-2 text-xs text-muted-foreground">
            <span><span className="mr-1.5 inline-block h-0.5 w-4 align-middle bg-blue-600" />Đường huyết</span>
            <span><span className="mr-1.5 inline-block h-2.5 w-4 rounded-sm border border-dashed border-[#43B5AE] bg-[#43B5AE]/20" />Dải cá nhân (AI dự đoán)</span>
            <span><span className="mr-1.5 inline-block h-2.5 w-2.5 rounded-full border-2 border-amber-600 align-middle" />Cần theo dõi (AI)</span>
            <span><span className="mr-1.5 inline-block h-2.5 w-2.5 rounded-full border-2 border-red-600 align-middle" />Nguy hiểm</span>
            <span>
              {filtered.length} lần đo
              {isZoomed ? ` · ${formatWindowLabel(windowStart, windowEnd)}` : ` trong ${RANGE_LABELS[range].toLowerCase()}`}
            </span>
          </div>
        </>
      ) : (
        <div className="flex h-[260px] items-center justify-center rounded-xl border border-dashed border-border bg-muted/30 px-6 text-center text-sm text-muted-foreground">
          Chưa có dữ liệu đường huyết trong khoảng thời gian này.
        </div>
      )}
    </div>
  );
}

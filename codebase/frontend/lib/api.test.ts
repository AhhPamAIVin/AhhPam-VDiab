// Golden testset for the glucose "Thời điểm" (meal-context) display bug —
// see the fix in this same commit's history. Root cause was two-part:
//   1. Write side (record-modal.tsx) smuggled the selected timing label
//      into the free-text `note` field ("<label> - <note>") instead of
//      sending it as the real `meal_context` API field.
//   2. Read side (health-monitoring.tsx) reconstructed "Thời điểm" by
//      parsing that same `note` field, which only worked by coincidence
//      for the app's own writes — for simulator-seeded demo data (which
//      correctly sets meal_context but uses `note` for an unrelated
//      `sim:SIM60-...` tracking tag), it displayed the raw tracking code
//      instead of a real label.
//
// These tests import the actual exported functions (not a re-implementation
// that could silently drift out of sync with the real code) and run them
// against real record shapes pulled from the live backend while diagnosing
// the bug — including the exact record from the reported screenshot.
//
// Run with `npm test` (or `pnpm test`) before/after touching meal_context/
// glucose-note handling in api.ts, record-modal.tsx, or health-monitoring.tsx.

import { describe, expect, it } from "vitest";
import { GLUCOSE_TIMING_OPTIONS, glucoseDisplayNote, glucoseTimingLabel, glucoseTimingValue } from "./api";

describe("glucoseTimingLabel", () => {
  it.each([
    // The exact reported bug: this simulator record's "Thời điểm" column
    // was showing "sim:SIM60-114189-GLU-0290" instead of a real label.
    ["BEFORE_SLEEP", "Trước khi ngủ"],
    ["FASTING", "Lúc đói"],
    ["BEFORE_MEAL", "Trước ăn"],
    ["AFTER_MEAL", "Sau ăn"],
    ["RANDOM", "Khác"],
    // Simulator-only value, no UI button — must still resolve to something
    // readable, not fall through to the "Khác" default.
    ["EVENT_RECOVERY", "Theo dõi lại"],
  ])("maps meal_context %s to %s", (mealContext, expected) => {
    expect(glucoseTimingLabel(mealContext)).toBe(expected);
  });

  it.each([[null], [undefined], [""], ["SOME_UNKNOWN_FUTURE_VALUE"]])(
    "falls back to 'Khác' for unrecognized/missing meal_context (%s)",
    (mealContext) => {
      expect(glucoseTimingLabel(mealContext)).toBe("Khác");
    },
  );
});

describe("glucoseTimingValue", () => {
  it.each(GLUCOSE_TIMING_OPTIONS)("round-trips label '$label' through glucoseTimingLabel", ({ label, value }) => {
    expect(glucoseTimingValue(label)).toBe(value);
    expect(glucoseTimingLabel(glucoseTimingValue(label))).toBe(label);
  });

  it("falls back to RANDOM for a label with no matching UI button", () => {
    expect(glucoseTimingValue("không tồn tại")).toBe("RANDOM");
  });
});

describe("glucoseDisplayNote", () => {
  it("hides the simulator's internal sim:SIM60-... tracking tag", () => {
    expect(glucoseDisplayNote("sim:SIM60-114189-GLU-0290")).toBe("");
  });

  it("keeps a real, patient-written free-text note", () => {
    expect(glucoseDisplayNote("hơi chóng mặt")).toBe("hơi chóng mặt");
  });

  it.each([[null], [undefined], [""]])("returns empty string for missing note (%s)", (note) => {
    expect(glucoseDisplayNote(note)).toBe("");
  });
});

describe("real record shapes (pulled from the live backend while diagnosing the bug)", () => {
  // Each case: [meal_context, note, expected Thời điểm, expected Ghi chú]
  it.each([
    // Simulator-seeded demo data — meal_context correct, note is a
    // tracking tag that must not leak into either display field as-is.
    ["BEFORE_SLEEP", "sim:SIM60-114189-GLU-0290", "Trước khi ngủ", ""],
    ["AFTER_MEAL", "sim:SIM60-114189-GLU-0288", "Sau ăn", ""],
    ["BEFORE_MEAL", "sim:SIM60-114189-GLU-0287", "Trước ăn", ""],
    ["FASTING", "sim:SIM60-114189-GLU-0286", "Lúc đói", ""],
    // Fresh manual submission after the write-side fix: meal_context set,
    // note null (nothing typed in "Ghi chú").
    ["BEFORE_MEAL", null, "Trước ăn", ""],
    // Fresh manual submission with a real note alongside a real meal_context.
    ["BEFORE_MEAL", "hơi chóng mặt", "Trước ăn", "hơi chóng mặt"],
    // Pre-fix legacy manual row: meal_context was never sent (old
    // record-modal.tsx bug), only the note carries the old "<label>" text.
    // Not a regression target — this is the *previous* bug's leftover data,
    // documented here so a future reader isn't surprised it shows "Khác"
    // rather than "Lúc đói" despite the note saying so.
    [null, "Lúc đói", "Khác", "Lúc đói"],
  ])("meal_context=%s note=%s -> Thời điểm=%s, Ghi chú=%s", (mealContext, note, expectedTiming, expectedNote) => {
    expect(glucoseTimingLabel(mealContext)).toBe(expectedTiming);
    expect(glucoseDisplayNote(note)).toBe(expectedNote);
  });
});

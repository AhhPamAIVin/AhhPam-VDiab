"use client";

import { useEffect, useState } from "react";
import { Camera, ChevronDown, ChevronUp, Loader2 } from "lucide-react";
import { api, type MealRecord, type MealType } from "@/lib/api";
import { CarbLoadBadge, MealAnalysisCard, carbTotalText } from "@/components/meal-analysis-card";
import { MealGlucoseResponsePanel } from "@/components/meal-glucose-response";
import { Pagination } from "@/components/ui/pagination";

const MEAL_LABELS: Record<MealType, string> = {
  breakfast: "Bữa sáng",
  lunch: "Bữa trưa",
  dinner: "Bữa tối",
  snack: "Bữa phụ",
};
const PAGE_SIZE = 10;

// Doctor's view of a patient's meals (Epic 1.5): the meal <-> post-meal
// glucose groups, then each meal with its read-only analysis (kcal shown).
export function PatientMealsPanel({ patientId }: { patientId: string }) {
  const [meals, setMeals] = useState<MealRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [page, setPage] = useState(1);

  useEffect(() => {
    let cancelled = false;
    api
      .meals(patientId)
      .then((result) => {
        if (!cancelled) setMeals(result);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Không tải được bữa ăn");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [patientId]);

  const pageCount = Math.max(1, Math.ceil(meals.length / PAGE_SIZE));
  const currentPage = Math.min(page, pageCount);
  const visible = meals.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE);

  return (
    <div className="space-y-4">
      <MealGlucoseResponsePanel patientId={patientId} audience="doctor" />

      <section className="space-y-3 rounded-2xl border border-border bg-card p-5">
        <h3 className="font-semibold">Nhật ký bữa ăn</h3>
        {loading && (
          <div className="flex justify-center py-4">
            <Loader2 className="h-5 w-5 animate-spin text-primary" />
          </div>
        )}
        {error && <p className="text-sm text-muted-foreground">{error}</p>}
        {!loading && !error && meals.length === 0 && (
          <p className="text-sm text-muted-foreground">Bệnh nhân chưa ghi bữa ăn nào.</p>
        )}
        <div className="space-y-2">
          {visible.map((meal) => (
            <div key={meal.id} className="overflow-hidden rounded-xl border border-border">
              <button
                type="button"
                onClick={() => setExpanded(expanded === meal.id ? null : meal.id)}
                aria-expanded={expanded === meal.id}
                className="flex w-full items-start gap-3 p-3 text-left"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                    <span className="font-medium text-[#2D9D97]">{MEAL_LABELS[meal.meal_type]}</span>
                    <span>{new Date(meal.meal_time).toLocaleString("vi-VN")}</span>
                    {meal.input_source === "photo" && (
                      <span className="inline-flex items-center gap-1">
                        <Camera className="h-3 w-3" aria-hidden /> từ ảnh
                      </span>
                    )}
                  </div>
                  <p className="mt-1 text-sm font-medium">{meal.description}</p>
                  {meal.analysis && (
                    <div className="mt-1.5 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                      <CarbLoadBadge load={meal.analysis.carb_load} />
                      <span>{carbTotalText(meal.analysis)}</span>
                    </div>
                  )}
                </div>
                {expanded === meal.id ? (
                  <ChevronUp className="h-4 w-4 text-muted-foreground" />
                ) : (
                  <ChevronDown className="h-4 w-4 text-muted-foreground" />
                )}
              </button>
              {expanded === meal.id && (
                <div className="space-y-2 border-t border-border p-3 text-sm text-muted-foreground">
                  {meal.portion && <p>Khẩu phần (bệnh nhân ghi): {meal.portion}</p>}
                  {meal.note && <p>Ghi chú: {meal.note}</p>}
                  <MealAnalysisCard mealId={meal.id} analysis={meal.analysis} readOnly showKcal />
                </div>
              )}
            </div>
          ))}
        </div>
        {meals.length > PAGE_SIZE && (
          <Pagination page={currentPage} totalItems={meals.length} onPageChange={setPage} />
        )}
      </section>
    </div>
  );
}

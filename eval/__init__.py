"""VDiab evaluation package.

Two independent subsystems live here:
- eval/harness/ + eval/run_eval.py — deterministic rule-engine/data-validation
  cases and pytest-coverage evidence generation (see docs/evaluation/).
- eval/respond_agent/ + eval/watch_classifier/ — LangSmith experiments and
  Ragas metrics for the respond-agent/RAG pipeline, plus the personalized-WATCH
  ML classifier gate (see docs/AI_Eval_Metrics_Plan.md).
"""

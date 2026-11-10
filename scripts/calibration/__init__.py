"""RF QA calibration loop — data collection → threshold fit → promotion.

Subsystems (see `~/.claude/plans/ok-now-we-plan-adaptive-yeti.md` and
`docs/rf_qa_detector_calibration_spec.md`):

  * candidate_selector — pick informative frames per detector (subsystem C)
  * label_store        — append-only PHI-clean label ledger (subsystem E)
  * fit_threshold      — ROC fit per detector (subsystem F)
  * promote_threshold  — registry update via diff + --apply (subsystem G)
  * audit_disagreement — score-vs-operator mechanism audit (subsystem I)
  * labelling_metrics  — per-detector readiness dashboard (subsystem J)
"""

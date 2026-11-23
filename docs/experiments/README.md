# Experiment logs

One file per experiment, written so that a later person or agent can pick the work up without the chat that produced it. Code and full results live with the code (`pastas/research/…`); these logs record **why** it was run, **what was decided**, **what turned out to be wrong**, and **where to continue**.

| date | log | branch | outcome |
|---|---|---|---|
| 2026-11-18 | [2026-11-18_alt-models.md](2026-11-18_alt-models.md) | `research/alt-models` | no other model family beats Pastas; a Pastas+SARIMAX ensemble comes close |
| 2026-11-19 → 23 | [2026-11-19_accepted-models.md](2026-11-19_accepted-models.md) | `research/accepted-models` | passed models 35 → 48 with one shared stress set (lake level + air pressure), 60 with a small menu |

**Where to continue:** [NEXT_STEPS.md](NEXT_STEPS.md).

## Conventions for a new log
- File name `YYYY-MM-DD_<topic>.md`, the date the experiment started.
- Sections: question, set-up (data, split, decision rule *fixed before the results*), results (numbers + path to the CSV), conclusions, mistakes and corrections, open questions.
- Commands to reproduce, with the exact `uv run --with …` line.
- Update the table above and `NEXT_STEPS.md`.

## Standing facts every experiment should know
- **The series** are piezometers in the dikes of the **Schermerboezem** around the Purmer and the Beemster (outer water about −0.50 m NAP). They are *not* Markermeer dikes; the Markermeer level works as a regional proxy.
- **Validated data:** 89 series (Purmer 48, Beemster 40, Schermer 1), about two years each, from the review stores (`scripts/run_local.py::validated_heads`). Committed results: `pastas/results_export/valid_pastas/`.
- **Forcing:** KNMI 249 Berkhout P and Makkink E (`pastas/data/forcing/knmi_forcing_249.csv`). Extremes: 16 IMPREX members, 1951–2000, one cell at Berkhout, **only pr, tas and evappot (no wind, no water level)**.
- **Acceptance** = the `passed` stamp of `tarso_pipeline/model_checks.py` (rsq ≥ 0.7, memory t95 < 0.5 · calibration, gain significant, bounds). Selection = `tarso_pipeline/selection.py::select_models` (passed tier → holdout guard vs Tarso → AIC → runaway rule on IMPREX).
- **Local runs need `pandas<3`.** pastas 1.12 `ps.check` fails under pandas 3 ("assignment destination is read-only"); the root `uv.lock` pins pandas 3.1.1. Databricks still runs pandas 2.
- **LightGBM 4.7 crashes on Windows**; pin 4.6.
- **Constraints from the user:** don't change write paths (`waterkeringbeheer_prd.jvanruitenbeek` stays the default schema); `gold_prd` is read-only; the user files GitHub issues by hand; commit/push only when asked.

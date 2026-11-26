# Experiment logs

One file per experiment, written so that a later person or agent can pick the work up without the chat that produced it. Code and full results live with the code (`pastas/research/…`); these logs record **why** it was run, **what was decided**, **what turned out to be wrong**, and **where to continue**.

| date | log | branch | outcome |
|---|---|---|---|
| 2026-11-18 | [2026-11-18_alt-models.md](2026-11-18_alt-models.md) | `research/alt-models` | no other model family beats Pastas; a Pastas+SARIMAX ensemble comes close |
| 2026-11-19 → 23 | [2026-11-19_accepted-models.md](2026-11-19_accepted-models.md) | `research/accepted-models` | passed models 35 → 55 with one shared stress set (lake level + air pressure, linear + bucket types), 60 with a small menu |
| 2026-11-23 | [2026-11-23_purmer-boezem.md](2026-11-23_purmer-boezem.md) | `research/accepted-models` | Purmer boezem level (as is, normalised, set point removed): +4 at best, not significant; lake + pressure stays |
| 2026-11-23 | [2026-11-23_schermerboezem.md](2026-11-23_schermerboezem.md) | `research/accepted-models` | true Schermerboezem (Beemster gauge for both polders): 44 with pressure, 54 with lake + pressure (no gain over 55); a synthetic boezem from P/E works for IMPREX but moves return levels only by mm–cm |
| 2026-11-24 | [2026-11-24_gemaal-peilen.md](2026-11-24_gemaal-peilen.md) | `research/accepted-models` | pumping-station in/uitstroompeil: the position hypothesis does not hold, no gain over lake + pressure (53 vs 55), nearer station's boezem level slightly better (p = 0.016); KNMI radar helps the Purmer, radar + lake + pressure 56 |
| 2026-11-24 | [2026-11-24_drought.md](2026-11-24_drought.md) | `research/accepted-models` | a root-zone bucket deficit (from P and E, rebuilt per IMPREX member) as an extra stress: better drought fit out of sample (640 vs 398 models), holdout R² +0.07, passed 60 → 65 with IMPREX; the KNMI deficit overfits; the deep Beemster filters drop in steps (pumping?), which no P/E indicator explains |
| 2026-11-26 | [2026-11-26_selection_holdout.md](2026-11-26_selection_holdout.md) | `research/accepted-models` | Beemster's holdout "fall" was a mixed-up comparison; the AIC pick favoured the bucket on the calibration fit. New holdout rules (bucket vs without, close to the best holdout, an R² guard): same 122 passed, median holdout R² of the picks 0.57 → 0.61, lowest quarter 0.15 → 0.31 |

**Where to continue:** [NEXT_STEPS.md](NEXT_STEPS.md).

## Conventions for a new log
- File name `YYYY-MM-DD_<topic>.md`, the date the experiment started.
- Sections: question, set-up (data, split, decision rule *fixed before the results*), results (numbers + path to the CSV), conclusions, mistakes and corrections, open questions.
- Commands to reproduce, with the exact `uv run --with …` line.
- Update the table above and `NEXT_STEPS.md`.

## Standing facts every experiment should know
- **The series** are piezometers in the dikes of the **Schermerboezem** around the Purmer and the Beemster (outer water about −0.50 m NAP). They are *not* Markermeer dikes; the Markermeer level works as a regional proxy.
- **Extra stress data** in `pastas/data/stresses/`: RWS Markermeer (in git; `build_forcing_sources.py` makes the pipeline's `stress_lake_edam.csv` from it). The HHNK pumping-station records `<Polder>_<Station>.csv` (instroompeil = polder side, uitstroompeil = boezem side; cleaned daily caches `gemaal_<station>_daily.csv`) are **archived since 2026-11-26** in `pastas/data/stresses/archive/gemaal/`: git-ignored, only on the laptop that has them, because the pipeline does not use them (2026-11-24 log). The Dijkgraaf de Boer boezem files of 2026-11-23 were removed by the user.
- **Validated data:** 89 series (Purmer 48, Beemster 40, Schermer 1), about two years each, from the review stores (`scripts/run_local.py::validated_heads`). Committed results: `pastas/results_export/valid_pastas/`.
- **Forcing:** KNMI 249 Berkhout P and Makkink E (`pastas/data/forcing/knmi_forcing_249.csv`). Radar per polder (2020 →, about 10 % wetter than Berkhout): `pastas/data/forcing/local/radar_<polder>.csv`. Extremes: 16 IMPREX members, 1951–2000, one cell at Berkhout, **only pr, tas and evappot (no wind, no water level)**.
- **Acceptance** = the `passed` stamp of `tarso_pipeline/model_checks.py` (rsq ≥ 0.7, memory t95 < 0.5 · calibration, gain significant, bounds). Selection = `tarso_pipeline/selection.py::select_models` (no runaway on IMPREX → passed tier → holdout rules → AIC); its thresholds are in `pastas/config/model_settings.toml` since 2026-11-26, so research that calls `select_models` uses the current rules.
- **Local runs need `pandas<3`.** pastas 1.12 `ps.check` fails under pandas 3 ("assignment destination is read-only"); the root `uv.lock` pins pandas 3.1.1. Databricks still runs pandas 2.
- **LightGBM 4.7 crashes on Windows**; pin 4.6.
- **Constraints from the user:** don't change write paths (`waterkeringbeheer_prd.jvanruitenbeek` stays the default schema); `gold_prd` is read-only; the user files GitHub issues by hand; commit/push only when asked.

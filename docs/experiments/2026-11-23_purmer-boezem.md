# 2026-11-23 · Purmer boezem level as an extra stress

> **Superseded the same day** by [2026-11-23_schermerboezem.md](2026-11-23_schermerboezem.md). The user then supplied two *true* boezem series; this "instroompeil" (−1.57 m NAP) is not the boezem. The workbook was removed from `data/stresses/`, and the variants `bz*` now read the Schermerboezem gauge. The numbers below are for the instroompeil (`results/instroompeil_*.csv`).

**Branch:** `research/accepted-models`. **Code:** `pastas/research/accepted/` (`stresses.boezem_level`, `setpoint_deviation`, `polders`; variants `bz`, `bz1`, `bzn`, `bzd` in `variants.py`; `compare_boezem.py`). **Results:** `pastas/research/accepted/results/instroompeil_scenarios.csv`, `instroompeil_fits.csv`.

## Question (the user)
A boezem level for the Purmer was supplied as `pastas/data/stresses/Purmer_Dijkgraaf_de_Boer_instroompeil.xlsx`. Test (1) the series as is, (2) normalised so Pastas picks up the signal of an artificially maintained level, (3) report whether the Pastas models improve.

## Data
- 15-minute `waterhoogte` (CET/CEST) at gemaal Dijkgraaf de Boer, from 2012-09 in the file, but **values only from 2019-05-02**. Daily mean cached as `pastas/data/stresses/purmer_dijkgraaf_de_boer_daily.csv` (2418 days; 2020 has gaps).
- Held at **−1.57 m NAP with a standard deviation of 1 cm** (daily range −1.62 … −1.49). The yearly mean rose about 1 cm in 2025–2026.
- ⚠️ −1.57 m NAP is neither the Schermerboezem target (about −0.50) nor a usual Purmer polder level. **Ask HHNK what this "instroompeil" measures** (which side of the pump, which water body).

## Set-up
Same harness and rules as [2026-11-19_accepted-models.md](2026-11-19_accepted-models.md): every series' fit with the pipeline's checks and `select_models`; one stress set for every series (the user's rule), the five base types plus that set on the linear and bucket types. No extremes in this comparison (the selection without runaway rule, as `scenarios_without_extremes.csv`). Run on all 89 series and reported per polder (series→polder via `boxplots/input_data/config.csv` `LD_project`).

| variant | stress | response |
|---|---|---|
| `bz` | level − calibration mean (as is) | Exponential |
| `bz1` | the same | instantaneous (`One`) |
| `bzn` | (level − mean) / std: **normalised** | Exponential |
| `bzd` | level minus its 31-day running median (**the set-point steps removed**), normalised | Exponential |
| `…+baro` | each of these plus air pressure | |

## Results (passed of 89; baseline 35)
| one shared set | passed | gained | p (McNemar) | Purmer (48) | Beemster (40) |
|---|---|---|---|---|---|
| baseline | 35 | – | – | 20 | 14 |
| **lake + air pressure (`wl1+baro`)** | **55** | +20 | < 0.001 | **33** | 21 |
| lake (`wl`) | 48 | +13 | < 0.001 | 27 | 20 |
| air pressure (`baro`) | 41 | +6 | 0.03 | 24 | 16 |
| boezem as is (`bz`) | 39 | +4 | 0.13 | 22 | 16 |
| boezem normalised (`bzn`) | 39 | +4 | 0.13 | 22 | 16 |
| boezem set-point deviation (`bzd`) | 35 | 0 | 1.0 | 20 | 14 |
| boezem + air pressure (`bz+baro`) | 39 | +4 | 0.13 | 22 | 16 |
| boezem instantaneous + air pressure (`bz1+baro`) | 41 | +6 | 0.03 | 22 | 18 |

No scenario loses a series. (The 55 includes the bucket versions `flex_*@wl1+baro`; with the linear types only it is 48, the figure in the earlier log.)

## What this says
1. **The boezem level does not raise the number of accepted models** in a significant way: +4 at best (p = 0.13), against +20 for lake + air pressure. In the Purmer itself: 22 vs 20 now, against 33 with lake + pressure.
2. **Normalising changes nothing** (`bzn` = `bz`, same fits to 0.001 in R²). Pastas already scales a stress's initial gain by its standard deviation, and in a linear model a scale factor only rescales the fitted gain.
3. **Removing the set point (`bzd`) makes it worse**: the 1-cm excursions carry no signal the heads follow (AIC +9 in the median, the boezem gain is significant in only 45 % of the fits).
4. **The boezem does improve the fit statistics** (`bz`: median AIC −32 in the Purmer, R² +0.01), **but not for a physical reason:**
   - it helps the **Beemster just as much** (AIC −58), although it is a Purmer gauge;
   - the fitted gains are implausible: median **6.8 m head per m boezem**, 90 % at 95 m/m (physically ≤ 1);
   - the response time often runs to the bound (a = 10 000 days), and the P/E memory check passes less often (47 % → 29 %).
   It acts as a slow **trend regressor** (the ~1 cm rise of the set point in 2025–2026), not as a boundary condition. That kind of stress would also give implausible extremes.
5. So the reason the boezem does not work is that it is **held too tightly**: a level that varies by 1 cm cannot explain heads that vary by decimetres. The managed boezem is effectively a constant; the head variations come from rain, evaporation and the regional water level/pressure signal.

## Conclusion
Keep **lake + air pressure (`wl1+baro`)** as the one shared stress set. Do not add the Purmer boezem level. (Extremes for the boezem picks were not run, as no variant improved the count.)

## Open
- What the −1.57 m NAP "instroompeil" actually is (see the data note). If it is a polder-side level, a real Schermerboezem gauge (−0.50 m NAP) could still be tested the same way, but given this result with a level held to 1 cm, a large gain is unlikely.

## Reproduce
```bash
cd pastas
UV="uv run --no-project --python 3.11 --with pastas==1.12.* --with pandas<3 --with pyyaml --with requests --with scipy --with openpyxl"
# start from the fits of the accepted-models run (baseline, wl, baro, ...):
mkdir -p local_runs/research/accepted_bz && cp local_runs/research/accepted/{fit_metrics,model_checks}.csv local_runs/research/accepted_bz/
$UV python research/accepted/run_variants.py --groups bz,bz1,bzn,bzd,bz+baro,bz1+baro,bzd+baro --out local_runs/research/accepted_bz
$UV python research/accepted/compare_boezem.py local_runs/research/accepted_bz
```
About 25 minutes on 7 workers.

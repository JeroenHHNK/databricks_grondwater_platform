# 2026-11-23 · Schermerboezem level as an extra stress, and a synthetic boezem for IMPREX

**Branch:** `research/accepted-models`. Follows [2026-11-23_purmer-boezem.md](2026-11-23_purmer-boezem.md) (the Purmer "instroompeil", which turned out not to be a boezem level).

**Code** (`pastas/research/accepted/`):
- `stresses.py`: `BOEZEM_FILES`, `despike`, `cache_boezem`, `boezem_level`;
- `variants.py`: `bz`, `bz1`, `bzn`, `bzd`, combinable with `baro` and `wl1`;
- `compare_boezem.py`, `emulate_boezem.py`, `extremes_boezem.py`.

**Pipeline:** `simulate_member(..., synthetic=)` and `simulate_pooled` accept a synthetic stress per member, with tests in `tests/test_simulate.py`.

**Results** (`pastas/research/accepted/results/`):
- `boezem_scenarios.csv`, `boezem_fits.csv`;
- `boezem_emulator_scores.csv`, `boezem_emulator_climate.csv`, `boezem_emulator.json`;
- `extremes_boezem.csv`, `extremes_boezem_diff_cm.csv`.

## Question (the user)
There are two new files with true boezem levels: `Beemster_Dijkgraaf_de_Boer_boezempeil.xlsx` and `Purmer_Purmer_Zuid_Boezempeil.xlsx`.
1. Check the series.
2. Add them as a stress for the Beemster and Purmer series, to raise the number of accepted models.
3. Evaluate whether the boezem can be imitated synthetically, so that return periods can still be calculated with IMPREX.

## 1. The series
| gauge | period with values | level | std (daily) | notes |
|---|---|---|---|---|
| Beemster, Dijkgraaf de Boer | 2019-05-02 → now (ongoing) | −0.48 m NAP | 1.7 cm | 35 jumps of more than 5 cm in 15 minutes, 5 values below −0.65 |
| Purmer Zuid | 2015-09-22 → **2021-12-06 (ended)** | −0.48 m NAP | 3.9 cm | many spikes and dips, mostly in 2016–2017 |

**Cleaning:** values outside −0.80 … −0.20 m NAP are dropped, and so is any 15-minute value more than 5 cm from its 3-hour running median. A daily mean needs at least 48 values. The results are cached in `pastas/data/stresses/boezem_<name>_daily.csv`.

**The two gauges are the same water body:** r = 0.88 on 567 common days, with a 1 cm offset. The boezem is **not** the Markermeer: r = 0.06 with RWS Edam.

**The Purmer Zuid gauge cannot calibrate anything.** Every validated series starts in 2024 (Beemster from 2024-08, Purmer from 2024-11), and the gauge stopped in 2021. So the **Beemster gauge is used for both polders.** That also keeps to the rule of one shared stress set. Purmer Zuid is used only as an out-of-sample test of the emulator.

**What drives the level:**
- **Heavy rain** gives peaks of up to +7 cm within 1–3 days, from the polders pumping in. The top-15 days all follow 12–70 mm in 3 days.
- **Management** is the rest, set by the water board: the level is held lower after wet weeks (r = −0.31 with 30-day net rain) and higher in summer (r = +0.30 with evaporation).

## 2. Accepted models (passed of 89, one shared set, without extremes)
| one shared set | passed | gained | p | Purmer (48) | Beemster (40) | holdout R² median, Purmer / Beemster |
|---|---|---|---|---|---|---|
| baseline | 35 | – | – | 20 | 14 | 0.40 / 0.44 |
| **lake + air pressure (`wl1+baro`)** | **55** | +20 | < 0.001 | **33** | 21 | 0.42 / 0.35 |
| **lake + pressure + boezem (`wl1+baro+bz1`)** | 54 | +19 | < 0.001 | 31 | **22** | **0.47 / 0.46** |
| boezem + air pressure (`bz1+baro`) | 44 | +9 | 0.004 | 24 | 19 | 0.43 / 0.43 |
| air pressure (`baro`) | 41 | +6 | 0.03 | 24 | 16 | |
| boezem as is (`bz`, Exponential) | 39 | +4 | 0.13 | 22 | 16 | |
| boezem instantaneous (`bz1`) | 39 | +4 | 0.13 | 21 | 17 | |
| boezem normalised (`bzn`) | 38 | +3 | 0.25 | 21 | 16 | |
| boezem without set point (`bzd`) | 36 | +1 | 1.0 | 20 | 15 | |

No scenario loses a series.

**The fitted boezem gains** (with the instantaneous response, `bz1+baro` picks, 58 models) are physically plausible, unlike the instroompeil's:
- a median of 0.31 m head per m boezem, with 91 % within ±1.5;
- a negative gain in about 10 % of the models.

The boezem contributes a median of 0.7 cm (std) to the head, and at most 3.4 cm.

**Conclusion:**
- **The boezem does not add accepted models on top of lake + pressure** (54 vs 55).
- **On its own it helps less than the lake** (39), and with pressure it reaches 44.
- What it does improve is the **holdout R² of the picks**: with all three stresses it rises from 0.42 to 0.47 in the Purmer and from 0.35 to 0.46 in the Beemster.

## 3. A synthetic boezem for IMPREX
**The emulator** is a Pastas model of the daily Beemster gauge, driven by KNMI 249 P and E (`emulate_boezem.py`).
- **Periods:** calibration 2019-05 → 2023-12; validation 2024 → now. Purmer Zuid 2015-09 → 2019-04 is a second, independent check (another gauge, compared after removing its mean offset).

| form | R² calibration | R² validation | R² Purmer Zuid | annual-max error, validation |
|---|---|---|---|---|
| `pe`: Recharge(P, E) | −0.01 | −0.02 | 0.00 | 5.1 cm |
| `p_e_season`: P (Gamma), E, annual harmonic | 0.26 | −0.59 | 0.20 | 2.5 cm |
| **`pulse_wet`**: rain pulse (a ≤ 10 d) + Recharge with a negative gain (lower after wet weeks) + annual harmonic | **0.35** | −0.24 | **0.22** | **2.3 cm** |

**The pick:** `pulse_wet`, by the smallest annual-maximum error out of sample (the wet tail is what matters for return periods).
- P and E explain about a quarter to a third of the boezem. The rest is management that rain cannot predict.
- The validation R² is below 0, partly because of a 0.7 cm drop in level in 2025–2026. The emulator does not know about that.

**Driven by the 16 IMPREX members** (800 member-years) against the observed 2019–2026:

| | mean | std | annual max, median | annual max, p90 | max |
|---|---|---|---|---|---|
| observed | −0.481 | 1.75 cm | −0.420 | −0.406 | −0.404 |
| IMPREX emulated | −0.479 | 0.97 cm | −0.454 | −0.443 | −0.398 |

The mean level is right, but the variability is damped: **the annual maxima are about 3.5 cm too low**, about half of the real peaks.

**Return periods** (`extremes_boezem.py`, the 58 `bz1+baro` picks that use the boezem, 16 members each):
- each model was run with the boezem *held at its mean* and with the *synthetic boezem*, put on the model's own reference;
- **all 116 runs were valid.**

Synthetic minus held:

| | T10 dry | T100 dry | T10 wet | T100 wet |
|---|---|---|---|---|
| median | +0.2 cm | +0.1 cm | +0.2 cm | +0.2 cm |
| 90 % | +0.9 cm | +0.9 cm | +1.4 cm | +2.0 cm |
| max | +2.6 cm | +3.0 cm | +3.8 cm | +8.0 cm |

Only 3 models move more than 5 cm (wet T100).

Even allowing for the damped emulator (the real boezem peaks are about twice as large), **the boezem's share of the return levels is a few centimetres at most.** Compare the lake held at its mean, which leaves out more than 25 cm for 22 models.

## Conclusions
1. **The Schermerboezem can be imitated well enough for IMPREX**, and it barely matters there: driving the models with the synthetic boezem changes the return levels by millimetres (2 cm at the 90th percentile). Holding it at its mean is practically as good. The pipeline now supports both.
2. **It does not raise the number of accepted models** beyond lake + air pressure: 55 against at most 44 (boezem + pressure) or 54 (all three).
3. **The trade-off for the one shared stress set:**
   - **`wl1+baro` (55 accepted).** The return periods miss the lake's set-up (up to 59 cm, more than 25 cm for 22 models) until a synthetic lake exists. That needs wind, which IMPREX does not have; see `PLAN_synthetic_lake.md`.
   - **`bz1+baro` (44 accepted).** The return periods are complete for the boezem. Air pressure is still held at its mean, which is reasonable: it has no memory and no trend, and its effect on the head is a few centimetres of daily noise.
   - **`wl1+baro+bz1` (54 accepted)** has the best holdout R² (0.47 / 0.46), but it is three stresses and it keeps the lake problem.
4. **Air pressure on IMPREX** is still held at its mean. Its effect is about −4 mm per hPa, mostly day-to-day, so it barely shows in annual extremes, but it has not been quantified. KNMI'23 `psl` would allow that.

## Open
- **The user's choice of the shared set (point 3).** My recommendation is `wl1+baro` now, with the lake held at its mean and that limitation stated on the return periods. The synthetic lake goes on the roadmap; the boezem stays out (it gains nothing).
- **The emulator does not capture management changes** such as the 2025–2026 drop. If HHNK has the set-point regime (target levels by date), adding it as a stress would replace the harmonic.
- **A longer boezem record** at Dijkgraaf de Boer (before 2019) would help the emulator. Only Purmer Zuid goes back to 2015, and it ended in 2021.

## Reproduce
```bash
cd pastas
UV="uv run --no-project --python 3.11 --with pastas==1.12.* --with pandas<3 --with pyyaml --with requests --with scipy --with openpyxl"
mkdir -p local_runs/research/accepted_bz2 && cp local_runs/research/accepted/{fit_metrics,model_checks}.csv local_runs/research/accepted_bz2/
$UV python research/accepted/run_variants.py --groups bz,bz1,bzn,bzd,bz+baro,bz1+baro,wl1+baro+bz,wl1+baro+bz1 --out local_runs/research/accepted_bz2   # ~35 min
$UV python research/accepted/compare_boezem.py local_runs/research/accepted_bz2
$UV python research/accepted/emulate_boezem.py local_runs/research/boezem_emulator                                                                   # ~1 min
# compare_boezem.py also writes picks_<scenario>.csv (with the .pas paths) for the next step
$UV python research/accepted/extremes_boezem.py local_runs/research/accepted_bz2 local_runs/research/boezem_emulator/boezem_synthetic_imprex.csv picks_bz1_baro.csv   # ~10 min
```

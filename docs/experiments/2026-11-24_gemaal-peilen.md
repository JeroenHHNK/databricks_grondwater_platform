# 2026-11-24 · Pumping-station levels (in- en uitstroompeil), station distance, and KNMI radar

**Branch:** `research/accepted-models`. Follows [2026-11-23_schermerboezem.md](2026-11-23_schermerboezem.md).

**Code** (`pastas/research/accepted/`):
- `stresses.py`: `GEMALEN`, `clean_gemaal`, `gemaal_daily`, `positions`, `stations_by_distance`, `polder_points`;
- `variants.py`: `up`, `ip`, `pos`, `posx`, the `…far` versions, and `radar`;
- `run_variants.py`: `--ids-from` freezes the series set, `--raw-polder` loads a polder's unvalidated series;
- `radar.py`, `compare_gemaal.py`;
- `research/data.py`: `raw_heads`.

**Notebooks** (`pastas/notebooks/exploration/`): [gemaal_peilen.ipynb](../../pastas/notebooks/exploration/gemaal_peilen.ipynb) (data and outliers) and [knmi_radar.ipynb](../../pastas/notebooks/exploration/knmi_radar.ipynb) (radar fetch, comparison, Pastas test).

**Results** (`pastas/research/accepted/results/`):
- `gemaal_scenarios.csv`;
- `gemaal_position.csv`, `gemaal_position_summary.csv`;
- `gemaal_stations.csv`, `gemaal_stations_summary.csv`;
- `radar_fits.csv`;
- `gemaal_hhw_raw_*.csv`.

## Question (the user)
New xlsx files hold the *instroompeil* and *uitstroompeil* of five pumping stations: Beemster Jacobus Bouman, Beemster Wouter Sluis, Purmer Stedelijk, Heerhugowaard Hensbroek and Heerhugowaard Huygendijk. The user asked for:
1. A notebook that explores the data. It should have per-polder graphs of the series and the filtered outliers, and identical raw and filtered graphs per input file. The xlsx files should be converted to CSV and then deleted.
2. A test of the hypothesis that the **crest** piezometers (buitenkruin, binnenkruin) follow the **uitstroompeil**, and the **lower** ones (berm, toe) the **instroompeil**, with these as extra stresses.
3. Beemster and Heerhugowaard each have two stations at opposite ends. Test whether the nearer station gives better models, and if so, scale that up.
4. A notebook that adds KNMI radar precipitation, one point per polder, and a test of whether it helps.
5. (Can the user validate Heerhugowaard meanwhile? Yes. The Beemster/Purmer runs use a frozen list of the 89 validated series, `--ids-from`.)

## 1. The data
**What the columns are.**
- **instroompeil** is the polder side of the pump: the polder level, at about −5.0 m NAP in the Beemster, −4.3 in the Purmer, and −3.4 / −3.9 in Heerhugowaard.
- **uitstroompeil** is the boezem side: the Schermerboezem, at about −0.5 m NAP.
- Huygendijk's original columns were `waterhoogte_krooshek` (inlet) and `waterhoogte_boezem` (outlet).
- The station locations come from OpenStreetMap / Nominatim (the station's address), good to a few hundred metres. Purmer Stedelijk has no location; it is the only Purmer record, so none is needed.

**The conversion.**
- Each xlsx became a CSV with the columns `time, instroompeil, uitstroompeil`, and the values unchanged. The round trip was checked exactly for every row and value.
- The xlsx files were then deleted.
- The CSVs are about 1.5× the xlsx size: 76 MB in total, of which Huygendijk is 35 MB.
- Huygendijk has 1,048,572 rows, which is just under Excel's row limit. Every year from 2002 to 2026 is complete, though, so it is not cut off. 2018–2020 are in the file twice, with identical values.

**The cleaning** (`clean_gemaal`, the same for every record and column):
1. Duplicate stamps are averaged.
2. **band**: values more than 0.40 m from the median are removed.
3. **spike**: values more than 0.10 m from the centred 3-hour running median are removed.
4. **flat**: runs of identical values longer than 2 days are removed.
5. The daily mean is kept if at least 48 of the 96 quarter-hours remain.

This removes 0.00–0.29 % of each record. What it catches:
- Purmer Stedelijk's boezem column read the *polder* level from 4 to 13 February 2026 (796 values).
- Wouter Sluis has a boezem dip to −1.85 m.
- Huygendijk has dropouts to about −5 / −1.1 m.
- Most instroompeil "spikes" are real dips at the pump inlet while pumping (clustered in wet winters). They are removed as local to the pump.

**Do the two stations of a polder differ?** Daily, 2024 onwards, from the notebook:

| polder | column | r | std of the difference |
|---|---|---|---|
| Beemster | instroompeil | 0.42 | 3.4 cm (each about 3 cm): the polder level differs between the ends |
| Beemster | uitstroompeil | 0.83 | 1.1 cm: the same boezem |
| Heerhugowaard | instroompeil | 0.67 | different level areas (−3.4 vs −3.9 m, 49 cm apart) |
| Heerhugowaard | uitstroompeil | 0.14 | 3.0 cm: hardly related |

## 2. Accepted models (89 validated series, one shared rule, without extremes)
The stresses:
- `up` is the uitstroompeil and `ip` the instroompeil, both from the series' **nearest** station.
- `pos` applies the hypothesis: crest series (BUKR, KR, BIKR) get `up`, and berm/toe series (BIB, INBIB, KRBIB, BIT) get `ip`.
- All of these use an instantaneous response (`ps.One`); `posx` uses Exponential.
- The `…far` versions take the other station.
- Every series gets the same rule, so this is still one stress set.

Per polder and level, the series are: Purmer 26 crest and 22 berm/toe, Beemster 19 crest and 21 berm/toe.

| one shared set | passed | gained | p | Purmer | Beemster | holdout R² median, Purmer / Beemster |
|---|---|---|---|---|---|---|
| baseline | 35 | – | – | 20 | 14 | 0.40 / 0.44 |
| lake + air pressure (`wl1+baro`) | 55 | +20 | < 0.001 | 33 | 21 | 0.42 / 0.35 |
| **radar + lake + pressure** | **56** | +21 | < 0.001 | **34** | 21 | **0.45 / 0.47** |
| lake + pressure + `pos` | 53 | +18 | < 0.001 | 32 | 20 | 0.44 / 0.47 |
| `pos` + pressure | 44 | +9 | 0.004 | 25 | 18 | 0.38 / 0.44 |
| radar | 40 | +5 | 0.06 | 23 | 16 | 0.41 / 0.48 |
| `up` / `ip` / `pos` / `up+ip` | 39 each | +4 | 0.13 | 20–22 | 16–18 | |
| `posx` | 38 | +3 | 0.25 | 21 | 16 | |
| `upfar` / `ipfar` / `posfar` | 39 / 36 / 37 | | | 20 | 15–18 | |

No scenario loses a series. **The pumping-station levels add nothing on top of lake + pressure** (53 against 55). On their own they behave like the Dijkgraaf de Boer boezem of the day before (39, and 44 with pressure).

## 3. The position hypothesis: not supported
For each series, the best AIC over the linear and bucket types was compared between `up` and `ip`. Tarso is left out on both sides, because it cannot carry an extra stress. Source: `results/gemaal_position_summary.csv`.

| polder | level | prefer `up` (ΔAIC < −2) | prefer `ip` (ΔAIC > 2) | sign test p | ΔAIC vs baseline, `up` / `ip` | median gain `up` / `ip` (m per m) |
|---|---|---|---|---|---|---|
| Beemster | berm/toe | **16** | 2 | 0.001 | −21 / −2 | **+0.47** / +0.08 |
| Beemster | crest | 11 | 8 | 0.65 | −13 / −9 | +0.42 / +0.15 |
| Purmer | berm/toe | 7 | 10 | 0.63 | −8 / −19 | −0.27 / **−0.74** |
| Purmer | crest | 6 | **19** | 0.015 | −1 / −14 | −0.14 / **−0.52** |

**Beemster.** Crest *and* toe follow the boezem side, the toe even more clearly, with a plausible positive gain of about 0.45 m per m. The polder level barely helps (gain 0.1). That is the opposite of the hypothesis for the lower series.

**Purmer.** The instroompeil fits better in both positions, but with a **negative gain**: the head goes *down* when the polder level goes *up*. That is not a physical response.
- The Purmer polder level has managed steps of about 5 cm (a higher summer level, visible in the notebook) that coincide with dry summers.
- So the instroompeil acts as a season proxy, the same trap as the "instroompeil" of 2026-11-23.

**Effect size.** The R² changes are tiny everywhere (median below 0.01). The pumping-station levels move the head by millimetres to a few centimetres.

## 4. The station test (Beemster, validated)
- 18 series are nearest Jacobus Bouman (east) and 22 nearest Wouter Sluis (west).
- The typical distance is 4 km to the near station and 7 km to the far one.
- The comparison is the best AIC with the near station against the far one. Source: `results/gemaal_stations_summary.csv`.

| stress | near better | far better | sign test p | median ΔAIC near − far | Spearman with the distance gap |
|---|---|---|---|---|---|
| `up` (boezem side) | 23 | 13 | 0.13 | −4.1 | −0.31 |
| `ip` (polder side) | 12 | 19 | 0.28 | +1.3 | −0.29 |
| `pos` | 14 | 18 | 0.60 | −0.4 | −0.38 |

- **The boezem level at the nearer station tends to fit better.** The bigger the distance gap, the more so. But the effect is not significant, and the accepted count does not change: `up` and `upfar` both give 39.
- **The polder level shows no locality.** The two Beemster instroompeilen correlate only 0.42, so what differs between them is local to each pump, not a gradient a piezometer 4 km away would see.
- **Conclusion for the Beemster alone:** no significant difference. Pooled with Heerhugowaard (§6), **the boezem side at the nearer station is better** (58 against 34 series, p = 0.016), and in Heerhugowaard it gives 32 passed against 28. The polder side shows no locality.
- **The scale-up is therefore the nearest-station rule**, which `up`, `ip` and `pos` already use for every series. A per-series choice of station would mean per-series stress sets, which the user has ruled out, and it is not needed.

**Heerhugowaard (raw, not validated)**: see §6.

## 5. KNMI radar
**The product.**
- KNMI Data Platform `rad_nl25_rac_mfbs_24h` 2.0: the climatological radar, 1 km, adjusted with both gauge networks.
- It comes as yearly zips of 600–900 MB. 2020-01-01 → 2026-10-20 was downloaded (about 5 GB), the three pixels were extracted, and the zips were deleted.
- Access uses KNMI's **public anonymous key** (from the developer portal, valid until 2027-08-01, shared and rate-limited). There is no personal key in the repo; `KNMI_API_KEY` overrides it.
- The radar is read at one point per polder: the centroid of the polder's piezometers.
- It uses the 08–08 UTC sum, moved one day back like `localP`: r = 0.74 with Berkhout shifted, against 0.52 unshifted.

**Against the gauges.**
- r = 0.98 with neerslagstation 251 West Beemster: the gauge adjustment dominates.
- It gives about **9–10 % more rain than Berkhout** (960–976 against 888 mm/year), steadily since 2021.
- Monthly sums correlate 0.97 with Berkhout. About 70 days differ by more than 10 mm.

**In Pastas:**
- **Purmer** (17 km from Berkhout, the farthest polder) gains:
  - 34 series fit better (ΔAIC < −2) against 13 worse;
  - the median ΔAIC is −51, the R² +0.011 and the holdout R² +0.03;
  - 23 pass instead of 20.
- **Beemster** (8 km) is mixed:
  - 17 better against 22 worse, with the median ΔAIC +23;
  - but the holdout R² rises by 0.03.
- **Accepted:** 40 against 35 alone. With lake + pressure it gives 56, the highest of any one shared set, with the holdout R² rising from 0.42 to 0.45 in the Purmer and from 0.35 to 0.47 in the Beemster.

**Radar + lake + pressure against lake + pressure** (asked on 2026-11-24; source: `results/radar_vs_wl1baro_fits.csv`).
- The comparison is per series, between the best models with the same stresses (lake level at Edam + air pressure, linear and bucket types). The only difference is the precipitation.

| polder | radar better (ΔAIC < −2) | radar worse (ΔAIC > 2) | sign test p | median ΔAIC | median ΔR² | median Δholdout R² |
|---|---|---|---|---|---|---|
| Purmer (48) | **33** | 15 | **0.013** | −30 | +0.007 | +0.048 |
| Beemster (40) | 18 | 21 | 0.75 | +6 | −0.001 | +0.005 |

- **Selected models:** 55 → 56 passed. 6 series were gained and 5 lost, so the swap is not significant (p = 1.0).
  - Purmer: 33 → 34 passed, with the pick holdout R² median 0.42 → 0.45.
  - Beemster: 21 → 21 passed, with the pick holdout R² median 0.35 → 0.47.
- **So radar improves the fits in the Purmer significantly** and is neutral in the Beemster. **It does not change the number of accepted models.**

**What radar would cost in the pipeline:**
1. **The cluster has no internet.** The radar CSV must be refreshed locally and uploaded, like the KNMI CSV, and the gauge-adjusted product lags about a month.
2. **The IMPREX extremes are bias-corrected to Berkhout.** A model calibrated on radar (10 % wetter) must get IMPREX members scaled per calendar month by radar ÷ Berkhout, as `report.py` already does for `localP`. Otherwise the return levels shift.
3. **It is a forcing change, not an extra stress,** so it also works for Tarso and adds no parameter.

## 6. Heerhugowaard: station test on raw data (indicative)
**Set-up.**
- The 70 Heerhugowaard series (Fugro, from 2025-01) are **not validated**. They were loaded from the raw export (`--raw-polder Heerhugowaard`) and put through the pipeline's own `clean_head`.
- The counts are therefore indicative. Rerun them on the validated data.
- 51 series are nearest Hensbroek (NE) and 22 nearest Huygendijk (SW). Huygendijk is closer for some of the series, so the distances overlap less than in the Beemster: about 2–4 km near, 4–9 km far.
- Source: `results/gemaal_hhw_raw_*.csv`.

| one shared set | passed (of 70) | gained | p | crest (42) | berm/toe (28) | holdout R² median |
|---|---|---|---|---|---|---|
| baseline | 27 | – | – | 13 | 14 | 0.38 |
| `up` (nearest station) | **32** | +5 | 0.06 | 17 | 15 | 0.44 |
| `pos` (nearest station) | **32** | +5 | 0.06 | 17 | 15 | 0.42 |
| `ip` (nearest station) | 30 | +3 | 0.25 | 15 | 15 | 0.40 |
| `upfar` / `ipfar` / `posfar` | 28 / 29 / 29 | +1 / +2 / +2 | | | | |
| radar | 31 | +4 | 0.13 | 16 | 15 | 0.52 |

**What the results show.**
- **Station:**
  - The boezem side at the near station is better in 35 series and the far station in 21 (p = 0.08).
  - The near station gives 32 passed against 28 for the far one.
  - The two Heerhugowaard uitstroompeilen hardly correlate (r = 0.14), so here the choice of station matters more than in the Beemster.
- **Pooled with the Beemster** (validated):
  - **near better 58, far better 34, p = 0.016** for the boezem side;
  - no effect for the polder side (40 / 49, p = 0.40) or `pos` (43 / 47).
- **Position:** again not as hypothesised.
  - Berm/toe series prefer the boezem side (17 against 9).
  - Crest series are split (17 / 18).
  - Both gains are positive (`up` 0.4–0.5 m per m, `ip` 0.2–0.3).
- **Radar:** neutral here (27 better, 35 worse, median ΔAIC +1.3). Heerhugowaard is only about 9 km from Berkhout.

## Conclusions
1. **Notebook and data:** done. The five records are clean apart from a few clear sensor errors. The CSVs replace the xlsx files.
2. **The position hypothesis does not hold:**
   - In the Beemster both crest and toe follow the **boezem side**; the polder level hardly matters.
   - In the Purmer the polder level fits only through its managed seasonal steps (negative gain).
   - The pumping-station levels do not raise the accepted count beyond lake + pressure (53 against 55).
3. **Station distance:**
   - For the **boezem side, the nearer station fits better** (58 against 34 series over the Beemster and Heerhugowaard, p = 0.016; Heerhugowaard raw 32 against 28 passed).
   - For the polder side there is no effect.
   - The nearest-station rule is the scale-up, and it is already in the variants.
   - In the validated Beemster and Purmer it does not raise the accepted count beyond lake + pressure.
4. **Radar:** a real but modest improvement where Berkhout is far (Purmer). The best one-shared-set count is now **radar + lake + pressure: 56**.

**Recommendation.**
- Keep `wl1+baro` as the shared set.
- Consider **radar as the forcing for all series** (not a stress). It is the only change today that improves the Purmer fits without a new parameter, but it needs the monthly IMPREX scaling and a local refresh routine first.
- Leave the pumping-station levels out.

## Open
- The user's choice of the shared set (now: `wl1+baro` 55, or `radar` + `wl1+baro` 56).
- **Heerhugowaard** with validated data: rerun the station test when the review is done (`--groups baseline,up,ip,pos,upfar,ipfar,posfar,radar` without `--raw-polder`).
- Radar for IMPREX: implement and test the monthly scaling, then rerun the extremes for the radar picks.
- The Purmer polder-level steps: if HHNK has the peilbesluit (summer/winter target levels by date), the instroompeil minus its target would be a cleaner stress.

## 7. Into the pipeline (same day, after the user's decision)
**The user's decision:**
- lake level + air pressure is the stress set;
- the precipitation source may differ per series (Berkhout *or* radar), as long as it is logged;
- "as many accepted models as we can".

**What was built** (see `pastas/README.md` §Which models and `docs/experiments/NEXT_STEPS.md` step 6):
- `tarso_pipeline.fit.EXTENDED_MODEL_TYPES`, 17 types:
  - the five standard ones;
  - `linear_*_lake_baro` and `flex_*_lake_baro`;
  - the same `*_radar`.
- `tarso_pipeline.inputs`: the inputs per model type and series. The polder comes from `object_data.csv`, and the IMPREX members are scaled per month for radar models.
- The data in `pastas/data/forcing/`, from `scripts/build_forcing_sources.py`.
- Every fit row records `precipitation_source`, `evaporation_source` and `extra_stresses`; the app shows *neerslag*, *verdamping* and *extra stresses*.
- The `return_levels` export is now parquet: 3.5 MB instead of a 74 MB CSV.

**The local pipeline run** (`scripts/run_local.py --imprex-dir data/forcing --export`, 2026-11-24):
- validated series, IMPREX extremes for all 1,541 models (all ok), the pipeline's own `select_models` with the runaway rule;
- 91 series, because two Heerhugowaard series were validated during the run.

| model types allowed | passed (of 91) | of the frozen 89 | Purmer / Beemster / Heerhugowaard | picks on radar | holdout R² median of the picks |
|---|---|---|---|---|---|
| standard 5 | 38 | 36 | 20 / 15 / 2 | 0 | 0.44 |
| + lake + pressure (11 types) | 56 | 54 | 31 / 22 / 2 | 0 | 0.47 |
| **extended: + the same on radar (17 types)** | **62** | **60** | **35 / 24 / 2** | 51 | **0.48** |

- No pick runs away. 37 of the 1,541 models do, and the selection avoids them.
- The pipeline's fits are identical to the research fits; AIC and R² were checked per model type.
- The 60 of the frozen 89 against the research count of 61: the research count had no runaway rule, and a few review stores changed.

## Reproduce
```bash
cd pastas
UV="uv run --no-project --python 3.11 --with pastas==1.12.* --with pandas<3 --with pyyaml --with requests --with scipy"
# data: the notebooks run on the committed CSVs (gemaal_peilen.ipynb needs matplotlib; knmi_radar.ipynb also h5py, pyproj)
$UV --with h5py --with pyproj python research/accepted/radar.py 2020 2026                     # ~15 min, ~5 GB download, deleted after
mkdir -p local_runs/research/gemaal && cp local_runs/research/accepted/{fit_metrics,model_checks}.csv local_runs/research/gemaal/
# the frozen 89 validated series (from the earlier run): local_runs/research/gemaal/ids_validated_89.csv
$UV python research/accepted/run_variants.py --groups up,ip,pos,posx,upfar,ipfar,posfar,up+ip,pos+baro,wl1+baro+pos,radar,radar+wl1+baro \
    --ids-from local_runs/research/gemaal/ids_validated_89.csv --out local_runs/research/gemaal        # ~35 min
$UV python research/accepted/compare_gemaal.py local_runs/research/gemaal
$UV python research/accepted/run_variants.py --groups baseline,up,ip,pos,upfar,ipfar,posfar,radar --raw-polder Heerhugowaard \
    --out local_runs/research/gemaal_hhw_raw                                                         # ~30 min
$UV python research/accepted/compare_gemaal.py local_runs/research/gemaal_hhw_raw
```

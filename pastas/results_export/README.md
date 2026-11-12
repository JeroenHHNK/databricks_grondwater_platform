results_export/<prefix>/ is written by notebooks/analysis/export_results.py; see that notebook.

    fit_metrics.csv  model_checks.csv  evt_selection.csv  return_levels.csv  simulations.parquet
    plots/<series_name>_return_periods.png   empirical points + the exp/gpd/gev curves (run_extremes)
    plots/<series_name>_qq.png               QQ per family (run_extremes)
    plots/<series_name>_confidence.png       GEV with 95% band (explore_shape_and_uncertainty)
    plots/<series_name>_results.png          ml.plots.results() of the fit (fit_pastas_models)
    plots/<series_name>_diagnostics.png      ml.plots.diagnostics() of the fit (fit_pastas_models)

plots/ holds one PNG per model and kind: the newest one over all run folders on the volume.
The app shows them under Pastas -> Familie kiezen (one model at a time: fit, return periods,
the pick per tail) and Pastas -> Modellen. The pick is saved to
../family_choice/<prefix>_family_choice.csv (locally), which the Boxplots read too.

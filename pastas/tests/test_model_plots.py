"""Figures only for the models in use: which stems, the export filter, pruning, drawing."""

import numpy as np
import pandas as pd
import pytest

from tarso_pipeline.model_plots import plot_models, prune_plots, stems_in_use
from tarso_pipeline.results import sync_plots


def _png(path, payload=b"x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def test_stems_in_use_include_the_old_tarso_name():
    in_use = pd.DataFrame({"meetreeks_id": ["a", "b"], "model_type": ["linear_dexp", "tarso"]})
    names = pd.DataFrame({"meetreeks_id": ["a", "b", "c"], "series_name": ["series_a", "series_b", "series_c"]})
    assert stems_in_use(in_use, names) == {"series_a_linear_dexp", "series_b_tarso", "series_b"}


def test_the_export_keeps_only_the_models_in_use(tmp_path):
    volume, target = tmp_path / "volume", tmp_path / "plots"
    for model in ("tarso", "linear_gamma", "linear_dexp"):
        _png(volume / "run_1" / "results_files" / f"series_a_{model}_results.png")
        _png(volume / "run_1" / "return_plots_fitted" / f"series_a_{model}_return_periods.png")
    _png(target / "series_a_tarso_results.png", b"was exported before")
    counts = sync_plots(volume, target, kinds=["results", "return_periods"], stems={"series_a_linear_dexp"})
    assert sorted(p.name for p in target.glob("*.png")) == [
        "series_a_linear_dexp_results.png", "series_a_linear_dexp_return_periods.png"]
    assert counts["removed"] == 1


def test_prune_removes_only_figures_of_models_not_in_use(tmp_path):
    run = tmp_path / "run_1"
    keep = _png(run / "results_files" / "series_a_linear_dexp_results.png")
    gone = _png(run / "qq_plots" / "series_a_tarso_qq.png")
    pas = _png(run / "pas_files" / "series_a_tarso.pas")
    pooled = _png(run / "pooled_extremes" / "series_a_tarso_dry.csv")
    assert prune_plots(str(run), {"series_a_linear_dexp"}, dry_run=True) == [str(gone)] and gone.exists()
    prune_plots(str(run), {"series_a_linear_dexp"})
    assert keep.exists() and pas.exists() and pooled.exists() and not gone.exists()


@pytest.mark.slow
def test_plot_models_draws_every_figure_from_the_run_folder(tmp_path):
    import matplotlib

    matplotlib.use("Agg")
    from tarso_pipeline.extremes_run import fit_pooled
    from tarso_pipeline.fit import build_model

    index = pd.date_range("2020-01-01", periods=1500, freq="D")
    rng = np.random.default_rng(0)
    p = pd.Series(rng.gamma(0.6, 3.0, 1500) / 1000, index=index)
    e = pd.Series(np.abs(np.sin(np.arange(1500) / 58.0)) * 2 / 1000, index=index)
    head = pd.Series(np.linspace(-2, -1, 900), index=index[600:])
    model = build_model("linear_gamma", "series_a", head, p, e)
    model.solve(report=False)
    (tmp_path / "pas_files").mkdir()
    model.to_file(str(tmp_path / "pas_files" / "series_a_linear_gamma.pas"))
    years = np.repeat(np.arange(1956, 2000), 16)
    pooled = {tail: pd.DataFrame({"member": np.tile(np.arange(16), 44), "year": years,
                                  "value": rng.normal(-3 if tail == "dry" else -1, 0.3, 704)})
              for tail in ("dry", "wet")}
    from tarso_pipeline.evt_report import VALUE_COLUMN

    pooled = {t: f.rename(columns={"value": VALUE_COLUMN[t]}) for t, f in pooled.items()}
    fit_pooled(pooled, "a", "series_a", str(tmp_path), "r", 16, make_plots=False, model_type="linear_gamma")
    assert not list(tmp_path.glob("**/*.png"))                       # the run itself drew nothing

    models = pd.DataFrame({"meetreeks_id": ["a"], "series_name": ["series_a"], "model_type": ["linear_gamma"]})
    drawn = plot_models(str(tmp_path), models)
    assert drawn["status"].tolist() == ["ok"], drawn["detail"].tolist()
    names = sorted(p.name for p in tmp_path.glob("**/*.png"))
    assert names == sorted(f"series_a_linear_gamma_{k}.png" for k in
                           ("results", "diagnostics", "return_periods", "threshold", "qq"))
    again = plot_models(str(tmp_path), models, only_missing=True)
    assert again["status"].tolist() == ["skipped"]

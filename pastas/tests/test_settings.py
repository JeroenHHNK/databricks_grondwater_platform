"""config/model_settings.toml: one place for the settings of every series."""

from pathlib import Path

import pytest

from tarso_pipeline import settings
from tarso_pipeline.settings import SETTINGS_FILE, Settings, load_settings, parse_settings


def test_the_defaults_are_what_the_pipeline_did_before_the_file():
    s = Settings()
    assert (s.fit.holdout, s.fit.min_days, s.fit.noise) == (0.3, 365, "none")
    assert (s.selection.aic_tolerance, s.selection.holdout_vs_tarso, s.selection.runaway_margin_m) == (2.0, 0.05, 1.5)
    assert s.selection.holdout_gap is None and s.selection.max_r2_loss is None
    assert (s.bucket.smax_mm, s.bucket.lp) == (150.0, 0.7)
    assert load_settings(Path("does/not/exist.toml")) == Settings()


def test_a_zero_threshold_is_kept_and_off_switches_a_rule_off():
    s = parse_settings('[selection]\nholdout_vs_without_bucket = 0.0\nholdout_gap = "off"\n')
    assert s.selection.holdout_vs_without_bucket == 0.0 and s.selection.holdout_gap is None


@pytest.mark.parametrize("text, match", [("[selction]\n", "unknown section"),
                                         ("[selection]\nholdout_gapp = 0.1\n", "unknown key")])
def test_a_typo_is_an_error_not_a_silent_default(text, match):
    with pytest.raises(ValueError, match=match):
        parse_settings(text)


def test_the_repo_file_reads_and_names_known_model_types():
    from tarso_pipeline.fit import CANDIDATE_MODEL_TYPES, EXTENDED_MODEL_TYPES, MODEL_SPECS

    s = load_settings(SETTINGS_FILE)
    assert s.source.endswith("config/model_settings.toml")
    assert set(s.fit.model_types) <= set(MODEL_SPECS)
    assert CANDIDATE_MODEL_TYPES == (s.fit.model_types or EXTENDED_MODEL_TYPES)
    assert s.selection.max_r2_loss is not None and s.selection.holdout_gap is not None


def test_the_bucket_label_follows_the_settings():
    from tarso_pipeline import inputs

    s = load_settings(SETTINGS_FILE)
    assert (inputs.BUCKET_SMAX_MM, inputs.BUCKET_LP) == (s.bucket.smax_mm, s.bucket.lp)
    if (s.bucket.smax_mm, s.bucket.lp) == (150.0, 0.7):
        assert inputs.STRESS_SOURCES["bucket"] == "bucket_150_07"


def test_the_overrides_file_in_the_repo_reads():
    from tarso_pipeline.overrides import OVERRIDES_FILE, read_overrides

    assert OVERRIDES_FILE.parent == settings.CONFIG_DIR
    assert read_overrides().empty  # raises on a wrong row; empty until someone adds one

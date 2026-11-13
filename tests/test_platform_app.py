"""Tests for the central app's own code (platform_app/): run from the repo root with `uv run pytest tests`."""

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
for pad in (ROOT, ROOT / "boxplots", ROOT / "data_validation", ROOT / "pastas" / "src"):
    if str(pad) not in sys.path:
        sys.path.insert(0, str(pad))

from modules import databricks_reeksen  # noqa: E402
from platform_app import stijghoogtes_data as data  # noqa: E402
from platform_app import toegang  # noqa: E402


@pytest.mark.parametrize("code, zone", [
    ("B_D11-DP_79+1_BIKR_GMW_PB2_F-4.26", "BIKR"),
    ("B_D11-DP_70+6_BUKR_GMW_PB1_F-1.71", "BUKR"),
    ("B_D11-DP_75+5_INBIB_GMW_PB2_F-8.28", "INBIB"),
    ("X_INSTEEK_GMW", "INST"),
    ("HB483PB01", data.ONBEKEND),
    (None, data.ONBEKEND),
])
def test_zone_token(code, zone):
    assert data.zone_token(code) == zone


def _bron():
    reeksen = pd.DataFrame({
        "meetlocatie_id": ["loc1", "loc2"], "meetreeks_id": ["r1", "r2"], "eenheid": ["m NAP", "m NAP"],
        "metingen": [10, 5],
    })
    dag = pd.DataFrame({
        "meetreeks_id": ["r1", "r1", "r2"],
        "datum": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-01"]),
        "gem": [-1.0, -1.2, 0.5], "min": [-1.1, -1.3, 0.4], "max": [-0.9, -1.1, 0.6], "n_status_1": [0, 2, 0],
    })
    return databricks_reeksen.Bron(databricks_reeksen.EXPORT, reeksen, dag)


def _config():
    return pd.DataFrame({
        "LD_project": ["P", "P", "P"], "LD_cross_section_key": ["DP1", "DP1", "DP1"],
        "LD_diepte_niveau": ["hoofd", "A", "hoofd"], "LD_peilbuis": ["PB1", "PB2", "PB3"],
        "LD_code": ["x_BIKR_GMW_PB1", "x_BIKR_GMW_PB2", "x_BUKR_GMW_PB1"],
        "LD_x_profiel": [50.0, 50.0, 40.0], "LD_bk_peilbuis": [3.5, 3.5, 3.6],
        "LD_bk_filter": [-1.0, -4.0, -1.2], "LD_ok_filter": [-2.0, -5.0, -2.2],
        "DD_meetlocatie_id": ["loc1", "loc2", None], "DD_meetreeks_id": [None, None, None],
    })


def test_catalogus_links_wells_to_series_and_zones():
    cat = data.catalogus(_bron(), _config())
    assert list(cat.columns) == data.KOLOMMEN
    rij = cat.set_index("peilbuis")
    assert rij.loc["PB1", "meetreeks_id"] == "r1" and rij.loc["PB1", "zone"] == "BIKR"
    assert rij.loc["PB2", "meetreeks_id"] == "r2" and rij.loc["PB2", "diepte_niveau"] == "A"
    assert pd.isna(rij.loc["PB3", "meetreeks_id"]) and rij.loc["PB3", "zone"] == "BUKR"
    assert data.zones_in(cat) == ["BUKR", "BIKR"]


@pytest.mark.parametrize("profiel, code, naam", [
    ("BE_0072", "B_BE0072+3_BUKR_GMW_PB1_F-385", "BE_0072_BUKR_PB1"),
    ("BE_0072", "B_BE0072+3_BIT_GMW_PB2_F-769", "BE_0072_BIT_PB2"),
    ("BE_0188", "B_BE0188+23_KR_GMW_PB1_F-547", "BE_0188_KR_PB1"),
    (None, "B_BE0072+3_BUKR_GMW_PB1_F-385", None),
])
def test_profielnaam(profiel, code, naam):
    assert data.profielnaam(profiel, code) == naam


def test_profielnamen_give_each_series_its_cross_section():
    namen = data.profielnamen(data.catalogus(_bron(), _config())).set_index("meetreeks_id")
    assert set(namen.index) == {"r1", "r2"}
    assert "_BIKR" in namen.loc["r1", "profielnaam"]


def test_samenvatting_and_breed():
    b = _bron()
    cat = data.catalogus(b, _config()).dropna(subset=["meetreeks_id"])
    dag = data.dagwaarden(b, cat["meetreeks_id"].tolist())
    s = data.samenvatting(cat, dag).set_index("peilbuis")
    assert s.loc["PB1", "dagen"] == 2 and s.loc["PB1", "laagste"] == -1.2
    wide = data.breed(dag, {"r1": "PB1", "r2": "PB2"})
    assert list(wide.columns) == ["PB1", "PB2"] and len(wide) == 2


class _Cursor:
    """A DB-API cursor that fails on statements containing any of `weiger`."""

    def __init__(self, weiger=(), tabellen=()):
        self.weiger, self.tabellen, self.sql, self.laatste = weiger, tabellen, [], ""

    def execute(self, sql):
        self.sql.append(sql)
        self.laatste = sql
        if any(w in sql for w in self.weiger):
            raise RuntimeError(f"PERMISSION_DENIED: {sql.split()[0]}")

    def fetchone(self):
        return ["app-sp-123"] if "current_user" in self.laatste else [0]

    def fetchall(self):
        if self.laatste.startswith("SHOW TABLES"):
            return [("s", t, False) for t in self.tabellen]
        return [(1,)]


def test_controleer_all_good_is_read_only():
    cursor = _Cursor(tabellen=["validated_meting"])
    wie, uit = toegang.controleer(cursor, "cat.schema")
    assert wie == "app-sp-123" and all(u.gelukt for u in uit)
    assert not any(s.startswith(("CREATE", "INSERT", "DROP")) for s in cursor.sql)


def test_controleer_reports_missing_read_grant_with_sql():
    wie, uit = toegang.controleer(_Cursor(weiger=["hl_cs_objecten.meetlocatie"]), "cat.schema")
    fout = [u for u in uit if not u.gelukt]
    assert len(fout) == 1 and "meetlocatie" in fout[0].controle
    assert "GRANT SELECT ON TABLE gold_prd.hl_cs_objecten.meetlocatie TO `app-sp-123`" in fout[0].oplossing


def test_controleer_checks_the_pastas_tables_that_exist():
    cursor = _Cursor(tabellen=["validated_meting", "valid_pastas_fit_metrics", "unvalid_pastas_fit_metrics"],
                     weiger=["unvalid_pastas_fit_metrics"])
    _, uit = toegang.controleer(cursor, "cat.schema")
    fout = [u for u in uit if not u.gelukt]
    assert [u.controle for u in fout] == ["Lezen: cat.schema.unvalid_pastas_fit_metrics"]
    assert fout[0].oplossing == "GRANT SELECT ON TABLE cat.schema.unvalid_pastas_fit_metrics TO `app-sp-123`;"
    assert any(u.controle == "Lezen: cat.schema.valid_pastas_fit_metrics" and u.gelukt for u in uit)
    assert "valid_pastas_return_levels" in uit[-1].detail                 # not there yet: one line


def test_schrijftest_cleans_up_after_a_failed_insert():
    cursor = _Cursor(weiger=["INSERT"])
    _, uit = toegang.controleer(cursor, "cat.schema", schrijftest=True)
    schrijf = uit[-1]
    assert not schrijf.gelukt and schrijf.detail.startswith("INSERT")
    assert "CREATE TABLE ON SCHEMA cat.schema" in schrijf.oplossing
    assert cursor.sql[-1] == f"DROP TABLE IF EXISTS cat.schema.{toegang.SCHRIJFTEST_TABEL}"


# --- Peilbuizen map -----------------------------------------------------------------------------

def _meta():
    return pd.DataFrame({
        "filename": ["A1", "A2", "B1"], "code": ["HB1-PB1", "HB1-PB2", "X"], "vendor": ["fugro", "fugro", "wiertsema"],
        "cs_naam": ["Dijk 1", "Dijk 1", None], "rd_x": [100.0, 100.04, None], "rd_y": [200.0, 200.0, None],
        "map_lon": [4.9, 4.9, None], "map_lat": [52.6, 52.6, None], "has_location": [True, True, False],
        "eind_filter": [-4.0, -1.5, -2.0],
    })


def test_filters_of_one_well_share_one_marker_shallowest_first():
    from platform_app import peilbuizen_data as pb

    locs = pb.locaties(_meta())
    assert len(locs) == 1 and locs.loc[0, "n_reeksen"] == 2
    assert pb.reeksen_op_locatie(_meta(), locs.loc[0, "loc"])["filename"].tolist() == ["A2", "A1"]
    assert pb.label(next(_meta().itertuples())) == "HB1-PB1 · Dijk 1 · fugro"


def test_daily_values_from_export_and_validated_store(tmp_path):
    from local_validation import validation_store as vs
    from platform_app import peilbuizen_data as pb

    pad = tmp_path / "A1.csv"
    pd.DataFrame({"Time": pd.date_range("2024-01-01", periods=48, freq="h"), "head": 1.0}).to_csv(pad, index=False)
    dag = pb.dag_uit_export(pad)
    assert dag["n"].tolist() == [24, 24] and dag["gem"].tolist() == [1.0, 1.0]
    assert pb.dag_uit_export(tmp_path / "missing.csv").empty

    store = vs.ensure_columns(pd.DataFrame({"timestamp": pd.date_range("2024-01-01", periods=48, freq="h"),
                                            "raw_data": 1.0, "gecontroleerd": 1, "afgekeurd": 0}))
    store.loc[:23, "afgekeurd"] = 1                          # day 1 rejected
    store, _ = vs.set_correction(store, "2024-01-02", "2024-01-03", 0.1)
    val = pb.dag_gevalideerd(store)
    assert val["datum"].tolist() == [pd.Timestamp("2024-01-02")] and val["gem"].tolist() == [1.1]


# --- Pastas return levels in the cross section ---------------------------------------------------

def test_return_levels_land_on_the_wells_of_the_cross_section():
    import plotly.graph_objects as go
    from platform_app import herhalingstijden as h

    putten = pd.DataFrame({"meetreeks_id": ["r1", "r2", None], "x_profiel": [10.0, 30.0, 50.0],
                           "peilbuis": ["PB1", "PB2", "PB3"], "diepte_niveau": "hoofd"})
    niveaus = pd.DataFrame({"meetreeks_id": ["r1", "r1", "r1", "r9"], "model_type": "tarso",
                            "tail": ["wet", "wet", "dry", "wet"], "family": "gev",
                            "return_period": [10, 100, 10, 10], "level": [0.5, 0.8, -2.0, 9.0]})
    pt = h.punten(putten, niveaus, [10, 100])
    assert set(pt["peilbuis"]) == {"PB1"} and len(pt) == 3          # r2 has no model, r9 is elsewhere
    fig = go.Figure(go.Scatter(x=[0], y=[0]), layout={"yaxis": {"range": [-1, 1]}})
    uit = h.voeg_toe(fig, pt)
    assert len(fig.data) == 1 and len(uit.data) == 4                  # the original is not changed
    assert tuple(uit.layout.yaxis.range) == (-2.3, 1.1)               # widened to show every marker


def test_the_return_levels_come_from_the_model_each_series_uses():
    from platform_app import herhalingstijden as h

    putten = pd.DataFrame({"meetreeks_id": ["r1", "r2"], "x_profiel": [10.0, 30.0], "peilbuis": ["PB1", "PB2"],
                           "diepte_niveau": "hoofd"})
    niveaus = pd.DataFrame({"meetreeks_id": ["r1", "r1", "r2", "r2"],
                            "model_type": ["tarso", "linear_gamma", "tarso", "linear_gamma"],
                            "tail": "wet", "family": "emp", "return_period": 10, "level": [1.0, 2.0, 3.0, 4.0]})
    gebruikt = pd.DataFrame({"meetreeks_id": ["r1", "r2"], "model_type": ["linear_gamma", "tarso"]})
    pt = h.punten(putten, niveaus, [10], gebruikt).set_index("peilbuis")
    assert pt["level"].to_dict() == {"PB1": 2.0, "PB2": 3.0}
    assert pt["model_type"].to_dict() == {"PB1": "linear_gamma", "PB2": "tarso"}
    assert h.punten(putten, niveaus, [10])["level"].tolist() == [1.0, 3.0]   # no selection: Tarso


def test_the_models_table_gets_the_stamp_and_the_model_in_use():
    from platform_app.pastas_pagina import voeg_selectie_toe

    tabel = pd.DataFrame({"meetreeks_id": ["r1", "r1"], "model_type": ["tarso", "linear_dexp"]})
    selectie = pd.DataFrame({"meetreeks_id": ["r1", "r1"], "model_type": ["tarso", "linear_dexp"],
                             "passed": [False, True], "selected": [False, True], "d_aic_tarso": [0.0, -297.0]})
    gebruikt = pd.DataFrame({"meetreeks_id": ["r1"], "model_type": ["linear_dexp"], "bron": ["automatisch"]})
    uit = voeg_selectie_toe(tabel, selectie, gebruikt).set_index("model_type")
    assert uit.loc["linear_dexp", ["stempel", "in_gebruik"]].tolist() == ["passed", "✓ automatisch"]
    assert uit.loc["tarso", ["stempel", "in_gebruik"]].tolist() == ["", ""]
    leeg = voeg_selectie_toe(tabel, pd.DataFrame(), gebruikt.iloc[:0])     # a run from before the selection
    assert leeg["stempel"].tolist() == ["", ""] and leeg["in_gebruik"].tolist() == ["", ""]


def test_a_return_period_is_one_line_across_the_wells_sorted_by_x():
    import plotly.graph_objects as go
    from platform_app import herhalingstijden as h

    # PB2 has two filters at the same x: the line goes through their mean, not up and down.
    pt = pd.DataFrame({"x_profiel": [30.0, 10.0, 30.0], "peilbuis": ["PB2", "PB1", "PB2"],
                       "diepte_niveau": "hoofd", "tail": "wet", "return_period": 10,
                       "level": [1.0, 0.5, 2.0], "family": "gev", "meetreeks_id": ["r2", "r1", "r3"]})
    uit = h.voeg_toe(go.Figure(), pt)
    lijn = uit.data[-1]
    assert lijn.mode == "lines+markers"
    assert list(lijn.x) == [10.0, 30.0] and list(lijn.y) == [0.5, 1.5]


def test_pastas_results_come_from_the_export_when_there_is_no_warehouse(monkeypatch, tmp_path):
    from platform_app import pastas_bron
    from tarso_pipeline import results

    monkeypatch.delenv("DATABRICKS_WAREHOUSE_ID", raising=False)
    monkeypatch.setattr(pastas_bron, "PASTAS", tmp_path)
    monkeypatch.setattr(pastas_bron, "ROOT", tmp_path)
    leeg = pastas_bron.lees("validated", "fit_metrics")
    assert leeg.bron == pastas_bron.GEEN and leeg.data.empty
    uit = results.export_dir(tmp_path, "validated")
    uit.mkdir(parents=True)
    pd.DataFrame({"meetreeks_id": ["a", "a"], "model_type": "tarso", "run_id": ["1", "2"],
                  "r2": [0.1, 0.9]}).to_csv(uit / "fit_metrics.csv", index=False)
    r = pastas_bron.lees("validated", "fit_metrics")
    assert r.bron == pastas_bron.EXPORT and r.data["r2"].tolist() == [0.9]   # the latest run only


def test_the_family_overview_shows_per_model_what_is_picked():
    from platform_app.pastas_familie import overzicht

    tabel = pd.DataFrame({"meetreeks_id": ["a", "b", "c"], "model_type": "tarso",
                          "series_name": ["sa", "sb", "sc"], "code": ["A", "B", "C"]})
    keuze = pd.DataFrame({"meetreeks_id": ["a", "a", "b"], "series_name": ["sa", "sa", "sb"],
                          "model_type": "tarso", "tail": ["dry", "wet", "wet"], "family": ["gpd", "exp", "gev"],
                          "n_kept": ["200", "", ""], "note": ["lang", "", ""], "chosen_by": "jeroen",
                          "chosen_at": "2026-11-12T15:09:09+01:00"})
    uit = overzicht(tabel, keuze).set_index("series_name")
    # b's only pick is an old gev one, which no longer counts.
    assert uit["status"].to_dict() == {"sa": "klaar", "sb": "open", "sc": "open"}
    assert uit.loc["sa", ["droog", "drempel droog", "drempel nat", "notitie droog"]].tolist() == [
        "GPD (staart vlakt af)", "200", "standaard", "lang"]
    assert uit.loc["sb", "nat"] == "gev (opnieuw kiezen)" and uit.loc["sb", "droog"] == "–"
    assert uit.loc["sa", "op droog"] == "2026-11-12 15:09"

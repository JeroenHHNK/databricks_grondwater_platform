"""Tests for the central app's own code (platform_app/): run from the repo root with `uv run pytest tests`."""

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
for pad in (ROOT, ROOT / "boxplots", ROOT / "data_validation"):
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


def test_schrijftest_cleans_up_after_a_failed_insert():
    cursor = _Cursor(weiger=["INSERT"])
    _, uit = toegang.controleer(cursor, "cat.schema", schrijftest=True)
    schrijf = uit[-1]
    assert not schrijf.gelukt and schrijf.detail.startswith("INSERT")
    assert "CREATE TABLE ON SCHEMA cat.schema" in schrijf.oplossing
    assert cursor.sql[-1] == f"DROP TABLE IF EXISTS cat.schema.{toegang.SCHRIJFTEST_TABEL}"

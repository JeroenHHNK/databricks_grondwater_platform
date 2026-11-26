"""De invoercontrole en de keuze van een meetreeks per peilbuis.

Dezelfde controles draaien in de app, in `maak_plots.py` en in `nieuw_dwarsprofiel.py`
(`workbook_lader.controleer_peilbuizen` en `valideer`); de keuze van de reeks per peilbuis
is `databricks_reeksen.kies_reeksen`. Zie de README, "Wat de controles betekenen".
"""

from __future__ import annotations

import pandas as pd
import pytest

from modules import databricks_reeksen as dr
from modules import paths, workbook_lader as wl

GEOMETRIE = pd.DataFrame({"x": [0.0, 10.0, 20.0, 30.0], "y": [2.0, 3.0, 1.0, 0.0]})
LIJN = (0.0, 0.0, 30.0, 0.0)  # langs de x-as: x in het profiel = x in RD


def _rij(peilbuis="PB1", code="B_X_BUKR_GMW_PB1", x=10.0, bk_pb=3.5, bk_f=1.0, ok_f=0.0,
         x_rd=None, y_rd=0.0, niveau="hoofd"):
    return {"LD_peilbuis": peilbuis, "LD_diepte_niveau": niveau, "LD_code": code, "LD_x_profiel": x,
            "LD_bk_peilbuis": bk_pb, "LD_bk_filter": bk_f, "LD_ok_filter": ok_f,
            "LD_x_rd": x if x_rd is None else x_rd, "LD_y_rd": y_rd}


def _meldingen(*rijen, lijn=LIJN):
    return [(b.ernst, b.melding) for b in wl.controleer_peilbuizen("K", pd.DataFrame(list(rijen)), GEOMETRIE, lijn)]


def test_een_goede_peilbuis_geeft_geen_meldingen():
    assert _meldingen(_rij()) == []


@pytest.mark.parametrize("rij, ernst, tekst", [
    (_rij(bk_f=0.0, ok_f=1.0), "fout", "ligt niet boven onderkant"),
    (_rij(bk_pb=3.5, bk_f=4.0, ok_f=0.0), "fout", "boven de bovenkant peilbuis"),
    (_rij(x=45.0), "fout", "buiten de geometrie"),
    (_rij(bk_pb=6.0), "waarschuwing", "t.o.v. het maaiveld"),
    (_rij(y_rd=8.0), "waarschuwing", "naast de profiellijn"),
    (_rij(x_rd=12.0), "waarschuwing", "wijkt"),
])
def test_elke_controle_vangt_zijn_geval(rij, ernst, tekst):
    found = _meldingen(rij)
    assert any(e == ernst and tekst in m for e, m in found), found


def test_zonder_profiellijn_geen_controle_op_ligging():
    assert _meldingen(_rij(y_rd=8.0, x_rd=12.0), lijn=None) == []


def test_zones_in_de_verkeerde_volgorde():
    kruin = _rij("PB1", "B_X_BUKR_GMW_PB1", x=20.0, bk_pb=1.5)
    teen = _rij("PB2", "B_X_BIT_GMW_PB1", x=10.0, bk_pb=3.5)
    assert any("volgorde" in m for _, m in _meldingen(kruin, teen))
    assert not any("volgorde" in m for _, m in _meldingen({**kruin, "LD_x_profiel": 5.0, "LD_x_rd": 5.0,
                                                           "LD_bk_peilbuis": 3.0}, teen))


def test_projectie_op_de_lijn():
    langs, loodrecht = wl.projecteer_op_lijn((0.0, 0.0, 10.0, 0.0), 4.0, -3.0)
    assert langs == pytest.approx(4.0) and loodrecht == pytest.approx(3.0)


def test_profiel_id():
    assert wl.profiel_id("BE_0072", "hoofd") == "BE_0072"
    assert wl.profiel_id("BE_0072", "A") == "BE_0072_A"


# --- per peilbuis één meetreeks -------------------------------------------------------

def _config(**extra):
    return pd.DataFrame([{"LD_project": "P", "LD_peilbuis": "PB1", "DD_meetlocatie_id": "loc",
                          "DD_meetreeks_id": None, **extra}])


REEKSEN = pd.DataFrame({
    "meetlocatie_id": ["loc", "loc", "loc"],
    "meetreeks_id": ["a", "b", "c"],
    "eenheid": ["m NAP", "m NAP", "cm"],
    "metingen": [10, 50, 999],
})


def test_de_reeks_met_de_meeste_metingen_in_m_nap():
    uit = dr.kies_reeksen(_config(), REEKSEN).iloc[0]
    assert uit["meetreeks_id"] == "b" and uit["keuze"] == dr.MEESTE and uit["kandidaten"] == 2


def test_een_vastgezette_reeks_wint():
    uit = dr.kies_reeksen(_config(DD_meetreeks_id="a"), REEKSEN).iloc[0]
    assert uit["meetreeks_id"] == "a" and uit["keuze"] == dr.VASTGEZET
    uit = dr.kies_reeksen(_config(DD_meetreeks_id="zz"), REEKSEN).iloc[0]
    assert uit["meetreeks_id"] is None and uit["keuze"] == dr.VASTGEZET_ONBEKEND


@pytest.mark.parametrize("config, keuze", [
    (_config(DD_meetlocatie_id=None), dr.GEEN_ID),
    (_config(DD_meetlocatie_id="elders"), dr.GEEN_REEKS),
])
def test_waarom_er_geen_reeks_is(config, keuze):
    uit = dr.kies_reeksen(config, REEKSEN).iloc[0]
    assert uit["meetreeks_id"] is None and uit["keuze"] == keuze


def test_geen_reeks_in_m_nap():
    uit = dr.kies_reeksen(_config(), REEKSEN[REEKSEN["eenheid"] == "cm"]).iloc[0]
    assert uit["meetreeks_id"] is None and uit["keuze"] == dr.GEEN_NAP


# --- de invoer in git ------------------------------------------------------------------

@pytest.mark.parametrize("project", paths.projecten())
def test_de_invoer_in_git_heeft_geen_fouten(project):
    """Wat `maak_plots.py --strict` zou weigeren: een fout stopt het project ook in de app."""
    fouten = [str(b) for b in wl.valideer(wl.laad_project(project)) if b.ernst == "fout"]
    assert not fouten, "\n".join(fouten)

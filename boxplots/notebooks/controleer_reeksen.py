# controleer_reeksen.py
"""
Welke peilbuizen en meetreeksen verdienen een controle, en waarom.

    uv run python notebooks/controleer_reeksen.py

Leest `input_data/config.csv` en de Databricks-export in
`input_data/databricks_export/` (niet live: de uitschieters staan alleen in de
export), en schrijft
`input_data/controle/te_controleren.csv`: één rij per bevinding per
peilbuis, met de sleutels om de reeks in Databricks terug te vinden.

| categorie | betekent |
|---|---|
| geen meetlocatie_id | niet gekoppeld; `toelichting` geeft `DD_koppeling` |
| geen meetreeks | de meetlocatie heeft geen enkele meetreeks |
| geen reeks in m NAP | alleen reeksen in een andere eenheid, of zonder metingen |
| vastgezette reeks onbekend | `DD_meetreeks_id` in config.csv hoort niet bij de meetlocatie |
| meerdere reeksen | parallelle sensoren; welke gekozen is en welke niet |
| filter wijkt af | bovenkant/onderkant filter in config.csv vs Databricks |
| bovenkant peilbuis wijkt af | idem |
| uitschieters | metingen meer dan 0,5 m van de dagmediaan van hun reeks |
| korte reeks | minder dan KORT_DAGEN dagen met metingen |
| reeks gestopt | laatste meting ruim voor de laatste meting in de export |
| andere eenheid op meetlocatie | naast de stijghoogte nog reeksen in een andere eenheid (temperatuur, druk) |

Bevindingen over de gekozen reeks gaan alleen over die reeks.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
from pandas import DataFrame

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules import databricks_reeksen as db, paths, workbook_lader  # noqa: E402

FILTER_DREMPEL_M: float = 0.25  # Databricks rondt filters af op 0,01 m, bovenkant peilbuis op 0,1 m
KORT_DAGEN: int = 180
GESTOPT_DAGEN: int = 30
UITVOER: str = "te_controleren.csv"

VOLGORDE: list[str] = [
    "geen meetlocatie_id", "geen meetreeks", "geen reeks in m NAP", "vastgezette reeks onbekend",
    "meerdere reeksen", "filter wijkt af",
    "bovenkant peilbuis wijkt af", "uitschieters", "korte reeks", "reeks gestopt",
    "andere eenheid op meetlocatie",
]


def bevindingen() -> DataFrame:
    config: DataFrame = workbook_lader.lees_config()
    reeksen: DataFrame = db.lees_reeksen()
    afw: DataFrame = pd.read_csv(db.export_map() / "afwijkingen.csv", parse_dates=["tijdstempel_utc"])
    keuze: DataFrame = db.kies_reeksen(config, reeksen)
    info: DataFrame = reeksen.dropna(subset=["reeks_nr"]).drop_duplicates("reeks_nr").set_index("reeks_nr")
    laatste_export = pd.to_datetime(info["tot"]).max()

    per_put: DataFrame = config.groupby(["LD_project", "LD_peilbuis"], sort=False).agg(
        cross_section_key=("LD_cross_section_key", lambda s: "/".join(dict.fromkeys(s))),
        diepte_niveau=("LD_diepte_niveau", "first"),
        koppeling=("DD_koppeling", "first"),
        bk_filter=("LD_bk_filter", "first"),
        ok_filter=("LD_ok_filter", "first"),
        bk_peilbuis=("LD_bk_peilbuis", "first"),
    )
    rijen: list[dict] = []

    def voeg_toe(k, categorie: str, toelichting: str, nr=None) -> None:
        put = per_put.loc[(k.project, k.peilbuis)]
        r = info.loc[nr] if nr is not None and nr in info.index else None
        rijen.append({
            "categorie": categorie,
            "project": k.project,
            "cross_section_key": put["cross_section_key"],
            "diepte_niveau": put["diepte_niveau"],
            "peilbuis": k.peilbuis,
            "meetreeks_code": None if r is None else r["meetreeks_code"],
            "toelichting": toelichting,
            "meetlocatie_id": k.meetlocatie_id,
            "meetreeks_id": None if r is None else r["meetreeks_id"],
        })

    for k in keuze.itertuples():
        put = per_put.loc[(k.project, k.peilbuis)]
        if k.keuze == db.GEEN_ID:
            voeg_toe(k, "geen meetlocatie_id", f"koppeling: {put['koppeling']}")
            continue
        alle = reeksen[reeksen["meetlocatie_id"] == k.meetlocatie_id].drop_duplicates("meetreeks_id")
        if k.keuze == db.GEEN_REEKS:
            voeg_toe(k, "geen meetreeks", "de meetlocatie bestaat, maar heeft geen meetreeksen")
            continue
        if k.keuze == db.GEEN_NAP:
            soorten = ", ".join(
                f"{x.meetreeks_code} ({x.eenheid}, {0 if pd.isna(x.metingen) else int(x.metingen)} metingen)"
                for x in alle.itertuples()
            )
            voeg_toe(k, "geen reeks in m NAP", soorten)
            continue
        if k.keuze == db.VASTGEZET_ONBEKEND:
            voeg_toe(k, "vastgezette reeks onbekend", "de DD_meetreeks_id in config.csv staat niet bij deze meetlocatie")
            continue

        nr = int(k.reeks_nr)
        r = info.loc[nr]
        if k.kandidaten > 1:
            andere = alle[alle["meetreeks_id"] != r["meetreeks_id"]]
            voeg_toe(k, "meerdere reeksen",
                     f"gekozen ({k.keuze}): {r['meetreeks_code']} {int(r['metingen'])} metingen, gem {r['gem']}; "
                     + "niet gekozen: " + "; ".join(
                         f"{x.meetreeks_code} {0 if pd.isna(x.metingen) else int(x.metingen)} metingen, gem {x.gem}"
                         for x in andere.itertuples()), nr)

        for naam, eigen, platform in (("bovenkant filter", put["bk_filter"], r["begin_filter"]),
                                      ("onderkant filter", put["ok_filter"], r["eind_filter"])):
            if pd.notna(eigen) and pd.notna(platform) and abs(float(eigen) - float(platform)) > FILTER_DREMPEL_M:
                voeg_toe(k, "filter wijkt af", f"{naam}: config {float(eigen):.2f}, Databricks {float(platform):.2f} m NAP", nr)
        if pd.notna(put["bk_peilbuis"]) and pd.notna(r["bovenkant_peilbuis"]) \
                and abs(float(put["bk_peilbuis"]) - float(r["bovenkant_peilbuis"])) > FILTER_DREMPEL_M:
            voeg_toe(k, "bovenkant peilbuis wijkt af",
                     f"config {float(put['bk_peilbuis']):.2f}, Databricks {float(r['bovenkant_peilbuis']):.1f} m NAP", nr)

        eigen_afw = afw[afw["reeks_nr"] == nr]
        if not eigen_afw.empty:
            voeg_toe(k, "uitschieters",
                     f"{len(eigen_afw)} metingen op {eigen_afw['tijdstempel_utc'].dt.date.nunique()} dagen, "
                     f"tot {eigen_afw['afwijking'].abs().max():.2f} m van de dagmediaan "
                     f"({eigen_afw['tijdstempel_utc'].min():%Y-%m-%d} t/m {eigen_afw['tijdstempel_utc'].max():%Y-%m-%d})", nr)
        if r["dagen"] < KORT_DAGEN:
            voeg_toe(k, "korte reeks", f"{int(r['dagen'])} dagen, {r['van'][:10]} t/m {r['tot'][:10]}", nr)
        anders = alle[alle["eenheid"] != db.EENHEID]
        if not anders.empty:
            voeg_toe(k, "andere eenheid op meetlocatie", ", ".join(
                f"{x.meetreeks_code} ({x.eenheid}, {0 if pd.isna(x.metingen) else int(x.metingen)} metingen)"
                for x in anders.itertuples()), nr)
        if (laatste_export - pd.to_datetime(r["tot"])).days > GESTOPT_DAGEN:
            voeg_toe(k, "reeks gestopt", f"laatste meting {r['tot'][:10]}, export loopt tot {laatste_export:%Y-%m-%d}", nr)

    uit = DataFrame(rijen)
    uit["volgorde"] = uit["categorie"].map({c: i for i, c in enumerate(VOLGORDE)})
    return uit.sort_values(["volgorde", "project", "cross_section_key", "peilbuis"]).drop(columns="volgorde")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    tabel: DataFrame = bevindingen()
    pad: Path = paths.controle_map() / UITVOER
    tabel.to_csv(pad, index=False)
    print(f"✅ {pad.relative_to(paths.repo_root())}: {len(tabel)} bevindingen bij "
          f"{tabel[['project', 'peilbuis']].drop_duplicates().shape[0]} peilbuizen\n")
    print(pd.crosstab(tabel["categorie"], tabel["project"], margins=True)
          .reindex([c for c in VOLGORDE if c in set(tabel["categorie"])] + ["All"]).to_string())

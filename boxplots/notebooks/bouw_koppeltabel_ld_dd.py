# bouw_koppeltabel_ld_dd.py
"""
Eén koppeltabel tussen onze peilbuizen en de meetreeksen op Databricks.

    uv run python notebooks/bouw_koppeltabel_ld_dd.py
    uv run python notebooks/bouw_koppeltabel_ld_dd.py --vul-config

Leest:
- LD_ (lokale data): `input_data/config.csv`, per project, met het profiel
  waaronder de peilbuis in de app staat;
- DD_ (Databricks-data): `input_data/databricks_export/meetreeksen.csv`, alle
  meetreeksen op het platform met hun meetlocatie, uit
  `notebooks/db_notebook_exporteer_meetreeksen.py`. Is die er nog niet, dan
  de oudere export `object_data_databricks_dwarsprofielen.csv` in dezelfde map.

Van de DD-kant doen alleen de reeksen mee die binnen VOORSELECTIE_M van een
van onze peilbuizen liggen, of dezelfde naam (put + filter) hebben; dat laatste
vangt meetlocaties zonder coördinaten.

Schrijft `input_data/controle/koppeltabel_LD_DD.csv`, met dezelfde opbouw als de
DD-export (één rij per meetreeks, dezelfde kolommen met DD_ ervoor) en de
peilbuis uit config.csv ernaast. Daarnaast een rij per peilbuis zonder
meetreeks en per meetreeks zonder peilbuis, zodat zichtbaar is wat ontbreekt.
Een peilbuis die in twee profielen staat, krijgt twee rijen.

De MATCH_-kolommen zeggen hoe zeker een koppeling is:

| MATCH_methode       | betekent                                                    |
|---------------------|-------------------------------------------------------------|
| locatie+naam+filter | binnen MAX_AFSTAND_M, zelfde put en filternummer            |
| locatie+filter      | binnen MAX_AFSTAND_M, zelfde filternummer, andere naam      |
| naam+project        | geen coördinaten, wel zelfde put+filter en projectnummer    |
| alleen naam         | geen coördinaten en geen projectnummer: alleen de naam klopt |

Per peilbuis blijven alleen de kandidaten van de beste methode staan, zodat de
buurput op 4 m niet naast de put zelf verschijnt. Kandidaat 1 is de meest
waarschijnlijke: zelfde naam, genoeg metingen, kleinste verschil in filterdiepte.

MATCH_status `meerdere sensoren` betekent: meer dan één reeks met dezelfde
sensorsleutel op dezelfde put, meestal een vervangen logger (KELLER naast
MSLV8). Die reeksen horen vermoedelijk achter elkaar, niet als alternatief. Op naam
alleen vindt HB002 in Beemster ook HB02 kilometers verderop, dus een naam die
op een bekende afstand of in een ander project ligt, telt niet mee.

`config.csv` blijft de bron. `--vul-config` zet daarin, per peilbuis:
- `DD_koppeling` op de status van kandidaat 1;
- een lege `DD_meetlocatie_id` op `DD_cs_meetlocatie_id`, maar alleen bij
  status `gekoppeld` of `meerdere sensoren` (één meetlocatie). Een ingevulde
  `DD_meetlocatie_id` wordt nooit overschreven; `twijfelachtig` en
  `meerdere kandidaten` blijven handwerk;
- bij elke `DD_meetlocatie_id`: `DD_meetlocatie_code`, `DD_x_rd`, `DD_y_rd`
  van die meetlocatie en `DD_afstand_m` tot `LD_x_rd`, `LD_y_rd`. Zo is te zien
  of de koppeling klopt. Alleen `LD_`-kolommen vul je zelf in.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from pandas import DataFrame

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules import paths, platform_match, workbook_lader  # noqa: E402

DD_EXPORT: str = "meetreeksen.csv"
DD_EXPORT_OUD: str = "object_data_databricks_dwarsprofielen.csv"
UITVOER: str = "koppeltabel_LD_DD.csv"

# Peilbuizen in één put staan op dezelfde plek; een andere put op hetzelfde
# profiel staat er meters vandaan. 5 m scheidt die twee.
MAX_AFSTAND_M: float = 5.0
# Verschil in bovenkant filter waarboven een koppeling een waarschuwing krijgt.
FILTER_WAARSCHUWING_M: float = 0.25
# Reeksen met minder metingen zijn meestal fragmenten (2-5 rijen bij Fugro).
MIN_METINGEN: int = 100
# Welke reeksen van het hele platform als kandidaat meedoen (zie boven).
VOORSELECTIE_M: float = 25.0

# Uit het blad `profielen`, per profiel; nodig om het dwarsprofiel te plotten.
PROFIEL_KOLOMMEN: dict[str, str] = {
    "x": "profiel_x_rd",
    "y": "profiel_y_rd",
    "teensloot_peil": "teensloot_peil",
    "stix_bestand": "stix_bestand",
    "stix_offset_x_handmatig": "stix_offset_x_handmatig",
    "coords_sheet": "coords_bestand",
    "actief": "profiel_actief",
}
# Statussen waarbij de meetlocatie vaststaat; zie --vul-config.
GEKOPPELD: tuple[str, ...] = ("gekoppeld", "meerdere sensoren")

METHODE_RANG: dict[str, int] = {
    "locatie+naam+filter": 1,
    "locatie+filter": 2,
    "naam+project": 3,
    "alleen naam": 4,
}


def peilbuizen_per_app_profiel(invoer: workbook_lader.ProjectInvoer) -> DataFrame:
    """Het blad `peilbuizen`, met het profiel waaronder elke peilbuis in de app staat."""
    # Het profiel waaronder de peilbuis in de app verschijnt: het profiel zelf
    # als het een hoofdprofiel is, anders zijn hoofdprofiel.
    profielinfo: DataFrame = invoer.profielen_df[
        ["profiel_id", "naam", "diepte_niveau", "hoofdprofiel_id"]
    ].rename(columns={"naam": "profiel_naam"})
    profielinfo["app_profiel_id"] = profielinfo["hoofdprofiel_id"].where(
        profielinfo["hoofdprofiel_id"] != "", profielinfo["profiel_id"]
    )
    namen: dict[str, str] = dict(zip(profielinfo["profiel_id"], profielinfo["profiel_naam"]))
    profielinfo["app_profiel_naam"] = profielinfo["app_profiel_id"].map(namen)

    peilbuizen: DataFrame = invoer.peilbuizen_df.merge(
        profielinfo.drop(columns="hoofdprofiel_id"), on="profiel_id", how="left"
    )
    voorop: list[str] = [
        "app_profiel_id",
        "app_profiel_naam",
        "profiel_id",
        "profiel_naam",
        "diepte_niveau",
    ]
    peilbuizen = peilbuizen[voorop + [k for k in peilbuizen.columns if k not in voorop]]
    return peilbuizen


def lokale_peilbuizen() -> DataFrame:
    """Alle peilbuizen uit alle werkboeken, met de profielgegevens erbij."""
    delen: list[DataFrame] = []
    for project in paths.projecten():
        invoer = workbook_lader.laad_project(project)
        pb: DataFrame = peilbuizen_per_app_profiel(invoer)
        profiel: DataFrame = invoer.profielen_df[["profiel_id", *PROFIEL_KOLOMMEN]].rename(
            columns=PROFIEL_KOLOMMEN
        )
        pb = pb.merge(profiel, on="profiel_id", how="left")
        pb.insert(0, "project", project)
        # De status uit een vorige run (de nieuwe staat in MATCH_status) en
        # wat --vul-config uit Databricks overneemt: dat is geen lokale data.
        pb = pb.drop(columns=["koppeling", *[k for k in pb.columns if k.startswith("DD_")]])
        delen.append(pb)

    ld: DataFrame = pd.concat(delen, ignore_index=True)
    # Lege koppelkolommen uit het werkboek: de voorstellen staan in MATCH_/DD_.
    ld = ld.drop(columns=[k for k in ld.columns if k.startswith("match_") or k.startswith("meetreeks_")
                          if ld[k].isna().all()])
    ld["_sleutel"] = ld["peilbuis_naam"].map(platform_match.sleutel_uit_werkboek)
    ld["_filter_nr"] = ld["_sleutel"].str.split("|").str[1]
    ld["_project_nr"] = ld["projectnummer"].astype(str).str.extract(r"(\d{4,})", expand=False)
    ld["_ld"] = np.arange(len(ld))
    return ld


def databricks_reeksen(pad: Path) -> DataFrame:
    """Alle meetreeksen uit de export, met de sleutels om te vergelijken."""
    dd: DataFrame = pd.read_csv(pad)
    dd["_sleutel"] = dd["sensor_sleutel"].map(platform_match.sleutel_uit_sensor).fillna(
        dd["code"].map(platform_match.sleutel_uit_meetreeks_code)
    )
    dd["_filter_nr"] = dd["_sleutel"].str.split("|").str[1]
    # Wiertsema zet ons projectnummer in de locatiecode: '_86349_HB002_(...)'.
    dd["_project_nr"] = dd["meetlocatie_code"].astype(str).str.extract(
        r"^_(\d{4,})_", expand=False
    )
    return dd


def voorselectie(dd: DataFrame, ld: DataFrame) -> DataFrame:
    """De reeksen binnen VOORSELECTIE_M van een peilbuis, of met de naam van een peilbuis."""
    xy_ld = ld[["x_rd", "y_rd"]].dropna().to_numpy(float)
    xy_dd = dd[["rd_x", "rd_y"]].to_numpy(float)
    # Per reeks de afstand tot de dichtstbijzijnde peilbuis, in blokken zodat een
    # platform met tienduizenden reeksen ook in het geheugen past.
    dichtstbij = np.full(len(dd), np.inf)
    for start in range(0, len(dd), 5000):
        blok = xy_dd[start:start + 5000]
        afstand = np.hypot(blok[:, None, 0] - xy_ld[None, :, 0], blok[:, None, 1] - xy_ld[None, :, 1])
        dichtstbij[start:start + 5000] = np.nanmin(np.where(np.isnan(afstand), np.inf, afstand), axis=1)
    namen: set[str] = set(ld["_sleutel"].dropna())
    gekozen: DataFrame = dd[(dichtstbij <= VOORSELECTIE_M) | dd["_sleutel"].isin(namen)].copy()
    gekozen["_dd"] = np.arange(len(gekozen))
    return gekozen.reset_index(drop=True)


def kandidaten(ld: DataFrame, dd: DataFrame) -> DataFrame:
    """Alle paren peilbuis × meetreeks die op plek of naam bij elkaar kunnen horen."""
    # 310 × 674 paren: klein genoeg om volledig te vergelijken.
    paren: DataFrame = ld[["_ld", "_sleutel", "_filter_nr", "_project_nr", "x_rd", "y_rd",
                           "bovenkant_filter_nap"]].merge(
        dd[["_dd", "_sleutel", "_filter_nr", "_project_nr", "rd_x", "rd_y", "begin_filter",
            "sensor_sleutel", "n_rows"]],
        how="cross",
        suffixes=("_ld", "_dd"),
    )
    paren["afstand_m"] = np.hypot(paren["x_rd"] - paren["rd_x"], paren["y_rd"] - paren["rd_y"])
    paren["naam_gelijk"] = paren["_sleutel_ld"].notna() & (paren["_sleutel_ld"] == paren["_sleutel_dd"])
    paren["filter_gelijk"] = paren["_filter_nr_ld"] == paren["_filter_nr_dd"]
    paren["project_gelijk"] = paren["_project_nr_ld"] == paren["_project_nr_dd"]
    paren["project_anders"] = paren["_project_nr_dd"].notna() & ~paren["project_gelijk"]
    paren["filter_verschil_m"] = (paren["bovenkant_filter_nap"] - paren["begin_filter"]).abs()

    heeft_afstand: pd.Series = paren["afstand_m"].notna()
    dichtbij: pd.Series = heeft_afstand & (paren["afstand_m"] <= MAX_AFSTAND_M) & paren["filter_gelijk"]
    paren["methode"] = np.select(
        [
            dichtbij & paren["naam_gelijk"],
            dichtbij,
            ~heeft_afstand & paren["naam_gelijk"] & paren["project_gelijk"],
            ~heeft_afstand & paren["naam_gelijk"] & ~paren["project_anders"],
        ],
        list(METHODE_RANG),
        default="",
    )
    paren = paren[paren["methode"] != ""].copy()
    paren["rang"] = paren["methode"].map(METHODE_RANG)

    # Een reeks die op plek én naam bij een peilbuis hoort, is geen kandidaat voor
    # de buurput met een andere naam (MB653 naast MB087).
    geclaimd: pd.Series = paren.loc[paren["methode"] == "locatie+naam+filter", "_dd"]
    paren = paren[~((paren["methode"] == "locatie+filter") & paren["_dd"].isin(geclaimd))]

    # Per peilbuis alleen de beste methode; daarbinnen naam en filterdiepte als
    # volgorde, zodat kandidaat 1 de meest waarschijnlijke is.
    paren = paren[paren["rang"] == paren.groupby("_ld")["rang"].transform("min")]
    paren["genoeg_metingen"] = paren["n_rows"].fillna(0) >= MIN_METINGEN
    paren = paren.sort_values(
        ["_ld", "naam_gelijk", "genoeg_metingen", "filter_verschil_m", "n_rows", "afstand_m"],
        ascending=[True, False, False, True, False, True],
        na_position="last",
    )
    paren["kandidaat_nr"] = paren.groupby("_ld").cumcount() + 1
    paren["n_kandidaten"] = paren.groupby("_ld")["_dd"].transform("size")

    status: dict[str, str] = {
        "locatie+naam+filter": "gekoppeld",
        "locatie+filter": "waarschijnlijk",
        "naam+project": "waarschijnlijk",
        "alleen naam": "twijfelachtig",
    }
    # Allemaal dezelfde sensorsleutel: één put met een vervangen logger, geen
    # keuze tussen putten. Alleen bij een plek- of projectbevestiging.
    een_sensor: pd.Series = (
        paren.groupby("_ld")["sensor_sleutel"].transform("nunique").eq(1)
        & paren["sensor_sleutel"].notna()
        & paren["methode"].isin(["locatie+naam+filter", "naam+project"])
    )
    paren["status"] = np.select(
        [paren["n_kandidaten"] == 1, een_sensor],
        [paren["methode"].map(status), "meerdere sensoren"],
        default="meerdere kandidaten",
    )

    waarschuwingen: list[pd.Series] = [
        np.where(paren["filter_verschil_m"] > FILTER_WAARSCHUWING_M,
                 "filter wijkt " + paren["filter_verschil_m"].round(2).astype(str) + " m af", ""),
        np.where(paren["methode"] == "locatie+filter", "naam verschilt", ""),
        np.where(~paren["genoeg_metingen"],
                 "weinig metingen (" + paren["n_rows"].fillna(0).astype(int).astype(str) + ")", ""),
    ]
    paren["waarschuwing"] = [
        "; ".join(w for w in rij if w) for rij in zip(*waarschuwingen)
    ]
    paren["afstand_m"] = paren["afstand_m"].round(2)
    paren["filter_verschil_m"] = paren["filter_verschil_m"].round(2)
    return paren[["_ld", "_dd", "status", "methode", "kandidaat_nr", "n_kandidaten",
                  "afstand_m", "filter_verschil_m", "naam_gelijk", "project_gelijk", "waarschuwing"]]


def bouw_koppeltabel(ld: DataFrame, dd: DataFrame) -> DataFrame:
    paren: DataFrame = kandidaten(ld, dd)

    alleen_lokaal: DataFrame = ld.loc[~ld["_ld"].isin(paren["_ld"]), ["_ld"]].assign(
        status="alleen lokaal"
    )
    alleen_dd: DataFrame = dd.loc[~dd["_dd"].isin(paren["_dd"]), ["_dd"]].assign(
        status="alleen databricks"
    )
    rijen: DataFrame = pd.concat([paren, alleen_lokaal, alleen_dd], ignore_index=True)

    # Een meetreeks die bij meer dan één peilbuis-naam hoort, is verdacht; één naam in
    # twee profielen (dezelfde put) is dat niet.
    namen_per_dd: pd.Series = (
        rijen.dropna(subset=["_ld", "_dd"])
        .assign(naam=lambda r: r["_ld"].map(ld.set_index("_ld")["peilbuis_naam"]))
        .groupby("_dd")["naam"].nunique()
    )
    gedeeld: set = set(namen_per_dd[namen_per_dd > 1].index)
    rijen["MATCH_reeks_bij_meerdere_peilbuizen"] = rijen["_dd"].isin(gedeeld)

    ld_kol: list[str] = [k for k in ld.columns if not k.startswith("_")]
    dd_kol: list[str] = [k for k in dd.columns if not k.startswith("_")]
    tabel: DataFrame = (
        rijen.merge(ld[["_ld", *ld_kol]].rename(columns={k: f"LD_{k}" for k in ld_kol}),
                    on="_ld", how="left")
        .merge(dd[["_dd", *dd_kol]].rename(columns={k: f"DD_{k}" for k in dd_kol}),
               on="_dd", how="left")
    )
    tabel = tabel.rename(columns={k: f"MATCH_{k}" for k in [
        "status", "methode", "kandidaat_nr", "n_kandidaten", "afstand_m",
        "filter_verschil_m", "naam_gelijk", "project_gelijk", "waarschuwing",
    ]})

    # Volgorde: per project en profiel zoals in de app, de reeksen zonder
    # peilbuis achteraan, gegroepeerd per DD-dwarsprofiel.
    # De DD_dwarsprofiel-kolommen staan alleen in de oudere export.
    volgorde: list[str] = [
        k for k in ["LD_project", "LD_app_profiel_id", "LD_profiel_id", "LD_positie", "MATCH_kandidaat_nr",
                    "DD_dwarsprofiel_id", "DD_zone_volgorde", "DD_filename", "DD_code"]
        if k in tabel.columns
    ]
    tabel = tabel.sort_values(volgorde, na_position="last", kind="stable")
    match_kol: list[str] = [k for k in tabel.columns if k.startswith("MATCH_")]
    return tabel[
        match_kol
        + [f"LD_{k}" for k in ld_kol]
        + [f"DD_{k}" for k in dd_kol]
    ].reset_index(drop=True)


def _meter(waarde: float) -> str:
    return "" if pd.isna(waarde) else f"{waarde:.2f}"


def vul_config(tabel: DataFrame, alle_dd: DataFrame) -> tuple[int, int]:
    """
    Zet `DD_koppeling`, lege `DD_meetlocatie_id` en de DD-informatie in
    config.csv; geeft (ingevuld, gewijzigd). `alle_dd` is het hele platform,
    zodat ook een met de hand gekozen meetlocatie buiten de voorselectie zijn
    gegevens krijgt.
    """
    pad: Path = paths.config_csv()
    # Alles als tekst, zodat de rest van het bestand byte voor byte gelijk blijft.
    config: DataFrame = pd.read_csv(pad, dtype=str, keep_default_na=False)
    sleutel: list[str] = ["LD_project", "LD_cross_section_key", "LD_diepte_niveau", "LD_peilbuis"]

    gekoppeld: DataFrame = tabel.dropna(subset=["LD_peilbuis_naam"])
    per_pb: DataFrame = (
        gekoppeld.groupby(["LD_project", "LD_dwarsprofiel", "LD_diepte_niveau", "LD_peilbuis_naam"])
        .agg(status=("MATCH_status", "first"), ids=("DD_cs_meetlocatie_id", lambda r: set(r.dropna())))
        .reset_index()
    )
    per_pb.columns = [*sleutel, "status", "ids"]
    voorstel = config[sleutel].merge(per_pb, on=sleutel, how="left")

    ingevuld = 0
    oud_status = config["DD_koppeling"].copy()
    for i, rij in voorstel.iterrows():
        if pd.isna(rij["status"]):
            continue
        config.at[i, "DD_koppeling"] = rij["status"]
        if not config.at[i, "DD_meetlocatie_id"] and rij["status"] in GEKOPPELD and len(rij["ids"]) == 1:
            config.at[i, "DD_meetlocatie_id"] = next(iter(rij["ids"]))
            ingevuld += 1

    locaties: DataFrame = (
        alle_dd.dropna(subset=["cs_meetlocatie_id"]).drop_duplicates("cs_meetlocatie_id")
        .set_index("cs_meetlocatie_id")[["cs_code", "rd_x", "rd_y"]]
    )
    for i, mid in config["DD_meetlocatie_id"].items():
        if mid not in locaties.index:
            continue
        loc = locaties.loc[mid]
        x, y = pd.to_numeric(config.at[i, "LD_x_rd"]), pd.to_numeric(config.at[i, "LD_y_rd"])
        config.at[i, "DD_meetlocatie_code"] = "" if pd.isna(loc["cs_code"]) else str(loc["cs_code"])
        config.at[i, "DD_x_rd"] = _meter(loc["rd_x"])
        config.at[i, "DD_y_rd"] = _meter(loc["rd_y"])
        config.at[i, "DD_afstand_m"] = _meter(np.hypot(x - loc["rd_x"], y - loc["rd_y"]))
    config.to_csv(pad, index=False)
    return ingevuld, int((config["DD_koppeling"] != oud_status).sum())


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--vul-config", action="store_true",
                        help="zet de DD_-kolommen in input_data/config.csv")
    args = parser.parse_args()

    ld: DataFrame = lokale_peilbuizen()
    bron: Path = paths.export_map() / DD_EXPORT
    if not bron.is_file():
        bron = paths.export_map() / DD_EXPORT_OUD
    print(f"Databricks-kant: {bron.relative_to(paths.repo_root())}")
    alle_dd: DataFrame = databricks_reeksen(bron)
    dd: DataFrame = voorselectie(alle_dd, ld)
    print(f"{len(alle_dd):,} meetreeksen, waarvan {len(dd):,} binnen {VOORSELECTIE_M:.0f} m "
          "of met de naam van een peilbuis")
    tabel: DataFrame = bouw_koppeltabel(ld, dd)

    uit: Path = paths.controle_map() / UITVOER
    tabel.to_csv(uit, index=False)
    print(f"✅ {uit}: {len(tabel)} rijen, {tabel.shape[1]} kolommen")

    per_pb: DataFrame = tabel.dropna(subset=["LD_peilbuis_naam"]).drop_duplicates(
        ["LD_project", "LD_profiel_id", "LD_peilbuis_naam"]
    )
    print("\nPeilbuizen per status (kandidaat 1):")
    print(pd.crosstab(per_pb["LD_project"], per_pb["MATCH_status"], margins=True).to_string())
    print(f"\nMeetreeksen zonder peilbuis: {(tabel['MATCH_status'] == 'alleen databricks').sum()}")

    if args.vul_config:
        ingevuld, gewijzigd = vul_config(tabel, alle_dd)
        print(f"\n✅ config.csv: {ingevuld} DD_meetlocatie_id ingevuld, {gewijzigd} koppelingen gewijzigd")

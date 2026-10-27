# bouw_koppeltabel_ld_dd.py
"""
Eén koppeltabel tussen onze peilbuizen en de meetreeksen op Databricks.

    uv run python scripts/bouw_koppeltabel_ld_dd.py
    uv run python scripts/bouw_koppeltabel_ld_dd.py --vul-config

Leest:
- LD_ (lokale data): `input_data/config.csv`, per project, met het profiel
  waaronder de peilbuis in de app staat;
- DD_ (data Databricks): `input_data/object_data_databricks_dwarsprofielen.csv`,
  een eerdere export van `meetreeks` + `meetlocatie` met de dwarsprofielindeling.

Schrijft `input_data/koppeltabel_LD_DD.csv`, met dezelfde opbouw als de
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
- `koppeling` op de status van kandidaat 1;
- een lege `meetlocatie_id` op `DD_cs_meetlocatie_id`, maar alleen bij status
  `gekoppeld` of `meerdere sensoren` (één meetlocatie). Een ingevulde
  `meetlocatie_id` wordt nooit overschreven; `twijfelachtig` en
  `meerdere kandidaten` blijven handwerk.
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

from Modules import paths, platform_match, workbook_lader  # noqa: E402

DD_EXPORT: str = "object_data_databricks_dwarsprofielen.csv"
UITVOER: str = "koppeltabel_LD_DD.csv"

# Peilbuizen in één put staan op dezelfde plek; een andere put op hetzelfde
# profiel staat er meters vandaan. 5 m scheidt die twee.
MAX_AFSTAND_M: float = 5.0
# Verschil in bovenkant filter waarboven een koppeling een waarschuwing krijgt.
FILTER_WAARSCHUWING_M: float = 0.25
# Reeksen met minder metingen zijn meestal fragmenten (2-5 rijen bij Fugro).
MIN_METINGEN: int = 100

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
        # De status uit een vorige run; de nieuwe staat in MATCH_status.
        pb = pb.drop(columns=["koppeling"])
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
    dd: DataFrame = pd.read_csv(pad)
    dd["_sleutel"] = dd["sensor_sleutel"].map(platform_match.sleutel_uit_sensor).fillna(
        dd["code"].map(platform_match.sleutel_uit_meetreeks_code)
    )
    dd["_filter_nr"] = dd["_sleutel"].str.split("|").str[1]
    # Wiertsema zet ons projectnummer in de locatiecode: '_86349_HB002_(...)'.
    dd["_project_nr"] = dd["meetlocatie_code"].astype(str).str.extract(
        r"^_(\d{4,})_", expand=False
    )
    dd["_dd"] = np.arange(len(dd))
    return dd


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
    tabel = tabel.sort_values(
        ["LD_project", "LD_app_profiel_id", "LD_profiel_id", "LD_positie",
         "MATCH_kandidaat_nr", "DD_dwarsprofiel_id", "DD_zone_volgorde", "DD_filename"],
        na_position="last", kind="stable",
    )
    match_kol: list[str] = [k for k in tabel.columns if k.startswith("MATCH_")]
    return tabel[
        match_kol
        + [f"LD_{k}" for k in ld_kol]
        + [f"DD_{k}" for k in dd_kol]
    ].reset_index(drop=True)


def vul_config(tabel: DataFrame) -> tuple[int, int]:
    """Zet `koppeling` en lege `meetlocatie_id` in config.csv; geeft (ingevuld, gewijzigd)."""
    pad: Path = paths.config_csv()
    # Alles als tekst, zodat de rest van het bestand byte voor byte gelijk blijft.
    config: DataFrame = pd.read_csv(pad, dtype=str, keep_default_na=False)
    sleutel: list[str] = ["project", "cross_section_key", "diepte_niveau", "peilbuis"]

    gekoppeld: DataFrame = tabel.dropna(subset=["LD_peilbuis_naam"])
    per_pb: DataFrame = (
        gekoppeld.groupby(["LD_project", "LD_dwarsprofiel", "LD_diepte_niveau", "LD_peilbuis_naam"])
        .agg(status=("MATCH_status", "first"), ids=("DD_cs_meetlocatie_id", lambda r: set(r.dropna())))
        .reset_index()
    )
    per_pb.columns = [*sleutel, "status", "ids"]
    voorstel = config[sleutel].merge(per_pb, on=sleutel, how="left")

    ingevuld = 0
    oud_status = config["koppeling"].copy()
    for i, rij in voorstel.iterrows():
        if pd.isna(rij["status"]):
            continue
        config.at[i, "koppeling"] = rij["status"]
        if not config.at[i, "meetlocatie_id"] and rij["status"] in GEKOPPELD and len(rij["ids"]) == 1:
            config.at[i, "meetlocatie_id"] = next(iter(rij["ids"]))
            ingevuld += 1
    config.to_csv(pad, index=False)
    return ingevuld, int((config["koppeling"] != oud_status).sum())


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--vul-config", action="store_true",
                        help="zet koppeling en lege meetlocatie_id in input_data/config.csv")
    args = parser.parse_args()

    ld: DataFrame = lokale_peilbuizen()
    dd: DataFrame = databricks_reeksen(paths.invoer_basis() / DD_EXPORT)
    tabel: DataFrame = bouw_koppeltabel(ld, dd)

    uit: Path = paths.invoer_basis() / UITVOER
    tabel.to_csv(uit, index=False)
    print(f"✅ {uit}: {len(tabel)} rijen, {tabel.shape[1]} kolommen")

    per_pb: DataFrame = tabel.dropna(subset=["LD_peilbuis_naam"]).drop_duplicates(
        ["LD_project", "LD_profiel_id", "LD_peilbuis_naam"]
    )
    print("\nPeilbuizen per status (kandidaat 1):")
    print(pd.crosstab(per_pb["LD_project"], per_pb["MATCH_status"], margins=True).to_string())
    print(f"\nMeetreeksen zonder peilbuis: {(tabel['MATCH_status'] == 'alleen databricks').sum()}")

    if args.vul_config:
        ingevuld, gewijzigd = vul_config(tabel)
        print(f"\n✅ config.csv: {ingevuld} meetlocatie_id ingevuld, {gewijzigd} koppelingen gewijzigd")

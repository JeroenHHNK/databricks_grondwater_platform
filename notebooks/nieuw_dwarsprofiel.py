# nieuw_dwarsprofiel.py
"""
Een nieuw dwarsprofiel uit een paar ingrediënten: stix, geometrie, profiellijn,
teenslootpeil en de namen van de peilbuizen. De rest leidt het script af uit
`input_data/databricks_export/meetreeksen.csv`.

1. Welke putten liggen er op de lijn?

    uv run python notebooks/nieuw_dwarsprofiel.py --zoek --lijn 119552.5,518899.3,119465.2,518927.8

2. Proefdraaien (schrijft niets) en daarna schrijven met --schrijf:

    uv run python notebooks/nieuw_dwarsprofiel.py --project Heerhugowaard ^
        --stix pad/HH0068+30_DWP003.stix --geometrie pad/HH_0068.csv ^
        --lijn 119552.533,518899.343,119465.158,518927.784 --teensloot -3.7 ^
        --hoofd B18PB02,HB16PB01,HB17PB01,B19PB02 --diep-a B18PB01,B19PB01

Wat het script invult, per peilbuis (zie README, "Ingrediënten"):
- `LD_x_profiel`: de projectie van de RD-coördinaten op de profiellijn;
- `LD_x_rd`, `LD_y_rd`, `LD_code`, `LD_bk_peilbuis`, `LD_bk_filter`,
  `LD_ok_filter`: van de meetlocatie en meetreeks op Databricks. Met
  `--inmeting` (csv `peilbuis,bk_peilbuis,bk_filter,ok_filter`) gaan de
  waarden uit de inmeting voor;
- `LD_projectnummer`: `--projectnummer`, anders het meest voorkomende in het project.

De DD_-kolommen blijven leeg; die vult `bouw_koppeltabel_ld_dd.py --vul-config`.
Bij `--vervang` blijven de DD_-kolommen van een peilbuis die er al stond staan,
zodat een met de hand gekozen `DD_meetlocatie_id` niet verloren gaat.

Het proefdraaien toont de rijen en dezelfde controles als `maak_plots.py`
(`workbook_lader.controleer_peilbuizen`). Een `fout` blokkeert `--schrijf`.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import pandas as pd
from pandas import DataFrame

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules import paths, platform_match, workbook_lader  # noqa: E402

# Zo ver van de profiellijn mag een meetlocatie liggen om mee te doen; dezelfde
# grens als de voorselectie in bouw_koppeltabel_ld_dd.py.
ZOEKAFSTAND_M: float = 25.0
EENHEID: str = "m NAP"


# --- Databricks-kant -----------------------------------------------------------

def lees_meetreeksen() -> DataFrame:
    """`meetreeksen.csv`, met de naam-sleutel (put|filter) van elke reeks."""
    pad: Path = paths.export_map() / "meetreeksen.csv"
    if not pad.is_file():
        sys.exit(f"❌ {pad} ontbreekt; draai eerst db_notebook_exporteer_meetreeksen in Databricks.")
    dd: DataFrame = pd.read_csv(pad)
    dd["_sleutel"] = dd["sensor_sleutel"].map(platform_match.sleutel_uit_sensor).fillna(
        dd["code"].map(platform_match.sleutel_uit_meetreeks_code)
    )
    return dd


def langs_lijn(dd: DataFrame, lijn: tuple[float, float, float, float]) -> DataFrame:
    """De reeksen binnen ZOEKAFSTAND_M van de lijn (als lijnstuk), met x langs de lijn."""
    dd = dd.dropna(subset=["rd_x", "rd_y"]).copy()
    x0, y0, x1, y1 = lijn
    lengte: float = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
    proj = [workbook_lader.projecteer_op_lijn(lijn, x, y) for x, y in zip(dd["rd_x"], dd["rd_y"])]
    dd["x_profiel"] = [p[0] for p in proj]
    dd["afstand_lijn_m"] = [p[1] for p in proj]
    binnen = (
        (dd["afstand_lijn_m"] <= ZOEKAFSTAND_M)
        & (dd["x_profiel"] >= -ZOEKAFSTAND_M)
        & (dd["x_profiel"] <= lengte + ZOEKAFSTAND_M)
    )
    return dd[binnen].sort_values(["x_profiel", "meetlocatie_code"])


def kies_reeks(naam: str, kandidaten: DataFrame) -> pd.Series:
    """De meetreeks van één peilbuisnaam (put + filter) uit de reeksen langs de lijn."""
    sleutel: str | None = platform_match.sleutel_uit_werkboek(naam)
    if sleutel is None:
        sys.exit(f"❌ {naam}: geen herkenbare put+filter (verwacht bv. HB16PB01 of B18PB02).")
    gevonden = kandidaten[(kandidaten["_sleutel"] == sleutel) & (kandidaten["eenheid"] == EENHEID)]
    locaties = gevonden.drop_duplicates("cs_meetlocatie_id")
    if len(locaties) == 1:
        # Bij meerdere sensoren op één meetlocatie: de reeks met de meeste metingen.
        return gevonden.sort_values("n_rows", ascending=False).iloc[0]
    if locaties.empty:
        zelfde_put = kandidaten[kandidaten["_sleutel"].str.split("|").str[0] == sleutel.split("|")[0]]
        hint = ", ".join(sorted(set(zelfde_put["code"].astype(str)))) or "geen"
        sys.exit(f"❌ {naam}: geen meetreeks binnen {ZOEKAFSTAND_M:g} m van de lijn. "
                 f"Zelfde put gevonden: {hint}. Draai --zoek om te zien wat er ligt.")
    lijst = "\n   ".join(f"{r.code}  {r.meetlocatie_code}" for r in locaties.itertuples())
    sys.exit(f"❌ {naam}: meerdere meetlocaties passen:\n   {lijst}\n"
             "Kies zelf; vul daarna DD_meetlocatie_id met de hand in config.csv.")


# --- Opbouw van de rijen -----------------------------------------------------------

def _getal(waarde: float) -> str:
    return "" if pd.isna(waarde) else f"{float(waarde):g}"


def bouw_rijen(args: argparse.Namespace, kandidaten: DataFrame, inmeting: DataFrame | None,
               projectnummer: str, sleutel: str) -> DataFrame:
    rijen: list[dict] = []
    for niveau, namen in (("hoofd", args.hoofd), ("A", args.diep_a), ("B", args.diep_b)):
        for naam in [n.strip() for n in (namen or "").split(",") if n.strip()]:
            r = kies_reeks(naam, kandidaten)
            bk_pb, bk_f, ok_f = r["bovenkant_peilbuis"], r["begin_filter"], r["eind_filter"]
            if inmeting is not None and naam in inmeting.index:
                m = inmeting.loc[naam]
                bk_pb, bk_f, ok_f = m["bk_peilbuis"], m["bk_filter"], m["ok_filter"]
            rijen.append({
                "LD_project": args.project, "LD_cross_section_key": sleutel, "LD_diepte_niveau": niveau,
                "LD_peilbuis": naam, "LD_projectnummer": projectnummer,
                "LD_code": platform_match._FILTER_IN_CODE.sub("", str(r["meetlocatie_code"])).strip(),
                "LD_x_profiel": f"{r['x_profiel']:.2f}",
                "LD_x_rd": f"{r['rd_x']:.3f}", "LD_y_rd": f"{r['rd_y']:.3f}",
                "LD_bk_peilbuis": _getal(bk_pb), "LD_bk_filter": _getal(bk_f), "LD_ok_filter": _getal(ok_f),
                "_afstand_lijn_m": r["afstand_lijn_m"], "_reeks": r["code"],
            })
    if not rijen:
        sys.exit("❌ Geen peilbuizen opgegeven (--hoofd).")
    uit = DataFrame(rijen)
    uit["_o"] = uit["LD_diepte_niveau"].map({"hoofd": 0, "A": 1, "B": 2})
    uit["_x"] = uit["LD_x_profiel"].astype(float)
    return uit.sort_values(["_o", "_x"], kind="stable").drop(columns=["_o", "_x"]).reset_index(drop=True)


def bepaal_sleutel(args: argparse.Namespace, eerste_code: str, bestaande: set[str]) -> str:
    """`--sleutel`, anders de dijkpaal uit de code van de eerste peilbuis (README-regel)."""
    if args.sleutel:
        return args.sleutel
    paal = platform_match.dijkpaal_uit_code(eerste_code)
    if paal is None:
        sys.exit(f"❌ Geen dijkpaal in '{eerste_code}'; geef --sleutel op.")
    prefix, nummer, offset = paal
    sleutel: str = platform_match.dwarsprofiel_sleutel(prefix, nummer)
    if sleutel in bestaande and not args.vervang:
        sys.exit(f"❌ Sleutel {sleutel} bestaat al. Bedoel je een ander profiel, gebruik dan "
                 f"--sleutel {platform_match.dwarsprofiel_sleutel(prefix, nummer, offset)}; "
                 "wil je het bestaande vervangen, gebruik --vervang.")
    return sleutel


def lees_geometrie(pad: Path) -> DataFrame:
    geo = workbook_lader._schoon_coords(pd.read_csv(pad))
    if len(geo) < 5:
        sys.exit(f"❌ {pad}: verwacht een csv met kolommen x,y (m langs het profiel, m NAP).")
    return geo


# --- Hoofdprogramma ------------------------------------------------------------------

def zoek(lijn) -> None:
    dd = langs_lijn(lees_meetreeksen(), lijn)
    if dd.empty:
        print(f"Geen meetreeksen binnen {ZOEKAFSTAND_M:g} m van de lijn.")
        return
    kolommen = ["code", "meetlocatie_code", "x_profiel", "afstand_lijn_m", "bovenkant_peilbuis",
                "begin_filter", "eind_filter", "eenheid", "n_rows"]
    pd.set_option("display.width", 250)
    print(dd[kolommen].round({"x_profiel": 2, "afstand_lijn_m": 1}).to_string(index=False))
    print("\nNaam voor --hoofd/--diep-a/--diep-b: put + PB + filter, bv. HB16-PB1 -> HB16PB01.")


def maak(args: argparse.Namespace, lijn) -> None:
    if args.project not in paths.projecten():
        sys.exit(f"❌ Project {args.project} staat niet in projecten.csv; voeg daar eerst een rij toe.")
    for pad in (args.stix, args.geometrie):
        if not Path(pad).is_file():
            sys.exit(f"❌ {pad} bestaat niet.")

    config: DataFrame = pd.read_csv(paths.config_csv(), dtype=str, keep_default_na=False)
    profielen: DataFrame = workbook_lader.lees_profielen()
    kandidaten = langs_lijn(lees_meetreeksen(), lijn)
    inmeting = None
    if args.inmeting:
        inmeting = pd.read_csv(args.inmeting, dtype={"peilbuis": str}).set_index("peilbuis")

    projectnummer: str = args.projectnummer or (
        config.loc[config["LD_project"] == args.project, "LD_projectnummer"].mode().iloc[0]
        if (config["LD_project"] == args.project).any() else ""
    )
    eerste = kies_reeks(args.hoofd.split(",")[0].strip(), kandidaten)
    sleutel: str = bepaal_sleutel(args, str(eerste["meetlocatie_code"]), set(config["LD_cross_section_key"]))
    andere_projecten = set(config.loc[config["LD_cross_section_key"] == sleutel, "LD_project"]) - {args.project}
    if andere_projecten:
        sys.exit(f"❌ Sleutel {sleutel} hoort al bij project {', '.join(andere_projecten)}.")

    rijen = bouw_rijen(args, kandidaten, inmeting, projectnummer, sleutel)
    geometrie = lees_geometrie(Path(args.geometrie))

    # --- tonen en controleren ---
    pd.set_option("display.width", 250)
    print(f"Dwarsprofiel {sleutel} ({args.project}), teenslootpeil {args.teensloot}\n")
    toon = ["LD_diepte_niveau", "LD_peilbuis", "_reeks", "LD_code", "LD_x_profiel", "_afstand_lijn_m",
            "LD_bk_peilbuis", "LD_bk_filter", "LD_ok_filter"]
    print(rijen[toon].round({"_afstand_lijn_m": 1}).to_string(index=False))
    lengte = geometrie["x"].max()
    lijnlengte = workbook_lader.projecteer_op_lijn(lijn, lijn[2], lijn[3])[0]
    if abs(lengte - lijnlengte) > 1.0:
        print(f"\n⚠️ De geometrie loopt tot x {lengte:.2f}, de profiellijn is {lijnlengte:.2f} m lang. "
              "Begint de geometrie (x 0) wel bij het startpunt van de lijn?")
    bevindingen = workbook_lader.controleer_peilbuizen(sleutel, rijen, geometrie, lijn)
    print()
    for b in bevindingen:
        print(f"{'❌' if b.ernst == 'fout' else '⚠️'} {b}")
    if not bevindingen:
        print("✅ Geen bevindingen.")
    fouten = [b for b in bevindingen if b.ernst == "fout"]

    if not args.schrijf:
        print("\nProefdraaien: niets geschreven. Herhaal met --schrijf om het profiel toe te voegen.")
        return
    if fouten:
        sys.exit("\n❌ Niet geschreven: los eerst de fouten op (of corrigeer met --inmeting).")
    schrijf(args, sleutel, rijen, geometrie, config, profielen, lijn)


def schrijf(args, sleutel, rijen, geometrie, config, profielen, lijn) -> None:
    oud = config[config["LD_cross_section_key"] == sleutel]
    if not oud.empty and not args.vervang:
        sys.exit(f"❌ {sleutel} staat al in config.csv; gebruik --vervang.")
    # DD_-kolommen van peilbuizen die er al stonden, blijven bewaard.
    dd_kolommen = [k for k in config.columns if k.startswith("DD_")]
    bewaard = oud.set_index(["LD_diepte_niveau", "LD_peilbuis"])[dd_kolommen]
    nieuw = rijen.drop(columns=[k for k in rijen.columns if k.startswith("_")]).copy()
    for k in dd_kolommen:
        nieuw[k] = [bewaard.at[(n, p), k] if (n, p) in bewaard.index else ""
                    for n, p in zip(nieuw["LD_diepte_niveau"], nieuw["LD_peilbuis"])]
    nieuw = nieuw[config.columns]

    if oud.empty:
        config = pd.concat([config, nieuw], ignore_index=True)
    else:  # op de plek van het oude profiel
        plek = oud.index.min()
        rest = config.drop(oud.index)
        config = pd.concat([rest.loc[: plek - 1], nieuw, rest.loc[plek:]], ignore_index=True)
    config.to_csv(paths.config_csv(), index=False, lineterminator="\r\n")

    p_oud = profielen[profielen["cross_section_key"] == sleutel]
    p_rij = {
        "project": args.project, "cross_section_key": sleutel, "teensloot_peil": f"{args.teensloot:g}",
        "stix_offset_x": p_oud["stix_offset_x"].iloc[0] if not p_oud.empty else "",
        **{k: f"{v:.3f}" for k, v in zip(workbook_lader.LIJN_KOLOMMEN, lijn)},
        "opmerking": p_oud["opmerking"].iloc[0] if not p_oud.empty else "",
    }
    if p_oud.empty:
        profielen = pd.concat([profielen, DataFrame([p_rij])], ignore_index=True)
    else:
        for k, v in p_rij.items():
            profielen.loc[p_oud.index, k] = v
    profielen.to_csv(paths.profielen_csv(), index=False, lineterminator="\r\n")

    paths.stix_map().mkdir(parents=True, exist_ok=True)
    shutil.copyfile(args.stix, paths.stix_map() / f"{sleutel}.stix")
    geometrie.to_csv(paths.dwarsprofiel_map() / f"{sleutel}.csv", index=False, float_format="%.2f")

    print(f"\n✅ {sleutel}: {len(nieuw)} rijen in config.csv, een rij in profielen.csv, "
          f"{paths.STIX_MAP}/{sleutel}.stix en {paths.DWARSPROFIEL_MAP}/{sleutel}.csv.")
    print("Volgende stappen:\n"
          "   uv run python notebooks/bouw_koppeltabel_ld_dd.py --vul-config\n"
          f"   uv run python notebooks/maak_plots.py --project {args.project} --strict")


def _lijn(tekst: str) -> tuple[float, float, float, float]:
    try:
        waarden = tuple(float(v) for v in tekst.split(","))
    except ValueError:
        waarden = ()
    if len(waarden) != 4:
        raise argparse.ArgumentTypeError("verwacht x_start,y_start,x_eind,y_eind in RD (m)")
    return waarden  # type: ignore[return-value]


def _argumenten() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--lijn", type=_lijn, required=True,
                   help="profiellijn in RD: x_start,y_start,x_eind,y_eind; het startpunt is x 0 van de geometrie")
    p.add_argument("--zoek", action="store_true", help="toon alleen de meetreeksen langs de lijn")
    p.add_argument("--project")
    p.add_argument("--stix", help="het D-Stability-bestand")
    p.add_argument("--geometrie", help="csv met x,y (m langs het profiel, m NAP)")
    p.add_argument("--teensloot", type=float, help="teenslootpeil (m NAP)")
    p.add_argument("--hoofd", help="peilbuizen van het hoofdprofiel, komma-gescheiden (bv. B18PB02,HB16PB01)")
    p.add_argument("--diep-a", help="diepere peilbuizen, niveau A (zandtussenlaag)")
    p.add_argument("--diep-b", help="nog diepere peilbuizen, niveau B (pleistoceen)")
    p.add_argument("--sleutel", help="sleutel van het dwarsprofiel; standaard de dijkpaal uit de eerste code")
    p.add_argument("--projectnummer", help="LD_projectnummer; standaard het meest voorkomende in het project")
    p.add_argument("--inmeting", help="csv peilbuis,bk_peilbuis,bk_filter,ok_filter; gaat voor Databricks")
    p.add_argument("--vervang", action="store_true", help="vervang een bestaand dwarsprofiel")
    p.add_argument("--schrijf", action="store_true", help="schrijf echt (anders alleen proefdraaien)")
    args = p.parse_args()
    if not args.zoek:
        ontbreekt = [k for k in ("project", "stix", "geometrie", "teensloot", "hoofd") if getattr(args, k) is None]
        if ontbreekt:
            p.error("nodig zonder --zoek: " + ", ".join("--" + k for k in ontbreekt))
    return args


if __name__ == "__main__":
    args = _argumenten()
    if args.zoek:
        zoek(args.lijn)
    else:
        maak(args, args.lijn)

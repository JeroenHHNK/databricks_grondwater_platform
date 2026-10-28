# analyse.py
import re

import numpy as np
from pandas import DataFrame


def vind_y_in_dwarsprofiel(
    profiel: list[tuple[float, float]], x_waarde: float
) -> float | None:
    """
    Vindt de y-coördinaat op een profiel voor een gegeven x-coördinaat door middel van lineaire interpolatie.

    Args:
        profiel (list[tuple[float, float]]): Een lijst van (x, y) coördinaten die het profiel representeren, gesorteerd op x.
        x_waarde (float): De x-coördinaat waarvoor de corresponderende y-coördinaat gevonden moet worden.

    Returns:
        float | None: De geïnterpoleerde y-coördinaat, of None als de x-waarde buiten het bereik van het profiel valt.
    """
    profiel = sorted([(float(x), float(y)) for x, y in profiel],key=lambda p: p[0])
    for i in range(len(profiel) - 1):
        x1, y1 = profiel[i]
        x2, y2 = profiel[i + 1]
        if x1 <= x_waarde <= x2:
            if x2 - x1 == 0:  # Voorkom delen door nul bij verticale lijnen
                return y1
            y_kruising: float = y1 + (y2 - y1) / (x2 - x1) * (x_waarde - x1)
            return y_kruising
    return None


def vind_x_in_dwarsprofiel(
    profiel: list[tuple[float, float]], waarde: float
) -> list[float]:
    """
    Vindt de x-coördinaten op een profiel voor een gegeven y-coördinaat.

    Args:
        profiel (List[Tuple[float, float]]): Een lijst van (x, y) coördinaten die het profiel representeren.
        waarde (float): De y-coördinaat waarvoor de corresponderende x-coördinaten gevonden moeten worden.

    Returns:
        List[float]: Een lijst van x-coördinaten waar het profiel de gegeven y-coördinaat heeft.
    """
    profiel = sorted([(float(x), float(y)) for x, y in profiel],key=lambda p: p[0])
    resultaat: list[float] = []
    for i in range(len(profiel) - 1):
        x1, y1 = profiel[i]
        x2, y2 = profiel[i + 1]
        if (y1 <= waarde <= y2) or (y2 <= waarde <= y1):
            if y2 - y1 == 0:  # Horizontaal lijnsegment
                if waarde == y1:
                    resultaat.append(x1)
            else:
                x_kruising: float = x1 + (x2 - x1) / (y2 - y1) * (waarde - y1)
                resultaat.append(x_kruising)
    return resultaat


# --- Zone-classificatie van peilbuizen (kruin / berm / teen) ---------------------------------
# De dijkzone zit als los token verwerkt in de vrije-tekst peilbuiscode, bv.
# "B_D11-DP_70+6_BUKR_GMW_PB1_F-1.71". Er is geen aparte categoriale kolom in de brondata.
_ZONE_PER_TOKEN: dict[str, str] = {
    "BUKR": "kruin", "BIKR": "kruin", "BIRK": "kruin",
    "INSD": "berm", "INBIB": "berm", "KRBIB": "berm", "BIB": "berm",
    "BIT": "teen", "BITA": "teen", "BUT": "teen", "BUTAL": "teen",
}
_ZONE_TOKEN_REGEX = re.compile(
    r"(?:^|_)(BUKR|BIKR|BIRK|INST[A-Z]*|INSD|INBIB|KRBIB|BIB|BITA|BUTAL|BIT|BUT)(?:_|$)"
)


def classificeer_peilbuis_zone(code: str | None) -> str | None:
    """
    Leidt de dijkzone ('kruin', 'berm' of 'teen') af uit een peilbuiscode zoals
    'B_D11-DP_70+6_BUKR_GMW_PB1_F-1.71'. Geeft None als er geen herkenbaar zone-token in staat.
    """
    if not code:
        return None
    match = _ZONE_TOKEN_REGEX.search(str(code))
    if not match:
        return None
    token = match.group(1)
    if token.startswith("INST"):
        return "berm"
    return _ZONE_PER_TOKEN.get(token)


def heeft_kruinzone_peilbuis(peilbuis_codes: list[str | None]) -> bool:
    """Geeft aan of er minstens één peilbuis in de kruinzone (BUKR/BIKR) tussen zit."""
    return any(classificeer_peilbuis_zone(code) == "kruin" for code in peilbuis_codes)


# --- Opbouw en clipping van de freatische lijn ------------------------------------------------


def _minimale_hoogte_tussen(
    dijkprofiel, x_start: float, x_eind: float
) -> float | None:
    """Laagste hoogte van het dijkprofiel tussen twee x-posities (incl. de randen zelf)."""
    x_lo, x_hi = (x_start, x_eind) if x_start <= x_eind else (x_eind, x_start)
    hoogtes: list[float] = [
        float(y) for x, y in dijkprofiel if x_lo <= float(x) <= x_hi
    ]
    for x in (x_lo, x_hi):
        y = vind_y_in_dwarsprofiel(dijkprofiel, x)
        if y is not None:
            hoogtes.append(y)
    return min(hoogtes) if hoogtes else None


def bepaal_buiten_anker(
    dijkprofiel,
    buitenwaterstand: float,
    x_peilbuis: list[float],
    heeft_kruin_peilbuis: bool,
) -> tuple[list[float], bool]:
    """
    Bepaalt de x-positie(s) van het buitenste ankerpunt van de freatische lijn en of daar een
    echt snijpunt van de buitenwaterstand met het buitentalud aan ten grondslag ligt.

    Hangt alleen af van de profielgeometrie en de peilbuisposities (niet van gemeten waarden),
    en kan dus eenmalig per profiel bepaald worden.

    Geeft ([], False) als er geen peilbuis in de kruinzone staat: dan mag de lijn niet voor de
    kruinzone getekend worden en is er geen buiten-ankerpunt.
    """
    if not heeft_kruin_peilbuis or not x_peilbuis:
        return [], False

    x_dijkprofiel_start = float(dijkprofiel[0][0])
    x_bukr = x_peilbuis[0]

    # Alleen snijpunten aan de buitenzijde van de BUKR-peilbuis (x <= x_bukr) horen bij het
    # buitentalud. Bij een flauw aflopend buitentalud dat de buitenwaterstand niet bereikt,
    # levert vind_x_in_dwarsprofiel soms alleen snijpunten verderop (bv. op het binnentalud)
    # op; die liggen landinwaarts van x_bukr en zijn geen geldig startpunt.
    x_buitenwaterstand = vind_x_in_dwarsprofiel(dijkprofiel, buitenwaterstand)
    x_buitenzijde = [x for x in x_buitenwaterstand if x <= x_bukr]

    if x_buitenzijde:
        x_bw_anker = min(x_buitenzijde, key=lambda x: abs(x - x_bukr))
        return [x_dijkprofiel_start, x_bw_anker], True

    return [x_dijkprofiel_start, x_dijkprofiel_start], False


def bouw_freatische_lijn_ankers(
    dijkprofiel,
    x_buiten: list[float],
    heeft_snijpunt: bool,
    buitenwaterstand: float,
    x_peilbuis: list[float],
    y_peilbuis: list[float],
    x_ts_min: float,
    x_ts_max: float,
    teensloot_peil: float,
) -> tuple[list[float], list[float]]:
    """
    Bouwt de (nog ongeclipte) ankerpunten van de freatische lijn: van het buitenwater (of, als
    er geen kruin-peilbuis is, vanaf de eerste berm/teen-peilbuis) via de peilbuizen naar de
    teensloot.

    `x_buiten`/`heeft_snijpunt` komen van `bepaal_buiten_anker` (eenmalig per profiel te
    bepalen). `buitenwaterstand` mag hier wel per aanroep verschillen (bv. de dagwaarde), zodat
    de lijn kan meebewegen met een fluctuerende buitenwaterstand.
    """
    if not x_peilbuis or not y_peilbuis:
        return [], []

    if not x_buiten:
        y_buiten: list[float] = []
    elif heeft_snijpunt:
        y_buiten = [buitenwaterstand, buitenwaterstand]
    else:
        # Geen (bruikbaar) snijpunt met de buitenwaterstand: laat de lijn in principe
        # meebewegen met de (gemeten of gemiddelde) waarde van de eerste peilbuis. Tenzij dat
        # de lijn boven het maaiveld én boven de buitenwaterstand zou duwen (fysisch
        # onmogelijk) -- houd de lijn dan op de buitenwaterstand en laat hem lineair doorlopen
        # naar de eerste peilbuis.
        x_dijkprofiel_start = x_buiten[0]
        x_bukr = x_peilbuis[0]
        eerste_pb_waarde = y_peilbuis[0]
        grond_min = _minimale_hoogte_tussen(dijkprofiel, x_dijkprofiel_start, x_bukr)
        fysisch_onmogelijk = (
            grond_min is not None
            and eerste_pb_waarde > grond_min
            and eerste_pb_waarde > buitenwaterstand
        )
        y_buiten = (
            [buitenwaterstand, buitenwaterstand]
            if fysisch_onmogelijk
            else [eerste_pb_waarde, eerste_pb_waarde]
        )

    x_ankers = x_buiten + list(x_peilbuis) + [x_ts_min, x_ts_max]
    y_ankers = y_buiten + list(y_peilbuis) + [teensloot_peil, teensloot_peil]
    return x_ankers, y_ankers


def bepaal_kern_bereik(
    x_buiten: list[float], heeft_snijpunt: bool, n_x_ankers: int
) -> tuple[int, int]:
    """
    Geeft (i_start, i_eind): de index-grenzen (inclusief) van het 'kern'-deel van de
    ankerpunten dat onder het dijkprofiel geclipt mag worden.

    Het buitenwaterstand-vlak (als er een echt snijpunt met het buitentalud is) en het
    teensloot-vlak zijn open water dat legitiem boven het lokale maaiveld kan liggen (een
    greppel/geul in het profiel) en blijven dus als rechte, ongeclipte lijn buiten de kern.
    """
    i_start = 1 if (x_buiten and heeft_snijpunt) else 0
    i_eind = n_x_ankers - 2  # laatste teensloot-punt (x_ts_max) blijft ongeclipt
    return i_start, i_eind


def bepaal_x_raster_onder_profiel(x_ankers: list[float], dijkprofiel) -> list[float]:
    """
    Combineert de anker-x-waarden met de dijkprofiel-knikpunten die daartussen liggen. Nodig om
    te kunnen toetsen (en zo nodig clippen) op plekken waar het profiel van vorm verandert
    tussen twee ankerpunten, niet alleen op de ankerpunten zelf.
    """
    if not x_ankers:
        return []
    x_min, x_max = min(x_ankers), max(x_ankers)
    profiel_x = {float(x) for x, _ in dijkprofiel if x_min <= float(x) <= x_max}
    return sorted(set(x_ankers) | profiel_x)


def interpoleer_en_clip_op_raster(
    x_raster: list[float],
    grondhoogte_raster: list[float | None],
    x_ankers: list[float],
    y_ankers: list[float],
    marge: float = 0.02,
) -> list[float]:
    """
    Interpoleert (x_ankers, y_ankers) op `x_raster` en zorgt dat de lijn nergens boven het
    dijkprofiel uitkomt (fysisch onmogelijk): waar dat wel zou gebeuren, wordt de lijn net
    onder het maaiveld gehouden.
    """
    y_ongeclipt = np.interp(x_raster, x_ankers, y_ankers)
    return [
        (grond - marge) if (grond is not None and y > grond - marge) else float(y)
        for y, grond in zip(y_ongeclipt, grondhoogte_raster)
    ]


def clip_lijn_onder_profiel(
    x_ankers: list[float], y_ankers: list[float], dijkprofiel, marge: float = 0.02
) -> tuple[list[float], list[float]]:
    """Resampled en clipt (x_ankers, y_ankers) in één stap, zie `interpoleer_en_clip_op_raster`."""
    x_raster = bepaal_x_raster_onder_profiel(x_ankers, dijkprofiel)
    if not x_raster:
        return [], []
    grondhoogte_raster = [vind_y_in_dwarsprofiel(dijkprofiel, x) for x in x_raster]
    y_geclipt = interpoleer_en_clip_op_raster(
        x_raster, grondhoogte_raster, x_ankers, y_ankers, marge
    )
    return x_raster, y_geclipt


def clip_kern_onder_profiel(
    x_ankers: list[float],
    y_ankers: list[float],
    x_buiten: list[float],
    heeft_snijpunt: bool,
    dijkprofiel,
    marge: float = 0.02,
) -> tuple[list[float], list[float]]:
    """
    Clipt alleen het 'kern'-deel van de lijn (zie `bepaal_kern_bereik`) onder het dijkprofiel;
    het buitenwaterstand-vlak en het teensloot-vlak blijven ongewijzigd.
    """
    i_start, i_eind = bepaal_kern_bereik(x_buiten, heeft_snijpunt, len(x_ankers))
    kern_x, kern_y = x_ankers[i_start : i_eind + 1], y_ankers[i_start : i_eind + 1]
    kern_x_geclipt, kern_y_geclipt = clip_lijn_onder_profiel(kern_x, kern_y, dijkprofiel, marge)
    return (
        x_ankers[:i_start] + kern_x_geclipt + x_ankers[i_eind + 1 :],
        y_ankers[:i_start] + kern_y_geclipt + y_ankers[i_eind + 1 :],
    )


def bouw_geclipte_freatische_lijn(
    dijkprofiel,
    buitenwaterstand: float,
    x_peilbuis: list[float],
    y_peilbuis: list[float],
    x_ts_min: float,
    x_ts_max: float,
    teensloot_peil: float,
    heeft_kruin_peilbuis: bool,
    marge: float = 0.02,
) -> tuple[list[float], list[float]]:
    """Bepaalt het buiten-ankerpunt, bouwt de lijn-ankers en clipt het kern-deel onder het profiel."""
    x_buiten, heeft_snijpunt = bepaal_buiten_anker(
        dijkprofiel, buitenwaterstand, x_peilbuis, heeft_kruin_peilbuis
    )
    x_ankers, y_ankers = bouw_freatische_lijn_ankers(
        dijkprofiel,
        x_buiten,
        heeft_snijpunt,
        buitenwaterstand,
        x_peilbuis,
        y_peilbuis,
        x_ts_min,
        x_ts_max,
        teensloot_peil,
    )
    if not x_ankers:
        return [], []
    return clip_kern_onder_profiel(x_ankers, y_ankers, x_buiten, heeft_snijpunt, dijkprofiel, marge)


def bereken_waterstand_lijnen(
    dijkprofiel: list[tuple[float, float]],
    buitenwaterstand: float,
    maatgevend_boezempeil: float,
    teensloot_peil: float,
    x_peilbuis: list[float],
    y_peilbuis_mv: list[float | None],
    gemiddelden: list[float],
    peilbuis_codes: list[str | None] | None = None,
) -> tuple[DataFrame, DataFrame | None, bool, float, float, bool]:
    """
    Berekent de gemiddelde en schematische freatische lijnen.

    Geeft ook `x_ts_min`, `x_ts_max` en `heeft_kruin_peilbuis` terug zodat de dagelijkse
    (schuifregelaar-)lijn in plotten.py op dezelfde manier opgebouwd kan worden.
    """
    heeft_kruin_peilbuis: bool = (
        heeft_kruinzone_peilbuis(peilbuis_codes) if peilbuis_codes is not None else True
    )

    x_buitenwaterstand: list[float] = vind_x_in_dwarsprofiel(
        dijkprofiel, buitenwaterstand
    )
    x_boezempeil: list[float] = vind_x_in_dwarsprofiel(
        dijkprofiel, maatgevend_boezempeil
    )
    x_teensloot: list[float] = vind_x_in_dwarsprofiel(dijkprofiel, teensloot_peil)
    voldoende_snijpunten: bool = bool(
        x_buitenwaterstand and x_teensloot and x_boezempeil
    )

    # x_ts_min/x_ts_max zijn de nabije/verre oever van de teensloot. Als het profiel niet ver
    # genoeg is opgemeten om de verre oever nog een keer boven het teensloot_peil te laten
    # uitkomen, levert vind_x_in_dwarsprofiel maar één snijpunt op; gebruik dan het einde van
    # het opgemeten profiel als verre oever in plaats van dezelfde (nabije) x te hergebruiken
    # -- anders krijgt de teensloot-lijn breedte nul en wordt hij niet zichtbaar getekend.
    if len(x_teensloot) >= 2:
        x_ts_min: float = (
            min(x_teensloot, key=lambda x: abs(x - max(x_peilbuis)))
            if x_peilbuis
            else min(x_teensloot)
        )
        x_ts_max: float = max(x_teensloot)
    elif len(x_teensloot) == 1:
        x_ts_min = x_teensloot[0]
        x_ts_max = dijkprofiel[-1][0]
    else:
        x_ts_min = x_ts_max = dijkprofiel[-1][0]
    x_dijkprofiel_start: float = dijkprofiel[0][0]

    x_gem, y_gem = bouw_geclipte_freatische_lijn(
        dijkprofiel,
        buitenwaterstand,
        x_peilbuis,
        gemiddelden,
        x_ts_min,
        x_ts_max,
        teensloot_peil,
        heeft_kruin_peilbuis,
    )
    df_gem_waterstand = DataFrame({"x": x_gem, "y": y_gem})

    freatische_lijn = None
    plot_schematisatie: bool = False
    if (
        len(x_peilbuis) >= 4
        and voldoende_snijpunten
        and len(y_peilbuis_mv) >= 4
        and all(y is not None for y in y_peilbuis_mv)
    ):
        y_peilbuis_mv_zonder_none: list[float] = [
            y for y in y_peilbuis_mv if y is not None
        ]
        fr_insteek_berm = y_peilbuis_mv_zonder_none[2] - 0.1
        fr_binnenteen = y_peilbuis_mv_zonder_none[3] - 0.1
        fr_binnenkruin: float = (
            maatgevend_boezempeil - (x_peilbuis[1] - x_peilbuis[0]) * 0.1
        )
        # De schematische lijn start altijd op het maatgevend boezempeil (een vast
        # ontwerpniveau, net als de buitenwaterstand) en loopt recht door tot de BUKR-peilbuis.
        x_buiten_schematisch: list[float] = [x_dijkprofiel_start, x_peilbuis[0]]
        xs_freatische_lijn: list[float] = (
            x_buiten_schematisch + x_peilbuis[1:] + [x_ts_min, x_ts_max]
        )
        ys_freatische_lijn: list[float] = (
            [maatgevend_boezempeil, maatgevend_boezempeil]
            + [fr_binnenkruin]
            + [fr_insteek_berm]
            + [fr_binnenteen]
            + 2 * [teensloot_peil]
        )
        # Ook de schematische lijn mag, net als de gemiddelde en dagelijkse lijn, nergens boven
        # het dijkprofiel uitkomen (fysisch onmogelijk); het boezempeil- en teensloot-vlak zelf
        # blijven wel recht (legitiem open water boven een lokale geul/greppel in het profiel).
        xs_geclipt, ys_geclipt = clip_kern_onder_profiel(
            xs_freatische_lijn, ys_freatische_lijn, x_buiten_schematisch, True, dijkprofiel
        )
        freatische_lijn: DataFrame = DataFrame(
            {"x": xs_geclipt, "y": ys_geclipt}
        )
        plot_schematisatie: bool = True

    return (
        df_gem_waterstand,
        freatische_lijn,
        plot_schematisatie,
        x_ts_min,
        x_ts_max,
        heeft_kruin_peilbuis,
    )

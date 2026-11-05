# platform_match.py
"""
Normalisatie van peilbuisnamen, om onze werkboeken aan het dataplatform te koppelen.

Het probleem in één regel: wij kennen een peilbuis als `HB002PB01` met code
`B_BE0072+3_BUKR_GMW_PB1_F-385`, en het platform kent hem als
`meetreeks.code = 'HB28-PB1 '` met `sensor_meetlocatie_sleutel = '507080_HB28_GMW_PB1'`.
Welke sleutel de twee verbindt is niet bekend, dus zijn er meerdere strategieën
nodig die náást elkaar lopen en waarvan de trefkans gemeten wordt.

Alles hier is pure functie zonder Spark, zodat het lokaal te testen is en de
notebooks er alleen gebruik van maken.
"""

from __future__ import annotations

import re

_NIET_ALFANUMERIEK = re.compile(r"[^A-Za-z0-9]+")
_CIJFERGROEP = re.compile(r"\d+")
_PB_IN_CODE = re.compile(r"GMW[_-]?(PB\d+)", re.IGNORECASE)
_FILTER_IN_CODE = re.compile(r"_F([+-]\d+(?:\.\d+)?)\s*$")
_PUT_EN_PB = re.compile(r"^([A-Z]+)(\d+)(PB)(\d+)$", re.IGNORECASE)


def canoniek(waarde: str | None) -> str:
    """
    Brengt een naam of code terug tot een vergelijkbare vorm.

    Hoofdletters, alle scheidingstekens eruit, en voorloopnullen per cijfergroep
    weg -- zo komen `HB001PB01`, `HB-1_PB-1` en `hb1pb01` allemaal op `HB1PB1`
    uit. De trailing space van `meetreeks.code` ('HB28-PB1 ') verdwijnt hier
    vanzelf, en dat is precies de bedoeling.
    """
    if not waarde:
        return ""
    plat: str = _NIET_ALFANUMERIEK.sub("", str(waarde)).upper()
    return _CIJFERGROEP.sub(lambda m: str(int(m.group())), plat)


def put_en_filter(naam: str | None) -> tuple[str, str]:
    """
    Splitst `HB002PB01` in ('HB2', 'PB1').

    Het putdeel is wat met `meetreeks.code` of met het middenstuk van
    `sensor_meetlocatie_sleutel` te vergelijken valt; het filternummer
    onderscheidt de geneste filters in dezelfde put.
    """
    if not naam:
        return "", ""
    match = _PUT_EN_PB.fullmatch(_NIET_ALFANUMERIEK.sub("", str(naam)))
    if not match:
        return canoniek(naam), ""
    letters, putnummer, _, filternummer = match.groups()
    return f"{letters.upper()}{int(putnummer)}", f"PB{int(filternummer)}"


def pb_nummer(code_peilbuis: str | None) -> str:
    """'B_BE0072+3_BUKR_GMW_PB1_F-385' -> 'PB1'."""
    if not code_peilbuis:
        return ""
    match = _PB_IN_CODE.search(str(code_peilbuis))
    return match.group(1).upper() if match else ""


def filter_diepte_uit_code(code_peilbuis: str | None) -> float | None:
    """
    Leest het `_F-385`-achtervoegsel als getal: -3.85.

    De eenheid is een *hypothese*: centimeters, en onbekend of het om m NAP of om
    diepte onder maaiveld gaat. Notebook 30 toetst dat tegen
    `bovenkant_filter_nap` in plaats van het aan te nemen.
    """
    if not code_peilbuis:
        return None
    match = _FILTER_IN_CODE.search(str(code_peilbuis).strip())
    if not match:
        return None
    return float(match.group(1)) / 100.0


def sensor_sleutel_delen(sleutel: str | None) -> tuple[str, str, str]:
    """
    '507080_HB28_GMW_PB1' -> ('507080', 'HB28', 'PB1').

    Het eerste deel is een locatienummer waarvan de herkomst niet vaststaat; het
    middenstuk is het putdeel en het laatste het filternummer.
    """
    if not sleutel:
        return "", "", ""
    delen: list[str] = [d for d in str(sleutel).strip().split("_") if d]
    if len(delen) < 2:
        return "", "", ""
    locatie: str = delen[0]
    pb: str = ""
    for deel in reversed(delen):
        if re.fullmatch(r"PB\d+", deel, re.IGNORECASE):
            pb = deel.upper()
            break
    kern: list[str] = [d for d in delen[1:] if d.upper() != "GMW" and d.upper() != pb]
    return locatie, (kern[0].upper() if kern else ""), pb


def afstand_rd(x1: float, y1: float, x2: float, y2: float) -> float:
    """Afstand in meters tussen twee punten in RD New (EPSG:28992)."""
    return ((x1 - x2) ** 2 + (y1 - y2) ** 2) ** 0.5


# --- put + filter als één sleutel -------------------------------------------
# Werkt op alle vormen die in de werkboeken en in `meetreeks` voorkomen:
#   werkboek      HB002PB01, HB017-APB01
#   sensorsleutel HB002_GMW_PB1, 507080_HB28_GMW_PB1, 11653#_HB15_GMW_PB1, HB017-A_GMW_PB1
#   meetreekscode HB20-PB1, B09-PB2
# en levert telkens 'HB2|1', 'HB17A|1', 'B9|2'. Voorloopnullen tellen niet:
# Wiertsema schrijft HB002, Fugro HB02.

_PUT = r"([A-Z]+)(\d+)(?:-?([A-Z]))?"
_WERKBOEK_NAAM = re.compile(rf"^{_PUT}PB(\d+)$")
_MEETREEKS_CODE = re.compile(rf"^{_PUT}-PB(\d+)$")
_PUT_ALLEEN = re.compile(rf"^{_PUT}$")
_PB = re.compile(r"^PB(\d+)$")


def _sleutel(letters: str, nummer: str, achtervoegsel: str | None, filter_nr: str) -> str:
    return f"{letters}{int(nummer)}{achtervoegsel or ''}|{int(filter_nr)}"


def sleutel_uit_werkboek(peilbuis_naam: str | None) -> str | None:
    """'HB002PB01' -> 'HB2|1'; 'HB017-APB01' -> 'HB17A|1'."""
    m = _WERKBOEK_NAAM.match(str(peilbuis_naam or "").strip().upper())
    return _sleutel(*m.groups()) if m else None


def sleutel_uit_sensor(sensor_sleutel: str | None) -> str | None:
    """'507080_HB28_GMW_PB1' -> 'HB28|1': het deel vóór en ná 'GMW'."""
    delen: list[str] = str(sensor_sleutel or "").strip().upper().split("_")
    if "GMW" not in delen:
        return None
    i: int = delen.index("GMW")
    if i == 0 or i + 1 >= len(delen):
        return None
    put, pb = _PUT_ALLEEN.match(delen[i - 1]), _PB.match(delen[i + 1])
    return _sleutel(*put.groups(), pb.group(1)) if put and pb else None


def sleutel_uit_meetreeks_code(code: str | None) -> str | None:
    """'HB20-PB1 ' -> 'HB20|1'. Fugro-codes; 'MSLV8_o54154' levert None."""
    m = _MEETREEKS_CODE.match(str(code or "").strip().upper())
    return _sleutel(*m.groups()) if m else None


# --- dwarsprofielsleutel uit de peilbuiscode --------------------------------
# De code van een peilbuis noemt de dijkpaal waar het dwarsprofiel ligt:
#   B_BE0072+3_BUKR_GMW_PB1_F-385   -> BE, paal 72, +3 m
#   BE115+00_BIKR_GMW_PB1_F-334     -> BE, paal 115
#   HB_BLM0020+100_BUKR_GMW_PB1     -> BLM, paal 20, +100 m
#   B_D11-DP_70+6_BUKR_GMW_PB1      -> D11-DP, paal 70, +6 m
#   PB_MB_DP 179+65 m_KR_1          -> DP, paal 179, +65 m   (Schellingwoude)
#   PB_HB_DP 176_KR                 -> DP, paal 176          (zonder offset)

_DIJKPAAL = re.compile(r"(?:^|_)([A-Z]+\d*[A-Z]?-DP|[A-Z]{2,3})_?(\d+)\+(\d+)")
_DIJKPAAL_DP = re.compile(r"(?:^|_)(DP) (\d+)(?:\+(\d+))?")


def dijkpaal_uit_code(code_peilbuis: str | None) -> tuple[str, int, int] | None:
    """'B_BE0072+3_BUKR_GMW_PB1_F-385' -> ('BE', 72, 3); None zonder dijkpaal."""
    tekst: str = str(code_peilbuis or "").strip().upper()
    m = _DIJKPAAL.search(tekst) or _DIJKPAAL_DP.search(tekst)
    if not m:
        return None
    return m[1], int(m[2]), int(m[3] or 0)


def dwarsprofiel_sleutel(prefix: str, paal: int, offset: int | None = None) -> str:
    """('BE', 72) -> 'BE_0072'; met offset 'BE_0072+3', voor als twee profielen een paal delen."""
    sleutel: str = f"{prefix}_{paal:04d}"
    return sleutel if offset is None else f"{sleutel}+{offset}"

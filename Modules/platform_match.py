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

# stix_verwerker.py
from collections import defaultdict

import numpy as np
from geolib.models import DStabilityModel
from shapely.geometry import Polygon


def argb_naar_rgb(argb_hex: str) -> str:
    """Converteert een ARGB hex-kleurstring naar een RGB hex-string."""
    if argb_hex.startswith("#") and len(argb_hex) == 9:
        return "#" + argb_hex[3:]
    elif argb_hex.startswith("#") and len(argb_hex) == 7:
        return argb_hex
    else:
        print(f"⚠️ Ongeldig (A)RGB hex formaat: {argb_hex}. Gebruik grijs.")
        return "#808080"


def haal_bodemlaag_eigenschappen_op(bodemlaag, stix_data, index=0):
    """Extraheert geometrie, code en kleur voor een bodemlaag uit STIX-data."""
    geometrie = None
    grondsoort_code: str | None = None
    grondsoort_kleur: str | None = None

    for laag in stix_data.geometries[index].Layers:
        if laag.Id == bodemlaag.LayerId:
            geometrie = laag
            break
    for grondsoort in stix_data.soils.Soils:
        if grondsoort.Id == bodemlaag.SoilId:
            grondsoort_code: str | None = grondsoort.Code
            break
    for visu in stix_data.soilvisualizations.SoilVisualizations:
        if visu.SoilId == bodemlaag.SoilId:
            grondsoort_kleur: str | None = visu.Color
            break
    return geometrie, grondsoort_code, grondsoort_kleur


def verwerk_stix_bestand(stix_bestands_pad):
    """
    Parset een .stix-bestand om geometrieën en kleuren van bodemlagen te extraheren.

    Returns:
        Een woordenboek met grondsoortcodes als sleutels en hun kleur en polygonen als waarden.
    """
    geometrie_per_grondsoort: defaultdict[str | None, list[Polygon]] = defaultdict(
        lambda: {"color": None, "polygons": []}
    )
    try:
        dm: DStabilityModel = DStabilityModel()
        dm.parse(stix_bestands_pad)
        stix_data = dm.datastructure

        if not stix_data.geometries or not stix_data.soillayers:
            print(f"⚠️ Geen geometrie of bodemlagen gevonden in {stix_bestands_pad.name}")
            return {}

        for bodemlaag in stix_data.soillayers[0].SoilLayers:
            geometrie, grondsoort_code, grondsoort_kleur = haal_bodemlaag_eigenschappen_op(
                bodemlaag, stix_data
            )
            if geometrie is None or grondsoort_code is None or grondsoort_kleur is None:
                continue

            if geometrie_per_grondsoort[grondsoort_code]["color"] is None:
                geometrie_per_grondsoort[grondsoort_code]["color"] = grondsoort_kleur

            coords = [(pt.X, pt.Z) for pt in geometrie.Points]
            try:
                polygoon: Polygon = Polygon(coords)
                if polygoon.is_valid:
                    geometrie_per_grondsoort[grondsoort_code]["polygons"].append(polygoon)
            except Exception:
                pass
        return geometrie_per_grondsoort
    except Exception as e:
        print(f"❌ Fout bij verwerken van {stix_bestands_pad.name}: {e}")
        return {}


def _stix_bovenrand(
    geometrie_per_grondsoort: dict, bin_breedte: float = 0.5
) -> tuple[np.ndarray, np.ndarray]:
    """
    Reconstrueert het grondoppervlak (bovenrand) van de STIX-geometrie: voor elke
    x-strook van `bin_breedte` breed wordt het hoogste punt uit alle bodemlaagpolygonen
    genomen.
    """
    xs, ys = [], []
    for waarde in geometrie_per_grondsoort.values():
        for poly in waarde["polygons"]:
            for px, py in poly.exterior.coords:
                xs.append(px)
                ys.append(py)
    xs = np.array(xs)
    ys = np.array(ys)

    bins = np.arange(xs.min(), xs.max() + bin_breedte, bin_breedte)
    indices = np.digitize(xs, bins)
    bovenrand_x, bovenrand_y = [], []
    for b in np.unique(indices):
        masker = indices == b
        bovenrand_x.append(xs[masker].mean())
        bovenrand_y.append(ys[masker].max())

    volgorde = np.argsort(bovenrand_x)
    return np.array(bovenrand_x)[volgorde], np.array(bovenrand_y)[volgorde]


def bepaal_horizontale_offset(
    dijkprofiel: np.ndarray,
    geometrie_per_grondsoort: dict,
    zoekbereik: float = 30.0,
    stap: float = 0.1,
) -> tuple[float, float, float]:
    """
    Bepaalt de horizontale verschuiving (dx) waarmee het Excel-dijkprofiel het beste
    aansluit op het grondoppervlak van de (ongewijzigde, leidende) STIX-geometrie.

    In tegenstelling tot de oudere "kruin-op-kruin"-matching (die maar één punt gebruikte
    en daardoor gevoelig was voor een net iets afwijkend hoogste punt) wordt hier de volle
    profiellengte gebruikt: dx minimaliseert de RMSE tussen het (verschoven) dijkprofiel en
    de STIX-bovenrand over het gehele overlappende traject.

    Returns:
        tuple: (dx, rmse_met_correctie, rmse_zonder_correctie). Bij onvoldoende overlap
        wordt (0.0, nan, nan) teruggegeven.
    """
    bovenrand_x, bovenrand_y = _stix_bovenrand(geometrie_per_grondsoort)

    dij_x, dij_y = dijkprofiel[:, 0], dijkprofiel[:, 1]
    volgorde = np.argsort(dij_x)
    dij_x, dij_y = dij_x[volgorde], dij_y[volgorde]

    def rmse_bij(dx: float) -> float | None:
        verschoven_dij_x = dij_x + dx
        masker = (bovenrand_x >= verschoven_dij_x.min()) & (
            bovenrand_x <= verschoven_dij_x.max()
        )
        if masker.sum() < 5:
            return None
        interp_dij_y = np.interp(bovenrand_x[masker], verschoven_dij_x, dij_y)
        verschil = interp_dij_y - bovenrand_y[masker]
        return float(np.sqrt(np.mean(verschil**2)))

    kandidaten = np.arange(-zoekbereik, zoekbereik, stap)
    scores = [(rmse_bij(dx), dx) for dx in kandidaten]
    scores = [(s, dx) for s, dx in scores if s is not None]

    rmse_zonder = rmse_bij(0.0)
    if not scores:
        return 0.0, float("nan"), rmse_zonder if rmse_zonder is not None else float("nan")

    beste_rmse, beste_dx = min(scores)
    return beste_dx, beste_rmse, rmse_zonder if rmse_zonder is not None else float("nan")

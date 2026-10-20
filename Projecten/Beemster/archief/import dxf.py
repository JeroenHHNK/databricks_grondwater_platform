# -*- coding: utf-8 -*-
"""
Created on Thu Jul 17 13:49:33 2025

@author: thanzen
"""

import itertools
from collections import defaultdict
from pathlib import Path

import ezdxf
import matplotlib.pyplot as plt

base_path = Path(
    "S:/Waterveiligheid_2030/Pilaar 3 - Analyse/06 Rekenbestanden/freatische_lijn/Beemster/Data"
)
dxf = base_path / "86349-1_DWP004.dxf"

read_dxf = ezdxf.readfile(dxf)

# Toegang tot modelspace (de hoofdtekening)
msp = read_dxf.modelspace()

e = next(iter(msp))  # pak de eerste entity
print(e.dxftype())
print(dir(e.dxf))  # toont alle beschikbare attributen
print(e.dxf.__dict__)  # toont de waarden van attributen als dict

for layer in read_dxf.layers:
    print(layer.dxf.name)

# Filter op laagnaam "Laagscheiding"
entities_laagscheiding = msp.query('*[layer=="Laagscheiding"]')

print(f"Aantal entities op laag 'Laagscheiding': {len(entities_laagscheiding)}")

for e in entities_laagscheiding:
    print(f"Type: {e.dxftype()}, Attributen: {e.dxf.attribs()}")


fig, ax = plt.subplots()

for e in entities_laagscheiding:
    if e.dxftype() == "LWPOLYLINE":
        points = e.get_points()
        x, y = zip(*[(p[0], p[1]) for p in points])
        if e.closed:
            x = list(x) + [x[0]]
            y = list(y) + [y[0]]
        ax.plot(x, y, "b-")

ax.set_aspect("equal")
plt.title("Entities op laag 'Laagscheiding'")
plt.show()

######################################################################################################


def plot_filled_lwpolyline(ax, entity, kleur):
    points = entity.get_points()
    x, y = zip(*[(p[0], p[1]) for p in points])
    if entity.closed:
        x = list(x) + [x[0]]
        y = list(y) + [y[0]]
        ax.fill(x, y, kleur, alpha=0.4, label="Polygoon")
    else:
        # Niet gesloten polyline gewoon als lijn
        ax.plot(x, y, kleur, label="Polyline")


def main():
    base_path = Path(
        "S:/Waterveiligheid_2030/Pilaar 3 - Analyse/06 Rekenbestanden/freatische_lijn/Beemster/Data"
    )
    dxf = base_path / "86349-1_DWP004.dxf"

    read_dxf = ezdxf.readfile(dxf)

    # Toegang tot modelspace (de hoofdtekening)
    msp = read_dxf.modelspace()

    fig, ax = plt.subplots()

    # Kleuren cyclus voor vullen
    kleuren = itertools.cycle(["red", "green", "blue", "orange", "purple", "cyan"])

    # Eerst vullen van polylines op laag Laagscheiding
    polylines = msp.query('LWPOLYLINE[layer=="Laagscheiding"]')

    for pline in polylines:
        kleur = next(kleuren)
        plot_filled_lwpolyline(ax, pline, kleur)

    # Dan blocks en attributen plotten
    blocks = msp.query('INSERT[layer=="Laagscheiding"]')
    for blk in blocks:
        insert_pos = blk.dxf.insert
        ax.plot(insert_pos[0], insert_pos[1], "ko")  # puntje waar block zit
        # Attribuutvelden printen en plotten
        for attrib in blk.attribs():
            waarde = attrib.dxf.text
            pos = attrib.dxf.insert
            ax.text(pos[0], pos[1], waarde, fontsize=8, color="black")

    ax.set_aspect("equal")
    plt.show()


if __name__ == "__main__":
    main()

###########################################################################


def plot_filled_lwpolyline(ax, entity, kleur):
    points = entity.get_points()
    x, y = zip(*[(p[0], p[1]) for p in points])
    if entity.closed:
        x = list(x) + [x[0]]
        y = list(y) + [y[0]]
        ax.fill(x, y, kleur, alpha=0.4, edgecolor="k")
    else:
        ax.plot(x, y, kleur)


def get_avg_top_y(polyline, top_pct=0.2):
    """Gebruik de bovenste 20% van y-waarden om hoogte van de bovenrand te bepalen."""
    points = polyline.get_points()
    y_coords = sorted([p[1] for p in points], reverse=True)
    n = max(1, int(len(y_coords) * top_pct))
    top_y_coords = y_coords[:n]
    return sum(top_y_coords) / len(top_y_coords)


def group_by_y_top(polylines, tolerance=1.0):
    """
    Groepeer polygonen die ongeveer dezelfde bovenrandhoogte hebben.
    :param polylines: lijst met gesloten polygonen
    :param tolerance: y-afwijking die nog als 'zelfde laag' telt
    """
    groepen = defaultdict(list)
    for pl in polylines:
        avg_y = get_avg_top_y(pl)
        matched = False
        for group_y in groepen:
            if abs(avg_y - group_y) < tolerance:
                groepen[group_y].append(pl)
                matched = True
                break
        if not matched:
            groepen[avg_y].append(pl)
    return groepen


def main():
    # === DXF-bestandspad ===
    base_path = Path(
        "S:/Waterveiligheid_2030/Pilaar 3 - Analyse/06 Rekenbestanden/freatische_lijn/Beemster/Data"
    )
    dxf = base_path / "86349-1_DWP004.dxf"

    if not dxf.exists():
        print(f"⚠️ Bestand niet gevonden: {dxf}")
        return

    # === DXF inlezen ===
    read_dxf = ezdxf.readfile(dxf)
    msp = read_dxf.modelspace()

    fig, ax = plt.subplots()

    # === Gesloten polygonen op laag 'Laagscheiding' ===
    polylines = [
        pl for pl in msp.query('LWPOLYLINE[layer=="Laagscheiding"]') if pl.closed
    ]

    # === Groepeer polygonen per hoogtegroep (bovenrand) ===
    y_groups = group_by_y_top(polylines, tolerance=1.0)

    # Sorteer van boven naar onder (hoge y eerst)
    sorted_groups = sorted(y_groups.items(), key=lambda g: -g[0])

    # === Kleur per laaggroep ===
    kleuren = itertools.cycle(
        [
            "#d9f0a3",
            "#addd8e",
            "#78c679",
            "#41ab5d",
            "#238443",
            "#006837",  # groen
            "#fee08b",
            "#fdae61",
            "#f46d43",
            "#d73027",  # oranje/rood
            "#ffffbf",
            "#d0d1e6",
            "#a6bddb",
            "#74a9cf",
            "#2b8cbe",
            "#045a8d",  # blauwgrijs
        ]
    )

    for _, groep in sorted_groups:
        kleur = next(kleuren)
        for pl in groep:
            plot_filled_lwpolyline(ax, pl, kleur)

    # === Plot attributen/blocks indien gewenst ===
    blocks = list(msp.query('INSERT[layer=="Laagscheiding"]'))
    for blk in blocks:
        insert_pos = blk.dxf.insert
        ax.plot(insert_pos[0], insert_pos[1], "ko")  # zwart puntje
        for attrib in blk.attribs():
            waarde = attrib.dxf.text
            pos = attrib.dxf.insert
            ax.text(
                pos[0],
                pos[1],
                waarde,
                fontsize=8,
                color="black",
                ha="center",
                va="center",
                bbox=dict(
                    facecolor="white", edgecolor="black", boxstyle="round,pad=0.2"
                ),
            )

    ax.set_aspect("equal")
    plt.xlabel("X [m]")
    plt.ylabel("Y [m]")
    plt.tight_layout()
    plt.show()


if __name__ == "__main__":
    main()

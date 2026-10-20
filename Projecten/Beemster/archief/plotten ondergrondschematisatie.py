# -*- coding: utf-8 -*-
"""
Created on Fri Jul 18 11:48:47 2025

@author: thanzen
"""

import json
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
from shapely.geometry import Polygon

# Pad naar bestand
base_path = Path(
    "s:/Waterveiligheid_2030/Pilaar 3 - Analyse/06 Rekenbestanden/freatische_lijn/Purmer/data"
)
txt = base_path / "schematisatie_profiel_16_purmer.txt"

# Bestand inladen
with open(txt, "r", encoding="utf-8") as f:
    data = json.load(f)

layers = data["Data"]["Layers"]

# Groepeer lagen op label
geometries_by_label = defaultdict(list)

for layer in layers:
    label = layer["Label"]
    points = layer["Points"]
    coords = [(pt["X"], pt["Z"]) for pt in points]
    if coords[0] != coords[-1]:
        coords.append(coords[0])
    polygon = Polygon(coords)
    if polygon.is_valid:
        geometries_by_label[label].append(polygon)
    else:
        print(f"Waarschuwing: Ongeldige geometrie voor label {label}")


# Extract surface line and save to Excel
all_points = []
for layer in layers:
    for point in layer["Points"]:
        all_points.append((point["X"], point["Z"]))

surface_points_dict = {}
for x, z in all_points:
    if x not in surface_points_dict or z > surface_points_dict[x]:
        surface_points_dict[x] = z

surface_points = sorted(surface_points_dict.items())

df = pd.DataFrame(surface_points, columns=["x", "y"])

output_path = base_path / "surface_line_profiel16_purmer.xlsx"
df.to_excel(output_path, index=False)

print(f"Surface line saved to {output_path}")


# Kleuren toekennen op basis van labelstart
def get_color(label):
    if label.startswith("K"):
        return "darkgreen"
    elif label.startswith("Zk") or label.startswith("Z"):
        return "#FFFFCC"  # lichtgeel
    elif label.startswith("V"):
        return "saddlebrown"
    else:
        return "gray"  # fallback kleur


# Plotten
fig, ax = plt.subplots(figsize=(12, 8))

# Houd bij welke labels al in de legenda staan
legend_labels_done = set()

for label, polygons in geometries_by_label.items():
    color = get_color(label)
    show_label = label not in legend_labels_done  # Toon label één keer
    for poly in polygons:
        x, y = poly.exterior.xy
        ax.fill(x, y, color=color, label=label if show_label else None)
        show_label = False
    legend_labels_done.add(label)

# Legenda automatisch afgeleid uit gebruikte labels
ax.legend(loc="upper right", bbox_to_anchor=(1.3, 1))
ax.set_title("Schematisatie ondergrond dijkprofiel 4 Beemster")
ax.set_xlabel("Diepte [m]")
ax.set_ylabel("Lengte [m]")
plt.tight_layout()
plt.show()

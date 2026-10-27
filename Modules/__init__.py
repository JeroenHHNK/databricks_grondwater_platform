# Modules/__init__.py
"""
WV2030 Boxplots package.

Deze package bevat modules voor het analyseren en plotten van grondwaterdata voor het
Waterveiligheid 2030 project.

Hier wordt bewust niets geïmporteerd: `from Modules import paths` laadt dan
alleen `paths`. Zo hoeft een Databricks-notebook dat alleen paden of de
config nodig heeft geen folium, plotly of shapely op het cluster te hebben.
"""

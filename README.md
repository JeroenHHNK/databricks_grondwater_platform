# Boxplots-pipeline

Python modules om grondwater in dijken te visualiseren.

Pipeline voor het inlezen van peilbuis-/meetdata en D-Stability (`.stix`)
profielen en het genereren van boxplots, tijdreeksen en overzichtskaarten per
project binnen Waterveiligheid 2030 (Pilaar 3 – Analyse).

## Structuur

- `main.py` — entrypoint van de pipeline.
- `Modules/` — herbruikbare modules:
  - `config_lader.py` — inlezen van project-`config.yaml`.
  - `data_lader.py` — inlezen van meetdata.
  - `stix_verwerker.py` — verwerken van D-Stability `.stix` bestanden.
  - `analyse.py` — analyse en afleiding (o.a. dijkzones).
  - `plotten.py` — boxplots en tijdreeksen.
  - `kaart_generator.py` — kaarten (folium).
- `genereer_overzichtskaart_alle_projecten.py` — overzichtskaart over alle projecten.
- `Projecten/<project>/` — per project: `config/config.yaml`, `data/` (invoer) en output.

## Vereisten

Python >= 3.12. Afhankelijkheden staan in `pyproject.toml`; installeren met [uv](https://docs.astral.sh/uv/):

```bash
uv sync
```

## Gebruik

```bash
uv run python main.py
```

## Data

Ruwe meetdata (`.xlsx`), D-Stability bestanden (`.stix`) en gegenereerde output
(`.html`, `.png`, `.pdf`) worden **niet** in deze repository bijgehouden (zie
`.gitignore`). Alleen code en per-project configuratie (`config.yaml`) staan onder
versiebeheer.

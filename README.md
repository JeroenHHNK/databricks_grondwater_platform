# Boxplots-pipeline

Python modules om grondwater in dijken te visualiseren.

Pipeline voor het inlezen van peilbuis-/meetdata en D-Stability (`.stix`)
profielen en het genereren van boxplots, tijdreeksen en overzichtskaarten per
project binnen Waterveiligheid 2030 (Pilaar 3 – Analyse).

## Structuur

```
input_data/<project>/                 handmatig onderhouden invoer (versiebeheerd)
    <project>.xlsx                      het werkboek: bron van waarheid
    neerslagdata_berkhout_*.xlsx
    D-Stability/*.stix
    extra/                              terugkeertijden, buitenwaterstanden, werkbestanden
    peilbuisdata/                       ruwe meetexports (NIET versiebeheerd)
input_data/_bron/<project>/           de oude config.yaml en werkboeken, als herkomst
Projecten/<project>/plots/            gegenereerde uitvoer (NIET versiebeheerd)
notebooks/                            Databricks, zie notebooks/README.md
Modules/                              de pipeline zelf
scripts/                              eenmalige migratie en controles
```

- `main.py` — entrypoint.
- `Modules/paths.py` — alle paden en Unity Catalog-namen op één plek.
- `Modules/workbook_lader.py` — leest het werkboek.
- `Modules/config_lader.py` — de oude YAML/Excel-lader; alleen nog in gebruik door
  `scripts/vergelijk_invoer.py` om de migratie te controleren.
- `Modules/data_lader.py` — meetreeksen, neerslag, terugkeertijden.
- `Modules/stix_verwerker.py` — D-Stability `.stix` (grondopbouw).
- `Modules/analyse.py` — freatische lijn en dijkzones.
- `Modules/plotten.py` — boxplots en tijdreeksen (Plotly).
- `Modules/kaart_generator.py` — overzichtskaarten (folium).
- `Modules/platform_match.py` — normalisatie van peilbuisnamen voor Databricks.

## Vereisten

Python >= 3.12. Afhankelijkheden staan in `pyproject.toml`; installeren met [uv](https://docs.astral.sh/uv/):

```bash
uv sync
```

## Gebruik

```bash
uv run python main.py --project Beemster      # één project
uv run python main.py --alle                  # alle projecten
uv run python main.py --alle --strict         # stop bij de eerste fout
uv run python genereer_overzichtskaart_alle_projecten.py   # kaart over alle projecten
```

Het project wordt met `--project` gekozen; dat was eerder een constante die je in
`main.py` moest aanpassen. Een run die nul plots oplevert eindigt met exitcode 1,
zodat een geplande job niet stil slaagt zonder iets te maken.

## Het werkboek per project

`input_data/<project>/<project>.xlsx` is de invoer en wordt met de hand
onderhouden. Vier soorten bladen:

| Blad | Inhoud |
|---|---|
| `project` | sleutel/waarde: projectnummer, buitenwaterstand, bestandsnamen |
| `profielen` | één rij per profiel, met `stix_bestand` expliciet erin |
| `peilbuizen` | **één rij per peilbuis per profiel** |
| `coords_<profiel_id>` | de dwarsprofielgeometrie (`x`, `y`) |

Het blad `peilbuizen` vervangt drie komma-/schuine-streep-gescheiden lijsten die
alleen op positie aan elkaar gekoppeld waren (`stijghoogte_namen`,
`Code_peilbuis`, `x_coordinaten_dwarsprofiel`) plus `metadata_pb_indices`: een
lijstje rij-indexen in een tweede werkboek. Eén ingevoegde rij daar verschoof
stil elk profiel. De metadatawaarden staan nu als waarde in het werkboek.

Verder heeft het blad een expliciete `zone`-kolom (`kruin`/`berm`/`teen`), die
eerder met een regex uit de vrije-tekst peilbuiscode werd geraden, en lege
`meetreeks_id`-kolommen die de Databricks-notebooks vullen.

### Werkboeken opnieuw bouwen

De werkboeken zijn eenmalig uit de oude bestanden gegenereerd:

```bash
uv run python scripts/bootstrap_workbook.py --alle          # bouwt ze opnieuw
uv run python scripts/vergelijk_invoer.py --alle            # bewijst dat niets verandert
```

`vergelijk_invoer.py` controleert het werkboek tegen `input_data/_bron/`: zelfde
algemene config, profielnamen, lijsten, metadata en geometrie. Draai dat na elke
wijziging aan `workbook_lader` of `bootstrap_workbook`. **Let op:** `bootstrap_workbook`
overschrijft handmatige aanvullingen in het werkboek, zoals ingevulde zones en
`meetreeks_id`. Het is een migratiescript, geen onderdeel van de pipeline.

## Databricks

De notebooks in `notebooks/` halen de stijghoogtes en de coördinaten per
dwarsprofiel uit het dataplatform, in plaats van uit een met de hand bijgewerkte
Excel-export. Zie [`notebooks/README.md`](notebooks/README.md). De boxplots
blijven voorlopig lokaal.

`input_data/` staat daarom **wel** onder versiebeheer, inclusief `.xlsx` en
`.stix`: de cluster heeft geen internet en geen S-schijf, dus de Git folder moet
de invoer leveren. De ruwe meetexports in `input_data/*/peilbuisdata/` blijven
erbuiten — die zijn samen ~95 MB en worden juist door de notebooks vervangen.

## Bekende aandachtspunten

- **Zone-token `KR`** kent `analyse.py` niet, dus levert het daar `None` op. Het
  staat in 12 peilbuizen in Beemster, Purmer, Schellingwoude en Schermer. In
  Schellingwoude staat het op positie 1 van profiel 1 t/m 4, waar normaal `BUKR`
  staat, waardoor `heeft_kruinzone_peilbuis` daar `False` geeft en de freatische
  lijn zonder kruinanker wordt opgebouwd. In het werkboek is de zone nu
  expliciet; `analyse.py` is bewust niet aangepast, omdat dat elke plot
  verandert. Dat is een aparte wijziging, met figuren voor en na.
- **13 peilbuizen in 13_6** hebben codes zonder zone-token (bv.
  `PB483-1 _F-207`); hun `zone` staat leeg en moet met de hand ingevuld worden.
- **`analyse.py` gebruikt twee mechanismen voor dezelfde vraag**: de zone uit de
  code, en de rij-volgorde (`y_peilbuis_mv[2]` = berm, `[3]` = teen). Die kunnen
  elkaar tegenspreken.
- **Bladnamen in de meetexport worden op deelstring gezocht**, eerste treffer
  wint. Met namen als `HB001PB01` en `HB010PB01` in bladnamen die Excel op 31
  tekens afkapt, kan dat het verkeerde blad pakken. Het werkboek heeft daarom een
  kolom `peilbuisdata_blad` om het blad expliciet vast te leggen.

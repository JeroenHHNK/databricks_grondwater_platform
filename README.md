# Boxplots-pipeline

Python modules om grondwater in dijken te visualiseren.

Pipeline voor het inlezen van peilbuis-/meetdata en D-Stability (`.stix`)
profielen en het genereren van boxplots, tijdreeksen en overzichtskaarten per
project binnen Waterveiligheid 2030 (Pilaar 3 – Analyse).

## Structuur

```
input_data/                           handmatig onderhouden invoer (versiebeheerd), alles csv
    config.csv                          één rij per peilbuis per dwarsprofiel, alle projecten
    projecten.csv                       één rij per project: buitenwaterstand, bestandsnamen
    D-Stability/<sleutel>.stix          één per dwarsprofiel (BE_0072.stix)
    dwarsprofielen/<sleutel>.csv        de dwarsprofielgeometrie (x, y)
    koppeltabel_LD_DD.csv               onze peilbuizen naast de meetreeksen op Databricks
    object_data_databricks_dwarsprofielen.csv   export van meetreeks + meetlocatie
    <project>/                          wat niet per dwarsprofiel is:
        neerslagdata_berkhout_*.csv
        extra/                            terugkeertijden, buitenwaterstanden (csv)
        peilbuisdata/                     meetreeksen (NIET versiebeheerd):
            <export>.xlsx                   de ruwe export
            <peilbuis_naam>.csv             één reeks per peilbuis, uit die export
Projecten/<project>/plots/            gegenereerde uitvoer (NIET versiebeheerd)
notebooks/                            Databricks, zie notebooks/README.md
Modules/                              de pipeline zelf
scripts/                              koppeltabel, export omzetten, geoloket-export aanvullen
```

- `main.py` — entrypoint; schrijft de plots als HTML.
- `app.py` — Streamlit-app; toont dezelfde plots zonder HTML-bestanden.
- `Modules/profiel.py` — de verwerking van één profiel tot figuren, gedeeld door
  `main.py` en `app.py`. De stijghoogtebron is een parameter.
- `Modules/paths.py` — alle paden en Unity Catalog-namen op één plek.
- `Modules/workbook_lader.py` — leest de centrale invoer en controleert die.
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

### Streamlit-app

```bash
uv run streamlit run app.py
```

Kies een project en profiel in de zijbalk. De profielplot en de tijdreeks staan
samen in één iframe, zodat de tijdslider gekoppeld blijft aan de markering in de
tijdreeks; "Download als HTML" levert hetzelfde bestand op als `main.py`. Er
wordt niets naar schijf geschreven.

De stijghoogtes komen uit `input_data/<project>/peilbuisdata/<peilbuis_naam>.csv`,
anders uit de export (xlsx) in diezelfde map. Is er geen van beide, dan vraagt de
app om een upload van de export, die alleen in het geheugen blijft.

**De meetreeksen staan niet in Git** (`peilbuisdata/` is uitgesloten). Zet een
export in `input_data/<project>/peilbuisdata/` onder de naam uit
`peilbuisdata_bestand` in `projecten.csv` en maak er csv's per peilbuis van:

```bash
uv run python scripts/zet_export_om.py --project Beemster
```

Het project wordt met `--project` gekozen; dat was eerder een constante die je in
`main.py` moest aanpassen. Een run die nul plots oplevert eindigt met exitcode 1,
zodat een geplande job niet stil slaagt zonder iets te maken.

## De invoer

Alle invoer staat centraal in `input_data/` en wordt met de hand onderhouden.

| Bestand | Inhoud |
|---|---|
| `config.csv` | **één rij per peilbuis per dwarsprofiel**, voor alle projecten |
| `projecten.csv` | één rij per project: buitenwaterstand, boezempeiloffset, bestandsnamen |
| `D-Stability/<sleutel>.stix` | de grondopbouw van een dwarsprofiel |
| `dwarsprofielen/<sleutel>.csv` | de geometrie van een dwarsprofiel (`x`, `y`) |
| `<project>/` | alleen wat niet per dwarsprofiel is: neerslag, `extra/`, `peilbuisdata/` |

### config.csv

Alleen wat Databricks niet levert. De stijghoogtes en de meetreeksen staan op
het platform en zijn te vinden via `meetlocatie_id` (`cs_meetlocatie_id`): één
filter van één put, ook als de logger is vervangen en er een nieuwe meetreeks bij
kwam.

| kolom | betekent |
|---|---|
| `project` | moet in `projecten.csv` staan |
| `cross_section_key` | de sleutel van het dwarsprofiel (`BE_0072`); uniek over alle projecten |
| `diepte_niveau` | `hoofd`, of `A`/`B` voor diepere peilbuizen in hetzelfde dwarsprofiel |
| `peilbuis` | put + filter (`MB001PB01`); ook de naam van de lokale reeks-csv |
| `projectnummer` | het onderzoeksproject van de peilbuis (`VN-86349-1`); nodig om te koppelen |
| `meetlocatie_id` | de sleutel naar Databricks; leeg = (nog) niet gekoppeld |
| `koppeling` | de status uit de koppeltabel, zodat zichtbaar is waarom een id ontbreekt |
| `x_profiel` | ligging in het dwarsprofiel (m); bepaalt ook de volgorde van de peilbuizen |
| `x_rd`, `y_rd` | RD-coördinaten van de put |
| `bk_peilbuis`, `bk_filter`, `ok_filter` | bovenkant peilbuis en filter (m NAP), uit de inmeting |
| `code` | volledige peilbuiscode; label in de plot en bron van de zone (`BUKR`, `BIT`, ...) |
| `teensloot_peil`, `stix_offset_x` | per dwarsprofiel, herhaald op elke rij ervan |

Een rij is uniek op `project + cross_section_key + diepte_niveau + peilbuis`.
Een peilbuisnaam alleen is geen sleutel: `HB013PB01` bestaat in zes projecten.
Filterdieptes en bovenkant peilbuis staan ook op Databricks, maar afgerond op
0,1 m en voor een paar putten 1,5 m anders dan de inmeting; daarom staan ze hier.
Het kaartpunt van een dwarsprofiel is de eerste peilbuis van het hoofdprofiel.

### Dwarsprofielen heten naar hun dijkpaal

Een dwarsprofiel heet naar de dijkpaal waar het ligt, zoals die in de
peilbuiscodes staat: `B_BE0072+3_BUKR_GMW_PB1_F-385` → `BE_0072`. Die sleutel is
de naam van het stix-bestand (`D-Stability/BE_0072.stix`), de geometrie
(`dwarsprofielen/BE_0072.csv`) en de plot (`plot_Beemster_BE_0072.html`).

- Delen twee dwarsprofielen een dijkpaal, dan krijgen beide de offset erbij:
  `BLM_0019+89` en `BLM_0019+99`, `D12-DP_0016+66` en `D12-DP_0016+187`.
- Diepere peilbuizen in hetzelfde dwarsprofiel krijgen dezelfde sleutel met
  `diepte_niveau` `A` (of `B`); de app toont ze in de plot van het hoofdprofiel.

### Een nieuw dwarsprofiel toevoegen

1. **Kies de sleutel.** Neem de dijkpaal uit de code van de eerste peilbuis
   (meestal de kruin): `B_BE0072+3_BUKR_GMW_PB1_F-385` → `BE_0072` (voorvoegsel,
   `_`, paalnummer met vier cijfers). Bestaat die sleutel al, in welk project
   dan ook, zet dan de offset erachter: `BE_0072+3`. Diepere peilbuizen in
   hetzelfde dwarsprofiel krijgen dezelfde sleutel met `diepte_niveau` `A` of `B`.
2. **Stix**: zet het bestand in `input_data/D-Stability/<sleutel>.stix`.
3. **Geometrie**: zet het dijkprofiel in `input_data/dwarsprofielen/<sleutel>.csv`
   met de kolommen `x,y` (m langs het profiel, m NAP). Gebruik dezelfde
   x-oorsprong als `x_profiel` van de peilbuizen.
4. **Peilbuizen**: voeg per peilbuis een rij toe aan `input_data/config.csv` en
   vul `project` tot en met `teensloot_peil` in; laat `meetlocatie_id` en
   `koppeling` leeg. Gaat het om een nieuw project, voeg dan eerst een rij toe
   aan `projecten.csv` en maak `input_data/<project>/` met een neerslagbestand.
5. **Koppel aan Databricks**:
   ```bash
   uv run python scripts/bouw_koppeltabel_ld_dd.py --vul-config
   ```
   Dat vult `meetlocatie_id` waar de koppeling eenduidig is (`gekoppeld`,
   `meerdere sensoren`) en zet in `koppeling` de status. Beoordeel
   `twijfelachtig` en `meerdere kandidaten` met de hand in
   `input_data/koppeltabel_LD_DD.csv` en vul daar zelf de `meetlocatie_id` in.
   Zijn de putten nieuw op het platform, ververs dan eerst
   `object_data_databricks_dwarsprofielen.csv`.
6. **Controleer**:
   ```bash
   uv run python main.py --project <project> --strict
   uv run streamlit run app.py
   ```
   `main.py` stopt bij een fout in de invoer: een sleutel die ook in een ander
   project staat, een ontbrekende geometrie, een dubbele rij of een
   `meetlocatie_id` die bij twee peilbuizen hoort. Een stix- of geometriebestand
   zonder rij in `config.csv` geeft een waarschuwing. Meldt de log een grote
   resterende afwijking tegen de stix (RMSE > 0,5 m), zet dan `stix_offset_x`.
7. **Commit** `config.csv`, het stix-bestand en de geometrie samen.

### Koppeltabel

```bash
uv run python scripts/bouw_koppeltabel_ld_dd.py              # alleen de tabel
uv run python scripts/bouw_koppeltabel_ld_dd.py --vul-config # en config.csv bijwerken
```

`input_data/koppeltabel_LD_DD.csv` zet onze peilbuizen (`LD_`) naast de
meetreeksen op Databricks (`DD_`, uit
`object_data_databricks_dwarsprofielen.csv`), met in `MATCH_` hoe zeker elke
koppeling is. `--vul-config` overschrijft nooit een ingevulde `meetlocatie_id`.

De invoer is eenmalig omgezet uit xlsx-werkboeken per project, en daarna uit
csv's per project naar deze centrale indeling; de werkboeken en de
migratiescripts staan in de git-geschiedenis.

## Databricks

De notebooks in `notebooks/` halen de stijghoogtes en de coördinaten per
dwarsprofiel uit het dataplatform, in plaats van uit een met de hand bijgewerkte
Excel-export. Zie [`notebooks/README.md`](notebooks/README.md). De boxplots
blijven voorlopig lokaal.

`input_data/` staat daarom **wel** onder versiebeheer, inclusief `.stix`: de cluster heeft geen internet en geen S-schijf, dus de Git folder moet
de invoer leveren. De ruwe meetexports in `input_data/*/peilbuisdata/` blijven
erbuiten — die zijn samen ~95 MB en worden juist door de notebooks vervangen.

## Bekende aandachtspunten

- **Zone-token `KR`** kent `analyse.py` niet, dus levert het daar `None` op. Het
  staat in 12 peilbuizen in Beemster, Purmer, Schellingwoude en Schermer. In
  Schellingwoude staat het op positie 1 van profiel 1 t/m 4, waar normaal `BUKR`
  staat, waardoor `heeft_kruinzone_peilbuis` daar `False` geeft en de freatische
  lijn zonder kruinanker wordt opgebouwd. `analyse.py` is bewust niet
  aangepast, omdat dat elke plot verandert. Dat is een aparte wijziging, met
  figuren voor en na. De controle in `workbook_lader.valideer` meldt deze
  peilbuizen als waarschuwing.
- **13 peilbuizen in 13_6** hebben codes zonder zone-token (bv.
  `PB483-1 _F-207`); hun zone is onbekend. Er is geen zonekolom meer: de zone
  komt alleen uit `code`, dus het token moet in de code staan.
- **`analyse.py` gebruikt twee mechanismen voor dezelfde vraag**: de zone uit de
  code, en de rij-volgorde (`y_peilbuis_mv[2]` = berm, `[3]` = teen). Die kunnen
  elkaar tegenspreken.
- **Bladnamen in de meetexport worden op deelstring gezocht**, eerste treffer
  wint. Met namen als `HB001PB01` en `HB010PB01` in bladnamen die Excel op 31
  tekens afkapt, kan dat het verkeerde blad pakken. Dit verdwijnt zodra de
  stijghoogtes via `meetlocatie_id` uit Databricks komen.

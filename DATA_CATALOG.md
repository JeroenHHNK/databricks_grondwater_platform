# Datacatalogus: wat we uit Databricks gebruiken

Welke catalog, tabellen en kolommen deze repository uit Databricks leest, met
welke query, en hoe we onze eigen peilbuizen eraan koppelen. Alles is
**alleen-lezen**: we schrijven nooit naar `gold_prd`. De volledige namen staan
in `modules/paths.py` (`TBL_*`). Achtergrond over het platform, in het Engels:
[`docs/DATABRICKS_HANDOFF.md`](docs/DATABRICKS_HANDOFF.md) (de meetschema's) en
[`docs/DATABRICKS_layer_cs_objecten.md`](docs/DATABRICKS_layer_cs_objecten.md)
(`meetlocatie`).

## Overzicht

| catalog.schema.tabel | wat | gebruikt door |
|---|---|---|
| `gold_prd.hl_grondwaterpeilmeting.meting` | de metingen: één rij per meting | app (live), beide db_notebooks |
| `gold_prd.hl_grondwaterpeilmeting.meetreeks` | de meetreeksen: één per sensor | app (live), beide db_notebooks |
| `gold_prd.hl_cs_objecten.meetlocatie` | de meetlocaties (putfilters) met ligging | app (live), beide db_notebooks |
| `gold_prd.hl_grondwaterpeilmeting.validatiestatus` | wat een `validatie_status` betekent | alleen `db_notebook_exporteer_stijghoogtes` |
| `gold_prd.hl_grondwaterpeilmeting.validatieresultaat` | open datakwaliteitsmeldingen | alleen `db_notebook_exporteer_stijghoogtes` |

Altijd drie-delige namen: `spark.catalog.currentCatalog()` geeft de naam van de
workspace terug, geen catalog waarop je kunt queryen.

```
meetlocatie ──< meetreeks ──< meting
   (id)        (id, meetlocatie_object_id / meetlocatie_code / code)
                              (meetreeks_id)
```

## Kolommen

### `meting`

| kolom | type | waarvoor |
|---|---|---|
| `meetreeks_id` | string | koppeling naar `meetreeks.id` |
| `tijdstempel_utc` | timestamp | tijdstip; de dag van een daggemiddelde is een UTC-dag |
| `waarde` | double | de stijghoogte in m NAP (bij `eenheid = 'm NAP'`) |
| `validatie_status` | int | alleen geteld in de export; de app filtert er (nog) niet op |

`meting.id` gebruiken we niet: die is niet uniek (het platform meldt dat zelf
in `validatieresultaat`). We ontdubbelen op `meetreeks_id + tijdstempel_utc`.
`tijdstempel` (lokale tijd) gebruiken we niet, omdat die in oktober een uur
dubbel heeft.

### `meetreeks`

| kolom | type | waarvoor |
|---|---|---|
| `id` | string (sha256) | de sleutel van de reeks; `DD_meetreeks_id` in `config.csv` |
| `code` | string | leesbare naam (`HB28-PB1 `, met spatie erachter: altijd `trim`) |
| `eenheid` | string | we kiezen alleen reeksen in `m NAP` |
| `begin_filter`, `eind_filter` | decimal(5,2) | filterdiepte; ter controle tegen onze inmeting |
| `bovenkant_peilbuis` | decimal(4,1) | idem |
| `sensor_meetlocatie_sleutel` | string | `507080_HB28_GMW_PB1`: put en filter, voor de naamkoppeling |
| `meetlocatie_object_id` | string | brug 1 naar `meetlocatie.id` |
| `meetlocatie_code` | string | brug 2 naar `meetlocatie.code`; bij Wiertsema staat ons projectnummer erin (`_86349_HB002_(...)`) |
| `bronsysteem` | string | leverancier (Fugro, Wiertsema, …), informatief |
| `object_id`, `eigenschap_id`, `validatie_status` | string/int | alleen meegeschreven in de export |

### `meetlocatie`

| kolom | type | waarvoor |
|---|---|---|
| `id` | string (sha256) | de sleutel van de put + filter; `DD_meetlocatie_id` in `config.csv` |
| `code` | string | brug 2 en 3; `DD_meetlocatie_code` in `config.csv` |
| `naam` | string | informatief, in de export |
| `rd_xcoordinaat`, `rd_ycoordinaat` | decimal(38,8) | ligging in RD (EPSG:28992); `DD_x_rd`, `DD_y_rd`, en de afstandskoppeling |
| `compartiment_code`, `meetreeks_actief` | string | informatief, in de export; `meetlocatie` bevat ook oppervlaktewater |

Persoonsnamen (`gebiedsbeheerder_naam`, `objectbeheerder`) lezen we niet.

## Hoe we koppelen

### Meetreeks → meetlocatie

Een meetreeks wijst op drie manieren naar zijn meetlocatie, en niet elke reeks
heeft ze alle drie gevuld. We nemen de eerste die iets vindt:

1. `meetreeks.meetlocatie_object_id = meetlocatie.id`
2. `meetreeks.meetlocatie_code = meetlocatie.code`
3. `trim(meetreeks.code) = meetlocatie.code`

De export schrijft in `reeksen.csv` welke het was (`brug`). Dezelfde volgorde
staat in beide db_notebooks en in `modules/databricks_live.py`.

### Onze peilbuis → meetlocatie

De sleutel is **`DD_meetlocatie_id`**, niet de meetreeks: een meetlocatie is één
filter van één put, en blijft hetzelfde als er een logger bij komt of vervangen
wordt. `notebooks/bouw_koppeltabel_ld_dd.py` zoekt hem, door elke peilbuis in
`config.csv` (LD) te vergelijken met de meetreeksen in `meetreeksen.csv` (DD):

| LD (`config.csv`) | DD (`meetreeksen.csv`) | waarom |
|---|---|---|
| `LD_x_rd`, `LD_y_rd` | `rd_x`, `rd_y` (uit `meetlocatie`) | putten in één profiel staan meters uit elkaar; filters in één put op dezelfde plek. Binnen 5 m telt als dezelfde put. |
| `LD_peilbuis` → put + filter (`HB2\|PB1`) | `sensor_sleutel` of `code` → put + filter | de naam bevestigt de plek, en vindt meetlocaties zonder coördinaten |
| filternummer (`PB1`, `PB2`) | filternummer | onderscheidt de filters in dezelfde put |
| `LD_projectnummer` (`VN-86349-1`) | het nummer in `meetlocatie_code` | maakt een naamkoppeling zonder coördinaten betrouwbaarder |
| `LD_bk_filter` | `begin_filter` | rangschikt kandidaten, geeft een waarschuwing boven 0,25 m |

Alleen reeksen binnen 25 m van een peilbuis, of met de naam van een peilbuis,
doen mee. De uitkomst per peilbuis staat in `DD_koppeling`: `gekoppeld` en
`meerdere sensoren` vult het script zelf in, `twijfelachtig` en
`meerdere kandidaten` beoordeel je zelf in `controle/koppeltabel_LD_DD.csv`.

### Meetlocatie → één meetreeks

Een meetlocatie kan meer reeksen hebben: parallelle sensoren op een ander
niveau, of een andere grootheid. Per peilbuis kiezen we er één
(`modules/databricks_reeksen.py`): alleen `m NAP` met metingen; een
`DD_meetreeks_id` in `config.csv` gaat voor; anders de reeks met de meeste
metingen.

## De queries

### 1. Alle meetreeksen (`db_notebook_exporteer_meetreeksen.py`)

Hangt niet af van `config.csv`. Schrijft `databricks_export/meetreeksen.csv`.
Twee lokale scripts lezen het: `bouw_koppeltabel_ld_dd.py` (de koppeling) en
`nieuw_dwarsprofiel.py`, dat er de `LD_`-kolommen van een nieuw profiel mee
voorinvult: `meetlocatie_code` → `LD_code` (zonder `_F-…`), `rd_x`/`rd_y` →
`LD_x_rd`/`LD_y_rd` en `LD_x_profiel`, `bovenkant_peilbuis`, `begin_filter`,
`eind_filter` → `LD_bk_peilbuis`, `LD_bk_filter`, `LD_ok_filter`. Die laatste
drie zijn op 0,1 m afgerond; een inmeting gaat voor (`--inmeting`).
Let op: filters horen bij de sensor (`sensor_sleutel`), ligging en bovenkant
bij de meetlocatie. Zijn sensor en meetlocatie op het platform verwisseld (zoals
B12/B13 in Heerhugowaard), dan passen ze niet bij elkaar; de invoercontrole
meldt dat als een filter boven de bovenkant peilbuis.

```sql
SELECT r.id AS meetreeks_id, trim(r.code) AS code, r.bronsysteem,
       r.sensor_meetlocatie_sleutel AS sensor_sleutel,
       CAST(r.begin_filter AS DOUBLE) AS begin_filter,
       CAST(r.eind_filter AS DOUBLE) AS eind_filter,
       CAST(r.bovenkant_peilbuis AS DOUBLE) AS bovenkant_peilbuis,
       r.object_id, r.eenheid, r.eigenschap_id, r.meetlocatie_object_id, r.meetlocatie_code,
       r.validatie_status,
       l.id AS cs_meetlocatie_id, l.code AS cs_code, l.naam AS cs_naam,
       l.compartiment_code, l.meetreeks_actief,
       CAST(l.rd_xcoordinaat AS DOUBLE) AS rd_x, CAST(l.rd_ycoordinaat AS DOUBLE) AS rd_y
FROM gold_prd.hl_grondwaterpeilmeting.meetreeks r
LEFT JOIN gold_prd.hl_cs_objecten.meetlocatie l_id   ON l_id.id     = r.meetlocatie_object_id
LEFT JOIN gold_prd.hl_cs_objecten.meetlocatie l_code ON l_code.code = r.meetlocatie_code
LEFT JOIN gold_prd.hl_cs_objecten.meetlocatie l_naam ON l_naam.code = trim(r.code)
LEFT JOIN gold_prd.hl_cs_objecten.meetlocatie l      ON l.id = coalesce(l_id.id, l_code.id, l_naam.id)
```

Daarna per reeks het aantal unieke tijdstippen met een waarde (`n_rows`), uit
`meting`. Dat leest de hele tabel; met de widget `tel_metingen = nee` sla je het
over.

### 2. Live stijghoogtes (de app, `modules/databricks_live.py`)

Bij elke nieuwe sessie, voor de `DD_meetlocatie_id`'s in `config.csv`. Eerst de
reeksen van die meetlocaties (klein), dan de daggemiddelden (de enige query op
`meting`):

```sql
SET TIME ZONE 'UTC';

WITH reeks_locatie AS (
    SELECT r.id AS meetreeks_id, trim(r.code) AS meetreeks_code, r.eenheid,
           CAST(r.begin_filter AS DOUBLE) AS begin_filter,
           CAST(r.eind_filter AS DOUBLE) AS eind_filter,
           CAST(r.bovenkant_peilbuis AS DOUBLE) AS bovenkant_peilbuis,
           coalesce(l_id.id, l_code.id, l_naam.id) AS meetlocatie_id
    FROM gold_prd.hl_grondwaterpeilmeting.meetreeks r
    LEFT JOIN gold_prd.hl_cs_objecten.meetlocatie l_id   ON l_id.id     = r.meetlocatie_object_id
    LEFT JOIN gold_prd.hl_cs_objecten.meetlocatie l_code ON l_code.code = r.meetlocatie_code
    LEFT JOIN gold_prd.hl_cs_objecten.meetlocatie l_naam ON l_naam.code = trim(r.code)
),
gekozen AS (
    SELECT DISTINCT * FROM reeks_locatie WHERE meetlocatie_id IN (<onze meetlocatie_id's>)
),
per_tijdstip AS (
    SELECT m.meetreeks_id, m.tijdstempel_utc, avg(m.waarde) AS waarde
    FROM gold_prd.hl_grondwaterpeilmeting.meting m
    WHERE m.meetreeks_id IN (SELECT meetreeks_id FROM gekozen) AND m.waarde IS NOT NULL
    GROUP BY m.meetreeks_id, m.tijdstempel_utc
)
SELECT meetreeks_id, to_date(tijdstempel_utc) AS datum,
       count(*) AS n, round(avg(waarde), 4) AS gem
FROM per_tijdstip
GROUP BY meetreeks_id, to_date(tijdstempel_utc)
```

Het aantal metingen per reeks (voor de keuze hierboven) is de som van `n`. De
id's gaan alleen de query in als ze er als sha256 uitzien (64 hex-tekens).

### 3. Stijghoogtes als export (`db_notebook_exporteer_stijghoogtes.py`)

Dezelfde koppeling en dezelfde daggemiddelden als de live query, met Spark, plus
wat alleen voor controle nodig is. Schrijft `databricks_export/`:

| bestand | inhoud |
|---|---|
| `reeksen.csv` | per peilbuis per meetreeks: `reeks_nr`, code, eenheid, filters, `brug`, aantal metingen, eerste/laatste, gem/min/max, meetinterval |
| `stijghoogtes_dag.csv` | per `reeks_nr` per UTC-dag: aantal, gem, min, max (en per `validatie_status`) |
| `afwijkingen.csv` | metingen meer dan 0,5 m van de dagmediaan van hun reeks |
| `schema.csv`, `validatie_tellingen.csv`, `validatiestatus.csv`, `validatieresultaat.csv` | de kolommen en validatiegegevens van de tabellen |

Eén verschil met live: bij twee waarden op hetzelfde tijdstip neemt de export er
één, live het gemiddelde.

## Rechten voor de app

De app leest als zijn eigen **service principal**, niet als de bezoeker. Die
heeft nodig:

- *Can use* op het SQL warehouse: dat regelt het toevoegen van de resource
  `sql-warehouse` in de app;
- leesrechten in Unity Catalog, te geven door een beheerder. `<application-id>`
  is de *Application ID* van de service principal; die staat in de details van
  de app, en bij *Settings → Identity and access → Service principals*:

```sql
GRANT USE CATALOG ON CATALOG gold_prd TO `<application-id>`;
GRANT USE SCHEMA ON SCHEMA gold_prd.hl_grondwaterpeilmeting TO `<application-id>`;
GRANT USE SCHEMA ON SCHEMA gold_prd.hl_cs_objecten TO `<application-id>`;
GRANT SELECT ON TABLE gold_prd.hl_grondwaterpeilmeting.meting TO `<application-id>`;
GRANT SELECT ON TABLE gold_prd.hl_grondwaterpeilmeting.meetreeks TO `<application-id>`;
GRANT SELECT ON TABLE gold_prd.hl_cs_objecten.meetlocatie TO `<application-id>`;
```

De db_notebooks draaien als jijzelf en gebruiken jouw rechten.

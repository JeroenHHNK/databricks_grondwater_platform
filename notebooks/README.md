# Databricks-notebooks

Scaffolding om de stijghoogtes en de coördinaten per dwarsprofiel uit het
dataplatform te halen, in plaats van uit een Excel-export die met de hand wordt
bijgewerkt.

**Scope van deze MVP: alleen ophalen.** De boxplots en de Plotly-figuren blijven
lokaal (`main.py`). Deze notebooks leveren de invoer daarvoor.

## Draaivolgorde

| Notebook | Doet | Schrijft |
|---|---|---|
| `00_omgeving_check.py` | rechten, bronschema's, volume, `config.csv` leesbaar | niets |
| `10_peilbuizen_uit_werkboek.py` | onze peilbuizen uit `config.csv` naar Delta | `boxplots_peilbuizen`, `boxplots_profielen` |
| `20_match_peilbuizen.py` | **vinden we onze peilbuizen, en onder welke sleutel?** | `boxplots_match_kandidaten`, `boxplots_match_rapport`, `boxplots_peilbuis_meetreeks` |
| `30_haal_reeksen_en_coordinaten_op.py` | stijghoogtes + coördinaten + dekking | `boxplots_stijghoogtes`, `boxplots_stijghoogte_dekking`, `boxplots_peilbuis_locaties` |

Notebook 20 is de kern. 30 heeft minstens één gekoppelde `meetreeks_id` nodig.

## Opzetten

1. **Workspace → Create → Git folder** op deze repo (niet notebooks uploaden).
   Een Git folder betekent dat uitrollen `git pull` is en dat de draaiende code
   bij een commit hoort die je kunt benoemen. `input_data/` komt met die pull
   mee: de cluster heeft geen internet en geen S-schijf.
2. Draai `00_omgeving_check.py`. Alles `OK`, op een eventuele `AF` op het volume
   na — dat raakt alleen de bestandsexport.
3. Draai 10 → 20 → 30.

Er wordt niets gehardcodeerd aan workspace-paden: `zoek_repo_root()` zoekt
omhoog naar `pyproject.toml`, dus dezelfde code draait vanuit een Git folder,
een lokale checkout of de map van een andere gebruiker. `__file__` wordt bewust
niet gebruikt — Databricks voert workspace-`.py`-bestanden met `exec` uit en
laat `__file__` ongedefinieerd.

## Rechten

Lezen uit `gold_prd`, schrijven **alleen** in `waterkeringbeheer_prd.thanzen` en
op het `misc_files`-volume. Nooit naar `gold_prd` schrijven. Alle namen staan
drie-delig in `Modules/paths.py`, omdat `current_catalog()` de workspace-naam
teruggeeft en een twee-delige naam dus niets vindt. Zie
[`../docs/DATABRICKS_PERMISSIONS.md`](../docs/DATABRICKS_PERMISSIONS.md).

## Het matchprobleem

Wij kennen een peilbuis als `HB002PB01` met code
`B_BE0072+3_BUKR_GMW_PB1_F-385`. Het platform kent
`meetreeks.code = 'HB28-PB1 '` (met spatie erachter) en
`sensor_meetlocatie_sleutel = '507080_HB28_GMW_PB1'`. **Welke sleutel die twee
verbindt is niet bekend.** Notebook 20 draait daarom vijf strategieën naast
elkaar en rapporteert een trefkans per strategie:

1. `naam_canoniek` — `HB002PB01` → `HB2PB1` tegen `trim(code)` → `HB2PB1`
2. `put_filter` — putdeel en filternummer los
3. `sensor_sleutel` — tegen het middenstuk van `sensor_meetlocatie_sleutel`
4. `ruimtelijk` — dichtstbijzijnde `meetlocatie` in RD; **de scheidsrechter**,
   want die kijkt niet naar namen
5. `ruimtelijk_filter` — plus filterdiepte, het enige dat de geneste filters
   PB1/PB2/PB3 in dezelfde put uit elkaar houdt

De normalisatie zit in [`../Modules/platform_match.py`](../Modules/platform_match.py)
als pure functies, zodat die lokaal te testen zijn.

**Een lage trefkans is een geldige uitkomst**, geen mislukking: dan is een
handmatige koppeltabel het antwoord in plaats van een normalisatieregel.
Tegenstrijdigheden (op naam reeks A, op plek reeks B) wegen zwaarder dan de
trefkans zelf.

## De terugkoppeling naar config.csv

`meetlocatie_id` (`cs_meetlocatie_id`) in `input_data/config.csv` is de
koppeling op lange termijn: één filter van één put, ook als de logger is
vervangen en er een nieuwe meetreeks bij kwam. De cyclus:

1. Lokaal: `uv run python scripts/bouw_koppeltabel_ld_dd.py --vul-config` vult
   lege `meetlocatie_id`'s waar de koppeling eenduidig is (`gekoppeld`,
   `meerdere sensoren`). Op Databricks: de export van notebook 20.
2. Beoordeel `twijfelachtig` en `meerdere kandidaten` met de hand en vul daar
   zelf de `meetlocatie_id` in. Een ingevulde waarde wordt nooit overschreven.
3. Committen, pushen, Git folder pullen, notebook 10 opnieuw draaien.

Niet-gekoppelde peilbuizen blijven als lege `meetlocatie_id` in `config.csv`
zichtbaar, met de reden in `koppeling`.

**Let op:** `config.csv` heeft geen maaiveldhoogte. Notebook 10 levert
`hoogte_maaiveld_nap` daarom leeg aan, zodat de strategie in notebook 20 die
filterdiepte onder maaiveld vergelijkt niets vindt. De andere strategieën werken
gewoon.

## Bewust niet gedaan

- **Geen plots op Databricks.** `d-geolib` leest `.stix` met een oude
  pydantic-pin en wil een echt bestandspad; dat is een apart traject.
- **Het platform wordt niet de autoriteit** voor coördinaten of filterdieptes.
  Die komen er als vergelijkingskolommen naast te staan. Zou het platform
  leidend worden, dan is de koppeling via een hash net zo kwetsbaar als het
  rij-indexlijstje dat we net kwijt zijn.
- **`sha256`-afleiding van `meetreeks.id` wordt niet nagejaagd.** Zodra het id in
  het werkboek staat, hoeft het niet reproduceerbaar te zijn.
- **Geen join op `dijkvak` of `grenswaardes_meetpunten`.** `dijkvak.id_meetreeks`
  was NULL in de steekproef en `grenswaardes_meetpunten.fk_meetpunt` wijst naar
  een onbevestigde sleutel. Allebei interessant, allebei een andere MVP.

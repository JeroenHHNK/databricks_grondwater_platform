# notebooks

Alles wat je draait, behalve de app. De volgorde staat in de
[README](../README.md#een-nieuw-dwarsprofiel-toevoegen); de tabellen en queries
in [DATA_CATALOG.md](../DATA_CATALOG.md).

| bestand | waar | leest | schrijft |
|---|---|---|---|
| `db_notebook_exporteer_meetreeksen.py` | Databricks | `meetreeks`, `meetlocatie`, `meting` (niet `config.csv`) | `input_data/databricks_export/meetreeksen.csv` |
| `nieuw_dwarsprofiel.py` | lokaal | `meetreeksen.csv`, stix, geometrie, profiellijn | met `--schrijf`: rijen in `config.csv` en `profielen.csv`, `D-Stability/<sleutel>.stix`, `dwarsprofielen/<sleutel>.csv`. Zonder `--schrijf` alleen tonen en controleren; `--zoek` toont de putten langs een lijn |
| `bouw_koppeltabel_ld_dd.py` | lokaal | `config.csv`, `meetreeksen.csv` | `input_data/controle/koppeltabel_LD_DD.csv`; met `--vul-config` de `DD_`-kolommen in `config.csv` |
| `db_notebook_exporteer_stijghoogtes.py` | Databricks | `config.csv` (`DD_meetlocatie_id`), `meetreeks`, `meetlocatie`, `meting` | `input_data/databricks_export/` (reeksen, dagwaarden, afwijkingen, validatie) |
| `controleer_reeksen.py` | lokaal | `config.csv`, `databricks_export/` | `input_data/controle/te_controleren.csv` |
| `maak_plots.py` | lokaal | alles; stijghoogtes live of uit de export | invoercontrole per project, dan `Projecten/<project>/plots/*.html` (niet versiebeheerd) |

De `db_notebook_`-bestanden draaien in de Databricks Git folder van deze repo.
Ze schrijven in de Git folder; commit en push daarna vanuit Databricks. Ze
lezen alleen uit `gold_prd` en schrijven daar nooit naartoe. De tabelnamen
staan drie-delig in `modules/paths.py`, zie
[`../docs/DATABRICKS_PERMISSIONS.md`](../docs/DATABRICKS_PERMISSIONS.md).

De app heeft de stijghoogte-export niet meer nodig: hij haalt ze live op. De
export blijft de terugval, en de bron van `controleer_reeksen.py`.

`meetreeksen.csv` vervangt `object_data_databricks_dwarsprofielen.csv`, een
oudere export van onbekende herkomst. Zolang `meetreeksen.csv` er niet is,
gebruikt `bouw_koppeltabel_ld_dd.py` die oude export nog.

De repo-root wordt gezocht via `pyproject.toml`, niet via `__file__`: Databricks
voert workspace-`.py`-bestanden uit zonder `__file__`.

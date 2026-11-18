"""The ``Grondwater_Monitoring`` dashboard: datasets and pages.

``python monitoring/scripts/build_dashboard.py`` writes it to
``databricks_dashboard/Grondwater_Monitoring.lvdash.json``. Change the dashboard here, not
in the JSON: Databricks rewrites the JSON when someone edits it in the UI, and those edits
are lost the next time this is generated (copy them back here first).

Almost every dataset reads the ``monitoring_*`` tables of the daily job
(``monitoring/notebooks/build_monitoring_tables.py``); the series page also reads
``validated_meting`` and ``valid_pastas_simulations`` for one series at a time.
"""

from __future__ import annotations

from monitoring_lib import sql
from monitoring_lib.lvdash import (
    Dataset,
    Page,
    bar,
    counter,
    dashboard,
    heatmap,
    line,
    parameter_filter,
    string_parameter,
    symbol_map,
    table,
    text,
)

GROEN, GEEL, ROOD, GRIJS, BLAUW = "#2E9E44", "#F2C200", "#D62728", "#A0A0A0", "#1F77B4"
STATUS_KLEUREN = {"ok": GROEN, "stil > 24 uur": GEEL, "stil > 7 dagen": ROOD, "nooit data": GRIJS}
PASTAS_KLEUREN = {sql.KLEUR_GROEN: GROEN, sql.KLEUR_GEEL: GEEL, sql.KLEUR_ROOD: ROOD, sql.KLEUR_GRIJS: GRIJS}
UPTIME_DOEL_PCT = 95.0   # the contract target line; change here
TOP = 30                 # bars per "top" chart


def build(catalog: str = "waterkeringbeheer_prd", schema: str = "jvanruitenbeek") -> dict:
    datasets, pages = definition(catalog, schema)
    return dashboard(datasets, pages)


def definition(catalog: str = "waterkeringbeheer_prd", schema: str = "jvanruitenbeek"):
    t = {kind: sql.table(kind, catalog, schema) for kind in sql.TABLES}
    validated = f"{catalog}.{schema}.validated_meting"
    simulations = f"{catalog}.{schema}.valid_pastas_simulations"
    status, uptime, flags = t["series_status"], t["uptime_daily"], t["flags_series"]
    change, pseries, pmodels = t["change_recent"], t["pastas_series"], t["pastas_models"]
    sensors = "NOT is_handwaarneming"
    meetreeks = [string_parameter("meetreeks")]

    ds = [
        # --- page 1: inventory and data flow ---------------------------------------
        Dataset("inv_totalen", "Inventaris: totalen", f"""
SELECT count_if({sensors})                                            AS sensoren,
       count_if(is_handwaarneming)                                    AS handwaarneming_reeksen,
       sum(metingen_ruw)                                              AS metingen,
       count_if({sensors} AND status <> 'ok')                         AS sensoren_stil,
       count_if({sensors} AND NOT metadata_compleet)                  AS metadata_incompleet,
       round(100 * count_if(leverancier = 'Wiertsema' AND gematcht)
             / nullif(count_if(leverancier = 'Wiertsema'), 0), 1)     AS pct_gekoppeld_wiertsema,
       round(100 * count_if(leverancier = 'Fugro' AND gematcht)
             / nullif(count_if(leverancier = 'Fugro'), 0), 1)         AS pct_gekoppeld_fugro,
       max(as_of)                                                     AS bijgewerkt
FROM {status}""", ["sensoren", "handwaarneming_reeksen", "metingen", "sensoren_stil", "metadata_incompleet",
                   "pct_gekoppeld_wiertsema", "pct_gekoppeld_fugro", "bijgewerkt"]),
        Dataset("inv_koppeling", "Inventaris: koppeling cs_objecten", f"""
SELECT leverancier,
       CASE WHEN gematcht THEN 'gekoppeld aan cs_objecten' ELSE 'niet gekoppeld' END AS koppeling,
       count(*) AS reeksen
FROM {status} WHERE {sensors}
GROUP BY 1, 2""", ["leverancier", "koppeling", "reeksen"]),
        Dataset("inv_actief", "Inventaris: actief/inactief", f"""
SELECT leverancier, meetreeks_actief, count(*) AS reeksen
FROM {status} WHERE {sensors}
GROUP BY 1, 2""", ["leverancier", "meetreeks_actief", "reeksen"]),
        Dataset("inv_metadata", "Inventaris: ontbrekende metadata", f"""
SELECT leverancier, veld, count(*) AS ontbreekt
FROM {status}
LATERAL VIEW stack(5, 'begin_filter', heeft_begin_filter, 'eind_filter', heeft_eind_filter,
                      'bovenkant_peilbuis', heeft_bovenkant_peilbuis,
                      'meetlocatie_code', heeft_meetlocatie_code, 'coordinaten', heeft_coordinaten) m AS veld, aanwezig
WHERE {sensors} AND NOT aanwezig
GROUP BY 1, 2""", ["leverancier", "veld", "ontbreekt"]),
        Dataset("inv_kaart", "Inventaris: kaart status", f"""
SELECT reeks_label, cs_code, cs_naam, leverancier, status, meetreeks_actief,
       CAST(laatste_meting AS string) AS laatste_meting, uptime_7d_pct, lat, lon
FROM {status} WHERE {sensors} AND lat IS NOT NULL""",
                ["reeks_label", "cs_code", "cs_naam", "leverancier", "status", "meetreeks_actief",
                 "laatste_meting", "uptime_7d_pct", "lat", "lon"]),
        Dataset("inv_stil", "Inventaris: sensoren zonder recente data", f"""
SELECT reeks_label, cs_code, leverancier, meetreeks_actief, status,
       laatste_meting, dagen_stil, uren_7d, uptime_7d_pct
FROM {status} WHERE {sensors} AND status <> 'ok'
ORDER BY meetreeks_actief = 'Ja' DESC, dagen_stil""",
                ["reeks_label", "cs_code", "leverancier", "meetreeks_actief", "status", "laatste_meting",
                 "dagen_stil", "uren_7d", "uptime_7d_pct"]),
        Dataset("uptime_lijn", "Uptime per leverancier per dag", f"""
WITH d AS (SELECT datum, leverancier, avg(uptime_pct) AS uptime_pct
           FROM {uptime}
           WHERE meetreeks_actief <> 'Nee' AND datum >= (SELECT max(datum) FROM {uptime}) - INTERVAL 90 DAYS
           GROUP BY 1, 2)
SELECT datum, leverancier, round(uptime_pct, 1) AS uptime_pct FROM d
UNION ALL
SELECT DISTINCT datum, 'contractdoel', {UPTIME_DOEL_PCT} FROM d""", ["datum", "leverancier", "uptime_pct"]),
        Dataset("uptime_sensor", "Uptime per sensor", f"""
WITH m AS (SELECT max(datum) AS d FROM {uptime})
SELECT u.reeks_label, u.leverancier, u.meetreeks_actief,
       round(avg(CASE WHEN u.datum > m.d - INTERVAL 7 DAYS  THEN u.uptime_pct END), 1) AS uptime_7d,
       round(avg(CASE WHEN u.datum > m.d - INTERVAL 30 DAYS THEN u.uptime_pct END), 1) AS uptime_30d,
       round(avg(CASE WHEN u.datum > m.d - INTERVAL 90 DAYS THEN u.uptime_pct END), 1) AS uptime_90d,
       round(avg(CASE WHEN u.datum > m.d - INTERVAL 90 DAYS THEN u.uptime_pct END), 1) >= {UPTIME_DOEL_PCT}
                                                                                       AS haalt_doel_90d
FROM {uptime} u CROSS JOIN m
GROUP BY 1, 2, 3
ORDER BY uptime_30d""", ["reeks_label", "leverancier", "meetreeks_actief", "uptime_7d", "uptime_30d",
                         "uptime_90d", "haalt_doel_90d"]),
        Dataset("uptime_14d", "Aanlevering laatste 14 dagen", f"""
SELECT concat(leverancier, ' | ', reeks_label) AS sensor, date_format(datum, 'MM-dd') AS dag, uptime_pct
FROM {uptime}
WHERE meetreeks_actief <> 'Nee' AND datum > (SELECT max(datum) FROM {uptime}) - INTERVAL 14 DAYS""",
                ["sensor", "dag", "uptime_pct"]),

        # --- page 2: measurements and data quality -----------------------------------
        Dataset("kw_totalen", "Kwaliteit: totalen", f"""
SELECT count_if(s.gevalideerd)                                              AS reeksen_gevalideerd,
       count(*)                                                              AS reeksen,
       round(100 * sum(s.uren_gevalideerd) / nullif(sum(s.uren_totaal), 0), 1) AS pct_uren_gevalideerd,
       sum(f.verdacht_totaal)                                                AS verdachte_uren,
       sum(f.verdacht_ongevalideerd)                                         AS verdacht_ongevalideerd,
       sum(f.verdacht_gisteren)                                              AS verdacht_gisteren,
       (SELECT count_if(verdachte_schommeling) FROM {change})                AS verdachte_schommelingen,
       (SELECT max(neerslag_7d_mm) FROM {change})                            AS neerslag_7d_mm
FROM {status} s LEFT JOIN {flags} f ON f.meetreeks_id = s.meetreeks_id
WHERE NOT s.is_handwaarneming AND s.uren_totaal > 0""",
                ["reeksen_gevalideerd", "reeksen", "pct_uren_gevalideerd", "verdachte_uren",
                 "verdacht_ongevalideerd", "verdacht_gisteren", "verdachte_schommelingen", "neerslag_7d_mm"]),
        Dataset("kw_gevalideerd", "Kwaliteit: aandeel gevalideerd per reeks", f"""
SELECT reeks_label, leverancier, gevalideerd_pct
FROM {status} WHERE {sensors} AND gevalideerd""", ["reeks_label", "leverancier", "gevalideerd_pct"]),
        Dataset("kw_flags", f"Kwaliteit: flags per reeks (top {TOP})", f"""
WITH top_reeksen AS (SELECT f.*, s.reeks_label FROM {flags} f JOIN {status} s ON s.meetreeks_id = f.meetreeks_id
             ORDER BY f.aandeel_verdacht DESC NULLS LAST LIMIT {TOP})
SELECT reeks_label, flag, uren
FROM top_reeksen
LATERAL VIEW stack(6, 'v1 buiten filter/peilbuis', v1_totaal, 'v2 sprong', v2_totaal,
                      'v3 bij filterbodem', v3_totaal, 'v4 uitschieter (IQR)', v4_totaal,
                      'regel A daling', rule_a_totaal, 'regel B onder filter', rule_b_totaal) x AS flag, uren
WHERE uren > 0""", ["reeks_label", "flag", "uren"]),
        Dataset("kw_ongevalideerd", f"Kwaliteit: verdachte ongevalideerde data (top {TOP})", f"""
SELECT s.reeks_label, s.leverancier, f.verdacht_ongevalideerd,
       round(100 * f.verdacht_ongevalideerd / nullif(f.uren_totaal - f.uren_gevalideerd, 0), 1)
           AS pct_van_ongevalideerd
FROM {flags} f JOIN {status} s ON s.meetreeks_id = f.meetreeks_id
WHERE f.verdacht_ongevalideerd > 0
ORDER BY f.verdacht_ongevalideerd DESC
LIMIT {TOP}""", ["reeks_label", "leverancier", "verdacht_ongevalideerd", "pct_van_ongevalideerd"]),
        Dataset("kw_gisteren", "Kwaliteit: gisteren", f"""
SELECT s.reeks_label, s.leverancier, s.status, c.uren_gisteren, f.verdacht_gisteren,
       f.v2_gisteren AS sprongen_gisteren, c.gisteren_gem, c.basis_gem, c.delta_m
FROM {status} s
LEFT JOIN {change} c ON c.meetreeks_id = s.meetreeks_id
LEFT JOIN {flags} f ON f.meetreeks_id = s.meetreeks_id
WHERE NOT s.is_handwaarneming AND s.meetreeks_actief <> 'Nee'
ORDER BY f.verdacht_gisteren DESC NULLS LAST, abs(c.delta_m) DESC NULLS LAST""",
                ["reeks_label", "leverancier", "status", "uren_gisteren", "verdacht_gisteren", "sprongen_gisteren",
                 "gisteren_gem", "basis_gem", "delta_m"]),
        Dataset("kw_schommeling", f"Kwaliteit: grootste schommelingen afgelopen week (top {TOP})", f"""
SELECT reeks_label, leverancier, week_bereik_m, neerslag_7d_mm, knmi_tot,
       CASE WHEN verdachte_schommeling IS NULL THEN 'geen actuele KNMI-data'
            WHEN verdachte_schommeling THEN 'verdacht: weinig regen' ELSE 'mogelijk door regen' END AS oordeel
FROM {change} WHERE week_bereik_m IS NOT NULL
ORDER BY week_bereik_m DESC
LIMIT {TOP}""", ["reeks_label", "leverancier", "week_bereik_m", "neerslag_7d_mm", "knmi_tot", "oordeel"]),
        Dataset("kw_natdroog", "Kwaliteit: natter/droger dan normaal", f"""
SELECT reeks_label, leverancier, natheid_index, nat_droog
FROM {change} WHERE natheid_index IS NOT NULL""", ["reeks_label", "leverancier", "natheid_index", "nat_droog"]),

        # --- page 3: one series ------------------------------------------------------
        Dataset("r_keuze", "Reeks: keuzelijst", f"""
SELECT meetreeks_keuze FROM {status} WHERE uren_totaal > 0 ORDER BY meetreeks_keuze""", ["meetreeks_keuze"]),
        Dataset("r_info", "Reeks: kenmerken", f"""
SELECT s.reeks_label, s.cs_naam, s.leverancier, s.status, CAST(s.laatste_meting AS string) AS laatste_meting,
       CASE WHEN s.is_handwaarneming THEN 'handwaarneming' ELSE 'sensor' END AS soort,
       CASE WHEN s.gevalideerd THEN 'ja' ELSE 'nee' END                      AS gevalideerd,
       coalesce(s.gevalideerd_pct, 0)                                        AS gevalideerd_pct,
       CASE WHEN s.heeft_valid_model THEN 'ja' ELSE 'nee' END                AS valid_pastas_model,
       p.valid_model, p.valid_r2, p.unvalid_r2, p.status_kleur,
       CASE WHEN s.metadata_compleet THEN 'ja' ELSE 'nee' END                AS metadata_compleet,
       s.begin_filter, s.eind_filter, s.bovenkant_peilbuis, s.uren_7d, s.uptime_7d_pct,
       coalesce(f.verdacht_totaal, 0) AS verdachte_uren
FROM {status} s
LEFT JOIN {pseries} p ON p.meetreeks_id = s.meetreeks_id
LEFT JOIN {flags} f ON f.meetreeks_id = s.meetreeks_id
WHERE s.meetreeks_keuze = :meetreeks""",
                ["reeks_label", "cs_naam", "leverancier", "status", "laatste_meting", "soort", "gevalideerd",
                 "gevalideerd_pct", "valid_pastas_model", "valid_model", "valid_r2", "unvalid_r2", "status_kleur",
                 "metadata_compleet", "begin_filter", "eind_filter", "bovenkant_peilbuis", "uren_7d",
                 "uptime_7d_pct", "verdachte_uren"], meetreeks),
        Dataset("r_dag", "Reeks: daggemiddelden", f"""
WITH sel AS (SELECT meetreeks_id, object_id, eind_filter, bovenkant_peilbuis
             FROM {status} WHERE meetreeks_keuze = :meetreeks),
sensor AS (SELECT to_date(h.ts) AS datum, avg(h.waarde) AS waarde
           FROM {t["hourly"]} h JOIN sel ON sel.meetreeks_id = h.meetreeks_id GROUP BY 1),
bereik AS (SELECT min(datum) AS van, max(datum) AS tot FROM sensor),
sim AS (SELECT x.* FROM {simulations} x JOIN sel ON sel.meetreeks_id = x.meetreeks_id
        JOIN {pseries} p ON p.meetreeks_id = x.meetreeks_id AND p.valid_model = x.model_type
        WHERE x.run_id = (SELECT max(run_id) FROM {simulations} y WHERE y.meetreeks_id = x.meetreeks_id))
SELECT datum, waarde, 'sensor (ruw)' AS bron FROM sensor
UNION ALL
SELECT to_date(v.tijdstempel_utc), avg(v.waarde), 'gevalideerd'
FROM {validated} v JOIN sel ON sel.meetreeks_id = v.meetreeks_id GROUP BY 1
UNION ALL
SELECT to_date(h.ts), avg(h.waarde), 'handwaarneming'
FROM {t["hourly"]} h
JOIN {status} hw ON hw.meetreeks_id = h.meetreeks_id AND hw.is_handwaarneming
JOIN sel ON sel.object_id = hw.object_id AND sel.meetreeks_id <> hw.meetreeks_id
GROUP BY 1
UNION ALL
SELECT to_date(datum), simulated, concat('pastas (', model_type, ')') FROM sim
UNION ALL
SELECT d, sel.eind_filter, 'onderkant filter' FROM sel CROSS JOIN bereik LATERAL VIEW explode(array(van, tot)) e AS d
WHERE sel.eind_filter IS NOT NULL
UNION ALL
SELECT d, sel.bovenkant_peilbuis, 'bovenkant peilbuis' FROM sel CROSS JOIN bereik
LATERAL VIEW explode(array(van, tot)) e AS d WHERE sel.bovenkant_peilbuis IS NOT NULL""",
                ["datum", "waarde", "bron"], meetreeks),
        Dataset("r_uur", "Reeks: laatste 30 dagen per uur", f"""
WITH sel AS (SELECT meetreeks_id FROM {status} WHERE meetreeks_keuze = :meetreeks),
m AS (SELECT max(h.ts) AS tot FROM {t["hourly"]} h JOIN sel ON sel.meetreeks_id = h.meetreeks_id)
SELECT h.ts AS tijd, h.waarde, 'sensor (ruw)' AS bron
FROM {t["hourly"]} h JOIN sel ON sel.meetreeks_id = h.meetreeks_id CROSS JOIN m
WHERE h.ts > m.tot - INTERVAL 30 DAYS
UNION ALL
SELECT v.tijdstempel_utc, v.waarde, 'gevalideerd'
FROM {validated} v JOIN sel ON sel.meetreeks_id = v.meetreeks_id CROSS JOIN m
WHERE v.tijdstempel_utc > m.tot - INTERVAL 30 DAYS""", ["tijd", "waarde", "bron"], meetreeks),
        Dataset("r_flags", "Reeks: flags", f"""
SELECT flag, totaal, laatste_7_dagen, gisteren
FROM {flags} f JOIN {status} s ON s.meetreeks_id = f.meetreeks_id
LATERAL VIEW stack(7,
    'v1 buiten filter/peilbuis', v1_totaal, v1_7d, v1_gisteren,
    'v2 sprong', v2_totaal, v2_7d, v2_gisteren,
    'v3 bij filterbodem', v3_totaal, v3_7d, v3_gisteren,
    'v4 uitschieter (IQR)', v4_totaal, v4_7d, v4_gisteren,
    'regel A daling', rule_a_totaal, rule_a_7d, rule_a_gisteren,
    'regel B onder filter', rule_b_totaal, rule_b_7d, rule_b_gisteren,
    'verdacht (v1-v4, B)', verdacht_totaal, verdacht_7d, verdacht_gisteren) x AS flag, totaal, laatste_7_dagen, gisteren
WHERE s.meetreeks_keuze = :meetreeks""", ["flag", "totaal", "laatste_7_dagen", "gisteren"], meetreeks),

        # --- page 4: Pastas -----------------------------------------------------------
        Dataset("p_totalen", "Pastas: totalen", f"""
SELECT count_if(valid_model IS NOT NULL)                AS valid_modellen,
       count_if(unvalid_model IS NOT NULL)              AS unvalid_modellen,
       count_if(status_kleur = '{sql.KLEUR_GROEN}')     AS groen,
       count_if(status_kleur = '{sql.KLEUR_GEEL}')      AS geel,
       count_if(status_kleur = '{sql.KLEUR_ROOD}')      AS rood,
       count_if(status_kleur = '{sql.KLEUR_GRIJS}')     AS grijs,
       count_if(lat IS NULL)                            AS zonder_coordinaten
FROM {pseries}""", ["valid_modellen", "unvalid_modellen", "groen", "geel", "rood", "grijs", "zonder_coordinaten"]),
        Dataset("p_kaart", "Pastas: kaart", f"""
SELECT reeks_label, cs_naam, lat, lon, status_kleur, unvalid_klasse,
       unvalid_model, unvalid_r2, valid_model, valid_r2, valid_r2_holdout, valid_t95_dagen, valid_rmse,
       keuze, modellen_ok,
       CASE WHEN pass_rsq THEN 'ja' WHEN NOT pass_rsq THEN 'nee' END    AS check_r2,
       CASE WHEN pass_memory THEN 'ja' WHEN NOT pass_memory THEN 'nee' END AS check_responstijd,
       CASE WHEN pass_bounds THEN 'ja' WHEN NOT pass_bounds THEN 'nee' END AS check_parametergrenzen
FROM {pseries} WHERE lat IS NOT NULL""",
                ["reeks_label", "cs_naam", "lat", "lon", "status_kleur", "unvalid_klasse", "unvalid_model",
                 "unvalid_r2", "valid_model", "valid_r2", "valid_r2_holdout", "valid_t95_dagen", "valid_rmse",
                 "keuze", "modellen_ok", "check_r2", "check_responstijd", "check_parametergrenzen"]),
        Dataset("p_kleur", "Pastas: reeksen per kleur", f"""
SELECT status_kleur, coalesce(valid_model, 'geen') AS valid_model, count(*) AS reeksen
FROM {pseries} GROUP BY 1, 2""", ["status_kleur", "valid_model", "reeksen"]),
        Dataset("p_modellen", "Pastas: alle gevalideerde modellen", f"""
SELECT s.reeks_label, m.model_type, m.in_gebruik, m.bron, m.success, round(m.r2, 3) AS r2,
       round(m.r2_holdout, 3) AS r2_holdout, round(m.rmse, 3) AS rmse, round(m.t95_days, 0) AS t95_dagen,
       m.pass_rsq, m.pass_memory, m.pass_bounds, m.pass_gain, m.haalt_checks, m.runaway, m.tier, m.reason
FROM {pmodels} m LEFT JOIN {status} s ON s.meetreeks_id = m.meetreeks_id
WHERE m.basis = 'valid_pastas'
ORDER BY s.reeks_label, m.in_gebruik DESC, m.model_type""",
                ["reeks_label", "model_type", "in_gebruik", "bron", "success", "r2", "r2_holdout", "rmse",
                 "t95_dagen", "pass_rsq", "pass_memory", "pass_bounds", "pass_gain", "haalt_checks", "runaway",
                 "tier", "reason"]),
    ]

    p1 = Page("inventaris", "Data-inventarisatie & datastroom", [
        text("t_inv", """
## Data-inventarisatie & datastroom
Alle meetreeksen uit `hl_grondwaterpeilmeting` met metingen; sensoren = alles behalve handwaarnemingen.
Bijgewerkt door de dagelijkse job `build_monitoring_tables` (tijden in UTC). Uptime = uren met een waarde / 24.""",
             (0, 0, 12, 2)),
        counter("c_sensoren", "inv_totalen", "sum(sensoren)", "Sensoren", (0, 2, 2, 3)),
        counter("c_metingen", "inv_totalen", "sum(metingen)", "Metingen in de bron", (2, 2, 2, 3)),
        counter("c_wiertsema", "inv_totalen", "max(pct_gekoppeld_wiertsema)", "% gekoppeld Wiertsema", (4, 2, 2, 3),
                "Wiertsema-reeksen die aan een meetlocatie in cs_objecten gekoppeld zijn"),
        counter("c_fugro", "inv_totalen", "max(pct_gekoppeld_fugro)", "% gekoppeld Fugro", (6, 2, 2, 3),
                "Fugro-reeksen die aan een meetlocatie in cs_objecten gekoppeld zijn"),
        counter("c_stil", "inv_totalen", "sum(sensoren_stil)", "Sensoren zonder data > 24 uur", (8, 2, 2, 3)),
        counter("c_meta", "inv_totalen", "sum(metadata_incompleet)", "Metadata incompleet", (10, 2, 2, 3)),
        bar("b_koppeling", "inv_koppeling", "leverancier", "reeksen", "Gekoppeld aan cs_objecten per leverancier",
            (0, 5, 6, 6), color="koppeling", color_mappings={"gekoppeld aan cs_objecten": GROEN,
                                                             "niet gekoppeld": ROOD}),
        bar("b_actief", "inv_actief", "leverancier", "reeksen", "Actief / inactief (meetreeks_actief)",
            (6, 5, 6, 6), color="meetreeks_actief"),
        bar("b_metadata", "inv_metadata", "veld", "ontbreekt", "Ontbrekende metadata per veld", (0, 11, 6, 7),
            color="leverancier", stacked=False),
        symbol_map("m_status", "inv_kaart", "Status per peilbuis", (6, 11, 6, 7), color="status",
                   extra=["reeks_label", "cs_naam", "leverancier", "laatste_meting", "uptime_7d_pct"],
                   color_mappings=STATUS_KLEUREN),
        table("tb_stil", "inv_stil", ["reeks_label", "cs_code", "leverancier", "meetreeks_actief", "status",
                                      "laatste_meting", "dagen_stil", "uren_7d", "uptime_7d_pct"],
              "Sensoren die de laatste 24 uur / 7 dagen geen data stuurden", (0, 18, 12, 8)),
        line("l_uptime", "uptime_lijn", "datum", "uptime_pct", "Uptime per leverancier (% uren ontvangen per dag)",
             (0, 26, 12, 7), color="leverancier",
             description=f"Gemiddeld over de sensoren die niet op inactief staan; contractdoel {UPTIME_DOEL_PCT:g}%",
             color_mappings={"contractdoel": GRIJS}),
        table("tb_uptime", "uptime_sensor", ["reeks_label", "leverancier", "meetreeks_actief", "uptime_7d",
                                             "uptime_30d", "uptime_90d", "haalt_doel_90d"],
              "Uptime per sensor (%), laagste eerst", (0, 33, 12, 8)),
        heatmap("h_14d", "uptime_14d", "dag", "sensor", "avg(uptime_pct)", "Aanlevering laatste 14 dagen (% uren)",
                (0, 41, 12, 22)),
    ])

    p2 = Page("kwaliteit", "Meetinventarisatie & datakwaliteit", [
        text("t_kw", """
## Meetinventarisatie & datakwaliteit
Flags zijn de checks van de validatie-app (`local_validation/checks.py`): v1 buiten filter/peilbuis, v2 sprong
(> +0,30 of < -0,05 m per uur), v3 binnen 0,15 m van de filterbodem, v4 uitschieter (1,5 x IQR), regel A daling
>= 0,10 m, regel B onder filterbodem + 0,10 m. *Verdacht* = v1-v4 of regel B (regel A is alleen ter info).""",
             (0, 0, 12, 3)),
        counter("c_gev", "kw_totalen", "sum(reeksen_gevalideerd)", "Reeksen met gevalideerde data", (0, 3, 2, 3)),
        counter("c_gevpct", "kw_totalen", "max(pct_uren_gevalideerd)", "% uren gevalideerd", (2, 3, 2, 3)),
        counter("c_verdacht", "kw_totalen", "sum(verdachte_uren)", "Verdachte uren", (4, 3, 2, 3)),
        counter("c_verdacht_ongev", "kw_totalen", "sum(verdacht_ongevalideerd)", "Verdacht en niet goedgekeurd",
                (6, 3, 2, 3)),
        counter("c_verdacht_gist", "kw_totalen", "sum(verdacht_gisteren)", "Verdachte uren gisteren", (8, 3, 2, 3)),
        counter("c_regen", "kw_totalen", "max(neerslag_7d_mm)", "Neerslag afgelopen 7 dagen (mm)", (10, 3, 2, 3),
                "KNMI, het station van de Pastas-forcering"),
        bar("b_gev", "kw_gevalideerd", "reeks_label", "gevalideerd_pct", "Aandeel gevalideerd per reeks (%)",
            (0, 6, 6, 15), color="leverancier"),
        bar("b_flags", "kw_flags", "reeks_label", "uren", f"Reeksen met de meeste flags (top {TOP})", (6, 6, 6, 15),
            color="flag"),
        bar("b_ongev", "kw_ongevalideerd", "reeks_label", "verdacht_ongevalideerd",
            f"Verdachte, nog niet gevalideerde uren per reeks (top {TOP})", (0, 21, 6, 10), color="leverancier"),
        bar("b_schommeling", "kw_schommeling", "reeks_label", "week_bereik_m",
            f"Grootste schommelingen afgelopen week (m, top {TOP})", (6, 21, 6, 10), color="oordeel",
            color_mappings={"verdacht: weinig regen": ROOD, "mogelijk door regen": BLAUW,
                            "geen actuele KNMI-data": GRIJS},
            description="Verdacht = bereik > 0,5 m terwijl er < 10 mm regen viel (instelbaar in de job)"),
        table("tb_gisteren", "kw_gisteren", ["reeks_label", "leverancier", "status", "uren_gisteren",
                                             "verdacht_gisteren", "sprongen_gisteren", "gisteren_gem", "basis_gem",
                                             "delta_m"],
              "Wat gebeurde er gisteren (delta = gisteren t.o.v. gemiddelde dag -14 t/m -2)", (0, 31, 12, 9)),
        bar("b_natdroog", "kw_natdroog", "reeks_label", "natheid_index",
            "Natter / droger dan normaal (+100 = maximum, 0 = mediaan, -100 = minimum van de reeks)",
            (0, 40, 12, 18), color="nat_droog",
            color_mappings={"natter dan normaal": BLAUW, "droger dan normaal": "#E07B00"}),
    ])

    p3 = Page("reeks", "Reeks-specifiek", [
        parameter_filter("f_reeks", "meetreeks", "Meetreeks", "r_keuze", "meetreeks_keuze",
                         ["r_info", "r_dag", "r_uur", "r_flags"], (0, 0, 6, 2)),
        text("t_reeks", "Kies een meetreeks (meetlocatie | code [id]). Handwaarnemingen op dezelfde peilbuis "
                        "worden mee getoond.", (6, 0, 6, 2)),
        table("tb_info", "r_info", ["reeks_label", "cs_naam", "soort", "leverancier", "status", "laatste_meting",
                                    "gevalideerd", "gevalideerd_pct", "valid_pastas_model", "valid_model",
                                    "valid_r2", "unvalid_r2", "status_kleur", "metadata_compleet", "begin_filter",
                                    "eind_filter", "bovenkant_peilbuis", "uptime_7d_pct", "verdachte_uren"],
              "Kenmerken", (0, 2, 12, 3)),
        line("l_dag", "r_dag", "datum", "waarde", "Stijghoogte (m NAP), daggemiddelden", (0, 5, 12, 9),
             color="bron", color_mappings={"sensor (ruw)": GRIJS, "gevalideerd": BLAUW, "handwaarneming": ROOD,
                                           "onderkant filter": "#7F7F7F", "bovenkant peilbuis": "#7F7F7F"}),
        line("l_uur", "r_uur", "tijd", "waarde", "Laatste 30 dagen per uur (m NAP)", (0, 14, 8, 7), color="bron",
             color_mappings={"sensor (ruw)": GRIJS, "gevalideerd": BLAUW}),
        table("tb_flags", "r_flags", ["flag", "totaal", "laatste_7_dagen", "gisteren"], "Flags (uren)",
              (8, 14, 4, 7)),
    ])

    p4 = Page("pastas", "Pastas", [
        text("t_pastas", f"""
## Pastas-modellen
Per reeks het model dat gebruikt wordt (handmatige keuze, anders de automatische keuze, anders Tarso).
Gevalideerde kaart: **groen** haalt R2 >= 0,7, de responstijd (t95) en de parametergrenzen; **geel** heeft een R2
maar faalt een van die checks; **rood** niet geaccepteerd (fit mislukt, loopt weg in de extremen of geen model
bruikbaar); **grijs** nog geen gevalideerde data of model. *modellen_ok* = hoeveel van de modeltypen de checks halen.""",
             (0, 0, 12, 3)),
        counter("c_valid", "p_totalen", "sum(valid_modellen)", "Reeksen met gevalideerd model", (0, 3, 2, 3)),
        counter("c_groen", "p_totalen", "sum(groen)", "Groen", (2, 3, 2, 3)),
        counter("c_geel", "p_totalen", "sum(geel)", "Geel", (4, 3, 2, 3)),
        counter("c_rood", "p_totalen", "sum(rood)", "Rood", (6, 3, 2, 3)),
        counter("c_grijs", "p_totalen", "sum(grijs)", "Grijs", (8, 3, 2, 3)),
        counter("c_zonder", "p_totalen", "sum(zonder_coordinaten)", "Niet op de kaart (geen coordinaten)",
                (10, 3, 2, 3)),
        symbol_map("m_unvalid", "p_kaart", "Pastas op ongevalideerde data (R2)", (0, 6, 6, 10), color="unvalid_r2",
                   extra=["reeks_label", "cs_naam", "unvalid_model", "unvalid_r2", "valid_r2"],
                   quantitative={"min": 0, "mid": 0.7, "max": 1}),
        symbol_map("m_valid", "p_kaart", "Pastas op gevalideerde data", (6, 6, 6, 10), color="status_kleur",
                   extra=["reeks_label", "cs_naam", "valid_model", "valid_r2", "unvalid_r2", "valid_r2_holdout",
                          "valid_t95_dagen", "valid_rmse", "check_r2", "check_responstijd",
                          "check_parametergrenzen", "modellen_ok", "keuze"],
                   color_mappings=PASTAS_KLEUREN),
        bar("b_kleur", "p_kleur", "status_kleur", "reeksen", "Reeksen per status en model in gebruik", (0, 16, 12, 6),
            color="valid_model"),
        table("tb_modellen", "p_modellen", ["reeks_label", "model_type", "in_gebruik", "bron", "success", "r2",
                                            "r2_holdout", "rmse", "t95_dagen", "pass_rsq", "pass_memory",
                                            "pass_bounds", "pass_gain", "haalt_checks", "runaway", "tier", "reason"],
              "Alle modeltypen per gevalideerde reeks", (0, 22, 12, 12)),
    ])
    return ds, [p1, p2, p3, p4]

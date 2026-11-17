"""SQL for the daily monitoring tables. Pure string builders, so they are unit-testable.

The notebook (``monitoring/notebooks/build_monitoring_tables.py``) runs them in this order:

1. ``hourly_sql`` -> table ``monitoring_hourly`` (and temp view ``mon_hourly`` on it): one
   row per series per UTC hour (mean of the raw values, plus how many raw rows there
   were). Every later step, and the dashboard's series page, reads this instead of the
   ~55 rows per day of ``meting``.
2. ``source_read.series_catalog_sql()`` -> temp view ``mon_catalog`` (supplier, the
   cs_objecten match, filter metadata), and ``validated_hours_sql`` -> ``mon_validated``.
3. ``series_status_sql`` -> ``monitoring_series_status``; the rest join on it.
4. ``uptime_daily_sql``, ``flag_input_sql`` (per batch of series into ``flags.flag_counts``),
   ``change_recent_sql``, ``pastas_*_sql``.

Times are UTC throughout; ``as_of`` is the run day (UTC) and "gisteren" is the day before.
"""

from __future__ import annotations

import datetime as dt
import re

GOLD = "gold_prd.hl_grondwaterpeilmeting"
METING = f"{GOLD}.meting"
OBJECT = f"{GOLD}.object"

HOURLY_VIEW = "mon_hourly"
CATALOG_VIEW = "mon_catalog"
VALIDATED_VIEW = "mon_validated"

TABLES = ("hourly", "series_status","uptime_daily", "flags_series", "change_recent", "knmi_daily",
          "pastas_series", "pastas_models")

# A sensor counts as silent when its last value is older than this.
SILENT_HOURS = 24

_NAME = re.compile(r"^[A-Za-z0-9_]+(\.[A-Za-z0-9_]+){0,2}$")


def table(kind: str, catalog: str, schema: str) -> str:
    if kind not in TABLES:
        raise ValueError(f"unknown monitoring table {kind!r}; use one of {TABLES}")
    return f"{catalog}.{schema}.monitoring_{kind}"


def _name(value: str) -> str:
    if not _NAME.fullmatch(value or ""):
        raise ValueError(f"not a plain table name: {value!r}")
    return value


def _date(value: dt.date) -> str:
    return f"DATE'{value.isoformat()}'"


def hourly_sql() -> str:
    return f"""
        SELECT meetreeks_id,
               date_trunc('HOUR', tijdstempel_utc) AS ts,
               avg(CAST(waarde AS double))         AS waarde,
               count(*)                            AS n_ruw
        FROM {METING}
        WHERE waarde IS NOT NULL AND tijdstempel_utc IS NOT NULL
        GROUP BY 1, 2
    """


def validated_hours_sql(validated_table: str | None) -> str:
    """The hours in ``validated_meting`` (approved), or an empty set when it does not exist."""
    if not validated_table:
        return "SELECT CAST(NULL AS string) AS meetreeks_id, CAST(NULL AS timestamp) AS ts WHERE 1 = 0"
    return f"""
        SELECT DISTINCT meetreeks_id, date_trunc('HOUR', tijdstempel_utc) AS ts
        FROM {_name(validated_table)}
    """


def series_status_sql(as_of: dt.date, valid_fit_metrics: str | None = None,
                      unvalid_fit_metrics: str | None = None) -> str:
    """One row per series that has measurements: supplier, match, metadata, data flow, status."""
    today = _date(as_of)

    def fitted(source: str | None, alias: str) -> str:
        if not source:
            return f"SELECT CAST(NULL AS string) AS meetreeks_id, false AS {alias} WHERE 1 = 0"
        return (f"SELECT meetreeks_id, bool_or(success) AS {alias} "
                f"FROM {_name(source)} GROUP BY meetreeks_id")

    return f"""
        WITH flow AS (
            SELECT meetreeks_id,
                   min(ts)                                            AS eerste_meting,
                   max(ts)                                            AS laatste_meting,
                   count(*)                                           AS uren_totaal,
                   sum(n_ruw)                                         AS metingen_ruw,
                   count_if(ts >= {today} - INTERVAL 7 DAYS AND ts < {today}) AS uren_7d
            FROM {HOURLY_VIEW} GROUP BY meetreeks_id
        ),
        val AS (SELECT meetreeks_id, count(*) AS uren_gevalideerd FROM {VALIDATED_VIEW} GROUP BY meetreeks_id),
        pv AS ({fitted(valid_fit_metrics, "heeft_valid_model")}),
        pu AS ({fitted(unvalid_fit_metrics, "heeft_unvalid_model")})
        SELECT
            c.meetreeks_id,
            c.code,
            coalesce(c.bronsysteem, 'onbekend')                          AS leverancier,
            o.code                                                       AS object_code,
            concat_ws('_', o.code, c.code)                               AS reeks_label,
            concat(coalesce(c.cs_code, concat_ws('_', o.code, c.code)), ' | ', c.code,
                   ' [', substr(c.meetreeks_id, 1, 8), ']')              AS meetreeks_keuze,
            upper(concat_ws('_', o.code, c.code)) LIKE '%HANDWAARNEMING%' AS is_handwaarneming,
            c.object_id,
            c.matched = 'matched'                                        AS gematcht,
            c.match_key,
            c.cs_code,
            c.cs_naam,
            coalesce(c.meetreeks_actief, 'onbekend')                     AS meetreeks_actief,
            c.lon,
            c.lat,
            c.begin_filter,
            c.eind_filter,
            c.bovenkant_peilbuis,
            c.begin_filter IS NOT NULL                                   AS heeft_begin_filter,
            c.eind_filter IS NOT NULL                                    AS heeft_eind_filter,
            c.bovenkant_peilbuis IS NOT NULL                             AS heeft_bovenkant_peilbuis,
            c.meetlocatie_code IS NOT NULL                               AS heeft_meetlocatie_code,
            c.lon IS NOT NULL AND c.lat IS NOT NULL                      AS heeft_coordinaten,
            (c.begin_filter IS NOT NULL AND c.eind_filter IS NOT NULL
             AND c.bovenkant_peilbuis IS NOT NULL AND c.meetlocatie_code IS NOT NULL
             AND c.lon IS NOT NULL AND c.lat IS NOT NULL)                AS metadata_compleet,
            f.eerste_meting,
            f.laatste_meting,
            coalesce(f.uren_totaal, 0)                                   AS uren_totaal,
            coalesce(f.metingen_ruw, 0)                                  AS metingen_ruw,
            coalesce(f.uren_7d, 0)                                       AS uren_7d,
            round(coalesce(f.uren_7d, 0) / 168.0 * 100, 1)               AS uptime_7d_pct,
            datediff({today}, to_date(f.laatste_meting))                 AS dagen_stil,
            CASE WHEN f.laatste_meting IS NULL THEN 'nooit data'
                 WHEN f.laatste_meting >= {today} - INTERVAL {SILENT_HOURS} HOURS THEN 'ok'
                 WHEN f.laatste_meting >= {today} - INTERVAL 7 DAYS THEN 'stil > 24 uur'
                 ELSE 'stil > 7 dagen' END                               AS status,
            coalesce(v.uren_gevalideerd, 0)                              AS uren_gevalideerd,
            coalesce(v.uren_gevalideerd, 0) > 0                          AS gevalideerd,
            round(coalesce(v.uren_gevalideerd, 0) / nullif(f.uren_totaal, 0) * 100, 1)
                                                                         AS gevalideerd_pct,
            coalesce(pv.heeft_valid_model, false)                        AS heeft_valid_model,
            coalesce(pu.heeft_unvalid_model, false)                      AS heeft_unvalid_model,
            {today}                                                      AS as_of
        FROM {CATALOG_VIEW} c
        LEFT JOIN {OBJECT} o ON o.id = c.object_id
        LEFT JOIN flow f ON f.meetreeks_id = c.meetreeks_id
        LEFT JOIN val v ON v.meetreeks_id = c.meetreeks_id
        LEFT JOIN pv ON pv.meetreeks_id = c.meetreeks_id
        LEFT JOIN pu ON pu.meetreeks_id = c.meetreeks_id
    """


def uptime_daily_sql(status_table: str, as_of: dt.date, days: int) -> str:
    """Hours received per sensor per UTC day (24 = complete), from its first day on.

    Hand readings are left out: they are not meant to arrive every hour.
    """
    today, start = _date(as_of), _date(as_of - dt.timedelta(days=days))
    return f"""
        WITH cal AS (SELECT explode(sequence({start}, {today} - INTERVAL 1 DAY, INTERVAL 1 DAY)) AS datum),
        s AS (SELECT meetreeks_id, reeks_label, leverancier, meetreeks_actief, to_date(eerste_meting) AS start
              FROM {_name(status_table)}
              WHERE NOT is_handwaarneming AND eerste_meting IS NOT NULL),
        h AS (SELECT meetreeks_id, to_date(ts) AS datum, count(*) AS uren
              FROM {HOURLY_VIEW} WHERE ts >= {start} AND ts < {today} GROUP BY 1, 2)
        SELECT s.meetreeks_id, s.reeks_label, s.leverancier, s.meetreeks_actief, c.datum,
               coalesce(h.uren, 0)                       AS uren_ontvangen,
               round(coalesce(h.uren, 0) / 24.0 * 100, 1) AS uptime_pct
        FROM s CROSS JOIN cal c
        LEFT JOIN h ON h.meetreeks_id = s.meetreeks_id AND h.datum = c.datum
        WHERE c.datum >= s.start
    """


def flag_input_sql(status_table: str, meetreeks_ids: list[str] | None = None) -> str:
    """The input of ``flags.flag_counts_frame``: every hour of the sensors, with metadata."""
    where = ""
    if meetreeks_ids:
        if not all(re.fullmatch(r"[0-9a-f]{16,64}", m) for m in meetreeks_ids):
            raise ValueError("meetreeks_ids must be plain hashes")
        where = "WHERE h.meetreeks_id IN (" + ", ".join(f"'{m}'" for m in meetreeks_ids) + ")"
    return f"""
        SELECT h.meetreeks_id, h.ts, h.waarde,
               v.meetreeks_id IS NOT NULL AS is_validated,
               s.begin_filter, s.eind_filter, s.bovenkant_peilbuis
        FROM {HOURLY_VIEW} h
        JOIN {_name(status_table)} s ON s.meetreeks_id = h.meetreeks_id AND NOT s.is_handwaarneming
        LEFT JOIN {VALIDATED_VIEW} v ON v.meetreeks_id = h.meetreeks_id AND v.ts = h.ts
        {where}
    """


def change_recent_sql(status_table: str, as_of: dt.date, knmi_table: str | None,
                      swing_m: float = 0.5, little_rain_mm: float = 10.0) -> str:
    """Yesterday vs the 14-day baseline, the range of the last week, and a wet/dry index.

    ``verdachte_schommeling``: the head moved more than ``swing_m`` within the last week
    while less than ``little_rain_mm`` fell; a swing that rain does not explain.
    The wet/dry index: +100 = at the series' maximum, 0 = its median, -100 = its minimum
    (as MOAD's ``API_Data_Wet_Dry``, with yesterday's mean as the current value).
    """
    today = _date(as_of)
    rain = (f"""SELECT sum(precipitation_mm) AS neerslag_7d_mm, max(datum) AS knmi_tot
               FROM {_name(knmi_table)} WHERE datum >= {today} - INTERVAL 7 DAYS AND datum < {today}"""
            if knmi_table else "SELECT CAST(NULL AS double) AS neerslag_7d_mm, CAST(NULL AS date) AS knmi_tot")
    return f"""
        WITH s AS (SELECT meetreeks_id, reeks_label, leverancier FROM {_name(status_table)}
                   WHERE NOT is_handwaarneming),
        h AS (SELECT h.* FROM {HOURLY_VIEW} h JOIN s ON s.meetreeks_id = h.meetreeks_id),
        lange_termijn AS (SELECT meetreeks_id, percentile_approx(waarde, 0.5) AS mediaan,
                                 min(waarde) AS minimum, max(waarde) AS maximum
                          FROM h GROUP BY meetreeks_id),
        basis AS (SELECT meetreeks_id, avg(waarde) AS basis_gem FROM h
                  WHERE ts >= {today} - INTERVAL 14 DAYS AND ts < {today} - INTERVAL 1 DAY GROUP BY meetreeks_id),
        gisteren AS (SELECT meetreeks_id, avg(waarde) AS gisteren_gem, count(*) AS uren_gisteren FROM h
                     WHERE ts >= {today} - INTERVAL 1 DAY AND ts < {today} GROUP BY meetreeks_id),
        week AS (SELECT meetreeks_id, min(waarde) AS week_min, max(waarde) AS week_max FROM h
                 WHERE ts >= {today} - INTERVAL 7 DAYS AND ts < {today} GROUP BY meetreeks_id),
        regen AS ({rain})
        SELECT s.meetreeks_id, s.reeks_label, s.leverancier,
               round(g.gisteren_gem, 3)                     AS gisteren_gem,
               coalesce(g.uren_gisteren, 0)                 AS uren_gisteren,
               round(b.basis_gem, 3)                        AS basis_gem,
               round(g.gisteren_gem - b.basis_gem, 3)       AS delta_m,
               round(w.week_max - w.week_min, 3)            AS week_bereik_m,
               round(r.neerslag_7d_mm, 1)                   AS neerslag_7d_mm,
               r.knmi_tot,
               (w.week_max - w.week_min) > {swing_m} AND coalesce(r.neerslag_7d_mm, 0) < {little_rain_mm}
                                                            AS verdachte_schommeling,
               round(greatest(-100, least(100, CASE
                   WHEN g.gisteren_gem >= l.mediaan
                       THEN 100.0 * (g.gisteren_gem - l.mediaan) / nullif(l.maximum - l.mediaan, 0)
                   ELSE -100.0 * (l.mediaan - g.gisteren_gem) / nullif(l.mediaan - l.minimum, 0) END)), 0)
                                                            AS natheid_index,
               CASE WHEN g.gisteren_gem IS NULL THEN 'geen data gisteren'
                    WHEN g.gisteren_gem >= l.mediaan THEN 'natter dan normaal'
                    ELSE 'droger dan normaal' END           AS nat_droog,
               {today}                                      AS as_of
        FROM s
        LEFT JOIN lange_termijn l ON l.meetreeks_id = s.meetreeks_id
        LEFT JOIN basis b ON b.meetreeks_id = s.meetreeks_id
        LEFT JOIN gisteren g ON g.meetreeks_id = s.meetreeks_id
        LEFT JOIN week w ON w.meetreeks_id = s.meetreeks_id
        CROSS JOIN regen r
    """


# --- Pastas -----------------------------------------------------------------------
# The check labels the pipeline writes are pastas' own (``selection.check_kind``):
# "rsq...", "t95..." (response memory; Tarso has two), "|...|" (gain), "Bounds..." (one
# row per parameter), "runs..." (kept out of the stamp).
_CHECK_KINDS = {"rsq": "rsq%", "memory": "t95%", "gain": "|%", "bounds": "Bounds%"}


def _latest(source: str, keys: str) -> str:
    on = " AND ".join(f"t.{k.strip()} = l.{k.strip()}" for k in (*keys.split(","), "run_id"))
    return (f"SELECT t.* FROM {_name(source)} t JOIN (SELECT {keys}, max(run_id) AS run_id "
            f"FROM {_name(source)} GROUP BY {keys}) l ON {on}")


def checks_pivot_sql(model_checks: str) -> str:
    """Per series x model type of the latest run: pass_rsq, pass_memory, pass_gain, pass_bounds."""
    cols = ",\n               ".join(
        f"bool_and(CASE WHEN `check` LIKE '{pattern}' THEN passed END) AS pass_{kind}"
        for kind, pattern in _CHECK_KINDS.items())
    return f"""SELECT meetreeks_id, model_type,
               {cols}
        FROM ({_latest(model_checks, "meetreeks_id, model_type")})
        GROUP BY meetreeks_id, model_type"""


def pastas_models_sql(prefix: str, tables: dict[str, bool], catalog: str, schema: str,
                      fit_columns: set[str] | None = None) -> str:
    """Every model type of every series of one basis (``valid_pastas``/``unvalid_pastas``).

    ``tables`` says which of ``fit_metrics``, ``model_checks``, ``model_selection`` and
    ``model_in_use`` exist. In use: the ``model_in_use`` view (a person's pick first), else
    the selection's pick, else Tarso. ``fit_columns``: the columns ``fit_metrics`` has (a
    table from an older run lacks e.g. ``t95_days`` and ``r2_holdout``; those become NULL).
    """
    def name(kind: str) -> str:
        return f"{catalog}.{schema}.{prefix}_{kind}"

    def col(column: str, kind: str = "double") -> str:
        if fit_columns is None or column in fit_columns:
            return f"f.{column}"
        return f"CAST(NULL AS {kind})"

    if not tables.get("fit_metrics"):
        raise ValueError(f"{name('fit_metrics')} does not exist")
    checks = (checks_pivot_sql(name("model_checks")) if tables.get("model_checks") else
              "SELECT CAST(NULL AS string) AS meetreeks_id, CAST(NULL AS string) AS model_type, "
              + ", ".join(f"CAST(NULL AS boolean) AS pass_{k}" for k in _CHECK_KINDS) + " WHERE 1 = 0")
    if tables.get("model_selection"):
        sel = (f"SELECT meetreeks_id, model_type, runaway, eligible, tier, reason, selected "
               f"FROM ({_latest(name('model_selection'), 'meetreeks_id')})")
    else:
        sel = ("SELECT CAST(NULL AS string) AS meetreeks_id, CAST(NULL AS string) AS model_type, "
               "CAST(NULL AS boolean) AS runaway, CAST(NULL AS boolean) AS eligible, "
               "CAST(NULL AS string) AS tier, CAST(NULL AS string) AS reason, "
               "CAST(NULL AS boolean) AS selected WHERE 1 = 0")
    in_use = (f"SELECT meetreeks_id, model_type, bron FROM {name('model_in_use')}"
              if tables.get("model_in_use") else
              "SELECT CAST(NULL AS string) AS meetreeks_id, CAST(NULL AS string) AS model_type, "
              "CAST(NULL AS string) AS bron WHERE 1 = 0")
    return f"""
        WITH fits AS ({_latest(name("fit_metrics"), "meetreeks_id, model_type")}),
        ck AS ({checks}),
        sel AS ({sel}),
        iu AS ({in_use}),
        pick AS (
            SELECT f.meetreeks_id,
                   coalesce(max(iu.model_type),
                            max(CASE WHEN sel.selected THEN sel.model_type END),
                            'tarso')                                              AS model_in_gebruik,
                   coalesce(max(iu.bron),
                            CASE WHEN max(CASE WHEN sel.selected THEN 1 END) = 1 THEN 'automatisch' END,
                            'standaard (tarso)')                                  AS bron
            FROM fits f
            LEFT JOIN iu ON iu.meetreeks_id = f.meetreeks_id
            LEFT JOIN sel ON sel.meetreeks_id = f.meetreeks_id AND sel.model_type = f.model_type
            GROUP BY f.meetreeks_id
        )
        SELECT '{prefix}'                       AS basis,
               f.run_id, f.meetreeks_id, f.series_name, f.model_type,
               f.success, {col("error_message", "string")} AS error_message,
               f.r2, {col("r2_holdout")} AS r2_holdout, f.evp, f.rmse, f.aic,
               coalesce({col("t95_days")}, greatest({col("t95_level0_days")}, {col("t95_level1_days")}))
                                                                                 AS t95_days,
               ck.pass_rsq, ck.pass_memory, ck.pass_gain, ck.pass_bounds,
               coalesce(ck.pass_rsq AND ck.pass_memory AND ck.pass_bounds, false) AS haalt_checks,
               sel.runaway, sel.eligible, sel.tier, sel.reason,
               f.model_type = p.model_in_gebruik AS in_gebruik,
               p.bron
        FROM fits f
        LEFT JOIN ck  ON ck.meetreeks_id = f.meetreeks_id AND ck.model_type = f.model_type
        LEFT JOIN sel ON sel.meetreeks_id = f.meetreeks_id AND sel.model_type = f.model_type
        LEFT JOIN pick p ON p.meetreeks_id = f.meetreeks_id
    """


# Map colours of the validated Pastas map (``status_kleur``), in legend order.
KLEUR_GROEN, KLEUR_GEEL, KLEUR_ROOD, KLEUR_GRIJS = (
    "groen: haalt de checks", "geel: R2, maar een check faalt",
    "rood: niet geaccepteerd", "grijs: nog geen gevalideerd model")


def pastas_series_sql(models_table: str, status_table: str) -> str:
    """One row per series that has any Pastas fit: the model in use of each basis and the colour.

    Colour (validated map): gray = no validated fit; red = the validated model in use failed,
    ran away in the extremes or is not eligible; yellow = it has an R2 but fails R2 >= 0.7,
    the response memory (t95) or the parameter bounds; green = it passes all three.
    """
    models = _name(models_table)
    return f"""
        WITH v AS (SELECT * FROM {models} WHERE basis = 'valid_pastas' AND in_gebruik),
        u AS (SELECT * FROM {models} WHERE basis = 'unvalid_pastas' AND in_gebruik),
        n AS (SELECT meetreeks_id, count_if(haalt_checks) AS n_modellen_ok, count(*) AS n_modellen
              FROM {models} WHERE basis = 'valid_pastas' GROUP BY meetreeks_id),
        ids AS (SELECT meetreeks_id FROM v UNION SELECT meetreeks_id FROM u)
        SELECT i.meetreeks_id,
               s.reeks_label, s.cs_code, s.cs_naam, s.leverancier, s.lon, s.lat, s.gevalideerd,
               u.model_type AS unvalid_model, round(u.r2, 3) AS unvalid_r2,
               v.model_type AS valid_model,   round(v.r2, 3) AS valid_r2,
               round(v.r2_holdout, 3) AS valid_r2_holdout,
               round(v.rmse, 3) AS valid_rmse, round(v.t95_days, 0) AS valid_t95_dagen,
               v.pass_rsq, v.pass_memory, v.pass_bounds, v.pass_gain, v.bron AS keuze,
               concat(coalesce(n.n_modellen_ok, 0), ' van ', coalesce(n.n_modellen, 0)) AS modellen_ok,
               CASE WHEN v.meetreeks_id IS NULL                                   THEN '{KLEUR_GRIJS}'
                    WHEN NOT coalesce(v.success, false) OR coalesce(v.runaway, false)
                         OR v.eligible = false OR v.r2 IS NULL                     THEN '{KLEUR_ROOD}'
                    WHEN v.haalt_checks                                            THEN '{KLEUR_GROEN}'
                    ELSE '{KLEUR_GEEL}' END                                        AS status_kleur,
               CASE WHEN u.r2 IS NULL THEN 'geen model'
                    WHEN u.r2 >= 0.7 THEN 'R2 >= 0.7' ELSE 'R2 < 0.7' END          AS unvalid_klasse
        FROM ids i
        LEFT JOIN v ON v.meetreeks_id = i.meetreeks_id
        LEFT JOIN u ON u.meetreeks_id = i.meetreeks_id
        LEFT JOIN n ON n.meetreeks_id = i.meetreeks_id
        LEFT JOIN {_name(status_table)} s ON s.meetreeks_id = i.meetreeks_id
    """

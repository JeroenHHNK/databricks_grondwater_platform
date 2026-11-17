"""Small builders for the AI/BI (Lakeview) dashboard JSON, in the format the workspace writes.

The shapes follow the dashboards this workspace exported (``databricks_dashboard/MOAD`` and
``Pastas_Dashboard``): a dataset is ``{name, displayName, queryLines}``; a widget holds
``queries`` (one ``main_query`` on a dataset, its ``fields`` as ``{name, expression}``) and a
``spec`` whose ``encodings`` refer to those field names. Positions use the 12-column grid.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

_AGG = re.compile(r"^(sum|avg|min|max|count)\((\w+|\*)\)$", re.IGNORECASE)


def _id(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:8]


@dataclass
class Dataset:
    name: str
    display: str
    sql: str
    columns: list[str]
    parameters: list[dict] = field(default_factory=list)

    def to_json(self) -> dict:
        lines = [line + "\n" for line in self.sql.strip("\n").split("\n")]
        out = {"name": self.name, "displayName": self.display, "queryLines": lines}
        if self.parameters:
            out["parameters"] = self.parameters
        return out


def string_parameter(keyword: str, default: str = "") -> dict:
    return {"displayName": keyword, "keyword": keyword, "dataType": "STRING",
            "defaultSelection": {"values": {"dataType": "STRING", "values": [{"value": default}]}}}


def _field(expr: str) -> dict:
    """``col`` -> the column; ``sum(col)``, ``avg(col)``, ``count(*)`` -> that aggregate."""
    match = _AGG.match(expr)
    if not match:
        return {"name": expr, "expression": f"`{expr}`"}
    fn, col = match.group(1).upper(), match.group(2)
    if col == "*":
        return {"name": "count(*)", "expression": "COUNT(`*`)"}
    return {"name": f"{fn.lower()}({col})", "expression": f"{fn}(`{col}`)"}


def columns_used(expr: str) -> list[str]:
    match = _AGG.match(expr)
    if not match:
        return [expr]
    return [] if match.group(2) == "*" else [match.group(2)]


def _frame(title: str | None, description: str | None = None) -> dict:
    frame: dict = {}
    if title:
        frame.update(showTitle=True, title={"value": title, "fields": []})
    if description:
        frame.update(showDescription=True, description={"value": description, "fields": []})
    return frame


@dataclass
class Widget:
    name: str
    dataset: str | None
    fields: list[str]
    spec: dict
    position: tuple[int, int, int, int]  # x, y, width, height
    extra_queries: list[dict] = field(default_factory=list)

    def to_json(self) -> dict:
        x, y, w, h = self.position
        widget: dict = {"name": self.name}
        if self.dataset:
            aggregated = any(_AGG.match(f) for f in self.fields)
            widget["queries"] = [{"name": "main_query", "query": {
                "datasetName": self.dataset, "fields": [_field(f) for f in self.fields],
                "disaggregated": not aggregated}}]
        if self.extra_queries:
            widget["queries"] = self.extra_queries
        if "multilineTextboxSpec" in self.spec:
            widget.update(self.spec)
        else:
            widget["spec"] = self.spec
        return {"widget": widget, "position": {"x": x, "y": y, "width": w, "height": h}}


def _scale(kind: str, **more) -> dict:
    return {"type": kind, **more}


def _enc(expr: str, kind: str, title: str | None = None, **scale) -> dict:
    enc = {"fieldName": _field(expr)["name"], "scale": _scale(kind, **scale)}
    if title:
        enc["displayName"] = title
    return enc


def text(name: str, markdown: str, position) -> Widget:
    lines = [line + "\n" for line in markdown.strip("\n").split("\n")]
    return Widget(name, None, [], {"multilineTextboxSpec": {"lines": lines}}, position)


def counter(name: str, dataset: str, value: str, title: str, position, description: str | None = None) -> Widget:
    spec = {"version": 2, "widgetType": "counter", "frame": _frame(title, description),
            "encodings": {"value": {"fieldName": _field(value)["name"], "displayName": title}},
            "data": {"queryName": "main_query"}}
    return Widget(name, dataset, [value], spec, position)


def bar(name: str, dataset: str, category: str, value: str, title: str, position, color: str | None = None,
        horizontal: bool = True, stacked: bool = True, description: str | None = None,
        color_mappings: dict[str, str] | None = None) -> Widget:
    cat = _enc(category, "categorical", sort={"by": "x-reversed" if horizontal else "y-reversed"})
    val = _enc(value, "quantitative")
    enc = {"x": val, "y": cat} if horizontal else {"x": cat, "y": val}
    fields = [category, value]
    if color:
        enc["color"] = _color(color, color_mappings)
        fields.append(color)
    spec = {"version": 3, "widgetType": "bar", "frame": _frame(title, description), "encodings": enc,
            "data": {"queryName": "main_query"}}
    if color and not stacked:
        spec["mark"] = {"layout": "group"}
    return Widget(name, dataset, fields, spec, position)


def _color(expr: str, mappings: dict[str, str] | None = None) -> dict:
    enc = {"fieldName": _field(expr)["name"], "scale": {"type": "categorical"}}
    if mappings:
        enc["scale"]["mappings"] = [{"value": v, "color": c} for v, c in mappings.items()]
    return enc


def line(name: str, dataset: str, x: str, y: str, title: str, position, color: str | None = None,
         description: str | None = None, color_mappings: dict[str, str] | None = None) -> Widget:
    enc = {"x": _enc(x, "temporal"), "y": _enc(y, "quantitative")}
    fields = [x, y]
    if color:
        enc["color"] = _color(color, color_mappings)
        fields.append(color)
    spec = {"version": 3, "widgetType": "line", "frame": _frame(title, description), "encodings": enc,
            "data": {"queryName": "main_query"}}
    return Widget(name, dataset, fields, spec, position)


def table(name: str, dataset: str, columns: list[str], title: str, position,
          description: str | None = None) -> Widget:
    spec = {"version": 2, "widgetType": "table", "frame": _frame(title, description),
            "encodings": {"columns": [{"fieldName": c, "displayName": c} for c in columns]},
            "data": {"queryName": "main_query"}}
    return Widget(name, dataset, list(columns), spec, position)


def heatmap(name: str, dataset: str, x: str, y: str, value: str, title: str, position,
            description: str | None = None) -> Widget:
    spec = {"version": 3, "widgetType": "heatmap", "frame": _frame(title, description),
            "encodings": {"x": _enc(x, "categorical"), "y": _enc(y, "categorical"),
                          "color": {"fieldName": _field(value)["name"], "scale": {
                              "type": "quantitative", "colorRamp": {"mode": "scheme", "scheme": "redyellowgreen"},
                              "domain": {"min": 0, "max": 100}}}},
            "data": {"queryName": "main_query"}}
    return Widget(name, dataset, [x, y, value], spec, position)


def symbol_map(name: str, dataset: str, title: str, position, color: str, extra: list[str],
               quantitative: dict | None = None, color_mappings: dict[str, str] | None = None,
               description: str | None = None, lat: str = "lat", lon: str = "lon") -> Widget:
    """A point map. ``quantitative``: ``{min, mid, max}`` for a red-yellow-green ramp;
    otherwise ``color`` is categorical (with fixed ``color_mappings`` if given)."""
    if quantitative:
        color_enc = {"fieldName": color, "legend": {"position": "right"}, "scale": {
            "type": "quantitative", "colorRamp": {"mode": "scheme", "scheme": "redyellowgreen"},
            "domain": quantitative}}
    else:
        color_enc = _color(color, color_mappings)
        color_enc["legend"] = {"position": "right"}
    spec = {"version": 2, "widgetType": "symbol-map", "frame": _frame(title, description),
            "mark": {"size": -2.1},
            "encodings": {"coordinates": {"coordinateType": "longitude-latitude",
                                          "latitude": {"fieldName": lat}, "longitude": {"fieldName": lon}},
                          "color": color_enc, "extra": [{"fieldName": e} for e in extra]},
            "data": {"queryName": "main_query"}}
    return Widget(name, dataset, [lat, lon, color, *extra], spec, position)


def parameter_filter(name: str, keyword: str, title: str, options_dataset: str, options_field: str,
                     datasets: list[str], position) -> Widget:
    """A single-select filter whose choice sets the parameter ``keyword`` on ``datasets``;
    the choices are the values of ``options_field`` in ``options_dataset``."""
    queries = [{"name": f"param_{ds}_{keyword}", "query": {
        "datasetName": ds, "parameters": [{"name": keyword, "keyword": keyword}], "disaggregated": False}}
        for ds in datasets]
    queries.append({"name": f"options_{options_dataset}_{options_field}", "query": {
        "datasetName": options_dataset,
        "fields": [{"name": options_field, "expression": f"`{options_field}`"},
                   {"name": f"{options_field}_associativity",
                    "expression": "COUNT_IF(`associative_filter_predicate_group`)"}],
        "disaggregated": False}})
    fields = [{"parameterName": keyword, "queryName": f"param_{ds}_{keyword}"} for ds in datasets]
    fields.append({"fieldName": options_field, "displayName": title,
                   "queryName": f"options_{options_dataset}_{options_field}"})
    spec = {"version": 2, "widgetType": "filter-single-select", "frame": _frame(title),
            "encodings": {"fields": fields}}
    return Widget(name, None, [options_field], spec, position, extra_queries=queries)


@dataclass
class Page:
    name: str
    display: str
    widgets: list[Widget]

    def to_json(self) -> dict:
        return {"name": self.name, "displayName": self.display, "pageType": "PAGE_TYPE_CANVAS",
                "layout": [w.to_json() for w in self.widgets]}


def dashboard(datasets: list[Dataset], pages: list[Page]) -> dict:
    return {"datasets": [d.to_json() for d in datasets], "pages": [p.to_json() for p in pages],
            "uiSettings": {"theme": {"widgetHeaderAlignment": "ALIGNMENT_UNSPECIFIED"}}}


def problems(datasets: list[Dataset], pages: list[Page]) -> list[str]:
    """Widgets that point at a dataset that is not there, or at a column it does not declare."""
    by_name = {d.name: d for d in datasets}
    found = []
    names = [w.name for p in pages for w in p.widgets]
    found += [f"widget name used twice: {n}" for n in sorted({n for n in names if names.count(n) > 1})]
    for page in pages:
        for w in page.widgets:
            targets = [w.dataset] if w.dataset else [q["query"]["datasetName"] for q in w.extra_queries]
            for target in targets:
                if target not in by_name:
                    found.append(f"{page.name}/{w.name}: no dataset {target}")
            if w.dataset in by_name:
                for f in w.fields:
                    for column in columns_used(f):
                        if column not in by_name[w.dataset].columns:
                            found.append(f"{page.name}/{w.name}: {w.dataset} has no column {column}")
            for q in w.extra_queries:
                for f in q["query"].get("fields", []):
                    column = f["expression"].strip("`")
                    ds = by_name.get(q["query"]["datasetName"])
                    if ds and not f["name"].endswith("_associativity") and column not in ds.columns:
                        found.append(f"{page.name}/{w.name}: {ds.name} has no column {column}")
                for p in q["query"].get("parameters", []):
                    ds = by_name.get(q["query"]["datasetName"])
                    if ds and p["keyword"] not in [x["keyword"] for x in ds.parameters]:
                        found.append(f"{page.name}/{w.name}: {ds.name} has no parameter {p['keyword']}")
    for d in datasets:
        for p in d.parameters:
            if f":{p['keyword']}" not in d.sql:
                found.append(f"{d.name}: parameter {p['keyword']} is never used in its SQL")
    return found

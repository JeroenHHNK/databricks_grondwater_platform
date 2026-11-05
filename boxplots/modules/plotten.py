import numpy as np
import pandas as pd
import plotly.graph_objects as go
from pandas import DataFrame, notna, concat, Series

from . import analyse

# Div-id's waaronder de twee figuren in de HTML-export terechtkomen; PROFIEL_PLOT_DIV_ID
# bevat de tijdslider, TIJDREEKS_PLOT_DIV_ID de tijdreeksen die daarmee gesynchroniseerd worden.
PROFIEL_PLOT_DIV_ID = "profiel_plot_div"
TIJDREEKS_PLOT_DIV_ID = "tijdreeks_plot_div"

# Standaardpalet. Stond eerder als een identiek `plot_colors`-blok in alle zeven
# config.yaml-bestanden; die waarden waren exact gelijk aan de fallbacks hier, en
# `deep_gw_line` stond in geen enkele config. Zeven kopieën van een constante die
# nooit per project is afgeweken, is geen configuratie. Een project dat toch wil
# afwijken kan `plot_colors` in `plot_config` meegeven.
PLOT_KLEUREN_STANDAARD: dict[str, str] = {
    "box_edge": "black",
    "box_face": "lightblue",
    "flier_edge": "#D55E00",
    "gw_line": "#0072B2",
    "last_point": "red",
    "last_point_edge": "black",
    "meanpoint": "#E69F00",
    "median": "#CC79A7",
    "deep_gw_line": "#8A2BE2",  # paars
}

# Eén kleur per peilbuis, dezelfde in de profielplot (buis en filter) en de
# tijdreeksplot (lijn), zodat een lijn bij zijn put te vinden is. Volgorde: het
# hoofdprofiel van links naar rechts, dan A, dan B. Hetzelfde gecontroleerde
# palet als de kaart; een profiel heeft er nu hooguit 6.
PEILBUIS_KLEUREN: list[str] = [
    "#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948",
]
BUITENWATERSTAND_KLEUR = "#5a5a55"


def peilbuis_kleur(i: int) -> str:
    return PEILBUIS_KLEUREN[i % len(PEILBUIS_KLEUREN)]

# Onderdelen van de profielplot die aan of uit kunnen. Voor de MVP staan de
# boxplots, de mediaan, de uitschieters en de terugkeertijden uit; de
# gemiddelden, de freatische lijnen en de dagelijkse meetpunten blijven. Per
# aanroep te overschrijven met `onderdelen` in `plot_config`.
ONDERDELEN_STANDAARD: dict[str, bool] = {
    "boxplot": False,
    "mediaan": False,
    "uitschieters": False,
    "terugkeertijden": False,
}

# Koppelt de tijdslider van de profielplot (boven) aan een verticale markeringslijn in de
# tijdreeksplot (onder): bij het verslepen van de slider wordt de bijbehorende datum uit het
# actieve slider-label gelezen en als 'shape' in de tijdreeksplot gezet. Nodig omdat de twee
# Plotly-figuren los van elkaar in de pagina staan en geen state delen.
SLIDER_SYNC_SCRIPT = f"""
<script>
(function() {{
    var profielDiv = document.getElementById("{PROFIEL_PLOT_DIV_ID}");
    var tijdreeksDiv = document.getElementById("{TIJDREEKS_PLOT_DIV_ID}");
    if (!profielDiv || !tijdreeksDiv) {{
        return;
    }}

    function parseSliderDatum(label) {{
        var delen = label.split("-");
        var dag = parseInt(delen[0], 10);
        var maand = parseInt(delen[1], 10) - 1;
        var jaar = 2000 + parseInt(delen[2], 10);
        return new Date(jaar, maand, dag);
    }}

    function zetTijdMarkering(datum) {{
        Plotly.relayout(tijdreeksDiv, {{
            shapes: [{{
                type: "line",
                xref: "x",
                yref: "paper",
                x0: datum,
                x1: datum,
                y0: 0,
                y1: 1,
                line: {{color: "rgba(80,80,80,0.5)", width: 1.5, dash: "dot"}}
            }}]
        }});
    }}

    profielDiv.on("plotly_sliderchange", function(event) {{
        zetTijdMarkering(parseSliderDatum(event.step.label));
    }});

    setTimeout(function() {{
        var slider = profielDiv.layout.sliders && profielDiv.layout.sliders[0];
        var actieveStap = slider && slider.steps[slider.active];
        if (actieveStap) {{
            zetTijdMarkering(parseSliderDatum(actieveStap.label));
        }}
    }}, 0);
}})();
</script>
"""


def maak_tijdreeks_plot(
    pb_gegevens: DataFrame,
    profiel_naam: str,
    metadata_pb: DataFrame,
    neerslag_data: DataFrame | None = None,
) -> go.Figure:
    """
    Genereert een Plotly-figuur voor de tijdreeksgegevens van peilbuizen.
    Args:
        pb_gegevens (DataFrame): DataFrame met een DatetimeIndex en kolommen voor elke peilbuis.
        profiel_naam (str): De naam van het profiel voor de titel.
        metadata_pb (DataFrame): DataFrame met metadata voor de peilbuizen.
        neerslag_data (DataFrame, optional): DataFrame met neerslaggegevens. Standaard is None.
    Returns:
        go.Figure: De gegenereerde Plotly-figuur.
    """
    figuur = go.Figure()

    # De metadata bevat de 'officiële' namen, maar de pb_gegevens kunnen extra
    # kolommen bevatten zoals de buitenwaterstand. We itereren over de data
    # en zoeken een passend label.
    code_labels = metadata_pb["Code_peilbuis"].tolist() if metadata_pb is not None else []

    for i, kolomnaam in enumerate(pb_gegevens.columns):
        serie = pb_gegevens[kolomnaam].dropna()
        # Gebruik het label uit de metadata als het bestaat, anders de kolomnaam zelf.
        label = code_labels[i] if i < len(code_labels) else kolomnaam
        kleur = peilbuis_kleur(i) if i < len(code_labels) else BUITENWATERSTAND_KLEUR
        figuur.add_trace(
            go.Scatter(
                x=serie.index, y=serie.values, mode="lines", name=label,
                line=dict(color=kleur, width=2),
            )
        )

    if neerslag_data is not None:
        figuur.add_trace(
            go.Bar(
                x=neerslag_data.index,
                y=neerslag_data["P(mm/d)"],
                name="Neerslag",
                yaxis="y2",
                # Neutraal, zodat het niet op een peilbuiskleur lijkt.
                marker_color="#a0a09a",
                opacity=0.6,
            )
        )

    figuur.update_layout(
        title=f"Tijdreeks Grondwaterstanden voor {profiel_naam}",
        xaxis_title="Datum",
        yaxis_title="Stijghoogte [m+NAP]",
        yaxis2=dict(
            title="Neerslag [mm/dag]",
            overlaying="y",
            side="right",
            autorange="reversed",
        ),
        legend=dict(x=1.05, xanchor="left"),
        legend_title="Legenda",
        width=1200,
        height=500,
        plot_bgcolor="#FAFAFA",
        font=dict(family="Arial, sans-serif", size=12, color="black"),
        hovermode="x unified",
    )

    return figuur


def maak_profiel_plot(plot_data: dict, plot_config: dict):
    """
    Genereert een Plotly-figuur voor een enkel profiel.

    Args:
        plot_data (dict): Een woordenboek met alle benodigde gegevens voor de plot.
        plot_config (dict): Een woordenboek met plotkleuren en andere instellingen.

    Returns:
        go.Figure: De gegenereerde Plotly-figuur.
    """
    figuur: go.Figure = go.Figure()

    # Pak gegevens uit de woordenboeken
    profiel_naam: str | None = plot_data["profiel"]["naam"]
    start_datum: str | None = plot_data["pb_gegevens"].index.min().strftime("%d-%m-%Y")
    eind_datum: str | None = plot_data["pb_gegevens"].index.max().strftime("%d-%m-%Y")
    geometrie_per_grondsoort: dict = plot_data["geometry_by_soil"]
    metadata_pb: DataFrame = plot_data["metadata_pb"]
    x_peilbuis: list[float] = plot_data["x_peilbuis"]
    stijghoogtes: list[np.ndarray] = plot_data["stijghoogtes"]
    gem_waterstanden: DataFrame = plot_data["gem_waterstanden"]
    freatische_lijn: None = plot_data["freatische_lijn"]
    plot_schematisatie: bool = plot_data["plot_schematisatie"]
    x_ts_min: float = plot_data.get("x_ts_min")
    x_ts_max: float = plot_data.get("x_ts_max")
    teensloot_peil: float = plot_data.get("teensloot_peil")
    heeft_kruin_peilbuis: bool = plot_data.get("heeft_kruin_peilbuis", True)
    buitenwaterstand: float = plot_data.get("buitenwaterstand")
    dijkprofiel: np.ndarray = plot_data["dijkprofiel"]
    pb_gegevens: DataFrame = plot_data["pb_gegevens"]
    terugkeertijden_data: DataFrame | None = plot_data.get("return_periods_data")
    project_map_naam: str = plot_data.get("project_folder_name")

    # Pak gegevens voor diepe peilbuizen uit (indien beschikbaar)
    profiel_A = plot_data.get("profiel_A")
    pb_gegevens_A = plot_data.get("pb_gegevens_A")
    metadata_pb_A = plot_data.get("metadata_pb_A")
    x_peilbuis_A = plot_data.get("x_peilbuis_A")
    stijghoogtes_A = plot_data.get("stijghoogtes_A")
    heeft_diepe_peilbuizen = profiel_A is not None

    # Pak gegevens voor nog diepere peilbuizen uit (indien beschikbaar)
    profiel_B = plot_data.get("profiel_B")
    pb_gegevens_B = plot_data.get("pb_gegevens_B")
    metadata_pb_B = plot_data.get("metadata_pb_B")
    x_peilbuis_B = plot_data.get("x_peilbuis_B")
    stijghoogtes_B = plot_data.get("stijghoogtes_B")
    heeft_nog_diepere_peilbuizen = profiel_B is not None

    # Plotkleuren: het standaardpalet, eventueel overschreven via plot_config.
    PLOT_KLEUREN: dict[str, str] = {
        **PLOT_KLEUREN_STANDAARD,
        **(plot_config.get("plot_colors") or {}),
    }
    BOX_RAND_KLEUR: str = PLOT_KLEUREN["box_edge"]
    BOX_VUL_KLEUR: str = PLOT_KLEUREN["box_face"]
    UITBIJTER_RAND_KLEUR: str = PLOT_KLEUREN["flier_edge"]
    GW_LIJN_KLEUR: str = PLOT_KLEUREN["gw_line"]
    LAATSTE_PUNT_KLEUR: str = PLOT_KLEUREN["last_point"]
    LAATSTE_PUNT_RAND: str = PLOT_KLEUREN["last_point_edge"]
    GEMIDDELDE_PUNT_KLEUR: str = PLOT_KLEUREN["meanpoint"]
    MEDIAAN_KLEUR: str = PLOT_KLEUREN["median"]
    DIEPE_GW_LIJN_KLEUR: str = PLOT_KLEUREN["deep_gw_line"]
    ONDERDELEN: dict[str, bool] = {
        **ONDERDELEN_STANDAARD,
        **(plot_config.get("onderdelen") or {}),
    }

    # Voeg bodemlagen toe (robuuste methode: één trace per grondsoort)
    for grondsoort, kleurgon in geometrie_per_grondsoort.items():
        polygonen = kleurgon["polygons"]
        kleur: str = kleurgon["color"]
        alle_x = []
        alle_y = []
        for poly in polygonen:
            x, y = poly.exterior.xy
            alle_x.extend(list(x))
            alle_y.extend(list(y))
            alle_x.append(None)  # Gebruik None om polygonen binnen één trace te scheiden
            alle_y.append(None)

        if alle_x:
            figuur.add_trace(
                go.Scatter(
                    x=alle_x,
                    y=alle_y,
                    fill="toself",
                    mode="lines",
                    line=dict(color=kleur, width=0.5),
                    fillcolor=kleur,
                    opacity=0.8,
                    name=grondsoort,
                    legendgroup=grondsoort,
                )
            )

    # Boxplot, gemiddelde, mediaan, uitschieters voor ondiepe peilbuizen
    labels = metadata_pb["Code_peilbuis"].tolist()
    gem_x, gem_y, med_x, med_y, uit_x, uit_y = [], [], [], [], [], []

    # Bereid data voor voor een enkele Box trace
    alle_box_y = []
    alle_box_x = []

    for idx, (pos, data) in enumerate(zip(x_peilbuis, stijghoogtes)):
        if len(data) == 0:
            continue

        # Voeg data samen voor de enkele Box trace
        alle_box_y.extend(data)
        alle_box_x.extend([pos] * len(data))

        # Bereken statistieken voor andere traces (gemiddelde, mediaan, uitschieters)
        q1, q3 = np.percentile(data, [25, 75])
        iqr = q3 - q1
        ondergrens, bovengrens = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        uitschieters = data[(data < ondergrens) | (data > bovengrens)]

        gem_x.append(pos)
        gem_y.append(float(np.mean(data)))
        med_x.append(pos)
        med_y.append(float(np.median(data)))
        uit_x.extend([pos] * len(uitschieters))
        uit_y.extend(uitschieters.tolist())

    # Voeg de enkele Box trace toe voor alle boxplots
    if alle_box_x and ONDERDELEN["boxplot"]:
        figuur.add_trace(
            go.Box(
                y=alle_box_y,
                x=alle_box_x,
                name="Boxplot (50% data)",
                boxpoints=False,
                marker=dict(color=BOX_VUL_KLEUR, line=dict(color=BOX_RAND_KLEUR)),
                line=dict(color=BOX_RAND_KLEUR),
                fillcolor=BOX_VUL_KLEUR,
                boxmean=False,
                width=3,
                legendgroup="boxplot",
            )
        )

    # Voeg statistische markers toe in de gewenste legenda-volgorde
    if uit_x and ONDERDELEN["uitschieters"]:
        figuur.add_trace(
            go.Scatter(
                x=uit_x,
                y=uit_y,
                mode="markers",
                marker=dict(
                    symbol="circle-open",
                    color=UITBIJTER_RAND_KLEUR,
                    size=6,
                    line=dict(width=2),
                ),
                name="Uitschieters",
                legendgroup="outliers",
                showlegend=True,
            )
        )
    figuur.add_trace(
        go.Scatter(
            x=gem_x,
            y=gem_y,
            mode="markers",
            marker=dict(
                symbol="diamond",
                color=GEMIDDELDE_PUNT_KLEUR,
                size=6,
                line=dict(color=LAATSTE_PUNT_RAND, width=1.5),
            ),
            name="Gemiddelde",
            legendgroup="gemiddelde",
        )
    )
    if ONDERDELEN["mediaan"]:
        figuur.add_trace(
            go.Scatter(
                x=med_x,
                y=med_y,
                mode="markers",
                marker=dict(
                    symbol="line-ew-open", color=MEDIAAN_KLEUR, size=18, line=dict(width=4)
                ),
                name="Mediaan",
                legendgroup="mediaan",
            )
        )

    # Filter dieptes
    filter_breedte: int = 1

    # Diepe peilbuizen
    if heeft_diepe_peilbuizen and x_peilbuis_A and metadata_pb_A is not None:
        for idx, locatie in enumerate(x_peilbuis_A):
            buis_kleur = peilbuis_kleur(len(x_peilbuis) + idx)
            # Eigenschappen diepe peilbuis
            diep_meta = metadata_pb_A.iloc[idx]
            diep_bovenkant_buis = diep_meta["Hoogte_bovenkant_peilbuis"]
            diep_filter_bovenkant = diep_meta["bovenkant_filter_M_NAP"]
            diep_filter_onderkant = diep_meta["onderkant_filter_M_NAP"]

            # Buis loopt van bovenkant buis tot bovenkant filter
            buis_start_y = diep_bovenkant_buis
            buis_eind_y = diep_filter_bovenkant

            # --- Teken buis met onderbreking voor ondiep filter ---
            dichtstbijzijnde_ondiep_meta = None
            if x_peilbuis:
                afstanden = [abs(locatie - loc_s) for loc_s in x_peilbuis]
                dichtstbijzijnde_ondiep_idx = np.argmin(afstanden)
                dichtstbijzijnde_ondiep_meta = metadata_pb.iloc[dichtstbijzijnde_ondiep_idx]

            if (
                dichtstbijzijnde_ondiep_meta is not None
                and buis_start_y > dichtstbijzijnde_ondiep_meta["onderkant_filter_M_NAP"]
                and buis_eind_y < dichtstbijzijnde_ondiep_meta["bovenkant_filter_M_NAP"]
            ):
                ondiep_filter_bovenkant = dichtstbijzijnde_ondiep_meta["bovenkant_filter_M_NAP"]
                ondiep_filter_onderkant = dichtstbijzijnde_ondiep_meta["onderkant_filter_M_NAP"]

                if buis_start_y > ondiep_filter_bovenkant:
                    figuur.add_trace(
                        go.Scatter(
                            x=[locatie, locatie],
                            y=[buis_start_y, ondiep_filter_bovenkant],
                            mode="lines",
                            line=dict(color=buis_kleur, width=2, dash="solid"),
                            showlegend=False,
                            hoverinfo="none",
                        )
                    )

                if ondiep_filter_onderkant > buis_eind_y:
                    figuur.add_trace(
                        go.Scatter(
                            x=[locatie, locatie],
                            y=[ondiep_filter_onderkant, buis_eind_y],
                            mode="lines",
                            line=dict(color=buis_kleur, width=2, dash="solid"),
                            showlegend=False,
                            hoverinfo="none",
                        )
                    )
            else:
                # Geen intersectie of geen ondiepe peilbuizen, teken de volledige buis.
                figuur.add_trace(
                    go.Scatter(
                        x=[locatie, locatie],
                        y=[buis_start_y, buis_eind_y],
                        mode="lines",
                        line=dict(color=buis_kleur, width=2, dash="solid"),
                        showlegend=False,
                        hoverinfo="none",
                    )
                )

            # --- Teken filterscherm ---
            figuur.add_shape(
                type="rect",
                x0=locatie - filter_breedte / 2,
                x1=locatie + filter_breedte / 2,
                y0=diep_filter_onderkant,
                y1=diep_filter_bovenkant,
                line=dict(color=buis_kleur, width=2, dash="solid"),
                fillcolor=buis_kleur,
                layer="above",
            )

    # Nog diepere peilbuizen
    if heeft_nog_diepere_peilbuizen and x_peilbuis_B and metadata_pb_B is not None:
        for idx, locatie in enumerate(x_peilbuis_B):
            buis_kleur = peilbuis_kleur(len(x_peilbuis) + len(x_peilbuis_A or []) + idx)
            diep_meta_B = metadata_pb_B.iloc[idx]
            diep_bovenkant_buis = diep_meta_B["Hoogte_bovenkant_peilbuis"]
            diep_filter_bovenkant = diep_meta_B["bovenkant_filter_M_NAP"]
            diep_filter_onderkant = diep_meta_B["onderkant_filter_M_NAP"]

            buis_start_y = diep_bovenkant_buis
            buis_eind_y = diep_filter_bovenkant

            if heeft_diepe_peilbuizen and x_peilbuis_A is not None and not metadata_pb_A.empty:
                afstanden_naar_A = [abs(locatie - x_a) for x_a in x_peilbuis_A]
                if afstanden_naar_A:
                    dichtstbijzijnde_A_idx = np.argmin(afstanden_naar_A)
                    if afstanden_naar_A[dichtstbijzijnde_A_idx] < 1.0:
                        dichtstbijzijnde_A_meta = metadata_pb_A.iloc[dichtstbijzijnde_A_idx]
                        buis_start_y = dichtstbijzijnde_A_meta["onderkant_filter_M_NAP"]

            dichtstbijzijnde_ondiep_meta = None
            if x_peilbuis:
                afstanden = [abs(locatie - loc_s) for loc_s in x_peilbuis]
                dichtstbijzijnde_ondiep_idx = np.argmin(afstanden)
                dichtstbijzijnde_ondiep_meta = metadata_pb.iloc[dichtstbijzijnde_ondiep_idx]

            if (
                dichtstbijzijnde_ondiep_meta is not None
                and buis_start_y > dichtstbijzijnde_ondiep_meta["onderkant_filter_M_NAP"]
                and buis_eind_y < dichtstbijzijnde_ondiep_meta["bovenkant_filter_M_NAP"]
            ):
                ondiep_filter_bovenkant = dichtstbijzijnde_ondiep_meta["bovenkant_filter_M_NAP"]
                ondiep_filter_onderkant = dichtstbijzijnde_ondiep_meta["onderkant_filter_M_NAP"]

                if buis_start_y > ondiep_filter_bovenkant:
                    figuur.add_trace(
                        go.Scatter(
                            x=[locatie, locatie],
                            y=[buis_start_y, ondiep_filter_bovenkant],
                            mode="lines",
                            line=dict(color=buis_kleur, width=2, dash="solid"),
                            showlegend=False,
                            hoverinfo="none",
                        )
                    )

                if ondiep_filter_onderkant > buis_eind_y:
                    figuur.add_trace(
                        go.Scatter(
                            x=[locatie, locatie],
                            y=[ondiep_filter_onderkant, buis_eind_y],
                            mode="lines",
                            line=dict(color=buis_kleur, width=2, dash="solid"),
                            showlegend=False,
                            hoverinfo="none",
                        )
                    )
            else:
                figuur.add_trace(
                    go.Scatter(
                        x=[locatie, locatie],
                        y=[buis_start_y, buis_eind_y],
                        mode="lines",
                        line=dict(color=buis_kleur, width=2, dash="solid"),
                        showlegend=False,
                        hoverinfo="none",
                    )
                )

            figuur.add_shape(
                type="rect",
                x0=locatie - filter_breedte / 2,
                x1=locatie + filter_breedte / 2,
                y0=diep_filter_onderkant,
                y1=diep_filter_bovenkant,
                line=dict(color=buis_kleur, width=2, dash="solid"),
                fillcolor=buis_kleur,
                layer="above",
            )

    # Ondiepe peilbuizen
    for idx, locatie in enumerate(x_peilbuis):
        filter_bovenkant = metadata_pb.iloc[idx]["bovenkant_filter_M_NAP"]
        filter_onderkant = metadata_pb.iloc[idx]["onderkant_filter_M_NAP"]
        hoogte_bovenkant = metadata_pb.iloc[idx]["Hoogte_bovenkant_peilbuis"]
        figuur.add_trace(
            go.Scatter(
                x=[locatie, locatie],
                y=[hoogte_bovenkant, filter_bovenkant],
                mode="lines",
                line=dict(color=peilbuis_kleur(idx), width=2),
                showlegend=False,
                name="Bovenkant filter",
            )
        )
        figuur.add_shape(
            type="rect",
            x0=locatie - filter_breedte / 2,
            x1=locatie + filter_breedte / 2,
            y0=filter_onderkant,
            y1=filter_bovenkant,
            line=dict(color=peilbuis_kleur(idx), width=2),
            fillcolor=peilbuis_kleur(idx),
            layer="above",
        )

    # Waterstandslijnen
    figuur.add_trace(
        go.Scatter(
            x=gem_waterstanden["x"],
            y=gem_waterstanden["y"],
            mode="lines",
            line=dict(color=GW_LIJN_KLEUR, width=2),
            name="Gemiddelde freatische lijn",
            legendgroup="gw_lijn",
        )
    )
    if plot_schematisatie and freatische_lijn is not None:
        figuur.add_trace(
            go.Scatter(
                x=freatische_lijn["x"],
                y=freatische_lijn["y"],
                mode="lines",
                line=dict(color="black", width=2, dash="dash"),
                name="Schematische freatische lijn",
                legendgroup="schematische_lijn",
            )
        )

    # Voeg terugkeertijdlijnen toe met specifieke kleuren
    if (
        ONDERDELEN["terugkeertijden"]
        and terugkeertijden_data is not None
        and not terugkeertijden_data.empty
    ):
        kleuren_map = {
            100: "darkorange",
            300: "red",
            1000: "darkred",
        }
        standaard_kleuren = ["#a6cee3", "#1f78b4", "#b2df8a", "#33a02c", "#fb9a99"]
        # Nat en droog krijgen dezelfde kleur per terugkeertijd, maar een ander
        # lijntype. Elke lijn (per periode én type) blijft individueel aan/uit
        # te zetten via de legenda; legendgroup + legendgrouptitle_text zorgen
        # er alleen voor dat nat en droog overzichtelijk gegroepeerd staan.
        dash_per_type = {"nat": "dot", "droog": "dash"}
        legendgroeptitel_per_type = {
            "nat": "Terugkeertijden (nat)",
            "droog": "Terugkeertijden (droog)",
        }

        peilbuis_codes = metadata_pb["Code_peilbuis"].tolist()
        if heeft_diepe_peilbuizen and metadata_pb_A is not None:
            peilbuis_codes.extend(metadata_pb_A["Code_peilbuis"].tolist())
        if heeft_nog_diepere_peilbuizen and metadata_pb_B is not None:
            peilbuis_codes.extend(metadata_pb_B["Code_peilbuis"].tolist())

        relevante_kolommen = [
            col for col in peilbuis_codes if col in terugkeertijden_data.columns
        ]

        if relevante_kolommen:
            x_coords_map = {
                code: x for code, x in zip(metadata_pb["Code_peilbuis"], x_peilbuis)
            }
            if heeft_diepe_peilbuizen and metadata_pb_A is not None and x_peilbuis_A:
                x_coords_map.update(
                    {
                        code: x
                        for code, x in zip(metadata_pb_A["Code_peilbuis"], x_peilbuis_A)
                    }
                )
            if heeft_nog_diepere_peilbuizen and metadata_pb_B is not None and x_peilbuis_B:
                x_coords_map.update(
                    {
                        code: x
                        for code, x in zip(metadata_pb_B["Code_peilbuis"], x_peilbuis_B)
                    }
                )

            kleur_idx = 0
            periode_kleuren: dict[int, str] = {}
            eerste_trace_per_type = {"nat": True, "droog": True}

            for (terugkeertijd, type_naam), rij in terugkeertijden_data.iterrows():
                punten = []
                for code in relevante_kolommen:
                    if code in rij and notna(rij[code]) and code in x_coords_map:
                        punten.append((x_coords_map[code], rij[code]))

                punten.sort()

                if not punten:
                    continue

                x_waarden, y_waarden = zip(*punten)

                if terugkeertijd not in periode_kleuren:
                    kleur = kleuren_map.get(terugkeertijd)
                    if kleur is None:
                        kleur = standaard_kleuren[kleur_idx % len(standaard_kleuren)]
                        kleur_idx += 1
                    periode_kleuren[terugkeertijd] = kleur
                kleur = periode_kleuren[terugkeertijd]

                trace_kwargs = dict(
                    x=x_waarden,
                    y=y_waarden,
                    mode="lines",
                    name=f"T = {terugkeertijd} jaar {type_naam}",
                    line=dict(color=kleur, dash=dash_per_type.get(type_naam, "dot")),
                    legendgroup=f"terugkeertijden_{type_naam}",
                )
                if eerste_trace_per_type.get(type_naam):
                    trace_kwargs["legendgrouptitle_text"] = legendgroeptitel_per_type.get(
                        type_naam, f"Terugkeertijden ({type_naam})"
                    )
                    eerste_trace_per_type[type_naam] = False

                figuur.add_trace(go.Scatter(**trace_kwargs))

    # Dijkprofiel
    figuur.add_trace(
        go.Scatter(
            x=dijkprofiel[:, 0],
            y=dijkprofiel[:, 1],
            mode="lines",
            line=dict(color="black", width=2.2),
            name="Dijkprofiel",
            legendgroup="dijkprofiel",
            showlegend=False,
        )
    )

    # --- Schuifregelaar voor dagelijkse metingen ---
    alle_pb_gegevens = pb_gegevens
    if heeft_diepe_peilbuizen and pb_gegevens_A is not None:
        alle_pb_gegevens = concat([alle_pb_gegevens, pb_gegevens_A], axis=1)
    if heeft_nog_diepere_peilbuizen and pb_gegevens_B is not None:
        alle_pb_gegevens = concat([alle_pb_gegevens, pb_gegevens_B], axis=1)
    datums = alle_pb_gegevens.index.normalize().unique().sort_values()

    # Buiten-ankerpunt (x-positie(s) + of daar een echt snijpunt met de buitenwaterstand zit)
    # hangt alleen af van de profielgeometrie en peilbuisposities, dus eenmalig te bepalen.
    # De schuifregelaar werkt via Plotly 'restyle' en herschrijft alleen de y-waarden van elke
    # trace, dus de x-array van de dagelijkse lijn moet voor elke dag hetzelfde blijven; het
    # raster wordt daarom ook eenmalig bepaald en per dag alleen opnieuw geïnterpoleerd/geclipt.
    # Alleen het 'kern'-deel (van het buitenwaterstand-snijpunt t/m de teensloot-aanzet) wordt
    # onder het profiel geclipt; het buitenwaterstand- en teensloot-vlak zelf blijven recht,
    # want dat is legitiem open water dat boven een lokale geul/greppel in het profiel kan liggen.
    x_buiten, heeft_snijpunt = analyse.bepaal_buiten_anker(
        dijkprofiel, buitenwaterstand, x_peilbuis, heeft_kruin_peilbuis
    )
    x_ankers_basis = x_buiten + list(x_peilbuis) + [x_ts_min, x_ts_max]
    i_kern_start, i_kern_eind = analyse.bepaal_kern_bereik(
        x_buiten, heeft_snijpunt, len(x_ankers_basis)
    )
    kern_x_basis = x_ankers_basis[i_kern_start : i_kern_eind + 1]
    kern_x_raster = analyse.bepaal_x_raster_onder_profiel(kern_x_basis, dijkprofiel)
    grondhoogte_kern_raster = [
        analyse.vind_y_in_dwarsprofiel(dijkprofiel, x) for x in kern_x_raster
    ]
    lijn_x_waarden = (
        x_ankers_basis[:i_kern_start] + kern_x_raster + x_ankers_basis[i_kern_eind + 1 :]
    )
    marker_x_waarden = x_peilbuis

    x_uitgebreid_diep = []
    gesorteerde_diepe_peilbuizen_indices = []
    if heeft_diepe_peilbuizen and x_peilbuis_A:
        gesorteerde_diepe_peilbuizen = sorted(zip(x_peilbuis_A, range(len(x_peilbuis_A))))
        x_peilbuis_A_gesorteerd = [x for x, i in gesorteerde_diepe_peilbuizen]
        gesorteerde_diepe_peilbuizen_indices = [i for x, i in gesorteerde_diepe_peilbuizen]

        start_x = min(lijn_x_waarden)
        eind_x = max(lijn_x_waarden)
        x_uitgebreid_diep = [start_x] + x_peilbuis_A_gesorteerd + [eind_x]

    x_uitgebreid_diep_B = []
    gesorteerde_diepe_peilbuizen_indices_B = []
    if heeft_nog_diepere_peilbuizen and x_peilbuis_B:
        gesorteerde_diepe_peilbuizen_B = sorted(zip(x_peilbuis_B, range(len(x_peilbuis_B))))
        x_peilbuis_B_gesorteerd = [x for x, i in gesorteerde_diepe_peilbuizen_B]
        gesorteerde_diepe_peilbuizen_indices_B = [i for x, i in gesorteerde_diepe_peilbuizen_B]

        start_x = min(lijn_x_waarden)
        eind_x = max(lijn_x_waarden)
        x_uitgebreid_diep_B = [start_x] + x_peilbuis_B_gesorteerd + [eind_x]

    slider_data = []
    for datum in datums:
        # Pak alle data voor de huidige dag
        dag_data_totaal = alle_pb_gegevens[
            alle_pb_gegevens.index.normalize() == datum
        ]
        dag_waarden_totaal = (
            dag_data_totaal.ffill().iloc[-1]
            if not dag_data_totaal.empty
            else Series(dtype="float64")
        )

        # Scheid de buitenwaterstand van de peilbuiswaarden voor deze dag
        buitenwaterstand_kolom = "Buitenwaterstand Markermeer"
        heeft_fluctuerende_bw_in_dag = buitenwaterstand_kolom in dag_waarden_totaal.index

        buitenwaterstand_dag = buitenwaterstand
        dag_waarden_peilbuizen = dag_waarden_totaal
        if heeft_fluctuerende_bw_in_dag:
            bw_dag = dag_waarden_totaal[buitenwaterstand_kolom]
            if notna(bw_dag):
                buitenwaterstand_dag = bw_dag
            # Verwijder de buitenwaterstand om alleen peilbuisdata over te houden
            dag_waarden_peilbuizen = dag_waarden_totaal.drop(buitenwaterstand_kolom)

        # Bouw de data voor de dagelijkse freatische lijn (ondiep)
        # Filter de buitenwaterstand-kolom uit de lijst voordat we selecteren.
        ondiepe_pb_kolommen = [
            col for col in pb_gegevens.columns if col != buitenwaterstand_kolom
        ]
        dag_waarden_ondiep = dag_waarden_peilbuizen[ondiepe_pb_kolommen]

        # Bouw dezelfde ankerpunten als de gemiddelde lijn, maar met de dagwaarden van de
        # peilbuizen (en, indien beschikbaar, de dagwaarde van de buitenwaterstand). Dit
        # regelt zowel het meebewegen met de eerste peilbuis bij een ontbrekend snijpunt met
        # de buitenwaterstand, als het overslaan van de kruinzone als er geen kruin-peilbuis
        # is. Vervolgens wordt de lijn op hetzelfde (vaste) raster als de gemiddelde lijn
        # geïnterpoleerd en onder het dijkprofiel geclipt.
        y_peilbuis_dag = list(dag_waarden_ondiep.values)
        _, y_ankers_dag = analyse.bouw_freatische_lijn_ankers(
            dijkprofiel,
            x_buiten,
            heeft_snijpunt,
            buitenwaterstand_dag,
            x_peilbuis,
            y_peilbuis_dag,
            x_ts_min,
            x_ts_max,
            teensloot_peil,
        )
        if y_ankers_dag:
            kern_y_dag = y_ankers_dag[i_kern_start : i_kern_eind + 1]
            kern_y_dag_geclipt = analyse.interpoleer_en_clip_op_raster(
                kern_x_raster, grondhoogte_kern_raster, kern_x_basis, kern_y_dag
            )
            ext_y = (
                y_ankers_dag[:i_kern_start]
                + kern_y_dag_geclipt
                + y_ankers_dag[i_kern_eind + 1 :]
            )
        else:
            ext_y = [None] * len(lijn_x_waarden)
        data_dict = {
            "marker_y": dag_waarden_ondiep.values,
            "line_y": ext_y,
        }

        if heeft_diepe_peilbuizen and pb_gegevens_A is not None:
            dag_data_A = pb_gegevens_A[pb_gegevens_A.index.normalize() == datum]
            dag_waarden_A = (
                dag_data_A.ffill().iloc[-1]
                if not dag_data_A.empty
                else pd.Series(dtype="float64")
            )
            if (
                dag_waarden_A.notna().any()
                and len(gesorteerde_diepe_peilbuizen_indices) > 0
            ):
                dag_waarden_A_gesorteerd = dag_waarden_A.iloc[gesorteerde_diepe_peilbuizen_indices]
                y_uitgebreid_stap = (
                    [dag_waarden_A_gesorteerd.iloc[0]]
                    + dag_waarden_A_gesorteerd.tolist()
                    + [dag_waarden_A_gesorteerd.iloc[-1]]
                )
                data_dict["line_y_A_extended"] = y_uitgebreid_stap
            else:
                data_dict["line_y_A_extended"] = [None] * len(x_uitgebreid_diep)

        if heeft_nog_diepere_peilbuizen and pb_gegevens_B is not None:
            dag_data_B = pb_gegevens_B[pb_gegevens_B.index.normalize() == datum]
            dag_waarden_B = (
                dag_data_B.ffill().iloc[-1]
                if not dag_data_B.empty
                else pd.Series(dtype="float64")
            )
            if (
                dag_waarden_B.notna().any()
                and len(gesorteerde_diepe_peilbuizen_indices_B) > 0
            ):
                dag_waarden_B_gesorteerd = dag_waarden_B.iloc[gesorteerde_diepe_peilbuizen_indices_B]
                y_uitgebreid_stap_B = (
                    [dag_waarden_B_gesorteerd.iloc[0]]
                    + dag_waarden_B_gesorteerd.tolist()
                    + [dag_waarden_B_gesorteerd.iloc[-1]]
                )
                data_dict["line_y_B_extended"] = y_uitgebreid_stap_B
            else:
                data_dict["line_y_B_extended"] = [None] * len(x_uitgebreid_diep_B)

        slider_data.append(data_dict)

    initiële_data = (
        slider_data[-1]
        if slider_data
        else {
            "marker_y": [],
            "line_y": [],
            "line_y_A_extended": [],
            "line_y_B_extended": [],
        }
    )

    # --- Voeg traces toe voor de dagelijkse metingen die door de slider worden bijgewerkt ---
    figuur.add_trace(go.Scatter(
            x=marker_x_waarden,
            y=initiële_data.get("marker_y"),
            mode="markers",
            name="Meetpunt (dagelijks)",
            marker=dict(
                color=LAATSTE_PUNT_KLEUR,
                line=dict(color=LAATSTE_PUNT_RAND, width=1.2),
                size=6,
                symbol="circle",
            ),
            legendgroup="daily_measurements",
        )
    )
    figuur.add_trace(go.Scatter(
            x=lijn_x_waarden,
            y=initiële_data.get("line_y"),
            mode="lines",
            name="Meetlijn (dagelijks)",
            line=dict(color=LAATSTE_PUNT_KLEUR, width=2, dash="dot"),
            legendgroup="daily_lines",
        )
    )
    if heeft_diepe_peilbuizen and x_peilbuis_A:
        figuur.add_trace(go.Scatter(
                x=x_uitgebreid_diep,
                y=initiële_data.get("line_y_A_extended", []),
                mode="lines",
                name="Stijghoogte zandlaag",
                line=dict(color=DIEPE_GW_LIJN_KLEUR, width=2, dash="dash"),
                legendgroup="deep_gw_line",
            )
        )
    if heeft_nog_diepere_peilbuizen and x_peilbuis_B:
        figuur.add_trace(go.Scatter(
                x=x_uitgebreid_diep_B,
                y=initiële_data.get("line_y_B_extended", []),
                mode="lines",
                name="Stijghoogte pleistocene zandlaag",
                line=dict(color="#008B8B", width=2, dash="dash"),  # DarkCyan
                legendgroup="even_deeper_gw_line",
            )
        )

    # --- Bepaal de indices van de traces die door de slider worden bijgewerkt ---
    basis_offset = 0
    diepe_lijn_trace_idx_B = -1
    if heeft_nog_diepere_peilbuizen and x_peilbuis_B:
        basis_offset += 1
        diepe_lijn_trace_idx_B = len(figuur.data) - basis_offset
    diepe_lijn_trace_idx = -1
    if heeft_diepe_peilbuizen and x_peilbuis_A:
        basis_offset += 1
        diepe_lijn_trace_idx = len(figuur.data) - basis_offset

    lijn_trace_idx = len(figuur.data) - (basis_offset + 1)
    marker_trace_idx = len(figuur.data) - (basis_offset + 2)

    # --- Bouw de stappen voor de slider ---
    stappen = []
    for i, datum in enumerate(datums):
        data = slider_data[i]

        restyle_y = [
            data.get("marker_y"),
            data.get("line_y"),
        ]
        restyle_traces = [marker_trace_idx, lijn_trace_idx]

        if heeft_diepe_peilbuizen and x_peilbuis_A and diepe_lijn_trace_idx != -1:
            restyle_y.append(data.get("line_y_A_extended", []))
            restyle_traces.append(diepe_lijn_trace_idx)
        if (
            heeft_nog_diepere_peilbuizen
            and x_peilbuis_B
            and diepe_lijn_trace_idx_B != -1
        ):
            restyle_y.append(data.get("line_y_B_extended", []))
            restyle_traces.append(diepe_lijn_trace_idx_B)

        stap = dict(
            method="restyle",
            args=[{"y": restyle_y}, restyle_traces],
            label=datum.strftime("%d-%m-%y"),
        )
        stappen.append(stap)

    schuifregelaars = [
        dict(
            active=len(stappen) - 1,
            currentvalue={"prefix": "Datum: "},
            pad={"t": 10, "b": 10},
            steps=stappen,
            x=0.5,
            y=-0.3,
            xanchor="center",
            yanchor="top",
        )
    ]

    # Layout
    y_min_waarden = metadata_pb["onderkant_filter_M_NAP"].tolist()
    if heeft_diepe_peilbuizen and metadata_pb_A is not None:
        y_min_waarden.extend(metadata_pb_A["onderkant_filter_M_NAP"].tolist())
    if heeft_nog_diepere_peilbuizen and metadata_pb_B is not None:
        y_min_waarden.extend(metadata_pb_B["onderkant_filter_M_NAP"].tolist())
    y_min = min(y_min_waarden) - 0.5

    y_max_data = [
        max(stijghoogte) for stijghoogte in stijghoogtes if len(stijghoogte) > 0
    ]
    if heeft_diepe_peilbuizen and stijghoogtes_A:
        y_max_data.extend([max(s) for s in stijghoogtes_A if len(s) > 0])
    if heeft_nog_diepere_peilbuizen and stijghoogtes_B:
        y_max_data.extend([max(s) for s in stijghoogtes_B if len(s) > 0])

    y_max = (
        max(y_max_data + [max(dijkprofiel[:, 1])]) + 0.5
        if y_max_data
        else max(dijkprofiel[:, 1]) + 0.5
    )

    # --- Robuuste X-as labels opbouwen ---
    # 1. Maak een map van x-coördinaat naar label, ALLEEN van het hoofdprofiel.
    # Dit is de 'bron van waarheid' voor de labels.
    label_map = {
        x: label
        for x, label in zip(x_peilbuis, metadata_pb["Code_peilbuis"])
        if notna(x) and notna(label)
    }

    # 2. Verzamel alle unieke x-coördinaten van alle profielen (hoofd, A, en B).
    alle_x_coords = set(x for x in x_peilbuis if notna(x))
    if x_peilbuis_A:
        alle_x_coords.update(x for x in x_peilbuis_A if notna(x))
    if x_peilbuis_B:
        alle_x_coords.update(x for x in x_peilbuis_B if notna(x))

    # 3. Maak de definitieve tick-waarden en -teksten, gesorteerd op coördinaat.
    tickvals = sorted(list(alle_x_coords))
    # Zoek voor elke tick-waarde het label op in de map. Als het niet bestaat
    # (omdat het een locatie is die alleen in profiel A of B voorkomt), gebruik dan een lege string.
    ticktext = [label_map.get(x, "") for x in tickvals]

    figuur.update_layout(
        sliders=schuifregelaars,
        title=dict(
            text=f"Grondwaterstand {project_map_naam} {profiel_naam}<br><span style='font-size:12px'>(Data van {start_datum} t/m {eind_datum})</span>",
            x=0.5,
        ),
        yaxis=dict(
            title="Hoogte [m+NAP]",
            range=[y_min, y_max],
            dtick=1,
            gridcolor="rgba(0,0,0,0.15)",
        ),
        xaxis=dict(
            tickvals=tickvals,
            ticktext=ticktext,
            tickangle=-15,
            gridcolor="rgba(0,0,0,0.15)",
        ),
        xaxis2=dict(
            overlaying="x",
            side="top",
            title="Afstand [m]",
            tickmode="array",
            tickvals=dijkprofiel[:, 0].tolist(),
            ticktext=[f"{int(x)}" for x in dijkprofiel[:, 0]],
            showgrid=False,
        ),
        legend=dict(
            bordercolor="black",
            borderwidth=1,
            orientation="v",
            x=1.01,
            y=1,
            xanchor="left",
            yanchor="top",
            # "toggleitem": elke terugkeertijdlijn (nat/droog, per periode) is los
            # aan/uit te zetten. De nat- en droog-terugkeertijden zijn met
            # legendgroup + legendgrouptitle_text wel visueel gegroepeerd (zie
            # hieronder) zodat ze overzichtelijk bij elkaar staan in de legenda.
            groupclick="toggleitem",
            tracegroupgap=10,
        ),
        margin=dict(l=60, r=250, t=120, b=150),
        width=1200,
        height=900,
        plot_bgcolor="#FAFAFA",
        font=dict(family="Arial, sans-serif", size=12, color="black"),
        hovermode="x unified",
    )

    return figuur

# config_lader.py
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from pandas import DataFrame


def laad_yaml_config(config_pad="config/config.yaml") -> dict:
    """Laadt het YAML-configuratiebestand."""
    with open(config_pad, "r") as f:
        return yaml.safe_load(f)


def laad_excel_config(
    basis_pad: Path, config: str, bladen: list[str]
) -> tuple[dict[str, Any], DataFrame]:
    """Laadt configuratie uit het opgegeven Excel-bestand."""
    config_pad: Path = basis_pad / config

    df_config: DataFrame = pd.read_excel(
        config_pad, sheet_name=bladen[0], index_col="key"
    )
    df_profielen: DataFrame = pd.read_excel(config_pad, sheet_name=bladen[1], dtype=str)
    algemene_config: dict = df_config["value"].to_dict()

    return algemene_config, df_profielen

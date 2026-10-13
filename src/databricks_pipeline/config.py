from pathlib import Path

import yaml


def load_config(environment: str = "dev") -> dict:
    config_path = Path(__file__).parent.parent.parent / "config" / f"{environment}.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)

from databricks_pipeline.config import load_config


def test_load_dev_config():
    config = load_config("dev")
    assert config["environment"] == "dev"
    assert "catalog" in config
    assert "storage" in config

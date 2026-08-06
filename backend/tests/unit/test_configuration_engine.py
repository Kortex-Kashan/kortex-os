"""
Unit tests for Configuration Engine.
"""

import json
import pytest
from pathlib import Path
from pydantic import BaseModel

from kortex.engines.configuration.engine import ConfigurationEngine, SystemSettings
from kortex.core.exceptions import ConfigurationLoadError, ConfigurationValidationError


class CustomAppConfig(BaseModel):
    app_title: str
    max_connections: int = 10


@pytest.mark.asyncio
async def test_configuration_engine_defaults() -> None:
    engine = ConfigurationEngine()
    assert engine.name == "configuration"
    assert engine.settings.app_name == "KORTEX OS"
    assert engine.get("environment") == "development"
    assert engine.get("non_existent", "default_val") == "default_val"


@pytest.mark.asyncio
async def test_configuration_engine_set_get() -> None:
    engine = ConfigurationEngine()
    engine.set("custom_feature", True)
    assert engine.get("custom_feature") is True

    engine.set("app_name", "KORTEX Enterprise")
    assert engine.settings.app_name == "KORTEX Enterprise"


@pytest.mark.asyncio
async def test_configuration_engine_load_json(tmp_path: Path) -> None:
    config_file = tmp_path / "config.json"
    content = {"theme": "dark", "port": 8080}
    config_file.write_text(json.dumps(content), encoding="utf-8")

    engine = ConfigurationEngine()
    loaded = engine.load_from_json(config_file)

    assert loaded["theme"] == "dark"
    assert engine.get("theme") == "dark"
    assert engine.get("port") == 8080


@pytest.mark.asyncio
async def test_configuration_engine_load_yaml(tmp_path: Path) -> None:
    config_file = tmp_path / "config.yaml"
    content = "database:\n  pool_size: 20\n"
    config_file.write_text(content, encoding="utf-8")

    engine = ConfigurationEngine()
    loaded = engine.load_from_yaml(config_file)

    assert loaded["database"]["pool_size"] == 20
    assert engine.get("database")["pool_size"] == 20


@pytest.mark.asyncio
async def test_configuration_engine_load_nonexistent_raises() -> None:
    engine = ConfigurationEngine()
    with pytest.raises(ConfigurationLoadError):
        engine.load_from_json(Path("nonexistent.json"))

    with pytest.raises(ConfigurationLoadError):
        engine.load_from_yaml(Path("nonexistent.yaml"))


@pytest.mark.asyncio
async def test_configuration_engine_schema_validation() -> None:
    engine = ConfigurationEngine()
    engine.set("app_title", "My Module")
    engine.set("max_connections", 50)

    schema = engine.get_validated_schema(CustomAppConfig)
    assert schema.app_title == "My Module"
    assert schema.max_connections == 50


@pytest.mark.asyncio
async def test_configuration_engine_schema_validation_fails() -> None:
    engine = ConfigurationEngine()
    engine.set("max_connections", "invalid_number")

    with pytest.raises(ConfigurationValidationError):
        engine.get_validated_schema(CustomAppConfig)

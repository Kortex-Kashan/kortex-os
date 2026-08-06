"""
KORTEX Configuration Engine.

Responsible for loading, validating, and serving system and module configuration
from .env files, JSON, YAML, environment variables, and default values.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional, Type, TypeVar

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from kortex.core.base_engine import BaseEngine, EngineState
from kortex.core.exceptions import ConfigurationLoadError, ConfigurationValidationError

if TYPE_CHECKING:
    from kortex.core.kernel import Kernel

T = TypeVar("T", bound=BaseModel)


class SystemSettings(BaseSettings):
    """Core platform system configuration settings."""

    model_config = SettingsConfigDict(env_prefix="KORTEX_", env_file=".env", extra="ignore")

    app_name: str = Field(default="KORTEX OS", description="Platform application title")
    environment: str = Field(default="development", description="Execution environment (development, staging, production)")
    version: str = Field(default="0.1.0", description="KORTEX release version")
    debug: bool = Field(default=False, description="Enable debug logging and diagnostics")

    # Database
    db_url: str = Field(default="sqlite+aiosqlite:///./kortex_local.db", description="Database connection URL")

    # AI / Ollama
    ollama_url: str = Field(default="http://localhost:11434", description="Ollama API base URL")
    ollama_default_model: str = Field(default="llama3", description="Default local LLM model name")

    # Storage paths
    data_dir: str = Field(default="./data", description="Directory path for local persistence")
    logs_dir: str = Field(default="./logs", description="Directory path for system log files")


class ConfigurationEngine(BaseEngine):
    """Configuration Engine implementation.

    Manages system-wide settings, environment variables, and external config files
    (.env, JSON, YAML).
    """

    def __init__(self, initial_settings: Optional[SystemSettings] = None) -> None:
        super().__init__()
        self._settings = initial_settings or SystemSettings()
        self._custom_config: Dict[str, Any] = {}

    @property
    def name(self) -> str:
        return "configuration"

    @property
    def settings(self) -> SystemSettings:
        """Access core system settings."""
        return self._settings

    async def initialize(self, kernel: Kernel) -> None:
        """Initialize the Configuration Engine."""
        self._set_state(EngineState.INITIALIZING)
        self.logger.info("Initializing Configuration Engine...")

        # Ensure required directories exist
        Path(self._settings.data_dir).mkdir(parents=True, exist_ok=True)
        Path(self._settings.logs_dir).mkdir(parents=True, exist_ok=True)

        self._set_state(EngineState.READY)

    async def start(self) -> None:
        """Start the Configuration Engine."""
        self.ensure_state(EngineState.READY)
        self._set_state(EngineState.RUNNING)
        self.logger.info("Configuration Engine running.")

    async def health_check(self) -> Dict[str, Any]:
        """Diagnostic health check."""
        return {
            "engine": self.name,
            "status": "healthy" if self.state == EngineState.RUNNING else "unhealthy",
            "environment": self._settings.environment,
            "data_dir_exists": Path(self._settings.data_dir).exists(),
        }

    async def stop(self) -> None:
        """Stop the Configuration Engine."""
        self._set_state(EngineState.STOPPING)
        self._set_state(EngineState.STOPPED)
        self.logger.info("Configuration Engine stopped.")

    # -- Engine Specific Public API -----------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        """Get a configuration value by dot-notation key or root key."""
        if hasattr(self._settings, key):
            return getattr(self._settings, key)
        if key in self._custom_config:
            return self._custom_config[key]
        if key in os.environ:
            return os.environ[key]
        return default

    def set(self, key: str, value: Any) -> None:
        """Set a runtime custom configuration value."""
        if hasattr(self._settings, key):
            setattr(self._settings, key, value)
        else:
            self._custom_config[key] = value
        self.logger.debug("Configuration value set for key '%s'", key)

    def load_from_json(self, file_path: Path | str) -> Dict[str, Any]:
        """Load and merge configuration from a JSON file."""
        path = Path(file_path)
        if not path.exists():
            raise ConfigurationLoadError(f"JSON configuration file not found: {path}")
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._custom_config.update(data)
            self.logger.info("Loaded configuration from JSON file: %s", path)
            return data
        except Exception as e:
            raise ConfigurationLoadError(f"Failed to parse JSON file '{path}': {e}") from e

    def load_from_yaml(self, file_path: Path | str) -> Dict[str, Any]:
        """Load and merge configuration from a YAML file."""
        path = Path(file_path)
        if not path.exists():
            raise ConfigurationLoadError(f"YAML configuration file not found: {path}")
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            self._custom_config.update(data)
            self.logger.info("Loaded configuration from YAML file: %s", path)
            return data
        except Exception as e:
            raise ConfigurationLoadError(f"Failed to parse YAML file '{path}': {e}") from e

    def get_validated_schema(self, schema_cls: Type[T], prefix: str = "") -> T:
        """Instantiate and validate a typed Pydantic configuration schema from loaded data.

        Args:
            schema_cls: The Pydantic model class.
            prefix: Optional dictionary subkey or environment prefix.

        Returns:
            Validated instance of schema_cls.
        """
        source_data = self._custom_config
        if prefix and prefix in self._custom_config:
            source_data = self._custom_config[prefix]

        try:
            return schema_cls.model_validate(source_data)
        except Exception as e:
            raise ConfigurationValidationError(f"Configuration validation failed for {schema_cls.__name__}: {e}") from e

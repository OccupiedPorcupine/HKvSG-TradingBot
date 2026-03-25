"""Load and validate APEX configuration from config.yaml.

Provides dot-notation access to nested config values. API credentials are
loaded exclusively from environment variables — never stored in code or config.
"""

import os
from pathlib import Path
from typing import Any, Optional

import yaml


# Two levels up from apex/core/ → project root
_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config.yaml"


class Config:
    """Typed configuration accessor for APEX.

    Usage::

        config = Config()
        url = config.get("api.base_url")
        tier1 = config.get("universe.tier_1_majors")
    """

    def __init__(self, config_path: Optional[Path] = None) -> None:
        path = config_path or _DEFAULT_CONFIG_PATH
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")
        with open(path) as f:
            self._data: dict = yaml.safe_load(f)
        self._validate()

    # ------------------------------------------------------------------
    # Generic access
    # ------------------------------------------------------------------

    def get(self, dotted_key: str, default: Any = None) -> Any:
        """Access nested config values using dot notation.

        Example::

            config.get("api.base_url")          -> "https://mock-api.roostoo.com"
            config.get("tier_caps.tier_1_2")     -> 0.08
            config.get("missing.key", "fallback") -> "fallback"
        """
        keys = dotted_key.split(".")
        value: Any = self._data
        for key in keys:
            if not isinstance(value, dict):
                return default
            value = value.get(key)
            if value is None:
                return default
        return value

    @property
    def raw(self) -> dict:
        """Access the raw config dictionary."""
        return self._data

    # ------------------------------------------------------------------
    # API credentials (from environment only)
    # ------------------------------------------------------------------

    @property
    def api_key(self) -> str:
        """Load API key from the environment variable named in config."""
        env_var = self.get("api.key_env_var", "ROOSTOO_API_KEY")
        key = os.environ.get(env_var, "")
        if not key:
            raise EnvironmentError(
                f"API key not found. Set: export {env_var}=<your_key>"
            )
        return key

    @property
    def api_secret(self) -> str:
        """Load API secret from the environment variable named in config."""
        env_var = self.get("api.secret_env_var", "ROOSTOO_API_SECRET")
        secret = os.environ.get(env_var, "")
        if not secret:
            raise EnvironmentError(
                f"API secret not found. Set: export {env_var}=<your_secret>"
            )
        return secret

    @property
    def base_url(self) -> str:
        """API base URL."""
        return self.get("api.base_url", "https://mock-api.roostoo.com")

    # ------------------------------------------------------------------
    # Universe helpers
    # ------------------------------------------------------------------

    def all_assets(self) -> list[str]:
        """Return flat list of every asset symbol in the universe."""
        assets: list[str] = []
        for tier_key in [
            "tier_1_majors", "tier_2_large_alts", "tier_3_defi",
            "tier_4_meme", "tier_5_obscure",
        ]:
            assets.extend(self.get(f"universe.{tier_key}", []))
        assets.append(self.get("universe.special.paxg", "PAXG"))
        assets.append(self.get("universe.special.trump", "TRUMP"))
        return assets

    def pair_for(self, asset: str) -> str:
        """Return the trading pair string for an asset, e.g. 'BTC/USD'."""
        suffix = self.get("universe.pair_suffix", "/USD")
        return f"{asset}{suffix}"

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate(self) -> None:
        """Validate that required config sections exist."""
        required = [
            "competition", "api", "universe",
            "data_ingestion", "features", "regime", "signals",
            "portfolio", "risk", "execution", "logging",
        ]
        missing = [s for s in required if s not in self._data]
        if missing:
            raise ValueError(f"Missing required config sections: {missing}")

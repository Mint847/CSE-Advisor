"""Settings loading: YAML config + .env secrets."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[2]
CONFIG_DIR = BACKEND_DIR / "config"
DATA_DIR = BACKEND_DIR / "data"
FRONTEND_DIST = BACKEND_DIR.parent / "frontend" / "dist"

load_dotenv(BACKEND_DIR / ".env")


def _load_yaml(name: str) -> dict[str, Any]:
    with open(CONFIG_DIR / name, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@lru_cache
def settings() -> dict[str, Any]:
    return _load_yaml("settings.yaml")


@lru_cache
def pattern_catalog() -> dict[str, dict[str, Any]]:
    """Flat map: pattern type -> {name, tier, enabled, base_reliability, category}."""
    raw = _load_yaml("pattern_catalog.yaml")
    flat: dict[str, dict[str, Any]] = {}
    for category, items in raw.items():
        for ptype, meta in items.items():
            flat[ptype] = {**meta, "category": category, "type": ptype}
    return flat


def env(name: str, default: str | None = None) -> str | None:
    val = os.getenv(name)
    return val if val not in (None, "") else default


def database_url() -> str:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return env("DATABASE_URL", f"sqlite:///{(DATA_DIR / 'cse_advisor.db').as_posix()}")

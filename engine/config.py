"""Config loading: YAML files + .env, resolved relative to the repo root."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"

load_dotenv(ROOT / ".env")


def _load_yaml(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@lru_cache
def profile(lane: str = "data_ai") -> dict:
    name = "profile.yaml" if lane == "data_ai" else f"profile_{lane}.yaml"
    data = _load_yaml(CONFIG_DIR / name)
    data["lane"] = lane
    return data


@lru_cache
def profiles() -> dict[str, dict]:
    return {lane: profile(lane) for lane in ("data_ai", "wellness")}


@lru_cache
def watchlist() -> dict:
    return _load_yaml(CONFIG_DIR / "watchlist.yaml")


@lru_cache
def scoring() -> dict:
    return _load_yaml(CONFIG_DIR / "scoring.yaml")


def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set (see .env.example)")
    return url


def artifacts_dir() -> Path:
    d = ROOT / "artifacts"
    (d / "followups").mkdir(parents=True, exist_ok=True)
    return d

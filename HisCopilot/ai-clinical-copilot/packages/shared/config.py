"""Configuration loader.

Reads `.env` and `infra/models.yaml`. Exposes typed config + model registry.
Business layer reads `get_settings()`; never hardcodes paths.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict


REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: str = "development"
    app_name: str = "ai-clinical-copilot"
    app_port: int = 8001
    frontend_port: int = 3000

    conda_env_main: str = "hiscopilot"
    conda_env_tcm: str = "hiscopilot-tcm"
    models_root: str = "/home/ma-user/modelarts/user-job-dir/yujc/HisCopilot/models"

    database_url: str = f"sqlite+aiosqlite:///{REPO_ROOT}/data/ai_clinical.db"
    cache_backend: str = "memory"
    object_store_backend: str = "local"
    object_store_dir: str = str(REPO_ROOT / "data" / "objects")

    model_gateway_url: str = "http://127.0.0.1:7860/v1"
    qwen_base_url: str = "http://127.0.0.1:7870/v1"
    shizhen_base_url: str = "http://127.0.0.1:7871/v1"
    asr_base_url: str = "http://127.0.0.1:7872/v1"
    embedding_base_url: str = "http://127.0.0.1:7873/v1"
    reranker_base_url: str = "http://127.0.0.1:7874/v1"
    ocr_base_url: str = "http://127.0.0.1:7875/v1"

    enable_second_opinion: bool = False
    enable_real_his: bool = False
    enable_ai_failure_simulation: bool = False
    no_external_llm: bool = True
    cuda_visible_devices: str = "1"

    # V1.2 §6: public base URL for QR codes / mobile access.
    # For private LAN this is the server IP (https://172.16.2.21:3443).
    # For nat-penetrated deployment set PUBLIC_BASE_URL in .env, e.g.
    #   PUBLIC_BASE_URL=https://130236mj6tq92.vicp.fun
    public_base_url: str = ""

    jwt_secret: str = "change-me-in-prod"
    jwt_alg: str = "HS256"
    access_token_expire_minutes: int = 480

    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()


# ---------------------------------------------------------------------------
# Model registry (from infra/models.yaml)
# ---------------------------------------------------------------------------
@dataclass
class ModelEntry:
    role: str
    name: str
    path: str
    arch: str
    type: str
    runtime: str
    provider: str
    base_url: str
    model_id: str
    gpu: str
    dtype: str
    enabled: bool = True
    status: str = "complete"
    notes: str = ""


def _models_yaml_path() -> Path:
    return REPO_ROOT / "infra" / "models.yaml"


@lru_cache
def load_model_registry() -> dict[str, ModelEntry]:
    p = _models_yaml_path()
    if not p.exists():
        return {}
    raw: dict[str, Any] = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    root = raw.get("models_root", get_settings().models_root)
    out: dict[str, ModelEntry] = {}
    for role, m in (raw.get("models") or {}).items():
        out[role] = ModelEntry(
            role=role,
            name=m.get("name", ""),
            path=os.path.join(root, m.get("path", "")),
            arch=m.get("arch", ""),
            type=m.get("type", ""),
            runtime=m.get("runtime", ""),
            provider=m.get("provider", "openai_compatible"),
            base_url=m.get("base_url", ""),
            model_id=m.get("model_id", m.get("name", "")),
            gpu=str(m.get("gpu", "1")),
            dtype=m.get("dtype", "bfloat16"),
            enabled=bool(m.get("enabled", True)) and m.get("status") != "downloading",
            status=m.get("status", "complete"),
            notes=m.get("notes", ""),
        )
    return out


def get_model(role: str) -> ModelEntry | None:
    return load_model_registry().get(role)

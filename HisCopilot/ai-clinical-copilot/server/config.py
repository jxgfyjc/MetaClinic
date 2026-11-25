"""Runtime settings for the v3 backend.

Reads the repo `.env` (same file the legacy stack used) so model endpoints and
PUBLIC_BASE_URL carry over; new keys are prefixed `XY_`.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"), env_file_encoding="utf-8",
        extra="ignore", case_sensitive=False,
    )

    # ---- general -----------------------------------------------------------
    app_env: str = "development"                       # development | production | test
    clinic_name: str = Field("MetaClinic", alias="XY_CLINIC_NAME")
    xy_host: str = "0.0.0.0"
    xy_port: int = 8011
    log_level: str = "INFO"
    public_base_url: str = ""                          # e.g. http://xxx.vicp.fun (QR links)
    cookie_secure: str = Field("auto", alias="XY_COOKIE_SECURE")   # auto | true | false
    cors_origins: str = Field("", alias="XY_CORS_ORIGINS")         # comma separated

    # ---- storage -----------------------------------------------------------
    xy_data_dir: str = str(REPO_ROOT / "data")
    xy_db_file: str = "xiangyuan.db"
    xy_legacy_db_file: str = "ai_clinical.db"

    # ---- auth --------------------------------------------------------------
    session_ttl_hours: int = 12
    session_idle_minutes: int = 240
    login_max_failures: int = 5
    login_lock_minutes: int = 10
    seed_demo_users: bool = Field(True, alias="XY_SEED_DEMO_USERS")
    demo_password: str = Field("demo123", alias="XY_DEMO_PASSWORD")

    # ---- model endpoints (shared with legacy .env keys) ----------------------
    qwen_base_url: str = "http://127.0.0.1:7870/v1"      # clinical_main (Qwen3.8-27B VLM+LLM)
    shizhen_base_url: str = "http://127.0.0.1:7871/v1"   # tcm (ShizhenGPT-7B-Omni)
    asr_base_url: str = "http://127.0.0.1:7872/v1"       # Qwen3-ASR-1.7B (CPU llama.cpp)
    ocr_base_url: str = "http://127.0.0.1:7875/v1"       # PaddleOCR-VL-1.6 (CPU llama.cpp)
    llm_main_model: str = "Qwen3.8-27B"
    llm_tcm_model: str = "ShizhenGPT-7B-Omni"
    llm_main_label: str = Field("", alias="XY_LLM_MAIN_LABEL")
    llm_tcm_label: str = Field("", alias="XY_LLM_TCM_LABEL")
    demo_local_persona: bool = Field(False, alias="XY_DEMO_LOCAL_PERSONA")
    llm_api_key: str = Field("", alias="XY_LLM_API_KEY")
    llm_extra_json: str = Field("", alias="XY_LLM_EXTRA_JSON")
    ocr_model: str = "PaddleOCR-VL-1.6"
    ai_mode: str = Field("live", alias="XY_AI_MODE")     # live | fake (tests)
    jobs_enabled: bool = Field(True, alias="XY_JOBS_ENABLED")

    # ---- style profiles (名医经验画像): OPTIONAL external model for case extraction -------------------------
    # Used only after the admin switch `style.allow_external` is on AND the case text was de-identified and confirmed by a doctor.
    # The key is read from the environment only — it is never stored in the database or returned by any API.
    style_ext_base_url: str = Field("", alias="XY_STYLE_EXT_BASE_URL")      # e.g. https://api.deepseek.com
    style_ext_api_key: str = Field("", alias="XY_STYLE_EXT_API_KEY")
    style_ext_model: str = Field("", alias="XY_STYLE_EXT_MODEL")            # e.g. deepseek-flash
    style_ext_extra_json: str = Field("", alias="XY_STYLE_EXT_EXTRA_JSON")  # extra request fields, e.g. {"thinking":{"type":"disabled"}}
    max_style_file_mb: int = 20

    # ---- limits ------------------------------------------------------------
    max_audio_mb: int = 25
    max_image_mb: int = 15
    max_audio_seconds: int = 180
    intake_start_per_ip_hour: int = 30

    # ---- derived -----------------------------------------------------------
    @property
    def data_dir(self) -> Path:
        return Path(self.xy_data_dir)

    @property
    def db_path(self) -> Path:
        return self.data_dir / self.xy_db_file

    @property
    def legacy_db_path(self) -> Path:
        return self.data_dir / self.xy_legacy_db_file

    @property
    def objects_dir(self) -> Path:
        return self.data_dir / "objects_v3"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:           # used by tests
    get_settings.cache_clear()

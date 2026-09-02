"""Application settings.

Everything configurable flows through :data:`settings`; never read ``os.environ``
directly elsewhere. In production the app refuses to boot with unset/placeholder
secrets (see :meth:`Settings.validate_production_secrets`).
"""

from __future__ import annotations

import base64
import binascii
import secrets
import warnings
from functools import lru_cache
from typing import Literal

from pydantic import ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["dev", "test", "production"]

_PLACEHOLDER_SECRETS = {"", "change-me", "dev-insecure-change-me", "changeme", "secret"}
_KEK_BYTES = 32
_MIN_JWT_SECRET_LEN = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── App ──────────────────────────────────────────────────
    app_env: Environment = "dev"
    app_name: str = "Assistant Studio"
    app_base_url: str = "http://localhost:3000"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    log_level: str = "INFO"
    log_format: Literal["console", "json"] = "console"
    cors_origins: str = "http://localhost:3000"

    # ── Auth / crypto ────────────────────────────────────────
    jwt_secret: str = "dev-insecure-change-me"
    jwt_algorithm: str = "HS256"
    jwt_access_ttl_seconds: int = 900
    jwt_refresh_ttl_seconds: int = 2_592_000
    app_kek: str = ""

    # ── Datastores ───────────────────────────────────────────
    database_url: str = "postgresql+asyncpg://app:app@localhost:5432/app"
    redis_url: str = "redis://localhost:6379/0"

    # ── Object storage ───────────────────────────────────────
    s3_endpoint: str = "http://localhost:9000"
    s3_region: str = "us-east-1"
    s3_bucket: str = "assistant-uploads"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"

    # ── Providers ────────────────────────────────────────────
    anthropic_api_key: str = ""
    voyage_api_key: str = ""
    rag_offline: bool = False

    # ── Agent runtime ────────────────────────────────────────
    agent_max_concurrency: int = 8
    approval_timeout_s: int = 300

    # ── Observability ────────────────────────────────────────
    otel_exporter_otlp_endpoint: str = ""
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = ""

    # ── Seed ─────────────────────────────────────────────────
    seed_admin_email: str = "admin@example.com"
    seed_admin_password: str = "admin12345"
    seed_org_name: str = "Demo Org"

    # ── Derived / helpers ────────────────────────────────────
    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def sync_database_url(self) -> str:
        """Alembic (and other sync consumers) need a non-async driver URL."""
        return self.database_url.replace("+asyncpg", "").replace("+aiosqlite", "")

    @field_validator("database_url")
    @classmethod
    def _check_async_driver(cls, v: str, info: ValidationInfo) -> str:
        if v.startswith("postgresql") and "+asyncpg" not in v:
            raise ValueError("DATABASE_URL must use the postgresql+asyncpg driver")
        if v.startswith("sqlite") and "+aiosqlite" not in v:
            raise ValueError("sqlite DATABASE_URL must use the sqlite+aiosqlite driver")
        return v

    @field_validator("app_kek")
    @classmethod
    def _check_kek_shape(cls, v: str) -> str:
        if not v:
            return v
        try:
            raw = base64.b64decode(v, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("APP_KEK must be base64") from exc
        if len(raw) != _KEK_BYTES:
            raise ValueError("APP_KEK must decode to exactly 32 bytes")
        return v

    @model_validator(mode="after")
    def validate_production_secrets(self) -> Settings:
        if not self.is_production:
            if self.jwt_secret in _PLACEHOLDER_SECRETS:
                # Keep dev unblocked but make it obvious this is ephemeral.
                object.__setattr__(self, "jwt_secret", secrets.token_urlsafe(48))
                warnings.warn(
                    "JWT_SECRET is a placeholder; generated an ephemeral one for this "
                    "process. Tokens will not survive a restart.",
                    stacklevel=2,
                )
            return self

        problems: list[str] = []
        if self.jwt_secret in _PLACEHOLDER_SECRETS or len(self.jwt_secret) < _MIN_JWT_SECRET_LEN:
            problems.append("JWT_SECRET must be set to a strong value (>= 32 chars)")
        if not self.app_kek:
            problems.append("APP_KEK must be set (base64 of 32 random bytes)")
        if not self.anthropic_api_key:
            problems.append("ANTHROPIC_API_KEY must be set")
        if self.log_format != "json":
            warnings.warn("LOG_FORMAT should be 'json' in production", stacklevel=2)
        if problems:
            raise RuntimeError("Refusing to start in production:\n  - " + "\n  - ".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

__all__ = ["Settings", "get_settings", "settings"]

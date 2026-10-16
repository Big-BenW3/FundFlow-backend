"""Application settings.

All runtime configuration is environment-driven so that:
- Kora can start in ``mock`` mode and switch to ``live`` with keys in ``.env``
- The database can start on local SQLite and switch to Neon later by only
  changing ``DATABASE_URL`` (no code changes).
"""

from functools import lru_cache

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- App ---
    app_name: str = "FundFlow"
    app_env: str = "development"
    debug: bool = True
    api_prefix: str = "/api/v1"

    # --- Database ---
    # Local dev default: SQLite. For Neon, set e.g.
    # DATABASE_URL=postgresql+psycopg://user:pass@host/neondb?sslmode=require
    # Plain ``postgres://`` / ``postgresql://`` URLs are normalised automatically.
    database_url: str = "sqlite:///./fundflow.db"
    auto_create_tables: bool = True

    # --- Auth (JWT) ---
    jwt_secret: str = "dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    jwt_expires_minutes: int = 60 * 24 * 7  # 7 days

    # --- Kora ---
    kora_mode: str = "mock"  # "mock" | "live"
    kora_base_url: str = "https://api.korapay.com/merchant/api/v1"
    kora_public_key: str = ""
    kora_secret_key: str = ""
    # Webhook HMAC secret. Falls back to the secret key, then a dev secret.
    kora_webhook_secret: str = ""
    # Live VBA provisioning: bank selected from GET /misc/banks, plus
    # KYC BVN required for live NGN accounts. Mock keeps "000"/no-BVN.
    kora_bank_code: str = "000"
    kora_bvn: str = ""
    # Mock provider simulated merchant balance (NGN).
    mock_merchant_balance: float = 50_000_000.0

    # --- CORS ---
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    # ------------------------------------------------------------- validators
    @model_validator(mode="after")
    def _require_live_secrets(self) -> "Settings":
        """Fail fast: live mode must not run on the dev webhook fallback."""
        if self.kora_mode.strip().lower() == "live" and not (
            self.kora_webhook_secret or self.kora_secret_key
        ):
            raise ValueError(
                "KORA_MODE=live requires KORA_WEBHOOK_SECRET (or KORA_SECRET_KEY)"
            )
        return self

    @field_validator("debug", mode="before")
    @classmethod
    def _coerce_debug(cls, value: object) -> object:
        """Tolerate non-boolean DEBUG values inherited from the shell.

        Some environments export ``DEBUG=release`` (or similar strings) globally,
        which would otherwise crash settings loading with a bool parsing error.
        """
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"release", "prod", "production"}:
                return False
            if normalized in {"dev", "development", "debug"}:
                return True
        return value

    # ------------------------------------------------------------- helpers
    @property
    def is_mock(self) -> bool:
        return self.kora_mode.strip().lower() != "live"

    @property
    def webhook_secret(self) -> str:
        return (
            self.kora_webhook_secret
            or self.kora_secret_key
            or "fundflow-mock-webhook-secret"
        )

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()

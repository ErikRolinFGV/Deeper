"""Configurações globais carregadas a partir do .env via Pydantic Settings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings carregados de variáveis de ambiente (ver .env.example)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Banco e cache
    DATABASE_URL: str
    REDIS_URL: str

    # APIs externas
    ANTHROPIC_API_KEY: str
    APIFY_TOKEN: str
    SERPAPI_KEY: str
    # Crunchbase saiu do escopo do MVP (free tier descontinuado) — opcional.
    CRUNCHBASE_API_KEY: str = "nao-usado"

    # LinkedIn/Apify
    APIFY_ACTOR_LINKEDIN: str = "apimaestro~linkedin-profile-detail"
    LINKEDIN_TTL_DIAS: int = 30  # não recoletar (pagar) perfil mais novo que isso

    # Auth
    JWT_SECRET: str
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 1440

    # App
    APP_ENV: str = "development"
    LOG_LEVEL: str = "INFO"
    CACHE_TTL_DAYS: int = 7


settings = Settings()

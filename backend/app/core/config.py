from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# .env lives at the repo root (one level above backend/), not inside backend/,
# so it's shared between the backend, docker-compose, and any future frontend.
REPO_ROOT_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT_ENV_FILE, extra="ignore")

    database_url: str = "postgresql+psycopg://cia:cia_dev_password@localhost:5432/cia"
    database_url_async: str = "postgresql+asyncpg://cia:cia_dev_password@localhost:5432/cia"
    openai_api_key: str = ""


settings = Settings()

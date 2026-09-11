"""Every value the app reads from the environment, in one typed place.

By step 6 this holds the master encryption key too, so it is worth having one
object rather than os.getenv() calls scattered around.
"""

import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # "+psycopg" selects psycopg 3. Without it SQLAlchemy looks for psycopg2.
    database_url: str = "postgresql+psycopg://forge:forge@localhost:5432/forge"

    # --- model providers (free tiers only; see app/runtime/models.py) ---------
    google_api_key: str | None = None
    groq_api_key: str | None = None

    # --- auth ---------------------------------------------------------------
    # Override in .env for anything real; the default keeps a fresh clone working.
    jwt_secret: str = "dev-only-secret-change-me-in-dot-env-32b"

    # --- the vault -----------------------------------------------------------
    # 32 bytes, base64url. The dev default exists so a fresh clone runs; set a
    # real one in .env. Generate: app.vault.envelope.generate_master_key()
    forge_master_key: str = "EvJlOzROmUa41BJZvHIoDVDIrBNyuNcaMuu0VOEfYDA="

    # --- credentials for local MCP servers -----------------------------------
    # Stands in for the vault until step 6 encrypts these properly.
    local_slack_token: str | None = None


settings = Settings()

# init_chat_model reads provider keys from the environment, so mirror whatever
# .env gave us back out. Keeps ".env is the single source of truth" true.
for _field, _env in (("google_api_key", "GOOGLE_API_KEY"), ("groq_api_key", "GROQ_API_KEY")):
    if (_value := getattr(settings, _field)) and _env not in os.environ:
        os.environ[_env] = _value

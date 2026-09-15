"""Every value the app reads from the environment, in one typed place.

Database, model providers, the JWT secret, the vault master key, the platform
admin - one typed object rather than os.getenv() calls scattered around.
"""

import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # "+psycopg" selects psycopg 3. Without it SQLAlchemy looks for psycopg2.
    # forge_app is deliberately NOT the superuser: superusers bypass row-level
    # security. See docker/db/init.sql.
    database_url: str = "postgresql+psycopg://forge_app:forge_app@localhost:5432/forge"

    # --- model providers (free tiers only; see app/runtime/models.py) ---------
    google_api_key: str | None = None
    groq_api_key: str | None = None

    # --- auth ---------------------------------------------------------------
    # Override in .env for anything real; the default keeps a fresh clone working.
    jwt_secret: str = "dev-only-secret-change-me-in-dot-env-32b"

    # The ONE platform admin. Created at startup if absent; cannot be created
    # through sign-up. Belongs to no company. Override both in .env.
    platform_admin_email: str = "admin@forge.dev"
    platform_admin_password: str = "Passw0rd!"

    # --- the vault -----------------------------------------------------------
    # 32 bytes, base64url. The dev default exists so a fresh clone runs; set a
    # real one in .env. Generate: app.vault.envelope.generate_master_key()
    forge_master_key: str = "EvJlOzROmUa41BJZvHIoDVDIrBNyuNcaMuu0VOEfYDA="


settings = Settings()

# init_chat_model reads provider keys from the environment, so mirror whatever
# .env gave us back out. Keeps ".env is the single source of truth" true.
for _field, _env in (("google_api_key", "GOOGLE_API_KEY"), ("groq_api_key", "GROQ_API_KEY")):
    if (_value := getattr(settings, _field)) and _env not in os.environ:
        os.environ[_env] = _value

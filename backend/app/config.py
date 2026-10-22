from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Neon connection string from Vercel Storage (the pooled one is fine).
    database_url: str = ""

    paypal_client_id: str = ""
    paypal_client_secret: str = ""
    paypal_base_url: str = "https://api-m.sandbox.paypal.com"
    paypal_webhook_id: str = ""

    # Comma-separated origins allowed to call the API (the Vercel frontend).
    cors_origins: str = "http://localhost:5173"


settings = Settings()

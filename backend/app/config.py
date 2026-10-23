from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Neon connection string from Vercel Storage (the pooled one is fine).
    database_url: str = ""

    paypal_client_id: str = ""
    paypal_client_secret: str = ""
    paypal_base_url: str = "https://api-m.sandbox.paypal.com"
    paypal_webhook_id: str = ""

    # AI: any OpenAI-compatible chat API. Defaults to Groq's free tier.
    # Switch to NVIDIA by setting AI_BASE_URL=https://integrate.api.nvidia.com/v1,
    # AI_MODEL=<an NVIDIA model id> and AI_API_KEY=<nvapi-...>.
    groq_api_key: str = ""
    ai_api_key: str = ""
    ai_base_url: str = "https://api.groq.com/openai/v1"
    ai_model: str = "openai/gpt-oss-120b"
    # Tried once if the main model is retired, overloaded or rate limited.
    ai_fallback_model: str = "qwen/qwen3.8-27b"

    # Shared secret the scheduler (Vercel Cron / GitHub Actions) sends to /api/watch/tick.
    cron_secret: str = ""

    # Comma-separated origins allowed to call the API (the Vercel frontend).
    cors_origins: str = "http://localhost:5173"


settings = Settings()

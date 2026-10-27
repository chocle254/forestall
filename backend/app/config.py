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

    # EasyPost (carrier tracking). Test keys start with EZTK.
    easypost_api_key: str = ""

    # Buyer emails over SMTP. Port 587 uses STARTTLS, 465 uses SSL.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""            # e.g. orders@yourshop.com
    smtp_reply_to: str = ""        # where buyers' email replies go (default: smtp_from)
    smtp_starttls: bool = True
    # Sandbox: send every buyer email here instead (sandbox buyer addresses don't receive mail).
    email_redirect_to: str = ""
    auto_shipping_email: bool = True
    store_name: str = "Forestall Demo Shop"
    # Public URL used in email links (default: the URL the request came in on).
    public_url: str = ""

    # Store connections: the webhook signing secret from each store's admin.
    woocommerce_webhook_secret: str = ""

    # Comma-separated origins allowed to call the API (the Vercel frontend).
    cors_origins: str = "http://localhost:5173"


settings = Settings()

import os

from waitress import serve

from ad_sync import start_background_sync
from app import app, init_db, validate_runtime_config


if __name__ == "__main__":
    validate_runtime_config()
    init_db()
    start_background_sync()
    proxy_address = os.environ.get("APP_TRUSTED_PROXY")
    proxy_options = {}
    if proxy_address:
        proxy_options = {
            "trusted_proxy": proxy_address,
            "trusted_proxy_headers": {"x-forwarded-for", "x-forwarded-proto"},
        }
    serve(
        app,
        host=os.environ.get("APP_HOST", "0.0.0.0"),
        port=int(os.environ.get("APP_PORT", "5000")),
        threads=int(os.environ.get("APP_THREADS", "8")),
        channel_timeout=int(os.environ.get("APP_CHANNEL_TIMEOUT", "300")),
        **proxy_options,
    )

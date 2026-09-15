"""Lightweight exchange construction shared by engine and dashboard.

The module deliberately imports ``ccxt`` inside :func:`build_exchange`.
Importing the dashboard must not import the autonomous engine, model stack,
XGBoost, scikit-learn, or pandas merely to obtain a factory function.
"""

from __future__ import annotations

import logging
from trading_assistant.exchange_authority import ReadOnlyExchangeProxy

from trading_assistant.config import Config

logger = logging.getLogger(__name__)


def build_exchange(config: Config):
    """Construct the configured CCXT exchange with existing safety rules.

    ``ccxt`` is loaded lazily so database-only dashboard endpoints remain
    lightweight and available even when no exchange endpoint is requested.
    """
    import ccxt  # Lazy by design; see module docstring.

    exchange_class = getattr(ccxt, config.exchange.name)
    exchange = exchange_class(
        {
            "enableRateLimit": True,
            "timeout": config.exchange.timeout_ms,
        }
    )

    api_key, api_secret = config.get_api_credentials()
    if config.is_live and not (api_key and api_secret):
        raise RuntimeError(
            f"mode: live requires {config.exchange.api_key_env}/"
            f"{config.exchange.api_secret_env} to be set "
            "(real credentials, not testnet ones) - refusing to start."
        )

    if api_key and api_secret:
        exchange.apiKey = api_key
        exchange.secret = api_secret

    if not config.is_live:
        exchange.set_sandbox_mode(True)
        logger.info("exchange configured in TESTNET/sandbox mode")
    else:
        logger.warning("exchange configured in LIVE mode - real funds are at risk")

    if config.execution_mode == "manual_assist":
        logger.warning(
            "execution authority is MANUAL_ASSIST - exchange mutations are blocked"
        )
        exchange = ReadOnlyExchangeProxy(exchange)

    return exchange

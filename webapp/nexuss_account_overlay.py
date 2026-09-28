"""Small account-evidence extension for a pinned, already-running dashboard image.

The base image retains its installed dependencies and source. Only the
read-only account evidence module and this route are layered onto it.
"""

import os
import sqlite3

from fastapi import HTTPException

from webapp.account_evidence import account_evidence
from webapp.price_call_results import results as btc_price_call_results
from webapp.server import app


@app.get("/api/agent/account-evidence")
def agent_account_evidence():
    try:
        return account_evidence(os.environ["ATS_TRADING_DB_PATH"])
    except (KeyError, sqlite3.Error, ValueError, OSError):
        raise HTTPException(503, "Account evidence unavailable") from None


@app.get("/api/agent/btc-price-call-results")
def agent_btc_price_call_results():
    try:
        return btc_price_call_results(
            os.environ.get("ATS_DASHBOARD_ANALYTICS_DB", "/app/dashboard_data/analytics.db")
        )
    except (sqlite3.Error, ValueError, OSError):
        raise HTTPException(503, "Price-call results unavailable") from None

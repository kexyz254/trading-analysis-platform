"""Small account-evidence extension for a pinned, already-running dashboard image.

The base image retains its installed dependencies and source. Only the
read-only account evidence module and this route are layered onto it.
"""

import os
import sqlite3

from fastapi import HTTPException

from webapp.account_evidence import account_evidence
from webapp.server import app


@app.get("/api/agent/account-evidence")
def agent_account_evidence():
    try:
        return account_evidence(os.environ["ATS_TRADING_DB_PATH"])
    except (KeyError, sqlite3.Error, ValueError, OSError):
        raise HTTPException(503, "Account evidence unavailable") from None

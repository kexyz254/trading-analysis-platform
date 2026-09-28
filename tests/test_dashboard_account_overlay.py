"""Verify the pinned-image overlay registers a read-only bounded API."""

import importlib
import sys
from types import ModuleType

from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_overlay_uses_existing_app_and_fails_closed_without_database(monkeypatch, tmp_path):
    existing = FastAPI()
    parent = ModuleType("webapp.server")
    parent.app = existing
    monkeypatch.setitem(sys.modules, "webapp.server", parent)
    monkeypatch.setenv("ATS_TRADING_DB_PATH", str(tmp_path / "missing.db"))
    sys.modules.pop("webapp.nexuss_account_overlay", None)
    try:
        overlay = importlib.import_module("webapp.nexuss_account_overlay")
        assert overlay.app is existing
        assert TestClient(existing).get("/api/agent/account-evidence").status_code == 503
        assert TestClient(existing).post("/api/agent/account-evidence").status_code == 405
    finally:
        sys.modules.pop("webapp.nexuss_account_overlay", None)

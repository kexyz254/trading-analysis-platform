"""ASGI entry point: uvicorn services.umil.app.main:app --port 8100."""

from .api import create_app


app = create_app()

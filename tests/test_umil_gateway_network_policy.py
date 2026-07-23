import os
from unittest.mock import patch

import pytest

from webapp.umil_gateway import _base_url


def test_accepts_internal_compose_umil_service() -> None:
    with patch.dict(
        os.environ,
        {"ATS_UMIL_BASE_URL": "http://umil:8100"},
    ):
        assert _base_url() == "http://umil:8100"


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com:8100",
        "https://umil:8100",
        "http://umil:8000",
        "http://user:pass@umil:8100",
    ],
)
def test_rejects_unapproved_umil_endpoints(url: str) -> None:
    with patch.dict(os.environ, {"ATS_UMIL_BASE_URL": url}):
        with pytest.raises(ValueError):
            _base_url()

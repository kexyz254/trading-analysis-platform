import pytest
from services.nexuss_bridge.serve import bind_address


@pytest.mark.parametrize("address", ["127.0.0.1", "::1", "100.93.64.23"])
def test_private_addresses(address):
    assert bind_address(address) == address


@pytest.mark.parametrize("address", ["0.0.0.0", "::", "167.233.158.160", "example.com", "192.168.1.2"])
def test_public_or_unreviewed_addresses_denied(address):
    with pytest.raises(ValueError):
        bind_address(address)

"""Private listener for loopback or the host's Tailscale IPv4 address."""
import ipaddress
import os


def bind_address(value):
    address = ipaddress.ip_address(value)
    if address.is_loopback or address in ipaddress.ip_network("100.64.0.0/10"):
        return str(address)
    raise ValueError("Bridge listener must use loopback or a private Tailscale address")


def main():
    import uvicorn
    host = bind_address(os.getenv("NEXUSS_BRIDGE_BIND", "127.0.0.1"))
    uvicorn.run("services.nexuss_bridge.app:from_environment", factory=True,
                host=host, port=8300, access_log=False, limit_concurrency=16)


if __name__ == "__main__":
    main()

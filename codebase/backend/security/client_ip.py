from collections.abc import Mapping


def client_ip_from_headers(headers: Mapping[str, str], peer: str | None) -> str:
    """Behind the deploy proxy every request comes from the proxy's address,
    so the left-most X-Forwarded-For entry (the original client) is used when
    present. A client can spoof that header only to land in a *different*
    bucket/audit IP, not to lift a limit on someone else's. `headers` keys are
    lower-case."""
    forwarded = headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return peer or "unknown"

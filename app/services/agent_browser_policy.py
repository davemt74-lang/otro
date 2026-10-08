"""A5B URL policy: deny local networks and bind each worker to one HTTPS host."""
from __future__ import annotations
import ipaddress
import re
import socket
from urllib.parse import urlsplit
from .agent_mission_runtime import MissionError

HOST=re.compile(r"^[a-z0-9.-]{1,253}$")
SUFFIXES=(".local",".localhost",".internal",".test",".invalid")

def parse_url(value: str) -> tuple[str,str,str]:
    value=str(value or "").strip()
    if not value or len(value)>1400 or any(ord(c)<32 for c in value):
        raise MissionError("Browser URL must be between 1 and 1400 characters.",422)
    try:
        parsed=urlsplit(value)
        host=str(parsed.hostname or "").lower().rstrip(".")
        port=parsed.port
    except ValueError as exc:
        raise MissionError("Invalid browser URL.",422) from exc
    if (parsed.scheme!="https" or not HOST.fullmatch(host) or "." not in host
        or parsed.username is not None or parsed.password is not None
        or port not in (None,443) or parsed.fragment
        or any(host.endswith(s) for s in SUFFIXES)):
        raise MissionError("Browser requires a public HTTPS origin.",422)
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise MissionError("IP address literals are forbidden.",422)
    return value,host,"https://"+host

def pinned_public_ipv4(host: str) -> str:
    try:
        records=socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)
    except OSError as exc:
        raise MissionError("Browser host could not be resolved.",422) from exc
    addresses={str(record[4][0]) for record in records}
    if not addresses:
        raise MissionError("Browser host could not be resolved.",422)
    if any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise MissionError("Private/reserved destination rejected.",403)
    ipv4=sorted(ip for ip in addresses if ":" not in ip)
    if not ipv4:
        raise MissionError("Public IPv4 address required.",422)
    return ipv4[0]

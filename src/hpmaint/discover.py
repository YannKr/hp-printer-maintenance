"""Printer discovery: mDNS/Bonjour first, then LAN port scan."""

from __future__ import annotations

import ipaddress
import re
import socket
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable

from .log import get_logger

log = get_logger(__name__)

# Strings that really identify an HP device. "hp" is matched as a whole word
# only: as a bare substring it also matches "php", so every PHP admin page on
# the LAN used to pass as a printer. "embedded web server" is deliberately not
# here — Canon, Ricoh and Xerox use that phrase too.
_HP_MARKERS = re.compile(
    r"\bhp\b"
    r"|hewlett[- ]?packard"
    r"|\benvy\b|officejet|deskjet|laserjet|photosmart"
    r"|/DevMgmt/|\bLEDM\b",
    re.IGNORECASE,
)


def _looks_hp(*parts: str) -> bool:
    """True if any of the given strings carries an HP identifier."""
    return bool(_HP_MARKERS.search(" ".join(p for p in parts if p)))


def _prop_strings(props: dict) -> list[str]:
    """Flatten an mDNS TXT record to plain strings (keys and values)."""
    out: list[str] = []
    for key, value in props.items():
        for item in (key, value):
            if isinstance(item, bytes):
                item = item.decode(errors="ignore")
            if item is not None:
                out.append(str(item))
    return out


# ------------------------------------------------------------------ data


@dataclass
class PrinterInfo:
    ip: str
    port: int = 80
    name: str = ""
    model: str = ""
    via: str = "scan"  # "mdns" or "scan"
    ews_port: int = 80
    ipp_port: int = 631

    @property
    def ews_url(self) -> str:
        return f"http://{self.ip}:{self.ews_port}"


# ------------------------------------------------------------------ mDNS


def discover_mdns(timeout: float = 5.0) -> list[PrinterInfo]:
    """Use zeroconf/Bonjour to find printers on the LAN."""
    log.info("Starting mDNS discovery (timeout=%.1fs)", timeout)
    try:
        from zeroconf import ServiceBrowser, Zeroconf
    except ImportError:
        log.warning("zeroconf not available — skipping mDNS discovery")
        return []

    found: list[PrinterInfo] = []
    zc = Zeroconf()

    class _Handler:
        def add_service(self, zc: Zeroconf, stype: str, name: str) -> None:
            info = zc.get_service_info(stype, name)
            if not info:
                return

            props = info.properties or {}
            # _http._tcp advertises everything with a web UI — Chromecasts,
            # NASes, routers. Require an HP identifier before treating an
            # advert as a printer. The tool only speaks HP's LEDM API.
            if not _looks_hp(info.name, name, *_prop_strings(props)):
                log.debug("mDNS ignored (no HP identifier): %s via %s", name, stype)
                return

            for addr in info.parsed_addresses():
                try:
                    ipaddress.ip_address(addr)
                except ValueError:
                    continue
                p = PrinterInfo(
                    ip=addr,
                    port=info.port,
                    name=info.name,
                    via="mdns",
                    ews_port=80,
                    ipp_port=info.port if info.port else 631,
                )
                for key in (b"ty", b"product", b"model"):
                    if key in props:
                        raw = props[key]
                        p.model = raw.decode(errors="ignore") if isinstance(raw, bytes) else str(raw)
                        break
                log.info("mDNS found: %s  name=%r  model=%r  via=%s",
                         addr, info.name, p.model, stype)
                found.append(p)

        def remove_service(self, *_: object) -> None:
            pass

        def update_service(self, *_: object) -> None:
            pass

    services = [
        "_printer._tcp.local.",
        "_pdl-datastream._tcp.local.",
        "_ipp._tcp.local.",
        "_ipps._tcp.local.",
        "_http._tcp.local.",
    ]
    handler = _Handler()
    # Kept alive for the duration of the browse; discarded on zc.close().
    _browsers = [ServiceBrowser(zc, svc, handler) for svc in services]
    time.sleep(timeout)
    zc.close()

    seen: set[str] = set()
    unique: list[PrinterInfo] = []
    for p in found:
        if p.ip not in seen:
            seen.add(p.ip)
            unique.append(p)
    log.info("mDNS discovery complete: %d unique printer(s) found", len(unique))
    return unique


# ------------------------------------------------------------------ port scan


def _local_subnet() -> list[str]:
    """Return host addresses on the local /24 subnet."""
    hostname = socket.gethostname()
    try:
        local_ip = socket.gethostbyname(hostname)
    except Exception:
        local_ip = "127.0.0.1"

    if local_ip.startswith("127."):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
        except Exception:
            pass

    try:
        net = ipaddress.IPv4Network(local_ip + "/24", strict=False)
        hosts = [str(h) for h in net.hosts()]
        log.debug("Scan subnet: %s  (%d hosts)", net, len(hosts))
        return hosts
    except Exception:
        log.warning("Could not determine local subnet from IP %s", local_ip)
        return []


def _port_open(host: str, port: int, timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, TimeoutError):
        return False


def _is_hp_ews(ip: str, port: int = 80, timeout: float = 3.0) -> bool:
    """Quick heuristic: does this host look like an HP EWS?

    The Server header is the strongest signal — HP's EWS reports
    "HP HTTP Server". The body is checked with the same word-boundary
    markers, so a PHP page no longer counts as a match.
    """
    try:
        import requests

        r = requests.get(f"http://{ip}:{port}/", timeout=timeout, allow_redirects=True)
        server = r.headers.get("Server", "")
        matched = _looks_hp(server, r.text)
        log.debug("HP EWS check %s → %s  (Server: %r)",
                  ip, "yes" if matched else "no", server)
        return matched
    except Exception as exc:
        log.debug("HP EWS check %s → error: %s", ip, exc)
        return False


def discover_scan(
    progress: Callable[[str], None] | None = None,
    workers: int = 64,
) -> list[PrinterInfo]:
    """Scan the local /24 for devices responding on printer ports."""
    log.info("Starting LAN port scan (workers=%d)", workers)
    hosts = _local_subnet()
    printer_ports = [80, 443, 631, 9100]
    candidates: list[str] = []

    def check(host: str) -> str | None:
        for port in printer_ports:
            if _port_open(host, port, timeout=0.35):
                return host
        return None

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(check, h): h for h in hosts}
        for fut in as_completed(futures):
            host = fut.result()
            if host:
                candidates.append(host)
                log.debug("Port scan candidate: %s", host)
            if progress:
                progress(futures[fut])

    log.info("Port scan found %d candidate(s); checking for HP EWS", len(candidates))
    found: list[PrinterInfo] = []
    for ip in candidates:
        if _is_hp_ews(ip):
            log.info("Confirmed HP printer at %s (via port scan)", ip)
            found.append(PrinterInfo(ip=ip, via="scan"))
    log.info("LAN scan complete: %d HP printer(s) confirmed", len(found))
    return found


# ------------------------------------------------------------------ public API


def discover_printers(
    mdns_timeout: float = 4.0,
    scan_fallback: bool = True,
    progress: Callable[[str], None] | None = None,
) -> list[PrinterInfo]:
    """Discover printers. mDNS first, then LAN scan if nothing found."""
    log.info("discover_printers() called")
    printers = discover_mdns(timeout=mdns_timeout)
    if not printers and scan_fallback:
        log.info("mDNS found nothing — falling back to LAN scan")
        printers = discover_scan(progress=progress)
    return printers

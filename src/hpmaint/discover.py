"""Printer discovery: mDNS/Bonjour first, then LAN port scan."""

from __future__ import annotations

import ipaddress
import socket
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable

from .log import get_logger

log = get_logger(__name__)

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
        from zeroconf import ServiceBrowser, ServiceInfo, Zeroconf
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
                props = info.properties or {}
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
    browsers = [ServiceBrowser(zc, svc, handler) for svc in services]
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
    """Quick heuristic: does this host look like an HP EWS?"""
    try:
        import requests

        r = requests.get(f"http://{ip}:{port}/", timeout=timeout, allow_redirects=True)
        text = r.text.lower()
        matched = any(
            kw in text
            for kw in ("hp", "hewlett", "envy", "officejet", "laserjet", "embedded web")
        )
        log.debug("HP EWS check %s → %s", ip, "yes" if matched else "no")
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

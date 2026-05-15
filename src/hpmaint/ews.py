"""HP EWS client — LEDM XML API.

This printer (HP Envy Photo 7800 series) runs an nginx-based EWS that is a
JavaScript SPA. It has NO traditional HTML forms at /hp/device/* paths.
All maintenance operations go through HP's LEDM (Lightweight Embedded Device
Management) XML REST API.

Confirmed endpoints (reverse-engineered from /framework/Unified.js + manifests):
  GET  /DevMgmt/InternalPrintCap.xml   → supported job types
  POST /DevMgmt/InternalPrintDyn.xml   → trigger any internal print job
  GET  /Calibration/State              → calibration/alignment state
  POST /Calibration/Session            → trigger printhead alignment
  GET  /DevMgmt/ConsumableConfigDyn.xml → ink levels
  GET  /DevMgmt/ProductStatusDyn.xml   → printer status
  GET  /AuthChk                        → {"hasAuth": true/false}

Auth: HTTP Digest Auth (admin + EWS password). Most GETs work unauthenticated;
POSTs to maintenance endpoints require credentials when hasAuth=true.
"""

from __future__ import annotations

import re
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any

try:
    import requests
    from requests.auth import HTTPDigestAuth
except ImportError as exc:
    raise SystemExit(f"Missing dependency: {exc}. Run ./setup.sh first.") from exc

from .log import get_logger

log = get_logger(__name__)

# ------------------------------------------------------------------ namespaces

_NS_IPDYN = "http://www.hp.com/schemas/imaging/con/ledm/internalprintdyn/2008/03/21"
_NS_IPCAP = "http://www.hp.com/schemas/imaging/con/ledm/internalprintcap/2008/03/21"
_NS_CAL   = "http://www.hp.com/schemas/imaging/con/cnx/markingagentcalibration/2009/04/08"

# Internal print job types (from /DevMgmt/InternalPrintCap.xml)
CLEAN_L1    = "cleaningPage"
CLEAN_L2    = "cleaningPageLevel2"
CLEAN_L3    = "cleaningPageLevel3"
CLEAN_VERIFY= "cleaningVerificationPage"
PQ_DIAG     = "pqDiagnosticsPage"
DEMO_PAGE   = "demoPage"
CONFIG_PAGE = "configurationPage"
DIAG_PAGE   = "diagnosticsPage"

_TIMEOUT = 12


# ------------------------------------------------------------------ data types

@dataclass
class InkLevel:
    color: str
    level_pct: int | None = None
    label: str = ""


@dataclass
class PrinterStatus:
    reachable: bool = False
    model: str = ""
    ink: list[InkLevel] = field(default_factory=list)
    ews_url: str = ""


@dataclass
class MaintenanceResult:
    success: bool
    message: str
    manual_instructions: str = ""


# ------------------------------------------------------------------ client

class EWSClient:
    """LEDM XML client for HP Envy Photo 7800 series EWS."""

    def __init__(
        self,
        ip: str,
        port: int = 80,
        username: str = "admin",
        password: str = "",
        timeout: int = _TIMEOUT,
    ) -> None:
        self.base_url = f"http://{ip}:{port}"
        self.timeout = timeout
        self._username = username
        self._password = password
        self._has_auth: bool | None = None  # None = not yet checked
        self._auth_counter: int = 0         # X-Auth-Counter from /AuthChk

        self._digest_auth: HTTPDigestAuth | None = None

        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "Mozilla/5.0 (compatible; hpmaint/1.0)",
            "Accept": "text/xml, application/xml, */*",
        })
        log.info("EWSClient created for %s", self.base_url)

    # ---------------------------------------------------------------- internals

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _auth(self) -> HTTPDigestAuth | None:
        """Return the shared Digest Auth instance (nonce is cached across calls)."""
        if self._has_auth and self._password:
            if self._digest_auth is None:
                self._digest_auth = HTTPDigestAuth(self._username, self._password)
            return self._digest_auth
        return None

    def _get(self, path: str, **kw: Any) -> requests.Response | None:
        url = self._url(path)
        log.debug("GET %s", url)
        try:
            r = self._session.get(url, timeout=self.timeout,
                                  allow_redirects=True, auth=self._auth(), **kw)
            log.debug("  → HTTP %d  (%d bytes)", r.status_code, len(r.content))
            if r.status_code >= 400:
                log.warning("GET %s returned HTTP %d", url, r.status_code)
                return None
            return r
        except requests.ConnectionError as e:
            log.debug("GET %s — connection error: %s", url, e)
            return None
        except requests.Timeout:
            log.warning("GET %s — timed out after %ds", url, self.timeout)
            return None
        except requests.RequestException as e:
            log.warning("GET %s — %s", url, e)
            return None

    def _post_xml(
        self,
        path: str,
        body: str,
        content_type: str = "text/xml; charset=utf-8",
        retries_on_503: int = 0,
        retry_delay: float = 60.0,
    ) -> requests.Response | None:
        url = self._url(path)
        auth = self._auth()
        extra_headers: dict = {
            "Content-Type": content_type,
            "X-Requested-With": "XMLHttpRequest",
            "Origin": self.base_url,
            "Referer": self.base_url + "/",
        }
        if self._auth_counter:
            extra_headers["X-Auth-Counter"] = str(self._auth_counter)

        attempts = max(1, retries_on_503 + 1)
        last_response: requests.Response | None = None
        for attempt in range(1, attempts + 1):
            attempt_tag = f" [attempt {attempt}/{attempts}]" if attempts > 1 else ""
            log.debug("POST %s  auth=%s  X-Auth-Counter=%s%s\n  body: %s",
                      url, f"{auth.username}:***" if auth else "none",
                      self._auth_counter or "none", attempt_tag, body[:300])
            try:
                r = self._session.post(
                    url,
                    data=body.encode("utf-8"),
                    headers=extra_headers,
                    timeout=self.timeout,
                    allow_redirects=True,
                    auth=auth,
                )
                log.debug("  → HTTP %d  (%d bytes)", r.status_code, len(r.content))
                last_response = r

                if r.status_code == 503 and attempt < attempts:
                    log.info(
                        "Printer busy (HTTP 503) on %s — retrying in %ds (%d/%d)",
                        path, int(retry_delay), attempt, retries_on_503,
                    )
                    sys.stderr.write(
                        f"  Printer busy, retrying in {int(retry_delay)}s "
                        f"({attempt}/{retries_on_503})\n"
                    )
                    sys.stderr.flush()
                    time.sleep(retry_delay)
                    continue

                if r.status_code >= 400:
                    log.warning("POST %s returned HTTP %d\n  body: %s",
                                url, r.status_code, r.text[:400])
                return r
            except requests.RequestException as e:
                log.warning("POST %s — %s", url, e)
                return None
        return last_response

    # ---------------------------------------------------------------- probe / auth

    def probe(self) -> bool:
        """Return True if the EWS is reachable. Also authenticates if needed.

        Auth flow mirrors the browser SPA:
          1. GET /  → establishes s- session cookie
          2. GET /AuthChk  → check if password is set (401 = yes)
          3. GET /AuthChk with Digest + X-Auth-Client-Counter → authenticate session
        POST requests then need X-Requested-With/Origin/Referer (nginx CSRF check).
        """
        import random
        log.info("Probing EWS at %s", self.base_url)

        # 1. Establish s- session cookie by visiting root (browser does this first).
        try:
            r0 = self._session.get(self._url("/"), timeout=self.timeout,
                                   allow_redirects=True)
            log.debug("GET / → HTTP %d", r0.status_code)
        except requests.RequestException as e:
            log.warning("GET / failed: %s", e)
            return False

        if r0.status_code >= 400:
            log.warning("EWS not reachable at %s (HTTP %d)", self.base_url, r0.status_code)
            return False
        log.info("EWS reachable at %s", self.base_url)

        # 2. Check auth requirement.
        url_auth = self._url("/AuthChk")
        try:
            r = self._session.get(url_auth, timeout=self.timeout, allow_redirects=True)
            log.debug("GET /AuthChk → HTTP %d", r.status_code)
            if r.status_code == 200:
                try:
                    self._has_auth = bool(r.json().get("hasAuth", False))
                except Exception:
                    self._has_auth = False
            elif r.status_code == 401:
                self._has_auth = True
                if not self._password:
                    log.warning(
                        "EWS requires a password but none is configured. "
                        "Run: ./hpmaint.py configure"
                    )
                else:
                    # 3. Authenticate: Digest Auth + X-Auth-Client-Counter (SPA nonce).
                    client_counter = random.randint(1, 999999)
                    r2 = self._session.get(
                        url_auth,
                        timeout=self.timeout,
                        allow_redirects=True,
                        auth=self._auth(),
                        headers={"X-Auth-Client-Counter": str(client_counter)},
                    )
                    log.debug("GET /AuthChk (Digest) → HTTP %d", r2.status_code)
                    if r2.status_code == 200:
                        counter = r2.headers.get("X-Auth-Counter", "")
                        if counter:
                            try:
                                self._auth_counter = int(counter)
                            except ValueError:
                                pass
                        log.info("Authenticated (X-Auth-Counter=%s)",
                                 self._auth_counter or "none")
                    else:
                        log.warning("Digest Auth failed (HTTP %d) — check EWS password",
                                    r2.status_code)
            else:
                self._has_auth = False
        except requests.RequestException as e:
            log.warning("AuthChk check failed: %s", e)
            self._has_auth = bool(self._password)

        log.info("Auth required: %s", self._has_auth)
        return True

    # ---------------------------------------------------------------- status / ink

    def get_status(self) -> PrinterStatus:
        log.info("Fetching printer status")
        status = PrinterStatus(ews_url=self.base_url)

        r = self._get("/DevMgmt/ProductStatusDyn.xml")
        if r:
            status.reachable = True
            try:
                root = ET.fromstring(r.content)
                # Model lives in various places; grab any text that looks like one
                for elem in root.iter():
                    t = (elem.text or "").strip()
                    if t and re.search(r"HP|Envy|OfficeJet|DeskJet", t, re.I):
                        if len(t) < 80:
                            status.model = t
                            break
            except ET.ParseError as e:
                log.warning("Could not parse ProductStatusDyn.xml: %s", e)
        else:
            # Root page is still a reachability signal
            if self._get("/"):
                status.reachable = True

        status.ink = self.get_ink_levels()
        return status

    def get_ink_levels(self) -> list[InkLevel]:
        log.info("Fetching ink levels from ConsumableConfigDyn.xml")
        r = self._get("/DevMgmt/ConsumableConfigDyn.xml")
        if not r:
            log.warning("ConsumableConfigDyn.xml not available")
            return []
        try:
            root = ET.fromstring(r.content)
        except ET.ParseError as e:
            log.warning("Could not parse ConsumableConfigDyn.xml: %s", e)
            return []

        # Map ConsumableLabelCode values → (color key, display label)
        _LABEL_MAP: dict[str, tuple[str, str]] = {
            "K":   ("black",    "Black"),
            "BK":  ("black",    "Black"),
            "C":   ("cyan",     "Cyan"),
            "M":   ("magenta",  "Magenta"),
            "Y":   ("yellow",   "Yellow"),
            "CMY": ("tricolor", "Tri-color (CMY)"),
            "CMP": ("tricolor", "Tri-color"),
            "LC":  ("cyan",     "Light Cyan"),
            "LM":  ("magenta",  "Light Magenta"),
        }

        levels: list[InkLevel] = []
        # Each cartridge lives in a ConsumableInfo container.
        for container in root.iter():
            if not container.tag.endswith("ConsumableInfo"):
                continue
            label_code = ""
            pct: int | None = None
            for child in container:
                local = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                text = (child.text or "").strip()
                if local == "ConsumableLabelCode":
                    label_code = text.upper()
                elif local == "ConsumablePercentageLevelRemaining":
                    try:
                        pct = int(float(text))
                    except ValueError:
                        pass
            if pct is not None and label_code:
                color, label = _LABEL_MAP.get(label_code, (label_code.lower(), label_code))
                levels.append(InkLevel(color=color, level_pct=pct, label=label))
                log.debug("Ink: %s = %d%%", label, pct)

        log.info("Ink levels found: %s",
                 [(l.label, l.level_pct) for l in levels] or "none")
        return levels

    # ---------------------------------------------------------------- internal print jobs

    def _internal_print(self, job_type: str) -> MaintenanceResult:
        """POST an internal print job via LEDM InternalPrintDyn."""
        log.info("_internal_print(%r)", job_type)

        # Verify job type is supported before trying
        cap = self._get("/DevMgmt/InternalPrintCap.xml")
        if cap:
            try:
                root = ET.fromstring(cap.content)
                supported = [e.text for e in root.iter()
                             if e.text and e.tag.endswith("JobType")]
                log.debug("Supported job types: %s", supported)
                if job_type not in supported:
                    log.warning("Job type %r not in supported list: %s",
                                job_type, supported)
                    return MaintenanceResult(
                        success=False,
                        message=f"Printer does not support job type '{job_type}'",
                    )
            except ET.ParseError as e:
                log.warning("Could not parse InternalPrintCap.xml: %s", e)

        body = (
            f'<?xml version="1.0" encoding="UTF-8"?>'
            f'<ipdyn:InternalPrintDyn'
            f' xmlns:ipdyn="{_NS_IPDYN}">'
            f'<ipdyn:JobType>{job_type}</ipdyn:JobType>'
            f'</ipdyn:InternalPrintDyn>'
        )
        r = self._post_xml(
            "/DevMgmt/InternalPrintDyn.xml", body,
            retries_on_503=3, retry_delay=60.0,
        )

        if r is None:
            return MaintenanceResult(success=False, message="No response from printer")

        if r.status_code in (200, 201, 202, 204):
            log.info("Internal print job %r accepted (HTTP %d)", job_type, r.status_code)
            return MaintenanceResult(success=True, message="Command accepted by printer")

        if r.status_code == 403:
            log.warning("HTTP 403 on %r — auth required or password wrong", job_type)
            return MaintenanceResult(
                success=False,
                message="Access denied (HTTP 403)",
                manual_instructions=(
                    "The printer requires EWS credentials for maintenance.\n"
                    "Run: ./hpmaint.py configure\n"
                    "Enter the EWS admin password (shown on the printer's\n"
                    "touchscreen under Settings → Printer Info → EWS Password,\n"
                    "or try the printer's WiFi Direct password)."
                ),
            )

        return MaintenanceResult(
            success=False,
            message=f"Printer returned HTTP {r.status_code}",
        )

    # ---------------------------------------------------------------- operations

    def clean_printhead(self, level: int = 1) -> MaintenanceResult:
        job = {1: CLEAN_L1, 2: CLEAN_L2, 3: CLEAN_L3}.get(level, CLEAN_L1)
        log.info("clean_printhead(level=%d) → job=%r", level, job)
        result = self._internal_print(job)
        if not result.success and not result.manual_instructions:
            result.manual_instructions = _manual_clean(level)
        return result

    def get_calibration_state(self) -> str | None:
        """GET /Calibration/State and return the current state string, or None."""
        r = self._get("/Calibration/State")
        if not r:
            return None
        log.debug("/Calibration/State body: %s", r.text[:500])
        try:
            root = ET.fromstring(r.content)
        except ET.ParseError as e:
            log.warning("Could not parse /Calibration/State: %s", e)
            return None
        for elem in root.iter():
            local = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            if local in ("CalibrationState", "State"):
                text = (elem.text or "").strip()
                if text:
                    return text
        return None

    def align_printhead(self) -> MaintenanceResult:
        log.info("align_printhead() → POST /Calibration/Session")

        # Diagnostic: the LEDM markingagentcalibration state machine governs
        # which transitions are legal. Surface the current state on failure.
        state = self.get_calibration_state()
        if state:
            log.info("Current calibration state: %s", state)

        # The endpoint requires Content-Type: text/xml and a schema-valid body.
        # The valid trigger value is `Printing` — the printer prints an
        # alignment pattern and reads it back. (`Scanning` selects the
        # scanner-based variant; `CalibrationRequired` is a printer-set status
        # that produces 409 because it's not a legal client-initiated state.)
        body = (
            f'<?xml version="1.0" encoding="UTF-8"?>'
            f'<cal:CalibrationState xmlns:cal="{_NS_CAL}">Printing</cal:CalibrationState>'
        )
        r = self._post_xml(
            "/Calibration/Session", body,
            retries_on_503=3, retry_delay=60.0,
        )

        if r is None:
            return MaintenanceResult(
                success=False,
                message="No response from printer",
                manual_instructions=_manual_align(),
            )
        if r.status_code in (200, 201, 202, 204):
            log.info("Alignment session accepted (HTTP %d)", r.status_code)
            return MaintenanceResult(success=True, message="Alignment started")
        if r.status_code == 403:
            return MaintenanceResult(
                success=False,
                message="Access denied (HTTP 403) — EWS password required",
                manual_instructions=_manual_align(),
            )
        if r.status_code == 409:
            state_info = f" — current state: {state}" if state else ""
            return MaintenanceResult(
                success=False,
                message=f"Printer rejected alignment (HTTP 409){state_info}",
                manual_instructions=_manual_align(),
            )
        return MaintenanceResult(
            success=False,
            message=f"Printer returned HTTP {r.status_code}",
            manual_instructions=_manual_align(),
        )

    def print_quality_report(self) -> MaintenanceResult:
        log.info("print_quality_report() → pqDiagnosticsPage")
        result = self._internal_print(PQ_DIAG)
        if not result.success and not result.manual_instructions:
            result.manual_instructions = _manual_quality_report()
        return result

    def print_test_page(self) -> MaintenanceResult:
        log.info("print_test_page() → demoPage")
        result = self._internal_print(DEMO_PAGE)
        if not result.success and not result.manual_instructions:
            result.manual_instructions = _manual_test_page()
        return result


# ------------------------------------------------------------------ manual fallbacks

def _manual_clean(level: int) -> str:
    label = {1: "Light", 2: "Medium", 3: "Deep"}.get(level, "Light")
    return (
        f"Manual printhead cleaning (level {level} — {label}):\n"
        "  1. On the printer touchscreen: Settings → Printer Maintenance\n"
        "  2. Select 'Clean Printhead'\n"
        f"  3. Choose Level {level} ({label}) and confirm.\n"
        "  4. Wait ~3-5 minutes for the cleaning cycle to complete."
    )


def _manual_align() -> str:
    return (
        "Manual printhead alignment:\n"
        "  1. On the printer touchscreen: Settings → Printer Maintenance\n"
        "  2. Select 'Align Printhead'\n"
        "  3. The printer will print and scan an alignment page automatically.\n"
        "  4. Ensure A4/Letter paper is loaded."
    )


def _manual_quality_report() -> str:
    return (
        "Manual print quality report:\n"
        "  1. On the printer touchscreen: Settings → Reports\n"
        "  2. Select 'Print Quality Report'\n"
        "  3. Inspect the printed page for missing nozzles or colour bands."
    )


def _manual_test_page() -> str:
    return (
        "Manual test page:\n"
        "  1. On the printer touchscreen: Settings → Reports\n"
        "  2. Select 'Printer Status Report' or 'Demo Page'."
    )

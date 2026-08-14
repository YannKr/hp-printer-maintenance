"""Shared test doubles.

No test in this suite opens a socket. Every HTTP call is served by
FakeSession, and the port scanner and mDNS browser are never started.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

import pytest
import requests


@dataclass
class FakeResponse:
    """Stands in for requests.Response."""

    status_code: int = 200
    body: str = ""
    headers: dict = field(default_factory=dict)

    @property
    def content(self) -> bytes:
        return self.body.encode()

    @property
    def text(self) -> str:
        return self.body

    def json(self):
        return json.loads(self.body)


@dataclass
class Call:
    method: str
    url: str
    kwargs: dict


class FakeSession:
    """A requests.Session that answers from a routing table.

    Routes map a URL path to either a FakeResponse, an exception instance to
    raise, or a list of those consumed one per call.
    """

    def __init__(self, routes: dict | None = None, default: object = None) -> None:
        self.routes = routes or {}
        self.default = default if default is not None else FakeResponse(404, "")
        self.headers: dict = {}
        self.calls: list[Call] = []

    def _answer(self, method: str, url: str, kwargs: dict):
        self.calls.append(Call(method, url, kwargs))
        path = url.split("://", 1)[-1].split("/", 1)
        path = "/" + path[1] if len(path) > 1 else "/"
        entry = self.routes.get(path, self.default)
        if isinstance(entry, list):
            entry = entry.pop(0) if entry else self.default
        if isinstance(entry, Exception):
            raise entry
        return entry

    def get(self, url, **kwargs):
        return self._answer("GET", url, kwargs)

    def post(self, url, **kwargs):
        return self._answer("POST", url, kwargs)

    def paths(self, method: str | None = None) -> list[str]:
        return [
            c.url.split("://", 1)[-1].split("/", 1)[-1]
            for c in self.calls
            if method is None or c.method == method
        ]


@pytest.fixture(autouse=True)
def isolate_environment(tmp_path, monkeypatch):
    """Never read or write the user's real config file or log file."""
    monkeypatch.setenv("HPMAINT_LOG_FILE", str(tmp_path / "logs" / "hpmaint.log"))
    monkeypatch.setenv("HPMAINT_CONFIG", str(tmp_path / "config.toml"))
    for var in ("HPMAINT_PRINTER_IP", "HPMAINT_PRINTER_PASSWORD", "HPMAINT_DEBUG"):
        monkeypatch.delenv(var, raising=False)

    # log.setup() adds a handler per call. Without this, handlers from earlier
    # tests stay attached and a later test's records land in their files too.
    root = logging.getLogger("hpmaint")
    saved = root.handlers[:]
    root.handlers.clear()
    yield
    for handler in root.handlers:
        handler.close()
    root.handlers[:] = saved


@pytest.fixture
def no_sleep(monkeypatch):
    """Make every retry and gap instant, and record what was waited on."""
    waited: list[float] = []

    def fake_sleep(secs):
        waited.append(secs)

    monkeypatch.setattr("time.sleep", fake_sleep)
    return waited


@pytest.fixture
def client_factory(monkeypatch):
    """Build an EWSClient whose session is a FakeSession."""
    from hpmaint.ews import EWSClient

    def make(routes=None, default=None, password="", **kw):
        client = EWSClient(ip="192.0.2.1", password=password, **kw)
        session = FakeSession(routes=routes, default=default)
        client._session = session
        return client, session

    return make


# Handy canned bodies.

CAP_XML_STANDARD = """<?xml version="1.0" encoding="UTF-8"?>
<ipcap:InternalPrintCap xmlns:ipcap="http://www.hp.com/x">
  <ipcap:InternalPrintJob><ipcap:JobType>cleaningPage</ipcap:JobType></ipcap:InternalPrintJob>
  <ipcap:InternalPrintJob><ipcap:JobType>cleaningPageLevel2</ipcap:JobType></ipcap:InternalPrintJob>
  <ipcap:InternalPrintJob><ipcap:JobType>demoPage</ipcap:JobType></ipcap:InternalPrintJob>
</ipcap:InternalPrintCap>
"""

# Same information, element cased differently by other firmware. Note the
# lowercase j: this does not end in "JobType", which is what the old scan
# required, so every job used to be refused.
CAP_XML_VARIANT = """<?xml version="1.0" encoding="UTF-8"?>
<InternalPrintCap>
  <jobType>cleaningPage</jobType>
  <jobType>demoPage</jobType>
</InternalPrintCap>
"""

# Nothing recognisable at all.
CAP_XML_UNKNOWN = """<?xml version="1.0" encoding="UTF-8"?>
<Capabilities><Thing>cleaningPage</Thing></Capabilities>
"""

CONSUMABLES_XML = """<?xml version="1.0" encoding="UTF-8"?>
<ccdyn:ConsumableConfigDyn xmlns:ccdyn="http://www.hp.com/x">
  <ccdyn:ConsumableInfo>
    <ccdyn:ConsumableLabelCode>K</ccdyn:ConsumableLabelCode>
    <ccdyn:ConsumablePercentageLevelRemaining>60</ccdyn:ConsumablePercentageLevelRemaining>
  </ccdyn:ConsumableInfo>
  <ccdyn:ConsumableInfo>
    <ccdyn:ConsumableLabelCode>CMY</ccdyn:ConsumableLabelCode>
    <ccdyn:ConsumablePercentageLevelRemaining>15</ccdyn:ConsumablePercentageLevelRemaining>
  </ccdyn:ConsumableInfo>
</ccdyn:ConsumableConfigDyn>
"""


def timeout_error() -> Exception:
    return requests.Timeout("read timed out")


def connection_error() -> Exception:
    return requests.ConnectionError("connection reset")

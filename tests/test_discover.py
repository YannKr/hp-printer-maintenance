"""Fault 8 — identify HP devices, not everything on the LAN.

No socket is opened here: the only HTTP call is monkeypatched.
"""

from __future__ import annotations

import pytest

from hpmaint.discover import _is_hp_ews, _looks_hp, _mdns_is_hp, _prop_strings


@pytest.mark.parametrize("text", [
    "<html><title>HP ENVY Photo 7855</title></html>",
    "Hewlett-Packard OfficeJet Pro 9015",
    "hewlett packard",
    '<script src="/DevMgmt/Unified.js"></script>',
    "HP HTTP Server; HP ENVY Photo 7800 series",
    "DESKJET 3630",
])
def test_real_hp_pages_match(text):
    assert _looks_hp(text) is True


@pytest.mark.parametrize("name", [
    "HPF3D024",                  # no word boundary between P and F
    "HP0123AB",
    "hpf3d024.local",
    "HP-Print-7855",
    "hp-setup",
    "NPI123456",                 # JetDirect / LaserJet naming
    "npiA1B2C3",
    "NPI123456._pdl-datastream._tcp.local.",
])
def test_real_hp_hostnames_match(name):
    """A trailing word boundary would lose all of these."""
    assert _looks_hp(name) is True


@pytest.mark.parametrize("text", [
    "<html><title>phpMyAdmin</title></html>",         # the reported fault
    "php admin panel",
    "PHP Version 8.3.2",
    "index.php",
    "xhprof profiler",
    "graphql php shop",
    "Chromecast Ultra",
    "Synology DiskStation",
    "OpenWrt LuCI",
    "Canon Embedded Web Server",                       # other vendor, same phrase
    "",
])
def test_non_hp_pages_do_not_match(text):
    assert _looks_hp(text) is False


def test_prop_strings_decodes_mdns_txt_records():
    props = {b"ty": b"HP ENVY Photo 7800 series", b"usb_MFG": b"HP", b"note": None}
    assert _looks_hp(*_prop_strings(props)) is True


def test_chromecast_txt_record_is_rejected():
    props = {b"md": b"Chromecast Ultra", b"fn": b"Living Room"}
    assert _looks_hp("Living Room._http._tcp.local.", *_prop_strings(props)) is False


# ---------------------------------------------------------------- mDNS decision


def test_ipp_txt_usb_mfg_is_the_reliable_path():
    """usb_MFG=HP identifies the printer even when nothing else does."""
    props = {b"usb_MFG": b"HP", b"usb_MDL": b"Printer", b"rp": b"ipp/print"}
    assert _mdns_is_hp("Office Printer._ipp._tcp.local.", props) is True


def test_ipp_txt_ty_field_is_used():
    props = {b"ty": b"HP ENVY Photo 7800 series", b"rp": b"ipp/print"}
    assert _mdns_is_hp("Printer._ipp._tcp.local.", props) is True


def test_hewlett_packard_spelled_out_in_txt():
    props = {b"MFG": b"Hewlett-Packard"}
    assert _mdns_is_hp("Some Printer._ipp._tcp.local.", props) is True


def test_service_name_is_the_fallback_without_a_txt_record():
    assert _mdns_is_hp("HPF3D024._http._tcp.local.", {}) is True


def test_other_vendor_ipp_advert_is_rejected():
    props = {b"usb_MFG": b"Brother", b"ty": b"Brother HL-L2350DW", b"rp": b"ipp/print"}
    assert _mdns_is_hp("Brother HL-L2350DW._ipp._tcp.local.", props) is False


def test_chromecast_http_advert_is_rejected():
    props = {b"md": b"Chromecast Ultra", b"fn": b"Living Room"}
    assert _mdns_is_hp("Living Room._http._tcp.local.", props) is False


def test_nas_http_advert_is_rejected():
    props = {b"model": b"DiskStation DS920+", b"vendor": b"Synology"}
    assert _mdns_is_hp("DiskStation._http._tcp.local.", props) is False


class _FakeHTTPResponse:
    def __init__(self, text, server="", status_code=200):
        self.text = text
        self.status_code = status_code
        self.headers = {"Server": server} if server else {}


def _patch_requests_get(monkeypatch, root, ledm=None):
    """Serve the landing page and the LEDM probe from canned responses."""
    import requests

    ledm = ledm if ledm is not None else _FakeHTTPResponse("", status_code=404)
    seen = []

    def fake_get(url, **kwargs):
        assert url.startswith("http://192.0.2."), url
        seen.append(url)
        return ledm if "/DevMgmt/" in url else root

    monkeypatch.setattr(requests, "get", fake_get)
    return seen


def test_php_page_is_no_longer_taken_for_a_printer(monkeypatch):
    _patch_requests_get(monkeypatch, _FakeHTTPResponse("<h1>phpMyAdmin</h1>"))
    assert _is_hp_ews("192.0.2.50") is False


def test_server_header_alone_identifies_the_printer(monkeypatch):
    seen = _patch_requests_get(
        monkeypatch, _FakeHTTPResponse("<html></html>", server="HP HTTP Server")
    )
    assert _is_hp_ews("192.0.2.51") is True
    assert len(seen) == 1                    # no need for the LEDM probe


def test_bare_spa_shell_is_found_by_the_ledm_probe(monkeypatch):
    """nginx SPA shell with no HP string anywhere — the reported blind spot."""
    shell = _FakeHTTPResponse(
        '<!doctype html><div id="app"></div><script src="/framework/Unified.js">'
        "</script>",
        server="nginx",
    )
    ledm = _FakeHTTPResponse(
        '<?xml version="1.0"?><ProductStatusDyn><ProductStatus/></ProductStatusDyn>'
    )
    seen = _patch_requests_get(monkeypatch, shell, ledm)
    assert _is_hp_ews("192.0.2.53") is True
    assert any("/DevMgmt/ProductStatusDyn.xml" in u for u in seen)


def test_ledm_probe_does_not_rescue_a_non_printer(monkeypatch):
    _patch_requests_get(
        monkeypatch,
        _FakeHTTPResponse("<h1>phpMyAdmin</h1>", server="nginx"),
        _FakeHTTPResponse("<h1>404 Not Found</h1>", status_code=404),
    )
    assert _is_hp_ews("192.0.2.54") is False


def test_ledm_probe_ignores_a_soft_404(monkeypatch):
    """A catch-all app returning 200 for everything must not pass."""
    _patch_requests_get(
        monkeypatch,
        _FakeHTTPResponse("<h1>Welcome</h1>", server="nginx"),
        _FakeHTTPResponse("<html>Not found</html>", status_code=200),
    )
    assert _is_hp_ews("192.0.2.55") is False


def test_unreachable_host_is_not_a_printer(monkeypatch):
    import requests

    def boom(url, **kwargs):
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(requests, "get", boom)
    assert _is_hp_ews("192.0.2.52") is False

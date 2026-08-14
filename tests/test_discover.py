"""Fault 8 — identify HP devices, not everything on the LAN.

No socket is opened here: the only HTTP call is monkeypatched.
"""

from __future__ import annotations

import pytest

from hpmaint.discover import _is_hp_ews, _looks_hp, _prop_strings


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


@pytest.mark.parametrize("text", [
    "<html><title>phpMyAdmin</title></html>",         # the reported fault
    "php admin panel",
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


class _FakeHTTPResponse:
    def __init__(self, text, server=""):
        self.text = text
        self.headers = {"Server": server} if server else {}


def _patch_requests_get(monkeypatch, response):
    import requests

    def fake_get(url, **kwargs):
        assert url.startswith("http://192.0.2."), url
        return response

    monkeypatch.setattr(requests, "get", fake_get)


def test_php_page_is_no_longer_taken_for_a_printer(monkeypatch):
    _patch_requests_get(monkeypatch, _FakeHTTPResponse("<h1>phpMyAdmin</h1>"))
    assert _is_hp_ews("192.0.2.50") is False


def test_server_header_alone_identifies_the_printer(monkeypatch):
    _patch_requests_get(
        monkeypatch, _FakeHTTPResponse("<html></html>", server="HP HTTP Server")
    )
    assert _is_hp_ews("192.0.2.51") is True


def test_unreachable_host_is_not_a_printer(monkeypatch):
    import requests

    def boom(url, **kwargs):
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(requests, "get", boom)
    assert _is_hp_ews("192.0.2.52") is False

"""Redirect handling: follow a same-host HTTPS upgrade, refuse everything else.

Nothing here opens a socket. The "https" requests are served by the same fake
session as the http ones.
"""

from __future__ import annotations

import pytest
from conftest import CONSUMABLES_XML, FakeResponse

from hpmaint.ews import _https_upgrade

SRC = "http://192.0.2.1:80/DevMgmt/ConsumableConfigDyn.xml"


# ---------------------------------------------------------------- the rule


def test_same_host_upgrade_is_accepted():
    target = _https_upgrade(SRC, "https://192.0.2.1/DevMgmt/ConsumableConfigDyn.xml")
    assert target == "https://192.0.2.1/DevMgmt/ConsumableConfigDyn.xml"


def test_explicit_443_is_accepted():
    assert _https_upgrade(
        SRC, "https://192.0.2.1:443/DevMgmt/ConsumableConfigDyn.xml"
    ) is not None


def test_host_is_compared_case_insensitively():
    src = "http://printer.local/AuthChk"
    assert _https_upgrade(src, "https://PRINTER.LOCAL/AuthChk") is not None


@pytest.mark.parametrize("location, why", [
    ("https://evil.example/DevMgmt/ConsumableConfigDyn.xml", "different host"),
    ("https://192.0.2.99/DevMgmt/ConsumableConfigDyn.xml",   "different address"),
    ("http://192.0.2.1/DevMgmt/ConsumableConfigDyn.xml",     "no upgrade"),
    ("http://evil.example/",                                 "downgrade elsewhere"),
    ("https://192.0.2.1/somewhere/else",                     "different path"),
    ("https://192.0.2.1/DevMgmt/ConsumableConfigDyn.xml?x=1", "query added"),
    ("https://192.0.2.1:8443/DevMgmt/ConsumableConfigDyn.xml", "odd port"),
    ("https://user:pw@192.0.2.1/DevMgmt/ConsumableConfigDyn.xml", "userinfo"),
    ("/DevMgmt/ConsumableConfigDyn.xml",                     "relative, stays http"),
    ("",                                                     "no Location"),
])
def test_everything_else_is_refused(location, why):
    assert _https_upgrade(SRC, location) is None, why


def test_https_source_is_never_upgraded_again():
    src = "https://192.0.2.1/AuthChk"
    assert _https_upgrade(src, "https://192.0.2.1/AuthChk") is None


# ---------------------------------------------------------------- GET path


def _redirect_to(location):
    return FakeResponse(301, "", {"Location": location})


def test_get_follows_a_same_host_https_upgrade(client_factory):
    client, session = client_factory(routes={
        "/DevMgmt/ConsumableConfigDyn.xml": [
            _redirect_to("https://192.0.2.1/DevMgmt/ConsumableConfigDyn.xml"),
            FakeResponse(200, CONSUMABLES_XML),
        ],
    })
    levels = client.get_ink_levels()
    assert [l.level_pct for l in levels] == [60, 15]

    assert session.calls[0].url.startswith("http://")
    assert session.calls[1].url == "https://192.0.2.1/DevMgmt/ConsumableConfigDyn.xml"
    assert session.calls[1].kwargs["allow_redirects"] is False


def test_upgraded_get_does_not_verify_the_certificate(client_factory):
    """Printer certificates are self-signed. This is deliberate and documented."""
    client, session = client_factory(routes={
        "/DevMgmt/ConsumableConfigDyn.xml": [
            _redirect_to("https://192.0.2.1/DevMgmt/ConsumableConfigDyn.xml"),
            FakeResponse(200, CONSUMABLES_XML),
        ],
    })
    client.get_ink_levels()
    assert session.calls[1].kwargs["verify"] is False


def test_plain_http_requests_never_pass_verify_false(client_factory):
    client, session = client_factory(
        routes={"/DevMgmt/ConsumableConfigDyn.xml":
                FakeResponse(200, CONSUMABLES_XML)},
    )
    client.get_ink_levels()
    assert all("verify" not in c.kwargs for c in session.calls)


def test_get_refuses_a_cross_host_redirect(client_factory):
    client, session = client_factory(routes={
        "/DevMgmt/ConsumableConfigDyn.xml": [
            _redirect_to("https://evil.example/DevMgmt/ConsumableConfigDyn.xml"),
            FakeResponse(200, CONSUMABLES_XML),
        ],
    })
    assert client.get_ink_levels() == []
    assert len(session.calls) == 1                      # never chased


def test_get_refuses_a_downgrade(client_factory):
    client, session = client_factory(routes={
        "/DevMgmt/ConsumableConfigDyn.xml": [
            _redirect_to("http://192.0.2.1/elsewhere"),
            FakeResponse(200, CONSUMABLES_XML),
        ],
    })
    assert client.get_ink_levels() == []
    assert len(session.calls) == 1


def test_authchk_upgrade_still_authenticates(client_factory):
    """A printer that sends the whole EWS to https must still work."""
    client, session = client_factory(
        routes={
            "/": FakeResponse(200, "HP"),
            "/AuthChk": [
                _redirect_to("https://192.0.2.1/AuthChk"),
                FakeResponse(200, '{"hasAuth": true}'),
                _redirect_to("https://192.0.2.1/AuthChk"),
                FakeResponse(200, "", {"X-Auth-Counter": "9"}),
            ],
        },
        password="pw",
    )
    assert client.probe() is True
    assert client._authenticated is True
    assert client._auth_counter == 9


# ---------------------------------------------------------------- POST path


def test_post_follows_a_same_host_https_upgrade(client_factory):
    from conftest import CAP_XML_STANDARD

    client, session = client_factory(routes={
        "/DevMgmt/InternalPrintCap.xml": FakeResponse(200, CAP_XML_STANDARD),
        "/DevMgmt/InternalPrintDyn.xml": [
            _redirect_to("https://192.0.2.1/DevMgmt/InternalPrintDyn.xml"),
            FakeResponse(201, ""),
        ],
    })
    assert client.clean_printhead(level=1).success is True

    posts = [c for c in session.calls if c.method == "POST"]
    assert len(posts) == 2
    assert posts[1].url.startswith("https://192.0.2.1/")
    assert posts[1].kwargs["verify"] is False
    # nginx CSRF check: Origin/Referer must name the scheme actually used.
    assert posts[1].kwargs["headers"]["Origin"] == "https://192.0.2.1"
    assert posts[1].kwargs["headers"]["Referer"] == "https://192.0.2.1/"


def test_post_refuses_a_cross_host_redirect(client_factory):
    from conftest import CAP_XML_STANDARD

    client, session = client_factory(routes={
        "/DevMgmt/InternalPrintCap.xml": FakeResponse(200, CAP_XML_STANDARD),
        "/DevMgmt/InternalPrintDyn.xml": [
            _redirect_to("https://evil.example/DevMgmt/InternalPrintDyn.xml"),
            FakeResponse(201, ""),
        ],
    })
    result = client.clean_printhead(level=1)
    assert result.success is False
    assert "301" in result.message
    assert len([c for c in session.calls if c.method == "POST"]) == 1


def test_post_upgrade_preserves_the_auth_counter(client_factory):
    from conftest import CAP_XML_STANDARD

    client, session = client_factory(routes={
        "/DevMgmt/InternalPrintCap.xml": FakeResponse(200, CAP_XML_STANDARD),
        "/DevMgmt/InternalPrintDyn.xml": [
            _redirect_to("https://192.0.2.1/DevMgmt/InternalPrintDyn.xml"),
            FakeResponse(201, ""),
        ],
    })
    client._auth_counter = 31
    client.clean_printhead(level=1)
    posts = [c for c in session.calls if c.method == "POST"]
    assert posts[1].kwargs["headers"]["X-Auth-Counter"] == "31"

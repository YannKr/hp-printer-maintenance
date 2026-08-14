"""Faults 2 and 3 — authentication and redirect handling."""

from __future__ import annotations

from conftest import FakeResponse


def _routes(authchk):
    return {"/": FakeResponse(200, "<html>HP</html>"), "/AuthChk": authchk}


def test_authenticates_on_200_with_has_auth(client_factory):
    """The whole point of fault 2: 200 {"hasAuth": true} must authenticate."""
    client, session = client_factory(
        routes=_routes([
            FakeResponse(200, '{"hasAuth": true}'),
            FakeResponse(200, "", {"X-Auth-Counter": "77"}),
        ]),
        password="pw",
    )
    assert client.probe() is True
    assert client._has_auth is True
    assert client._authenticated is True
    assert client._auth_counter == 77
    # Two /AuthChk calls: the check, then the Digest handshake.
    assert session.paths("GET").count("AuthChk") == 2


def test_authenticates_on_401(client_factory):
    client, session = client_factory(
        routes=_routes([
            FakeResponse(401, ""),
            FakeResponse(200, "", {"X-Auth-Counter": "5"}),
        ]),
        password="pw",
    )
    assert client.probe() is True
    assert client._authenticated is True
    assert client._auth_counter == 5


def test_no_handshake_when_has_auth_false(client_factory):
    client, session = client_factory(
        routes=_routes(FakeResponse(200, '{"hasAuth": false}')),
        password="pw",
    )
    assert client.probe() is True
    assert client._has_auth is False
    assert client._authenticated is False
    assert session.paths("GET").count("AuthChk") == 1


def test_no_handshake_without_a_password(client_factory):
    client, session = client_factory(
        routes=_routes(FakeResponse(200, '{"hasAuth": true}')),
        password="",
    )
    assert client.probe() is True
    assert client._has_auth is True
    assert client._authenticated is False
    assert session.paths("GET").count("AuthChk") == 1


def test_wrong_password_is_recorded(client_factory):
    client, _ = client_factory(
        routes=_routes([FakeResponse(200, '{"hasAuth": true}'), FakeResponse(401, "")]),
        password="wrong",
    )
    client.probe()
    assert client._authenticated is False


def test_auth_counter_header_is_sent_on_post(client_factory):
    from conftest import CAP_XML_STANDARD

    client, session = client_factory(
        routes={
            "/": FakeResponse(200, "HP"),
            "/AuthChk": [
                FakeResponse(200, '{"hasAuth": true}'),
                FakeResponse(200, "", {"X-Auth-Counter": "42"}),
            ],
            "/DevMgmt/InternalPrintCap.xml": FakeResponse(200, CAP_XML_STANDARD),
            "/DevMgmt/InternalPrintDyn.xml": FakeResponse(201, ""),
        },
        password="pw",
    )
    client.probe()
    assert client.clean_printhead(level=1).success is True
    post = [c for c in session.calls if c.method == "POST"][0]
    assert post.kwargs["headers"]["X-Auth-Counter"] == "42"


# ---------------------------------------------------------------- fault 2, messages


def _client_with_state(client_factory, password, authenticated):
    client, _ = client_factory(
        routes={
            "/DevMgmt/InternalPrintCap.xml": FakeResponse(404, ""),
            "/DevMgmt/InternalPrintDyn.xml": FakeResponse(403, "denied"),
        },
        password=password,
    )
    client._has_auth = bool(password)
    client._authenticated = authenticated
    return client


def test_403_without_password_says_configure(client_factory):
    client = _client_with_state(client_factory, "", False)
    result = client.clean_printhead(level=1)
    assert result.success is False
    assert "no EWS password configured" in result.message
    assert "hpmaint.py configure" in result.manual_instructions


def test_403_with_rejected_password_says_so(client_factory):
    client = _client_with_state(client_factory, "wrong", False)
    result = client.clean_printhead(level=1)
    assert "rejected the configured EWS password" in result.message


def test_403_after_successful_auth_does_not_tell_user_to_configure(client_factory):
    """The misleading message: telling a user to run configure when they did."""
    client = _client_with_state(client_factory, "right", True)
    result = client.clean_printhead(level=1)
    assert "authenticated session" in result.message
    assert "configure" not in result.manual_instructions


# ---------------------------------------------------------------- fault 3, redirects


def test_authenticated_calls_do_not_follow_redirects(client_factory):
    client, session = client_factory(
        routes={
            "/": FakeResponse(200, "HP"),
            "/AuthChk": [
                FakeResponse(200, '{"hasAuth": true}'),
                FakeResponse(200, "", {"X-Auth-Counter": "1"}),
            ],
            "/DevMgmt/InternalPrintCap.xml": FakeResponse(200, "<a/>"),
            "/DevMgmt/InternalPrintDyn.xml": FakeResponse(201, ""),
        },
        password="pw",
    )
    client.probe()
    client.clean_printhead(level=1)

    for call in session.calls:
        if call.url.endswith("192.0.2.1:80/"):
            # The one unauthenticated reachability probe may still redirect.
            continue
        assert call.kwargs["allow_redirects"] is False, call.url


def test_redirect_is_not_treated_as_a_successful_get(client_factory):
    client, _ = client_factory(
        routes={"/DevMgmt/ConsumableConfigDyn.xml":
                FakeResponse(302, "", {"Location": "http://evil.example/"})},
    )
    assert client.get_ink_levels() == []

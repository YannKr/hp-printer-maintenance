"""Faults 6 and 7 — retries, and capability parsing."""

from __future__ import annotations

from conftest import (
    CAP_XML_STANDARD,
    CAP_XML_UNKNOWN,
    CAP_XML_VARIANT,
    CONSUMABLES_XML,
    FakeResponse,
    connection_error,
    timeout_error,
)


def _job_routes(cap_body, dyn):
    return {
        "/DevMgmt/InternalPrintCap.xml": FakeResponse(200, cap_body),
        "/DevMgmt/InternalPrintDyn.xml": dyn,
    }


# ---------------------------------------------------------------- fault 7


def test_known_job_type_is_accepted(client_factory):
    client, _ = client_factory(_job_routes(CAP_XML_STANDARD, FakeResponse(201, "")))
    assert client.clean_printhead(level=1).success is True


def test_unsupported_job_type_is_still_refused(client_factory):
    client, session = client_factory(
        _job_routes(CAP_XML_STANDARD, FakeResponse(201, ""))
    )
    result = client.print_quality_report()   # pqDiagnosticsPage is not listed
    assert result.success is False
    assert "does not support" in result.message
    assert session.paths("POST") == []


def test_element_naming_variant_is_understood(client_factory):
    """InternalPrintJobType, not JobType — used to refuse everything."""
    client, session = client_factory(
        _job_routes(CAP_XML_VARIANT, FakeResponse(201, ""))
    )
    assert client.clean_printhead(level=1).success is True
    assert session.paths("POST") == ["DevMgmt/InternalPrintDyn.xml"]


def test_unrecognised_capability_xml_permits_the_attempt(client_factory):
    """Fall back to letting the printer decide, not to refusing."""
    client, session = client_factory(
        _job_routes(CAP_XML_UNKNOWN, FakeResponse(201, ""))
    )
    result = client.clean_printhead(level=2)
    assert result.success is True
    assert session.paths("POST") == ["DevMgmt/InternalPrintDyn.xml"]


def test_unreadable_capability_xml_permits_the_attempt(client_factory):
    client, _ = client_factory(_job_routes("not xml at all", FakeResponse(201, "")))
    assert client.clean_printhead(level=1).success is True


def test_missing_capability_xml_permits_the_attempt(client_factory):
    client, _ = client_factory({
        "/DevMgmt/InternalPrintCap.xml": FakeResponse(404, ""),
        "/DevMgmt/InternalPrintDyn.xml": FakeResponse(201, ""),
    })
    assert client.clean_printhead(level=1).success is True


def test_job_type_matching_is_case_insensitive(client_factory):
    cap = CAP_XML_STANDARD.replace("cleaningPage", "CLEANINGPAGE")
    client, _ = client_factory(_job_routes(cap, FakeResponse(201, "")))
    assert client.clean_printhead(level=1).success is True


# ---------------------------------------------------------------- fault 6


def test_timeout_is_retried(client_factory, no_sleep):
    client, session = client_factory(
        _job_routes(
            CAP_XML_STANDARD,
            [timeout_error(), timeout_error(), FakeResponse(201, "")],
        )
    )
    result = client.clean_printhead(level=1)
    assert result.success is True
    assert session.paths("POST").count("DevMgmt/InternalPrintDyn.xml") == 3
    assert no_sleep == [60.0, 60.0]


def test_connection_error_is_retried(client_factory, no_sleep):
    client, session = client_factory(
        _job_routes(CAP_XML_STANDARD, [connection_error(), FakeResponse(201, "")])
    )
    assert client.clean_printhead(level=1).success is True
    assert session.paths("POST").count("DevMgmt/InternalPrintDyn.xml") == 2


def test_retries_are_bounded(client_factory, no_sleep):
    client, session = client_factory(
        _job_routes(CAP_XML_STANDARD, [timeout_error() for _ in range(10)])
    )
    result = client.clean_printhead(level=1)
    assert result.success is False
    assert result.message == "No response from printer"
    # retries_on_503=3 means 4 attempts in total.
    assert session.paths("POST").count("DevMgmt/InternalPrintDyn.xml") == 4


def test_503_is_still_retried(client_factory, no_sleep):
    client, session = client_factory(
        _job_routes(
            CAP_XML_STANDARD, [FakeResponse(503, "busy"), FakeResponse(201, "")]
        )
    )
    assert client.clean_printhead(level=1).success is True
    assert session.paths("POST").count("DevMgmt/InternalPrintDyn.xml") == 2


def test_alignment_timeout_is_retried(client_factory, no_sleep):
    client, session = client_factory(
        {
            "/Calibration/State": FakeResponse(404, ""),
            "/Calibration/Session": [timeout_error(), FakeResponse(200, "")],
        }
    )
    assert client.align_printhead().success is True
    assert session.paths("POST").count("Calibration/Session") == 2


# ---------------------------------------------------------------- ink parsing


def test_ink_levels_are_parsed(client_factory):
    client, _ = client_factory(
        {"/DevMgmt/ConsumableConfigDyn.xml": FakeResponse(200, CONSUMABLES_XML)}
    )
    levels = client.get_ink_levels()
    assert [(l.color, l.level_pct) for l in levels] == [
        ("black", 60), ("tricolor", 15),
    ]

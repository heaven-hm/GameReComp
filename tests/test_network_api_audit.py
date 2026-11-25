"""Issue #12: fail-closed network API status with import+subsystem attribution."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.network_api_audit import decide, main, sha256_file  # noqa: E402


def _trace(tmp_path, name="capture.bin", payload=b"loopback-wire-bytes"):
    path = tmp_path / name
    path.write_bytes(payload)
    return path


def _import(dll, symbol=None, module="fixture.exe", subsystem=None, **extra):
    record = {"module": module, "dll": dll}
    if symbol is not None:
        record["symbol"] = symbol
    if subsystem is not None:
        record["subsystem"] = subsystem
    record.update(extra)
    return record


def _imports(*records):
    return {"source": "unit fixture", "records": list(records)}


def _selftests(*results):
    return {"source": "unit fixture", "results": list(results)}


def _loopback(trace, api="winsock2_overlapped", **overrides):
    result = {
        "id": "ws2-loopback-01",
        "api": api,
        "scenario": "loopback",
        "status": "pass",
        "real": True,
        "loopback_address": "127.0.0.1",
        "port": 45111,
        "bound": True,
        "connected": True,
        "bytes_exchanged": 96,
        "sha256": sha256_file(trace),
        "trace_path": str(trace),
    }
    result.update(overrides)
    return result


def _multi_process(trace, api="winsock2_overlapped", **overrides):
    result = _loopback(trace, api=api, scenario="multi_process",
                       id="ws2-two-process-01", **overrides)
    result.setdefault("processes", [
        {"id": "guest-server-1", "role": "server"},
        {"id": "guest-client-2", "role": "client"},
    ])
    return result


def _api(report, name):
    return next(item for item in report["apis"] if item["api"] == name)


def test_registration_alone_never_yields_supported():
    report = decide(
        _imports(_import("ws2_32.dll", "WSARecv")),
        _selftests({"id": "registry", "api": "winsock2_overlapped",
                    "scenario": "registration", "status": "pass",
                    "real": True, "registered": True}),
    )
    result = _api(report, "winsock2_overlapped")
    assert result["verdict"] == "DEFERRED"
    assert "REGISTRATION_ONLY" in result["reasons"]
    assert result["evidence"]["hash_verified"] is False
    assert report["verdict"] != "SUPPORTED"
    assert all(item["verdict"] != "SUPPORTED" for item in report["apis"])


def test_import_mentions_only_deferred_with_exact_attribution():
    report = decide(
        _imports(_import("dplayx.dll", "DirectPlayCreate",
                         clsid="AAE1F000-0000-0000-C000-000000000046")),
        _selftests({"id": "census", "api": "directplay",
                    "scenario": "import_mentions"}),
    )
    result = _api(report, "directplay")
    assert result["verdict"] == "DEFERRED"
    assert result["attribution"]["subsystem"] == "directplay"
    assert result["attribution"]["dlls"] == ["dplayx.dll"]
    assert result["attribution"]["records"][0]["clsid"] == \
        "AAE1F000-0000-0000-C000-000000000046"
    assert "DirectPlay LAN session not demonstrated" in result["limits"] or \
        "not a legacy multiplayer game oracle" in result["limits"]


def test_real_loopback_with_verified_hash_is_supported(tmp_path):
    trace = _trace(tmp_path)
    report = decide(
        _imports(_import("ws2_32.dll", "WSARecv", subsystem="winsock2")),
        _selftests(_loopback(trace)),
    )
    result = _api(report, "winsock2_overlapped")
    assert result["verdict"] == "SUPPORTED"
    assert result["reasons"] == []
    assert result["evidence"]["hash_verified"] is True
    assert result["attribution"]["records"][0]["symbol"] == "WSARecv"
    assert report["verdict"] == "SUPPORTED"


def test_real_multi_process_with_distinct_processes_is_supported(tmp_path):
    trace = _trace(tmp_path)
    report = decide(
        _imports(_import("ws2_32.dll", "WSARecv")),
        _selftests(_multi_process(trace)),
    )
    result = _api(report, "winsock2_overlapped")
    assert result["verdict"] == "SUPPORTED"
    assert result["evidence"]["qualifying_scenarios"] == 1


def test_reused_process_identity_is_rejected(tmp_path):
    trace = _trace(tmp_path)
    report = decide(
        _imports(_import("ws2_32.dll", "WSARecv")),
        _selftests(_multi_process(trace, processes=[
            {"id": "same-process", "role": "server"},
            {"id": "same-process", "role": "client"},
        ])),
    )
    result = _api(report, "winsock2_overlapped")
    assert result["verdict"] == "UNSUPPORTED"
    assert "NON_DISTINCT_PROCESSES" in result["reasons"]


def test_fake_loopback_is_rejected(tmp_path):
    trace = _trace(tmp_path)
    report = decide(
        _imports(_import("ws2_32.dll", "WSARecv")),
        _selftests(_loopback(
            trace, synthetic=True, real=False,
            loopback_address="8.8.8.8", bytes_exchanged=0,
        )),
    )
    result = _api(report, "winsock2_overlapped")
    assert result["verdict"] == "UNSUPPORTED"
    assert {"FAKE_LOOPBACK", "SYNTHETIC_SCENARIO", "NON_LOOPBACK_ADDRESS",
            "NO_BYTES_EXCHANGED"}.issubset(set(result["reasons"]))
    assert report["verdict"] == "UNSUPPORTED"


def test_missing_hash_is_deferred_not_supported(tmp_path):
    trace = _trace(tmp_path)
    result = decide(
        _imports(_import("ws2_32.dll", "WSARecv")),
        _selftests(_loopback(trace, sha256=None, trace_path=None)),
    )
    api = _api(result, "winsock2_overlapped")
    assert api["verdict"] == "DEFERRED"
    assert {"NO_HASH", "NO_TRACE"}.issubset(set(api["reasons"]))
    assert api["evidence"]["hash_verified"] is False


def test_tampered_trace_is_detected_and_rejected(tmp_path):
    trace = _trace(tmp_path)
    scenario = _loopback(trace)
    imports = _imports(_import("ws2_32.dll", "WSARecv"))
    assert _api(decide(imports, _selftests(scenario)),
                "winsock2_overlapped")["verdict"] == "SUPPORTED"

    trace.write_bytes(b"tampered-wire-bytes")
    report = decide(imports, _selftests(scenario))
    result = _api(report, "winsock2_overlapped")
    assert result["verdict"] == "UNSUPPORTED"
    assert "HASH_MISMATCH" in result["reasons"]
    assert report["tamper_detected"] is True


def test_expected_input_hash_mismatch_blocks_all_support(tmp_path, capsys):
    imports_file = tmp_path / "imports.json"
    selftests_file = tmp_path / "selftests.json"
    expected_file = tmp_path / "expected.json"
    out_file = tmp_path / "report.json"
    trace = _trace(tmp_path)

    imports_file.write_text(json.dumps(
        _imports(_import("ws2_32.dll", "WSARecv"))))
    selftests_file.write_text(json.dumps(_selftests(_loopback(trace))))
    expected_file.write_text(json.dumps({
        "imports": "0" * 64,
        "selftests": sha256_file(selftests_file),
    }))

    code = main(["--imports", str(imports_file),
                 "--selftests", str(selftests_file),
                 "--expected-hashes", str(expected_file),
                 "--json-out", str(out_file)])
    report = json.loads(out_file.read_text())
    assert code == 1
    assert "EXPECTED_IMPORTS_HASH_MISMATCH" in report["reasons"]
    assert report["tamper_detected"] is True
    assert all(item["verdict"] != "SUPPORTED" for item in report["apis"])
    assert json.loads(capsys.readouterr().out)["verdict"] == "UNSUPPORTED"


def test_external_service_is_unavailable_not_supported():
    report = decide(
        _imports(_import("wininet.dll", "InternetOpen", subsystem="external_service")),
        _selftests(),
    )
    result = _api(report, "external_service")
    assert result["verdict"] == "UNSUPPORTED"
    assert "EXTERNAL_SERVICE_UNAVAILABLE" in result["reasons"]


def test_scenario_does_not_cross_api_boundaries(tmp_path):
    trace = _trace(tmp_path)
    report = decide(
        _imports(_import("ws2_32.dll", "WSARecv"),
                 _import("dplayx.dll", "DirectPlayCreate")),
        _selftests(_loopback(trace, api="winsock2_overlapped")),
    )
    assert _api(report, "winsock2_overlapped")["verdict"] == "SUPPORTED"
    directplay = _api(report, "directplay")
    assert directplay["verdict"] == "DEFERRED"
    assert "NO_REAL_SCENARIO" in directplay["reasons"]


def test_dll_level_mention_still_yields_deferred_attribution():
    report = decide(_imports(_import("wsock32.dll", subsystem="winsock1")),
                    _selftests())
    result = _api(report, "winsock1_compat")
    assert result["verdict"] == "DEFERRED"
    assert result["attribution"]["dlls"] == ["wsock32.dll"]
    assert result["attribution"]["import_count"] == 1


def test_ordinal_and_protocol_attribution_preserved():
    report = decide(
        _imports(_import("ws2_32.dll", "WSAIoctl", ordinal=1001, protocol="ipx")),
        _selftests(),
    )
    result = _api(report, "ipx_spx")
    assert result["verdict"] == "DEFERRED"
    assert result["attribution"]["records"][0]["ordinal"] == 1001
    assert result["attribution"]["records"][0]["protocol"] == "ipx"


def test_cli_records_input_hashes_and_returns_zero_for_deferred(tmp_path, capsys):
    imports_file = tmp_path / "imports.json"
    selftests_file = tmp_path / "selftests.json"
    out_file = tmp_path / "report.json"
    imports_file.write_text(json.dumps(
        _imports(_import("ws2_32.dll", "WSARecv"))))
    selftests_file.write_text(json.dumps(_selftests()))

    code = main(["--imports", str(imports_file),
                 "--selftests", str(selftests_file),
                 "--json-out", str(out_file)])
    report = json.loads(out_file.read_text())
    assert code == 0
    assert report["input_hashes"]["imports"] == sha256_file(imports_file)
    assert report["input_hashes"]["selftests"] == sha256_file(selftests_file)
    assert report["verdict"] == "DEFERRED"
    assert json.loads(capsys.readouterr().out) == report


def test_malformed_input_is_blocked(tmp_path):
    imports_file = tmp_path / "imports.json"
    selftests_file = tmp_path / "selftests.json"
    out_file = tmp_path / "report.json"
    imports_file.write_text("{not-json")
    selftests_file.write_text(json.dumps(_selftests()))

    code = main(["--imports", str(imports_file),
                 "--selftests", str(selftests_file),
                 "--json-out", str(out_file)])
    report = json.loads(out_file.read_text())
    assert code == 2
    assert report["verdict"] == "BLOCKED"
    assert report["reasons"][0].startswith("INVALID_INPUT:")


def test_empty_supplied_evidence_is_unsupported():
    report = decide({"records": []}, {"results": []})
    assert report["verdict"] == "UNSUPPORTED"
    assert "NO_OBSERVED_NETWORK_API" in report["reasons"]
    assert report["apis"] == []

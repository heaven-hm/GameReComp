#!/usr/bin/env python3
"""Fail-closed network API audit for GitHub issue #12.

This CLI reads ONLY CLI-supplied import records and selftest results. It does
not scan imports itself, register shims, open sockets, or run a game. Its
purpose is to keep diagnostics honest:

  SUPPORTED   - emitted only for a *real* loopback or multi-process selftest
                whose hash-identified trace verifies byte for byte.
  DEFERRED    - demand/registration is visible but no qualifying scenario
                with verified hashes was supplied.
  UNSUPPORTED - a purported proof was rejected (synthetic/fake loopback,
                hash mismatch, tampered input) or the API is an out-of-scope
                external service.

Every non-SUPPORTED verdict carries exact dll/symbol/ordinal/GUID/subsystem
attribution from the supplied records. Registration, an import mention, or a
claimed loopback can never produce SUPPORTED. No legacy multiplayer support is
promised by this tool.
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import sys
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1.0.0"
ISSUE_NUMBER = 12

# Generic API families under audit. Presence in this catalogue is NOT a claim
# that any of them are implemented; it only names what a verdict refers to.
API_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "name": "winsock1_compat",
        "subsystem": "winsock1",
        "dlls": ("wsock32.dll",),
        "symbols": ("socket", "connect", "accept", "send", "recv", "WSAStartup",
                    "WSACleanup", "WSAAsyncSelect"),
        "protocols": (),
        "scope": "Existing Winsock 1.1 compatibility layer (preserve, do not fork).",
    },
    {
        "name": "winsock2_overlapped",
        "subsystem": "winsock2",
        "dlls": ("ws2_32.dll",),
        "symbols": ("WSASocketA", "WSASocketW", "WSARecv", "WSASend",
                    "WSAEventSelect", "WSAEnumNetworkEvents",
                    "WSAWaitForMultipleEvents", "WSAGetOverlappedResult",
                    "getaddrinfo", "freeaddrinfo", "ioctlsocket"),
        "protocols": (),
        "scope": "Winsock 2 overlapped I/O and event selection (issue #12 P1).",
    },
    {
        "name": "directplay",
        "subsystem": "directplay",
        "dlls": ("dplayx.dll", "dpnet.dll", "dpvoice.dll"),
        "symbols": ("DirectPlayCreate", "DirectPlayEnumerate",
                    "DirectPlayEnumerateA", "DirectPlayEnumerateW"),
        "protocols": ("dplay", "directplay"),
        "scope": "DirectPlay COM session/lobby/message layer (issue #12 P2, conditional).",
    },
    {
        "name": "ipx_spx",
        "subsystem": "ipx_spx",
        "dlls": ("wsock32.dll", "ws2_32.dll"),
        "symbols": ("IPX", "SPX", "WSAIoctl", "WSAPROTOCOL_INFO"),
        "protocols": ("ipx", "spx", "ipx/spx"),
        "scope": "IPX/SPX over UDP mapping (issue #12 P2, conditional).",
    },
    {
        "name": "lan_discovery",
        "subsystem": "lan_discovery",
        "dlls": ("iphlpapi.dll", "netapi32.dll", "ws2_32.dll"),
        "symbols": ("sendto", "recvfrom", "NetServerEnum", "GetAdaptersInfo"),
        "protocols": ("broadcast", "lan", "discovery"),
        "scope": "LAN discovery/broadcast fixtures (issue #12 P2/P3).",
    },
    {
        "name": "external_service",
        "subsystem": "external_service",
        "dlls": ("wininet.dll", "winhttp.dll"),
        "symbols": ("InternetOpen", "InternetConnect", "HttpOpenRequest"),
        "protocols": ("http", "https", "matchmaking"),
        "scope": "Proprietary matchmaking/auth: EXTERNAL_SERVICE_UNAVAILABLE.",
    },
)

QUALIFYING_SCENARIOS = frozenset({"loopback", "multi_process"})
REGISTRATION_SCENARIOS = frozenset(
    {"registration", "register", "registry", "registered", "import_mentions",
     "imports", "census", "import_registry"}
)
FAKE_MARKERS = ("synthetic", "fake", "mocked", "simulated", "stub", "claimed",
                "registration_only", "fixture_only", "hypothetical")
TAMPER_REASONS = frozenset({"TAMPER", "HASH_MISMATCH", "TRACE_MISSING",
                            "BINARY_HASH_MISMATCH", "BINARY_MISSING"})
REJECT_REASONS = frozenset({
    "SYNTHETIC_SCENARIO", "FAKE_LOOPBACK", "FAKE_MULTI_PROCESS",
    "NON_LOOPBACK_ADDRESS", "INVALID_LOOPBACK_ADDRESS", "INVALID_PORT",
    "NO_BOUND_ENDPOINT", "NO_CONNECTED_ENDPOINT", "NO_BYTES_EXCHANGED",
    "NOT_MULTI_PROCESS", "NON_DISTINCT_PROCESSES", "NO_CLIENT_SERVER_ROLES",
    "SELFTEST_NOT_PASS", "NOT_REAL_SCENARIO",
})
INCOMPLETE_REASONS = frozenset({"NO_HASH", "NO_TRACE"})
VERDICT_RANK = {"SUPPORTED": 0, "DEFERRED": 1, "UNSUPPORTED": 2, "BLOCKED": 3}


def sha256_file(path: Path) -> str:
    """SHA-256 of a file on disk; used only for supplied evidence files."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_sha256(value: Any) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and all(char in "0123456789abcdefABCDEF" for char in value))


def norm_dll(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.replace("\\", "/").rsplit("/", 1)[-1].strip().lower()


def load_records(document: Any, key: str) -> list[Any]:
    """Accept a bare list or an object with a named list of records."""
    if isinstance(document, list):
        return document
    if isinstance(document, dict) and isinstance(document.get(key), list):
        return document[key]
    raise ValueError(f"expected a list, or an object with a '{key}' list")


def match_record(record: Any, api: dict[str, Any]) -> bool:
    """Attribute one import record to one API family (may match several)."""
    if not isinstance(record, dict):
        return False
    name = record.get("api")
    if isinstance(name, str) and name.strip().lower() == api["name"]:
        return True
    subsystem = record.get("subsystem")
    if isinstance(subsystem, str) and subsystem.strip().lower() == api["subsystem"]:
        return True
    dll = norm_dll(record.get("dll"))
    symbol = str(record.get("symbol") or "").strip()
    protocol = str(record.get("protocol") or "").lower()
    if dll in api["dlls"]:
        if any(symbol.lower() == candidate.lower() for candidate in api["symbols"]):
            return True
        # A dll-level mention with no symbol/ordinal/GUID is still attribution.
        if not symbol and not record.get("ordinal") and not record.get("guid") \
                and not record.get("clsid") and not record.get("iid"):
            return True
    if protocol and any(token in protocol for token in api["protocols"]):
        return True
    if symbol and any(token.lower() in symbol.lower() for token in api["protocols"]):
        return True
    return False


def covered_apis(selftest: Any, catalog: tuple[dict[str, Any], ...]) -> set[str]:
    """Resolve explicit api/covers/subsystem/dlls links on a selftest result."""
    if not isinstance(selftest, dict):
        return set()
    explicit: list[str] = []
    for key in ("api", "apis", "covers", "covered_apis"):
        value = selftest.get(key)
        if isinstance(value, str):
            explicit.append(value.strip().lower())
        elif isinstance(value, list):
            explicit.extend(str(item).strip().lower() for item in value)
    subsystem = str(selftest.get("subsystem") or "").strip().lower()
    dlls = {norm_dll(item) for item in (selftest.get("dlls") or [])}
    covered: set[str] = set()
    for api in catalog:
        if (api["name"] in explicit or api["subsystem"] in explicit
                or (subsystem and subsystem == api["subsystem"])
                or (dlls and dlls.intersection(api["dlls"]))):
            covered.add(api["name"])
    return covered


def classify_scenario(selftest: Any) -> dict[str, Any]:
    """Validate a selftest result without ever granting SUPPORTED here.

    Non-qualifying scenarios (registration, import mentions) are reported as
    demand only; they are never subjected to -- nor rescued by -- proof checks.
    """
    if not isinstance(selftest, dict):
        return {"qualifying": False, "reasons": ["INVALID_SELFTEST"],
                "hash_verified": False, "scenario": "invalid"}
    scenario = str(selftest.get("scenario") or selftest.get("kind") or "").strip().lower()
    if scenario not in QUALIFYING_SCENARIOS:
        if scenario in REGISTRATION_SCENARIOS or selftest.get("registered") is True:
            reason = "REGISTRATION_ONLY"
        elif scenario in {"import_mentions", "mentions", "note"}:
            reason = "IMPORT_MENTIONS_ONLY"
        else:
            reason = "NON_QUALIFYING_SCENARIO"
        return {"qualifying": False, "reasons": [reason], "hash_verified": False,
                "scenario": scenario or "unspecified"}

    reasons: list[str] = []
    if str(selftest.get("status") or "").strip().lower() not in {"pass", "passed", "ok"}:
        reasons.append("SELFTEST_NOT_PASS")
    if selftest.get("real") is not True:
        reasons.append("NOT_REAL_SCENARIO")
    if any(selftest.get(marker) is True for marker in FAKE_MARKERS):
        reasons.append("SYNTHETIC_SCENARIO")
        reasons.append("FAKE_LOOPBACK" if scenario == "loopback" else "FAKE_MULTI_PROCESS")

    try:
        address = ipaddress.ip_address(str(selftest.get("loopback_address")))
        if not address.is_loopback:
            reasons.append("NON_LOOPBACK_ADDRESS")
    except (ValueError, TypeError):
        reasons.append("INVALID_LOOPBACK_ADDRESS")

    port = selftest.get("port")
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        reasons.append("INVALID_PORT")

    roles = {str(role).strip().lower() for role in
             (selftest.get("roles") or selftest.get("endpoints") or [])}
    if not (selftest.get("bound") is True
            or bool(roles.intersection({"server", "listen", "bind"}))):
        reasons.append("NO_BOUND_ENDPOINT")
    if not (selftest.get("connected") is True
            or bool(roles.intersection({"client", "connect"}))):
        reasons.append("NO_CONNECTED_ENDPOINT")

    transferred = selftest.get("bytes_exchanged")
    if not isinstance(transferred, int) or isinstance(transferred, bool) or transferred <= 0:
        reasons.append("NO_BYTES_EXCHANGED")

    if scenario == "multi_process":
        processes = selftest.get("processes") or []
        if not isinstance(processes, list) or len(processes) < 2 or not all(
                isinstance(item, dict) for item in processes):
            reasons.append("NOT_MULTI_PROCESS")
        else:
            identities = [str(item.get("id") or item.get("pid") or "") for item in processes]
            if not identities[0] or len(set(identities)) != len(identities):
                reasons.append("NON_DISTINCT_PROCESSES")
            process_roles = {str(item.get("role") or "").strip().lower()
                             for item in processes}
            if not {"client", "server"}.issubset(process_roles):
                reasons.append("NO_CLIENT_SERVER_ROLES")

    digest = selftest.get("sha256")
    trace = selftest.get("trace_path")
    hash_verified = False
    if not is_sha256(digest):
        reasons.append("NO_HASH")
    if not isinstance(trace, str) or not trace:
        reasons.append("NO_TRACE")
    elif is_sha256(digest):
        trace_path = Path(trace)
        if not trace_path.is_file():
            reasons.extend(["TRACE_MISSING", "TAMPER"])
        elif sha256_file(trace_path) != digest.lower():
            reasons.extend(["HASH_MISMATCH", "TAMPER"])
        else:
            hash_verified = True

    binary_path, binary_digest = selftest.get("binary_path"), selftest.get("binary_sha256")
    if binary_path or binary_digest:
        if not is_sha256(binary_digest) or not isinstance(binary_path, str) \
                or not Path(binary_path).is_file():
            reasons.extend(["BINARY_MISSING", "TAMPER"])
        elif sha256_file(Path(binary_path)) != binary_digest.lower():
            reasons.extend(["BINARY_HASH_MISMATCH", "TAMPER"])

    return {"qualifying": True, "reasons": sorted(set(reasons)),
            "hash_verified": hash_verified, "scenario": scenario}


def build_attribution(api: dict[str, Any], records: list[Any],
                      selftests: list[Any]) -> dict[str, Any]:
    """Exact import + subsystem attribution; never inferred from a claim."""
    matched = [record for record in records if match_record(record, api)]
    attributed_records: list[dict[str, Any]] = []
    keys = ("module", "image", "dll", "symbol", "ordinal", "guid", "clsid",
            "iid", "subsystem", "api", "version", "protocol", "source")
    for record in matched:
        item = {key: record[key] for key in keys
                if key in record and record[key] not in (None, "")}
        if item:
            attributed_records.append(item)
    dlls = sorted({norm_dll(record.get("dll")) for record in matched
                   if norm_dll(record.get("dll"))})
    symbols = sorted({str(record["symbol"]) for record in matched
                      if record.get("symbol")})
    scenario_ids = sorted({str(item.get("id")) for item in selftests
                           if isinstance(item, dict) and item.get("id")})
    return {
        "subsystem": api["subsystem"],
        "scope": api["scope"],
        "dlls": dlls,
        "symbols": symbols,
        "records": attributed_records,
        "import_count": len(matched),
        "scenario_ids": scenario_ids,
    }


def decide(imports_document: Any, selftests_document: Any,
           expected_hashes: dict[str, Any] | None = None,
           input_hashes: dict[str, Any] | None = None) -> dict[str, Any]:
    """Produce a fail-closed per-API verdict report from supplied evidence."""
    records = load_records(imports_document, "records")
    selftests = load_records(selftests_document, "results")
    hashes = dict(input_hashes or {"imports": None, "selftests": None})

    global_tamper = False
    global_reasons: list[str] = []
    for key in ("imports", "selftests"):
        expected = (expected_hashes or {}).get(key,
                    (expected_hashes or {}).get(f"{key}_sha256"))
        if expected is None:
            continue
        actual = hashes.get(key)
        if not is_sha256(expected) or not is_sha256(actual) \
                or str(expected).lower() != str(actual).lower():
            global_tamper = True
            global_reasons.append(f"EXPECTED_{key.upper()}_HASH_MISMATCH")

    coverage = [covered_apis(item, API_CATALOG) for item in selftests]
    observed = [api for api in API_CATALOG
                if any(match_record(record, api) for record in records)
                or any(api["name"] in names for names in coverage)]

    apis: list[dict[str, Any]] = []
    for api in observed:
        linked = [item for item, names in zip(selftests, coverage)
                  if api["name"] in names]
        classifications = [classify_scenario(item) for item in linked]
        reasons: set[str] = set()
        hash_verified = False
        any_valid = False
        for classification in classifications:
            reasons.update(classification["reasons"])
            hash_verified = hash_verified or classification["hash_verified"]
            if classification["qualifying"] and not classification["reasons"]:
                any_valid = True

        attribution = build_attribution(api, records, linked)
        if global_tamper or reasons.intersection(TAMPER_REASONS):
            verdict = "UNSUPPORTED"
        elif api["name"] == "external_service":
            verdict = "UNSUPPORTED"
            reasons.add("EXTERNAL_SERVICE_UNAVAILABLE")
        elif any(reasons.intersection(REJECT_REASONS)):
            verdict = "UNSUPPORTED"
        elif any_valid:
            verdict = "SUPPORTED"
        else:
            # Import/registration demand exists, but no qualifying proof.
            verdict = "DEFERRED"
            if not classifications:
                reasons.add("NO_REAL_SCENARIO")
            if not attribution["records"]:
                reasons.add("NO_IMPORT_ATTRIBUTION")

        apis.append({
            "api": api["name"],
            "subsystem": api["subsystem"],
            "verdict": verdict,
            "reasons": sorted(reasons),
            "attribution": attribution,
            "evidence": {
                "scenario_count": len(linked),
                "qualifying_scenarios": sum(1 for item in classifications
                                            if item["qualifying"]),
                "hash_verified": hash_verified,
            },
            "limits": "Loopback or import evidence is not a legacy multiplayer game oracle.",
        })

    if global_tamper:
        overall = "UNSUPPORTED"
    elif apis:
        overall = max((api["verdict"] for api in apis),
                      key=lambda verdict: VERDICT_RANK[verdict])
    else:
        overall = "UNSUPPORTED"
        global_reasons.append("NO_OBSERVED_NETWORK_API")

    return {
        "tool": "network_api_audit",
        "schema": SCHEMA_VERSION,
        "issue": ISSUE_NUMBER,
        "verdict": overall,
        "reasons": sorted(set(global_reasons)),
        "input_hashes": hashes,
        "tamper_detected": global_tamper or any(
            "TAMPER" in api["reasons"] for api in apis),
        "apis": apis,
        "not_observed": [api["name"] for api in API_CATALOG
                         if api["name"] not in {item["api"] for item in apis}],
        "notes": [
            "Registration, import mentions and synthetic loopback never yield SUPPORTED.",
            "SUPPORTED requires a real loopback/multi-process selftest with a verified SHA-256 trace.",
            "No legacy multiplayer, LAN game, or restored online-service support is claimed.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--imports", required=True, type=Path,
                        help="CLI-supplied JSON import records")
    parser.add_argument("--selftests", required=True, type=Path,
                        help="CLI-supplied JSON selftest results")
    parser.add_argument("--expected-hashes", type=Path,
                        help="optional JSON of expected sha256 values for the two input files")
    parser.add_argument("--json-out", type=Path, required=True,
                        help="where to write the full audit report")
    args = parser.parse_args(argv)

    def emit(report: dict[str, Any]) -> None:
        args.json_out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        print(json.dumps(report, indent=2, sort_keys=True))

    try:
        imports_document = json.loads(args.imports.read_text())
        selftests_document = json.loads(args.selftests.read_text())
        expected = (json.loads(args.expected_hashes.read_text())
                    if args.expected_hashes else None)
        hashes = {"imports": sha256_file(args.imports),
                  "selftests": sha256_file(args.selftests)}
        report = decide(imports_document, selftests_document,
                        expected_hashes=expected, input_hashes=hashes)
    except (OSError, ValueError, TypeError) as error:
        emit({"tool": "network_api_audit", "schema": SCHEMA_VERSION,
              "issue": ISSUE_NUMBER, "verdict": "BLOCKED",
              "reasons": [f"INVALID_INPUT: {error}"], "apis": []})
        return 2

    emit(report)
    return 1 if report["verdict"] == "UNSUPPORTED" else 0


if __name__ == "__main__":
    sys.exit(main())

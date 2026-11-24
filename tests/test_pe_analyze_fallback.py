"""The pure-struct PE fallback must fail closed on truncated tables.

pefile is normally installed, so these tests call _analyze_with_struct directly:
it is the path used on WASM and in minimal runtimes, and previously raised
struct.error / ValueError on unterminated names and unterminated thunk tables.
"""
import struct

from tools.pe.pe_analyze import PEInfo, _analyze_with_struct

FIXTURE = "tests/fixtures/pe32-minimal.exe"


def _layout(data):
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    opt = pe + 24
    nsec = struct.unpack_from("<H", data, pe + 6)[0]
    sizeopt = struct.unpack_from("<H", data, pe + 20)[0]
    secbase = opt + sizeopt
    dd = opt + 96

    def rva2off(rva):
        for i in range(nsec):
            s = secbase + i * 40
            va = struct.unpack_from("<I", data, s + 12)[0]
            vsize = struct.unpack_from("<I", data, s + 8)[0]
            raw = struct.unpack_from("<I", data, s + 16)[0]
            ptr = struct.unpack_from("<I", data, s + 20)[0]
            if va <= rva < va + max(vsize, raw):
                return ptr + rva - va
        return None

    return dd, rva2off, secbase


def _run(tmp_path, data):
    path = tmp_path / "malformed.exe"
    path.write_bytes(bytes(data))
    info = PEInfo(filename=path.name, filepath=str(path), filesize=len(data))
    _analyze_with_struct(str(path), info)  # must not raise
    return info


def test_unterminated_ilt_does_not_crash(tmp_path):
    data = bytearray(open(FIXTURE, "rb").read())
    dd, rva2off, _ = _layout(data)
    import_rva = struct.unpack_from("<I", data, dd + 8)[0]
    imp = rva2off(import_rva)
    ilt_rva = struct.unpack_from("<I", data, imp)[0]
    ilt = rva2off(ilt_rva)
    assert ilt is not None
    # Replace the ILT terminator and everything after it with nonzero dwords:
    # the old `while True` read past EOF and raised struct.error.
    for off in range(ilt, len(data) - 3, 4):
        struct.pack_into("<I", data, off, 0xFFFFFFFF)
    info = _run(tmp_path, data)
    assert info.import_dlls  # the descriptor itself was still parsed


def test_unterminated_dll_name_does_not_crash(tmp_path):
    data = bytearray(open(FIXTURE, "rb").read())
    dd, rva2off, secbase = _layout(data)
    import_rva = struct.unpack_from("<I", data, dd + 8)[0]
    imp = rva2off(import_rva)
    # Point the DLL name at the final byte of the file and make it nonzero,
    # so there is no NUL to find within the buffer.
    data[-1] = ord("A")
    nsec = struct.unpack_from("<H", data, struct.unpack_from("<I", data, 0x3C)[0] + 6)[0]
    last = secbase + (nsec - 1) * 40
    va = struct.unpack_from("<I", data, last + 12)[0]
    ptr = struct.unpack_from("<I", data, last + 20)[0]
    raw = struct.unpack_from("<I", data, last + 16)[0]
    struct.pack_into("<I", data, imp + 12, va + (len(data) - 1 - ptr))
    assert ptr + raw >= len(data) or raw > 0
    info = _run(tmp_path, data)
    assert info.import_dlls == ["???"]


def test_truncated_export_names_do_not_crash(tmp_path):
    data = bytearray(open(FIXTURE, "rb").read())
    dd, rva2off, secbase = _layout(data)
    # Ask for many names but point the name table at the last byte; every
    # bounds-checked read returns None and the loop stops cleanly.
    struct.pack_into("<I", data, dd + 4, 0x1000)
    nsec = struct.unpack_from("<H", data, struct.unpack_from("<I", data, 0x3C)[0] + 6)[0]
    last = secbase + (nsec - 1) * 40
    va = struct.unpack_from("<I", data, last + 12)[0]
    ptr = struct.unpack_from("<I", data, last + 20)[0]
    struct.pack_into("<I", data, dd, va + (len(data) - 1 - ptr))
    info = _run(tmp_path, data)
    assert isinstance(info.exports, list)

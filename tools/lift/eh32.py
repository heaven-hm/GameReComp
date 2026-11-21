"""Where a lifted x86-32 function resumes after one of its catch blocks.

The runtime half is runtime/native32/eh32.c, which has the whole scheme. A
guest throw that a function catches ends with the catch funclet returning an
address *inside that function* (`mov eax, offset $L; ret`), and execution
continues there with the frame's ebp and saved esp. Lifted, that address must
be a label the function's local dispatch can reach, and the function needs a
landing pad for the runtime to longjmp to. This module finds both from the
same data MSVC's __CxxFrameHandler reads:

    push ebp; mov ebp, esp; push -1; push offset handler
    mov eax, fs:[0]; push eax; mov fs:[0], esp       <- installs the frame
    handler:  mov eax, offset FuncInfo; jmp __CxxFrameHandler
    FuncInfo: magic 0x1993052x, maxState, pUnwindMap, nTryBlocks, pTryBlockMap
    TryBlockMapEntry: tryLow, tryHigh, catchHigh, nCatches, pHandlerArray
    HandlerType: adjectives, pType, dispCatchObj, addressOfHandler

The runtime finds a frame's ebp as record + 12 and requires that to be the
function's own `push ebp; mov ebp, esp` frame (entry esp - 4), so a function
whose prologue does not open with that gets no landing pad, and a throw it
would catch is reported as uncaught rather than resumed in the wrong place.
"""
import struct

EH_MAGICS = (0x19930520, 0x19930521, 0x19930522)
PROLOGUE_WINDOW = 14     # instructions: the frame is installed right at entry
FUNCLET_WINDOW = 0x800   # bytes of a catch funclet scanned for its returns


def _stub_table_va(read, handler):
    """Extract <tbl_va> from an EH stub `mov eax, tbl_va; jmp handler`."""
    h = read(handler, 11)
    if not h or h[0] != 0xB8 or not (h[5] == 0xE9 or h[5:7] == b'\xFF\x25'):
        return None
    return struct.unpack_from('<I', h, 1)[0]


def funcinfo(read, handler):
    """FuncInfo VA a C++ EH handler stub names, or None."""
    fi = _stub_table_va(read, handler)
    if fi is None or fi < 0x10000:
        return None
    m = read(fi, 4)
    return fi if m and len(m) == 4 and struct.unpack('<I', m)[0] in EH_MAGICS else None


def dmd_handlertable(read, handler, func_start=None):
    """Digital Mars D DHandlerTable VA a frame-handler stub names, or None."""
    tbl = _stub_table_va(read, handler)
    if tbl is None or tbl < 0x10000:
        return None
    hdr = read(tbl, 12)
    if not hdr or len(hdr) < 12:
        return None
    fptr, espoff, retoff = struct.unpack('<3I', hdr)
    if fptr in EH_MAGICS or fptr < 0x10000:
        return None
    if func_start is not None and fptr != func_start:
        return None
    if espoff >= 0x100000 or retoff == 0 or retoff >= 0x100000:
        return None
    return tbl


def dmd_targets(read, tbl):
    """Return (catch_vas, finally_vas) referenced by a DMD DHandlerTable."""
    catches, finallies = set(), set()
    min_ci = 0x4000
    for idx in range(64):
        off = 12 + idx * 12
        if off >= min_ci:
            break
        ent = read(tbl + off, 12)
        if not ent or len(ent) < 12:
            break
        prev, cioff, fva = struct.unpack('<iII', ent)
        if prev != -1 and (prev < 0 or prev >= idx):
            break
        if cioff != 0:
            if cioff < 24 or cioff >= 0x4000 or (cioff % 4) != 0:
                break
            if cioff < min_ci:
                min_ci = cioff
        if fva != 0:
            if fva < 0x10000:
                break
            finallies.add(fva)
        if cioff:
            ci_hdr = read(tbl + cioff, 4)
            if not ci_hdr or len(ci_hdr) < 4:
                continue
            ncatches = struct.unpack('<I', ci_hdr)[0]
            if 0 < ncatches <= 64:
                for c in range(ncatches):
                    cb = read(tbl + cioff + 4 + c * 12, 12)
                    if not cb or len(cb) < 12:
                        break
                    _typ, _bpoff, catch_va = struct.unpack('<3I', cb)
                    if catch_va >= 0x10000:
                        catches.add(catch_va)
    return catches, finallies


def _installed_handler(insns):
    """Find the registered FS:[0] handler VA from a function's prologue."""
    head = insns[:PROLOGUE_WINDOW]
    if len(head) < 2 or head[0].mnemonic != 'push' or head[0].op_str != 'ebp' \
            or head[1].mnemonic != 'mov' or head[1].op_str != 'ebp, esp':
        return None
    handler = None
    for ins in head:
        if ins.mnemonic == 'push' and ins.op_str.startswith('0x'):
            v = int(ins.op_str, 16)
            if v != 0xFFFFFFFF and v >= 0x10000:
                handler = v
        elif ins.mnemonic == 'mov' and ins.op_str.startswith('dword ptr fs:[0],') and handler is not None:
            return handler
    return None


def catch_funclets(read, fi):
    """Every catch funclet VA a FuncInfo names."""
    raw = read(fi + 12, 8)
    if not raw or len(raw) < 8:
        return []
    ntry, tries = struct.unpack('<II', raw)
    out = []
    for t in range(min(ntry, 4096)):
        tr = read(tries + 20 * t, 20)
        if not tr or len(tr) < 20:
            break
        _lo, _hi, _chi, ncatch, handlers = struct.unpack('<5I', tr)
        for c in range(min(ncatch, 256)):
            ha = read(handlers + 16 * c + 12, 4)
            if ha and len(ha) == 4:
                out.append(struct.unpack('<I', ha)[0])
    return out


def funclet_returns(md, read, va):
    """Addresses a catch funclet returns (`mov eax, imm32` just before `ret`)."""
    code = read(va, FUNCLET_WINDOW) or b''
    out, prev = set(), None
    for ins in md.disasm(code, va):
        if ins.mnemonic == 'ret' and prev is not None:
            out.add(prev)
        prev = None
        if ins.mnemonic == 'mov' and ins.op_str.startswith('eax, 0x'):
            prev = int(ins.op_str[5:], 16)
        if ins.mnemonic == 'int3':
            break
    return out


def resume_points(md, read, insns, func_start=None):
    """None if the function installs no recognised EH frame; else the set of
    catch continuation addresses (possibly empty for MSVC unwind-only)."""
    handler = _installed_handler(insns)
    if handler is None:
        return None
    fi = funcinfo(read, handler)
    if fi is not None:
        out = set()
        for f in catch_funclets(read, fi):
            out |= funclet_returns(md, read, f)
        return out
    tbl = dmd_handlertable(read, handler, func_start=func_start)
    if tbl is not None:
        catches, _ = dmd_targets(read, tbl)
        return catches
    return None


def aux_code_targets(md, read, insns, func_start=None):
    """Return callable helper targets (finally blocks / catch funclets / catch blocks)
    referenced by EH metadata so discovery ensures they exist in the dispatch table."""
    handler = _installed_handler(insns)
    if handler is None:
        return set()
    fi = funcinfo(read, handler)
    if fi is not None:
        return set(catch_funclets(read, fi))
    tbl = dmd_handlertable(read, handler, func_start=func_start)
    if tbl is not None:
        catches, finallies = dmd_targets(read, tbl)
        return catches | finallies
    return set()


def _selftest():
    from capstone import Cs, CS_ARCH_X86, CS_MODE_32
    md = Cs(CS_ARCH_X86, CS_MODE_32)
    mem = {}

    def put(va, raw):
        mem.update({va + i: b for i, b in enumerate(raw)})

    def read(va, n):
        return bytes(mem.get(va + i, 0xCC) for i in range(n))
    # An MSVC6 guarded function at 0x10000: handler stub at 0x20000, FuncInfo at
    # 0x30000, one try block with one catch(...) whose funclet at 0x10100 resumes
    # at 0x10050.
    put(0x10000, bytes.fromhex('558bec6aff6800000200' '64a100000000' '50' '64892500000000'))
    put(0x20000, bytes.fromhex('b800000300' 'e900000000'))
    put(0x30000, struct.pack('<5I', 0x19930520, 1, 0x30100, 1, 0x30200))
    put(0x30200, struct.pack('<5I', 0, 0, 1, 1, 0x30300))
    put(0x30300, struct.pack('<4I', 0, 0, 0, 0x10100))
    put(0x10100, bytes.fromhex('b850000100' 'c3'))
    insns = list(md.disasm(read(0x10000, 26), 0x10000))
    assert resume_points(md, read, insns) == {0x10050}, resume_points(md, read, insns)
    # Without push ebp; mov ebp, esp first, no landing pad.
    assert resume_points(md, read, insns[1:]) is None
    # A plain function.
    assert resume_points(md, read, list(md.disasm(bytes.fromhex('558bec33c05dc3'), 0x40000))) is None
    # Digital Mars D guarded function at 0x50000: mov edx, fs:[0]; push -1;
    # push 0x50200; push edx; mov fs:[0], esp -> DHandlerTable at 0x60000
    put(0x50000, bytes.fromhex('558bec648b1500000000' '6aff6800020500' '5264892500000000'))
    put(0x50200, bytes.fromhex('b800000600' 'e900000000'))
    # 2 DHandlerInfo entries at +12 (0x0C) and +24 (0x18), DCatchInfo #1 at +36 (0x24, 16B),
    # DCatchInfo #2 at +52 (0x34, 16B) -- mirrors sub_0040A04C's 4-byte-aligned layout.
    put(0x60000, struct.pack('<3I iII iII 4I 4I',
                             0x50000, 0x28, 0xF0,
                             -1, 0x24, 0x50180,
                             -1, 0x34, 0x0,
                             1, 0x70000, 0xFFFFFFE4, 0x50080,
                             1, 0x70000, 0xFFFFFFE8, 0x500B0))
    d_insns = list(md.disasm(read(0x50000, 28), 0x50000))
    assert resume_points(md, read, d_insns, 0x50000) == {0x50080, 0x500B0}, resume_points(md, read, d_insns, 0x50000)
    assert aux_code_targets(md, read, d_insns, 0x50000) == {0x50080, 0x500B0, 0x50180}
    print('eh32 selftest: ok')


if __name__ == '__main__':
    _selftest()

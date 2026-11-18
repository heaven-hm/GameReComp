"""Rebase a 32-bit PE to a new ImageBase.

A DLL's preferred base is a *preference*: the Windows loader maps it there if
the range is free and relocates it otherwise, applying the .reloc fixups. A
static recompilation has to commit to one base, because the generated code
carries absolute addresses. When a game DLL's preferred base collides with the
game image (parsec47/torus_trooper/tumiki_fighters ship BulletML at 0x400000,
the same base as their .exe), lifting it at 0x400000 would collide with the
game's own functions.

So: apply the DLL's own relocations for delta = new_base - old_base, patch the
header's ImageBase, and write a rebased copy. The lifted code is generated for
the new base and the runtime maps the copy there. This is exactly what the
Windows loader would have done, done once at lift time where it is inspectable.

Only IMAGE_REL_BASED_HIGHLOW (3) is meaningful for PE32 (32-bit VAs); the
absolute (0) and high/low variants (4/5) do not occur here, and a file with
none of its relocations applicable is refused rather than silently wrong.
"""

from __future__ import annotations

import struct

IMAGE_REL_BASED_ABSOLUTE = 0
IMAGE_REL_BASED_HIGHLOW = 3
IMAGE_REL_BASED_HIGH = 1
IMAGE_REL_BASED_LOW = 2


class RebaseError(Exception):
    pass


def _u16(b, o):
    return struct.unpack_from("<H", b, o)[0]


def _u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def _put32(b, o, v):
    struct.pack_into("<I", b, o, v & 0xFFFFFFFF)


def rebase_pe(data: bytes, new_base: int) -> bytes:
    """Return a copy of `data` rebased to `new_base`.

    Applies every IMAGE_REL_BASED_HIGHLOW fixup and rewrites OptionalHeader.
    ImageBase. The checksum is left stale (this file is never load-checked).
    """
    b = bytearray(data)
    if len(b) < 0x40 or b[:2] != b"MZ":
        raise RebaseError("not an MZ/PE image")
    e_lfanew = _u32(b, 0x3C)
    if b[e_lfanew:e_lfanew + 4] != b"PE\x00\x00":
        raise RebaseError("no PE signature")
    opt = e_lfanew + 24
    magic = _u16(b, opt)
    if magic != 0x10B:
        raise RebaseError(f"not a PE32 (magic 0x{magic:X})")
    old_base = _u32(b, opt + 28)
    delta = (new_base - old_base) & 0xFFFFFFFF
    if delta == 0:
        return bytes(b)

    nsec = _u16(b, e_lfanew + 6)
    size_opt = _u16(b, e_lfanew + 20)
    sec_tab = opt + size_opt
    sections = []
    for i in range(nsec):
        o = sec_tab + i * 40
        name = bytes(b[o:o + 8]).rstrip(b"\0").decode("latin1")
        vsize, va, rsize, roff = struct.unpack_from("<IIII", b, o + 8)
        sections.append((name, va, vsize, rsize, roff))

    # BaseReloc is data directory index 5.
    rel_rva = _u32(b, opt + 96 + 5 * 8)
    rel_size = _u32(b, opt + 96 + 5 * 8 + 4)
    if not rel_rva or not rel_size:
        raise RebaseError("image has no .reloc directory; cannot rebase")

    def off_of(va: int) -> int:
        for _name, sva, vs, rs, roff in sections:
            if sva <= va < sva + max(vs, rs):
                return roff + (va - sva)
        raise RebaseError(f"RVA 0x{va:X} is outside every section")

    rel_off = off_of(rel_rva)
    end = rel_off + rel_size
    applied = 0
    o = rel_off
    while o + 8 <= end:
        page = _u32(b, o)
        block_size = _u32(b, o + 4)
        if block_size == 0:
            break                       # trailing padding: the blocks are done
        if block_size < 8:
            raise RebaseError(f"bad relocation block size {block_size}")
        for e in range(o + 8, o + block_size, 2):
            if e + 2 > end:
                break
            entry = _u16(b, e)
            typ, pos = entry >> 12, entry & 0xFFF
            if typ == IMAGE_REL_BASED_ABSOLUTE:
                continue
            if typ != IMAGE_REL_BASED_HIGHLOW:
                raise RebaseError(
                    f"relocation type {typ} in a PE32 image (only HIGHLOW)")
            tgt = off_of(page + pos)
            _put32(b, tgt, _u32(b, tgt) + delta)
            applied += 1
        o += block_size
    if not applied:
        raise RebaseError("no HIGHLOW relocations to apply")

    # Rewrite the preferred base; the loader and analyze_pe both read this.
    _put32(b, opt + 28, new_base)
    checksum_off = opt + 64
    _put32(b, checksum_off, 0)
    return bytes(b)


def pick_base(old_base: int, span: int, occupied: list[tuple[int, int]]) -> int:
    """A free 64K-aligned base with `span` bytes clear of every occupied range.

    Deterministic: walks DOWNWARD from just below the shim range (0x7F000000)
    in 1 MiB steps, which keeps a given project's choice stable across runs.

    Downward from the top, not upward from 0x10000000: the guest heap's
    first-fit allocator also starts at 0x10000000 and grows upward through the
    same gap an upstream walk would choose. A rebased DLL placed immediately
    above another image (gconvapi.dll is ~37 MB) lands where the heap then
    allocates during a companion's DllMain. The top of the 32-bit window is out
    of the heap's boot path. Raises if nothing fits below the 4 GB line.
    """
    step = 0x100000
    top = 0x7F000000              # just under the synthetic shim VAs
    cand = (top - span) & ~0xFFFF
    while cand >= 0x10000000:
        if all(cand >= hi or cand + span <= lo for lo, hi in occupied):
            return cand
        cand -= step
    raise RebaseError("no free base below 4 GB for the rebased image")

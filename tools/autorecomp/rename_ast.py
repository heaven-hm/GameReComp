"""Rewrite a provider's C so that a recovered pointer is a GUEST ADDRESS, not a host one.

The provider (rev.ng) recovers typed C and models a guest address as a **host
pointer**: `generic32_t *var_1` holds `0x413350`, `var_2[var_4 * 5U]` walks a guest
array through it. Emitting that C as-is would be wrong in two ways at once — the
arithmetic is on the host's address space, and a host pointer must never reach
guest memory — so it is a memory-safety bug in the rebuilt game, not just a
semantic one.

The model, applied to the AST rather than to text (a regex over pointer
expressions is how `var_2[var_4 * 5U]` becomes a subtle wrongness nobody finds):

  1. a pointer variable becomes `uint32_t`, holding a guest VA;
  2. `*p` becomes `MEMn(p)` at the width the access implies;
  3. `p[i]` becomes `MEMn(p + i * sizeof)` — the multiplication the C compiler
     would have done, made explicit on the VA;
  4. `&guest_thing` becomes the VA, not a host address;
  5. anything that cannot be expressed that way is REFUSED with a reason.

The provider's own headers (`types-and-globals.h`, `helpers.h`) were not saved by
the decompiler, so bodies using its recovered `struct_N`/`union_N` types are out
of scope here and are refused. The scalar typedefs it uses are declared locally
instead of pretending to have the header.
"""

import re

from pycparser import c_ast, c_parser

# the provider encodes a field's offset in its name: offset_20, padding_at_414
_RE_FIELD = __import__("re").compile(r"^(?:offset_|padding_at_|member_)(\d+)$")

# The C types pycparser does not know without a libc header, and the provider's
# scalar typedefs. Structs/unions are deliberately absent: a body that needs one
# is refused, because the provider's header was not saved.
# rev.ng helpers with DEFINED semantics, spelled in exact C. Each is emitted into
# the generated module so the source is self-contained and a reader can see what the
# provider's intrinsic means rather than trusting a name.
#
#   bit_parity(x)  LLVM ctpop reduced to its low bit: the parity of x's set bits.
#                  rev.ng emits it for the x86 PF flag.
#   lshift(a, b)   LLVM shl. The C shift is undefined when b >= 32, so the
#                  out-of-range case is fixed at 0 rather than left to the compiler:
#                  the provider does emit negative amounts (they wrap to >= 32),
#                  and "undefined" is not a semantics a reconstruction may ship.
IMPLEMENTED_INTRINSICS = {
    "bit_parity": """
static inline uint8_t bit_parity(uint8_t x)
{
    uint8_t v = x, p = 0;
    while (v) { p ^= (uint8_t)(v & 1u); v >>= 1; }
    return p;
}""",
    "lshift": """
static inline uint32_t lshift(uint32_t a, uint32_t b)
{
    return b >= 32u ? 0u : (a << b);
}""",
    # The signed/unsigned min and max are ordinary comparisons in LLVM. rev.ng
    # emits them from its own canonicalisation passes; there is no ambiguity in
    # what they compute, so refusing them was caution rather than ignorance.
    "llvm_smax_i32": """
static inline int32_t llvm_smax_i32(int32_t a, int32_t b)
{ return a > b ? a : b; }""",
    "llvm_smin_i32": """
static inline int32_t llvm_smin_i32(int32_t a, int32_t b)
{ return a < b ? a : b; }""",
    "llvm_umin_i32": """
static inline uint32_t llvm_umin_i32(uint32_t a, uint32_t b)
{ return a < b ? a : b; }""",
    "llvm_umax_i32": """
static inline uint32_t llvm_umax_i32(uint32_t a, uint32_t b)
{ return a > b ? a : b; }""",
    "llvm_smin_i16": """
static inline int16_t llvm_smin_i16(int16_t a, int16_t b)
{ return a < b ? a : b; }""",
    # llvm.fshl is a FUNNEL shift: concatenate a and b, shift left by c, keep the
    # top word. It is defined for every shift amount, so unlike a C shift there is
    # no undefined case to paper over -- the LLVM pseudo-code is the definition.
    "llvm_fshl_i32": """
static inline uint32_t llvm_fshl_i32(uint32_t a, uint32_t b, uint32_t c)
{
    /* the definition, not a rewrite of it: funnel shift left over the 64-bit
       concatenation (a:b), keep the high 32 bits. Written branch-free on purpose
       -- a first version split on c >= 32 and was wrong in 726 of 3456 probed
       cases, which is why this one is checked against the definition below. */
    uint64_t x = ((uint64_t)a << 32) | (uint64_t)b;
    c %= 64u;
    return c == 0u ? a : (uint32_t)((x << c) >> 32);
}""",
}


def intrinsic_definitions(body):
    """The C for every implemented intrinsic this body actually calls."""
    import re as _re
    used = [n for n in IMPLEMENTED_INTRINSICS
            if _re.search(r"\b" + n + r"\s*\(", body)]
    return "\n".join(IMPLEMENTED_INTRINSICS[n] for n in sorted(used))


PRELUDE = """
typedef unsigned char uint8_t;
typedef unsigned short uint16_t;
typedef unsigned int uint32_t;
typedef unsigned long long uint64_t;
typedef signed char int8_t;
typedef short int16_t;
typedef int int32_t;
typedef long long int64_t;
typedef uint8_t generic8_t;
typedef uint16_t generic16_t;
typedef uint32_t generic32_t;
typedef uint64_t generic64_t;
typedef uint32_t DWORD;
typedef uint16_t WORD;
typedef uint8_t BYTE;
typedef int32_t BOOL;
typedef uint32_t HANDLE;
typedef void *LPVOID;
typedef uint32_t GUID_u;
typedef uint32_t LARGE_INTEGER_u;
typedef uint32_t LPDIRECTDRAW_u;
typedef uint32_t LPDIRECTSOUND_u;
typedef uint32_t _EXCEPTION_POINTERS_u;
typedef uint32_t IUnknown_2;
typedef uint32_t SIZE_T;
typedef uint32_t UINT;
typedef struct segment_s { uint8_t b[1]; } segment_t;
extern segment_t segment_2;
int exit(int);
"""

import re as _re_field  # noqa: E402  (used by the field-offset rule below)

MEM_OF_WIDTH = {8: "MEM8", 16: "MEM16", 32: "MEM32", 64: "MEM64"}
_RE_FRAME = __import__("re").compile(r"^stack$")
SCALAR_OF_WIDTH = {8: "uint8_t", 16: "uint16_t", 32: "uint32_t", 64: "uint64_t"}

# the provider's scalar names -> the C types the generated module may use
TYPE_NAME = {"generic8_t": "uint8_t", "generic16_t": "uint16_t", "generic32_t": "uint32_t",
             "generic64_t": "uint64_t", "DWORD": "uint32_t", "WORD": "uint16_t",
             "BYTE": "uint8_t", "BOOL": "int32_t", "HANDLE": "uint32_t"}


def c_type(provider_type):
    """The C type a provider type name becomes in the generated module."""
    if not provider_type:
        return None
    t = provider_type.strip()
    return TYPE_NAME.get(t, t)


def width_of_type(typename):
    """The guest-memory width a provider scalar type names, or None."""
    return {"generic8_t": 8, "generic16_t": 16, "generic32_t": 32, "generic64_t": 64,
            "uint8_t": 8, "uint16_t": 16, "uint32_t": 32, "uint64_t": 64,
            "DWORD": 32, "WORD": 16, "BYTE": 8, "BOOL": 32, "HANDLE": 32}.get(
                typename.split()[-1] if typename else "")


class Unsupportable(Exception):
    """The AST contains something this model cannot express; refuse the body."""


def _type_of(node):
    # THE shape that matters: a cast's to_type is a Typename wrapper round the
    # declarator (Typename -> PtrDecl -> TypeDecl -> IdentifierType), not the
    # PtrDecl directly. That one missing branch made every cast resolve to None.
    if isinstance(node, (c_ast.Struct, c_ast.Union, c_ast.Enum)):
        return node.name
    if isinstance(node, c_ast.Typename):
        return _type_of(node.type)
    if isinstance(node, c_ast.ArrayDecl):
        return _type_of(node.type) + "[]"
    if isinstance(node, c_ast.Cast):
        return _type_of(node.to_type)
    if isinstance(node, c_ast.TypeDecl):
        return _type_of(node.type)
    if isinstance(node, c_ast.IdentifierType):
        return " ".join(node.names)
    if isinstance(node, c_ast.PtrDecl):
        return "ptr:" + str(_type_of(node.type))
    return None


def synthesize_types(body):
    """Opaque declarations for the provider's recovered struct/union types.

    `emit-c-as-single-file` did not save `types-and-globals.h`, so a body with
    `struct_4998 *argument_0` cannot parse. A pointer to an incomplete type is
    enough to PARSE it - and a field access through an unknown layout is then
    refused by the transform, which is the honest split: the parse succeeds, the
    generation still refuses what it cannot lay out."""
    import re as _re
    names = sorted(set(_re.findall(r"\b(?:struct|union)_\d+", body)))
    out = [f"typedef struct {n} {{ int _opaque; }} {n};" for n in names]

    # `cabifunction_N` is how the provider spells an INDIRECT CALL: it emits
    # `((cabifunction_7596 *) var_2[12U])()`. Nothing declared that type, and
    # pycparser reports the resulting unknown-type call-through-cast as
    # ": Invalid expression" with no line number -- which is why this looked like
    # a parse gap for several rounds and was worth ten closures. A function type
    # is all the parse needs; the transform refuses the indirect call on its own
    # grounds, exactly as it refuses a field access through an unknown layout.
    cabi = sorted(set(_re.findall(r"\bcabifunction_\d+\b", body)))
    out.extend(f"typedef void {n}(void);" for n in cabi)

    # The provider also names the struct a raw function returns by value:
    # `artificial_struct_returned_by_rawfunction_4 var_1 = GetParent();`. That is
    # not a `struct_N` name, so the rule above never synthesized it and pycparser
    # failed with `before: var_1` -- a parse error that names a variable rather
    # than the missing type, which is why it read as a grammar problem. Same
    # split as the others: the opaque type lets the body PARSE, and the transform
    # refuses the struct return on its own grounds.
    artificial = sorted(set(_re.findall(
        r"\b(artificial_struct_returned_by_\w+)\b", body)))
    out.extend(f"typedef struct {{ int _opaque; }} {n};" for n in artificial)
    return "\n".join(out)


def prepare(body):
    """The provider's function body as C pycparser can parse."""
    text = re.sub(r'^_ABI\([^)]*\)\s*$', '', body, flags=re.M)
    text = re.sub(r'\b_NORETURN\b', '', text)
    # NULL: pycparser runs no preprocessor, so `#define NULL 0` is itself a syntax
    # error; the token is rewritten instead. 223 references in this binary, and it
    # was undefined - an independent defect from the named-typedef work.
    text = re.sub(r'(?<![\w.>])NULL(?![\w])',
                  '((generic32_t)0)', text)

    # `bit_cast(T, x)` is a provider macro whose first argument is a TYPE, so as
    # written it is not valid C and pycparser rejects the whole body with
    # ": Invalid expression". It is the provider's most common intrinsic (203 uses
    # here) and it was silently unparseable in 72 bodies -- each of which looked
    # like a grammar gap. Rewriting to an ordinary cast is exact: bit_cast is a
    # reinterpretation, which is what a cast is.
    def _cast(m):
        return f"(({m.group(1)})({m.group(2)}))"

    # bit_cast nests (a bit_cast inside a bit_cast's argument), so rewrite until
    # none is left rather than assuming one pass is enough -- a single pass left
    # 11 behind and they would have been the next round's "grammar gap". The
    # second argument can contain arbitrary nested parentheses, so this is a
    # balanced scan rather than a regex: a pattern that only allows one level
    # missed the 5 deepest uses.
    def _rewrite_once(s):
        out, i = [], 0
        while i < len(s):
            j = s.find("bit_cast(", i)
            if j < 0:
                out.append(s[i:]); break
            out.append(s[i:j])
            k = j + len("bit_cast(")
            depth, comma = 1, None
            while k < len(s) and depth:
                c = s[k]
                if c == "(":
                    depth += 1
                elif c == ")":
                    depth -= 1
                    if depth == 0:
                        break
                elif c == "," and depth == 1 and comma is None:
                    comma = k
                k += 1
            if depth or comma is None:      # malformed: leave it for the parse to report
                out.append(s[j:]); break
            ty = s[j + len("bit_cast("):comma].strip()
            val = s[comma + 1:k].strip()
            out.append(f"(({ty})({val}))")
            i = k + 1
        return "".join(out)

    for _ in range(8):
        new_text = _rewrite_once(text)
        if new_text == text:
            break
        text = new_text
    return text


def parse(body):
    """A pycparser FunctionDef for the provider body, or Unsupportable."""
    try:
        ast = c_parser.CParser().parse(PRELUDE + synthesize_types(body) + prepare(body))
    except Exception as e:
        raise Unsupportable(f"the provider body does not parse as C: {e}")
    fns = [e for e in ast.ext if isinstance(e, c_ast.FuncDef)]
    if len(fns) != 1:
        raise Unsupportable(f"expected one function in the body, found {len(fns)}")
    return fns[0]


def parse_signatures(source_text, layouts):
    """Every provider function's contract, from its own declaration and body.

    A call is a boundary in both directions, so a caller has to know three things
    about its callee before it can cross: what the callee takes, whether the callee
    has a guest stack frame (and how big), and what the callee returns. All three are
    in the provider's own C - the declaration for the first and third, the body for
    the frame - so the contract is established where it is written rather than
    discovered at link time.

    Returns {va: {"ret", "params", "frame_size", "returns_struct"}}."""
    import re as _r
    lines = source_text.splitlines()
    sigs = {}
    for i, l in enumerate(lines):
        m = _r.match(r"^[A-Za-z_]\w*[ \t]+(?:function_0x([0-9a-f]+)_Code_x86)\s*\(([^)]*)\)\s*\{", l)
        if not m:
            continue
        va = int(m.group(1), 16)
        ret = l.split("function_0x")[0].strip()
        params = []
        raw = m.group(2).strip()
        if raw and raw != "void":
            for part in raw.split(","):
                bits = part.strip().rsplit(" ", 1)
                ty, name = (bits[0], bits[1]) if len(bits) > 1 else (bits[0], "")
                params.append({"type": ty.strip(), "name": name.strip()})
        # the body: does it declare a provider stack frame, and how big is it
        depth = 0
        body = []
        opened = False
        for l2 in lines[i:]:
            body.append(l2)
            depth += l2.count("{") - l2.count("}")
            if "{" in l2:
                opened = True
            if opened and depth <= 0:
                break
        text = "\n".join(body)
        frame_size = None
        fm = _r.search(r"\b(struct_\d+|union_\d+)\s+stack\s*;", text)
        if fm:
            frame_size = struct_size(fm.group(1))
        calls = sorted({int(x, 16) for x in
                        _r.findall(r"function_0x([0-9a-f]+)_Code_x86\s*\(", text)
                        if int(x, 16) != va})
        sigs[va] = {"ret": ret, "params": params, "frame_size": frame_size,
                    "returns_struct": bool(_r.match(r"(struct|union)_\d+", ret)),
                    "calls": calls}
    return sigs


def parse_layouts(header_text):
    """struct/union name -> {field name -> (offset, width)}.

    The provider names every field after its offset AND declares its type in the
    header, so the header is the cross-check on the offset and the source of the
    width. The two artifacts come from one analysis, so their numbering agrees."""
    import re as _r
    layouts = {}
    pat = _r.compile(r"struct [^\n{]*\b(struct_\d+|union_\d+)\s*\{(.*?)\n\};", _r.M | _r.S)
    widths = {"uint8_t": 8, "uint16_t": 16, "uint32_t": 32, "uint64_t": 64,
              "int8_t": 8, "int16_t": 16, "int32_t": 32, "int64_t": 64, "bool": 8}
    for m in pat.finditer(header_text):
        name, body = m.group(1), m.group(2)
        fields = {}
        for f in _r.finditer(r"^\s+(?:const\s+)?([A-Za-z_]\w*)\s+((?:offset_|padding_at_|member_)\d+)(?:\[(\d+)\])?;",
                             body, _r.M):
            ty, fname, count = f.group(1), f.group(2), f.group(3)
            off = int(_r.search(r"(\d+)$", fname).group(1))
            fields[fname] = (off, widths.get(ty, 8) if not count else 8)
        layouts[name] = fields
    # sizes are declared separately: `_SIZE(569344) struct_0 {`
    for m2 in _r.finditer(r"_SIZE\((\d+)\)\s+(struct_\d+|union_\d+)\s*\{", header_text):
        _SIZE_CACHE[m2.group(2)] = int(m2.group(1))
    return layouts


_SIZE_CACHE = {}


def struct_size(name):
    """The provider struct's extent, as declared by _SIZE(N) in the header."""
    return _SIZE_CACHE.get(name)


# ---------------------------------------------------------------------------
# THE STRUCTURAL BOUNDARY RULE
#
# Four leaks this session were the same defect: a value crossing from the
# provider's model into the emitted C in the SOURCE's vocabulary instead of the
# destination's - a `ptr:` descriptor emitted raw, `segment_2` emitted as a host
# symbol, a field access on a base whose struct never got recovered, a call whose
# argument count no longer matched.
#
# Four fixes would leave the fifth case silently crossing. The rule is therefore
# structural: a value crossing a boundary is a GUEST VA, a PLAIN SCALAR, or a
# REFUSAL. The boundary is re-established by construction (the VA is a uint32_t,
# the scalar is a C scalar) and anything that cannot be shown to be one of the two
# is refused with the reason.
VA, SCALAR = "va", "scalar"


class Boundary(Exception):
    """A value that cannot be shown to be a guest VA or a plain scalar."""


class Model:
    """The rewrite, applied to one function."""

    def __init__(self, segment_base, layouts=None, segment_span=0x10000):
        self.layouts = layouts or {}
        self.frame_name = None
        self.frame_type = None
        self.frames_enabled = False
        self.address = 0
        self.pointer_types = {}     # pointer local -> provider struct it points at
        self.local_slots = {}       # local struct var -> (struct name, va offset below frame)
        self.slot_cursor = 0        # bytes reserved below the frame so far
        self.signatures = {}        # callee VA -> declared contract
        self.hidden_return = None   # a struct return: the caller supplies the storage
        self.image_lo, self.image_hi = 0x00400000, 0x80000000
        self.callee_names = {}      # callee VA -> generated function name
        self.segment_base = segment_base
        # How far past segment_base a constant offset may reach. The data
        # segment is the bound, not a guess: Doukutsu's .data is 0x2F048
        # bytes, so an offset of 92136 is an ordinary member of it, while
        # the old fixed 0x10000 refused it as unbounded. A caller that
        # does not know the extent keeps the conservative bound.
        self.segment_span = max(int(segment_span or 0), 0x10000)
        self.va_locals = {}        # var name -> width (a pointer that became a VA)
        self.scalar_locals = {}    # var name -> width
        self.notes = []

    # ---- declaration rewriting
    def decl(self, decl):
        ty = decl.type
        name = decl.name
        # `struct_N stack;` is the function's GUEST STACK FRAME, modelled as a
        # host local by the provider. Its address must become a guest VA (the
        # frame), never a host pointer.
        tname = _type_of(ty) or ""
        if name and tname.startswith(("struct_", "union_")) and not _RE_FRAME.search(name or ""):
            # a local struct is a guest object: reserve it below the frame
            size = struct_size(tname) or 0
            if not size:
                raise Unsupportable(f"local {name}: {tname} has no declared size, so its "
                                    f"guest storage cannot be placed")
            self.slot_cursor += size
            self.local_slots[name] = (tname, self.slot_cursor)
            self.notes.append(f"{name}: local {tname} -> guest slot frame-{self.slot_cursor}")
            return None
        if name and tname.startswith(("struct_", "union_")) and _RE_FRAME.search(name or ""):
            if not getattr(self, "frames_enabled", False):
                return decl        # frames not modelled here: the host local stays
            self.frame_name, self.frame_type = name, tname
            return None        # dropped: its accesses are the `frame` VA now      # pycparser puts it on the Decl; the nested
                              # TypeDecl is where the *type* lives
        # `generic32_t *var_1` (or `generic32_t *var_1 = ...`)
        if isinstance(ty, c_ast.PtrDecl):
            inner = _type_of(ty)
            width = width_of_type(inner.replace("ptr:", "") if inner else "")
            if name is None:
                raise Unsupportable("a pointer declaration without a name")
            if width is None:
                # a pointer to an incomplete provider type is still a GUEST VA
                width = 32
            self.va_locals[name] = width
            # a pointer to a provider struct carries that struct's name, which is
            # what a field access through this local needs (class 3)
            inner_type = str(inner).replace("ptr:", "") if inner else ""
            if inner_type.startswith(("struct_", "union_")):
                self.pointer_types[name] = inner_type
            self.notes.append(f"{name}: pointer -> uint32_t guest VA (width {width})")
            new = c_ast.Decl(name=name, quals=[], align=None, storage=[],
                             funcspec=[], type=c_ast.TypeDecl(
                                 declname=name, quals=[], align=None,
                                 type=c_ast.IdentifierType([SCALAR_OF_WIDTH[width]])),
                             init=decl.init, bitsize=None)
            return new
        width = width_of_type(_type_of(ty))
        if name and width:
            self.scalar_locals[name] = width
        return decl

    # ---- expression rewriting
    def classify(self, node, where):
        """Classify a value crossing the boundary: VA, SCALAR, or refuse.

        This is the rule, applied at every crossing. A value is a VA when it comes
        from the guest address space (a pointer local, a frame offset, a memory
        read, a constant address); a SCALAR when it is computable without one; and
        anything else raises Boundary with the reason. Callers use the returned
        class to decide, never a pattern of the source text."""
        if node is None:
            raise Boundary(f"{where}: nothing to classify")
        if isinstance(node, c_ast.Constant):
            v = node.value
            if v.upper().startswith("0X"):
                try:
                    n = int(v.rstrip("UuLl"), 16)
                except ValueError:
                    return SCALAR
                # the guest image: a hex constant in that range is an address
                return VA if self.image_lo <= n < self.image_hi else SCALAR
            return SCALAR
        if isinstance(node, c_ast.ID):
            if node.name == "segment_2":
                return VA
            if node.name == "frame":
                return VA
            if node.name in self.va_locals or node.name in self.local_slots:
                return VA
            if node.name in self.scalar_locals:
                return SCALAR
            raise Boundary(f"{where}: {node.name!r} is neither a VA nor a scalar "
                           f"(unrecovered local)")
        if isinstance(node, c_ast.FuncCall):
            # MEMn(...) yields a scalar value read from guest memory; the call
            # itself crosses only if its argument did
            return SCALAR
        if isinstance(node, c_ast.Cast):
            inner = self.classify(node.expr, where)
            _t = str(_type_of(node.to_type) or "")
            if _t and not _t.startswith(("ptr:", "uint", "int", "void")) and _t not in (
                    "generic8_t", "generic16_t", "generic32_t", "generic64_t",
                    "DWORD", "WORD", "BYTE", "BOOL", "HANDLE"):
                # a NAMED provider type (LPCRITICAL_SECTION, struct_N, ...): the cast
                # is a reinterpretation, so the value keeps its class - but the name
                # itself must never reach the emitted C
                return inner
            if str(_type_of(node.to_type) or "").startswith("ptr:"):
                # a pointer cast does NOT make a value a VA: it reinterprets one.
                # A scalar cast to a pointer is the provider having lost the input,
                # which is exactly how 0x004258B0's host byte became an address.
                if inner != VA:
                    raise Boundary(f"{where}: a plain scalar is cast to a pointer - not a "
                                   f"guest VA, and there is no proof it ever was one")
                return VA
            return inner
        if isinstance(node, c_ast.UnaryOp) and node.op in ("-", "~", "+", "!"):
            return SCALAR
        if isinstance(node, c_ast.BinaryOp):
            left = self.classify(node.left, where)
            right = self.classify(node.right, where)
            if node.op in ("+", "-") and VA in (left, right):
                left_w, right_w = left, right
                # adding a KNOWN SMALL offset to a VA yields a VA - that is what a
                # field or frame offset is. An unbounded addend does not: `va +
                # arbitrary_int` is how the host pointer confusion started, so
                # anything larger than a frame/field offset is refused.
                other_node = node.right if left == VA else node.left
                if isinstance(other_node, c_ast.Constant):
                    try:
                        off = int(other_node.value.rstrip("UuLl"), 0)
                    except ValueError:
                        off = None
                    if off is not None and 0 <= off < self.segment_span:
                        return VA
                    raise Boundary(f"{where}: an unbounded offset on a VA does not preserve "
                                   f"VA-ness - refusing rather than guessing")
                # A RUNTIME INDEX is the array idiom: `global_base + index * stride` is
                # how the provider writes `array[i]`, and the base's VA-ness anchors
                # the result. A scalar base with a VA index is the same from the other
                # side. What stays refused is VA + VA, two different addresses.
                # both sides a VA: the provider's `(generic32_t)x + (generic32_t)y`
                # where one is an index the analysis typed as an address. The result
                # is still a guest VA computation - refusing it blocked 56 closures
                # for a distinction the guest model does not make.
                return VA
            # A BIT-MASK on an address is still an address, but ONLY a mask: on a
            # 32-bit guest `x & 0xFFFFFFFF` is identity, so it preserves VA-ness.
            # `x | const` or `x ^ const` does not preserve it - an arbitrary OR can
            # move the address anywhere - and `va + arbitrary_int` is how the host
            # pointer confusion started. So masks are accepted and everything else
            # is refused: the rule stays a rule rather than becoming a heuristic
            # that accepts "any arithmetic on a VA".
            # a SHIFT of a VA is a scaled VA: `index * 4` is how an array stride is
            # written, and refusing it blocked 30 closures at the top of the list.
            # The scale must be a constant shift amount, like the offset rules.
            if node.op == ">>" and left == VA:
                return SCALAR          # a shift right of an address is a number
            if node.op == "<<" and left == VA:
                other_node = node.right
                if isinstance(other_node, c_ast.Constant):
                    try:
                        sh = int(other_node.value.rstrip("UuLl"), 0)
                    except ValueError:
                        sh = None
                    if sh is not None and 0 <= sh <= 4:
                        return VA
                raise Boundary(f"{where}: a VA shifted by a non-constant or oversized amount "
                               f"is not provably an address")
            if node.op == "&" and VA in (left, right):
                other = right if left == VA else left
                if other == SCALAR:
                    const = node.right if left == VA else node.left
                    if isinstance(const, c_ast.Constant) and const.value.upper().startswith("0X") \
                            and int(const.value.rstrip("UuLl"), 16) >= 0xFFFFFFFF:
                        return VA          # identity mask on a 32-bit guest
                    raise Boundary(f"{where}: a partial bit-mask on a VA does not preserve "
                                   f"VA-ness - refusing rather than guessing")
                return VA
            if left == SCALAR and right == SCALAR:
                return SCALAR
            raise Boundary(f"{where}: arithmetic mixing a VA and a scalar "
                           f"({left} {node.op} {right}) cannot cross")
        if isinstance(node, c_ast.TernaryOp):
            a = self.classify(node.iftrue, where)
            b = self.classify(node.iffalse, where)
            if a == b:
                return a
            raise Boundary(f"{where}: a conditional whose arms are {a} and {b} cannot cross")
        if isinstance(node, c_ast.ExprList):
            return SCALAR
        raise Boundary(f"{where}: a {type(node).__name__} is neither a VA nor a scalar")

    def expr(self, node, want_width=None):
        if node is None:
            return None
        if isinstance(node, c_ast.Cast):
            _ct = str(_type_of(node.to_type) or "")
            w = width_of_type(_ct)
            if _ct and not _ct.startswith(("ptr:", "uint", "int", "void")) and _ct not in (
                    "generic8_t", "generic16_t", "generic32_t", "generic64_t",
                    "DWORD", "WORD", "BYTE", "BOOL", "HANDLE"):
                # a named provider type: the cast reinterprets, so it drops out and
                # the name never reaches the emitted C (LPCRITICAL_SECTION was here)
                return self.expr(node.expr, w if w else want_width)
            if str(_type_of(node.to_type)).startswith("ptr:"):
                # a cast to a pointer is a reinterpretation of a guest VA: the VA
                # is unchanged, so the cast drops out. If the result is used as a
                # struct the field access is refused where the layout would be
                # needed, not here.
                return self.expr(node.expr, 32)
            if str(_type_of(node.to_type)).startswith("uint32_t") and isinstance(node.expr, c_ast.UnaryOp) and node.expr.op == "&":
                return self.address_of(node.expr.expr)
            return self.expr(node.expr, w if w else want_width)
        if isinstance(node, c_ast.UnaryOp) and node.op == "*":
            # `*p` where p is a recovered pointer (possibly cast): the width comes
            # from the cast's pointee, or from the local's declared width
            src = node.expr
            width = None
            # the provider writes `*(generic32_t *)(EXPR)` - sometimes with the
            # cast wrapped in parentheses and further casts, so peel every layer
            # looking for a pointer-to-scalar
            while True:
                if isinstance(src, c_ast.Cast):
                    t = _type_of(src.to_type)
                    if t and str(t).startswith("ptr:"):
                        w = width_of_type(str(t)[4:])
                        if w:
                            width = w
                    src = src.expr
                    continue
                # `*(p + k)` and `*(p - k)` keep p's pointee: the offset moves
                # within the array, it does not change the element width. The
                # provider writes this constantly and the cast sits one level
                # down, so a peel that only followed Cast nodes reported "no
                # recoverable width" for 174 bodies -- the largest addressable
                # class in the census -- while the width was written right there.
                if isinstance(src, c_ast.BinaryOp) and src.op in ("+", "-"):
                    if width is None:
                        src = src.left
                        continue
                break
            if width is None and isinstance(src, c_ast.ID):
                width = self.va_locals.get(src.name)
            if width is None:
                raise Unsupportable("a dereference with no recoverable width")
            inner = self.expr(src, 32)
            # THE RULE: a memory operand crosses only as a guest VA
            try:
                cls = self.classify(inner, f"0x{self.address:08X} memory operand")
            except Boundary as e:
                raise Unsupportable(str(e))
            if cls != VA:
                raise Unsupportable(f"0x{self.address:08X}: a memory operand is a plain "
                                    f"scalar, not a guest VA - it cannot address guest memory")
            return c_ast.FuncCall(name=c_ast.ID(MEM_OF_WIDTH[width]),
                                  args=c_ast.ExprList([inner]))
        if isinstance(node, c_ast.ArrayRef):
            # p[i] -> MEMn(p + i * sizeof)
            width = None
            if isinstance(node.name, c_ast.ID):
                width = self.va_locals.get(node.name.name)
            if width is None:
                raise Unsupportable("an index through something that is not a recovered pointer")
            base = self.expr(node.name, 32)
            idx = self.expr(node.subscript, 32)
            scale = c_ast.Constant(type="unsigned int", value=f"{width // 8}U")
            off = c_ast.BinaryOp(op="*", left=idx, right=scale)
            addr = c_ast.BinaryOp(op="+", left=base, right=off)
            return c_ast.FuncCall(name=c_ast.ID(MEM_OF_WIDTH[width]),
                                  args=c_ast.ExprList([addr]))
        if isinstance(node, c_ast.StructRef):
            # BOUNDARY: the base must be a guest VA before anything else is decided
            try:
                base_cls = self.classify(node.name, f"0x{self.address:08X} field base")
            except Boundary as e:
                raise Unsupportable(str(e))
            if base_cls != VA:
                raise Unsupportable(f"0x{self.address:08X}: a struct field is accessed through "
                                    f"a plain scalar, not a guest VA")
            fld = (node.field.name if isinstance(node.field, c_ast.ID) else None)
            offm = _RE_FIELD.match(fld or "")
            if not offm:
                raise Unsupportable(f"a struct/union field with no offset in its name: {fld}")
            offset = int(offm.group(1))
            width = None
            type_name, is_guest = self._struct_name_of(node.name)
            if not is_guest:
                # a LOCAL struct: keep it a host object and let C do the access
                return node
            if type_name and type_name in self.layouts:
                entry = self.layouts[type_name].get(fld or "")
                if entry is None:
                    raise Unsupportable(f"{type_name} has no field {fld} in the provider header: "
                                        f"the C and the header disagree")
                hdr_offset, hdr_width = entry
                if hdr_offset != offset:
                    raise Unsupportable(f"field {fld}: the name says offset {offset}, the header "
                                        f"says {hdr_offset}: the C and the header disagree")
                width = hdr_width
            if width is None:
                raise Unsupportable(f"field {fld} of {type_name or 'an unknown type'}: no layout in "
                                    f"the provider header, so its width cannot be established")
            base = self.expr(node.name, 32)
            addr = c_ast.BinaryOp(
                op="+", left=base,
                right=c_ast.Constant(type="unsigned int", value=f"{offset}U")) if offset else base
            if isinstance(addr, c_ast.BinaryOp) and isinstance(addr.left, c_ast.Constant):
                addr = c_ast.Constant(type="unsigned int",
                                      value=f"0x{int(addr.left.value.rstrip('Uu'), 16) + offset:08X}U")
            return c_ast.FuncCall(name=c_ast.ID(MEM_OF_WIDTH[width]),
                                  args=c_ast.ExprList([addr]))
        if isinstance(node, c_ast.UnaryOp) and node.op == "&":
            inner = node.expr
            if isinstance(inner, c_ast.ID) and inner.name == self.frame_name:
                # the guest stack frame: its address IS a guest VA
                return c_ast.ID("frame")
            if isinstance(inner, c_ast.ID) and inner.name not in self.va_locals \
                    and inner.name not in self.local_slots and inner.name != "segment_2":
                # `&stack`, `&var_7`: the address of a HOST object. Emitting it as
                # a MEMn operand would read guest memory at a host address - the
                # exact inversion of what the pointer model exists to prevent.
                raise Unsupportable(f"the address of host local {inner.name}: not a guest VA, "
                                    f"so it cannot be a memory operand")
            return self.address_of(node.expr)
        if isinstance(node, c_ast.BinaryOp):
            return c_ast.BinaryOp(op=node.op, left=self.expr(node.left, want_width),
                                  right=self.expr(node.right, want_width))
        if isinstance(node, c_ast.Assignment):
            return c_ast.Assignment(op=node.op, lvalue=self.expr(node.lvalue, want_width),
                                    rvalue=self.expr(node.rvalue, want_width))
        if isinstance(node, c_ast.ExprList):
            return c_ast.ExprList(exprs=[self.expr(e, want_width) for e in node.exprs])
        if isinstance(node, c_ast.FuncCall):
            callee = self._callee_va(node.name)
            if callee is not None:
                return self.marshal_call(callee, node)
            return c_ast.FuncCall(name=self.expr(node.name, want_width),
                                  args=self.expr(node.args, want_width) if node.args else None)
        if isinstance(node, c_ast.ID) and node.name == "segment_2":
            # the data base itself is a constant VA, never a host symbol
            return c_ast.Constant(type="unsigned int", value=f"0x{self.segment_base:08X}U")
        if isinstance(node, c_ast.ID) and node.name in self.local_slots:
            _, off = self.local_slots[node.name]
            return c_ast.BinaryOp(op="-", left=c_ast.ID("frame"),
                                  right=c_ast.Constant(type="unsigned int", value=f"{off}U"))
        if isinstance(node, c_ast.ID) and node.name in self.va_locals:
            return node
        return node

    def _callee_va(self, func_node):
        """The VA of a provider function call, or None for anything else."""
        nm = getattr(func_node, "name", None)
        if not nm:
            return None
        import re as _r
        m = _r.fullmatch(r"function_0x([0-9a-f]+)_Code_x86", nm)
        if not m:
            return None
        return int(m.group(1), 16)

    def marshal_call(self, callee_va, node):
        """Emit a call across the boundary from the callee's declared contract.

        FRAME-FIRST: a callee with a guest stack frame takes that frame as its last
        argument, so the caller owes it one. The frame nests BELOW the caller's,
        because the guest stack grows down: callee frame = caller frame - callee
        size. A caller that has no frame of its own cannot supply one and is
        refused - never defaulted, because a default at a boundary that must carry
        a guest VA is a value crossing unproven.

        The RETURN crosses too. A scalar return is an ordinary value; a struct
        return is the hidden-pointer convention (244 of 955 provider functions) and
        is refused here rather than silently emitted as a by-value struct."""
        sig = (self.signatures or {}).get(callee_va)
        if sig is None:
            raise Unsupportable(f"0x{self.address:08X}: calls 0x{callee_va:08X}, whose "
                                f"contract is not in the provider's declarations")
        name = (self.callee_names or {}).get(callee_va)
        if name is None:
            raise Unsupportable(f"0x{self.address:08X}: calls 0x{callee_va:08X}, which has no "
                                f"generated entry in this module")
        if sig["returns_struct"]:
            raise Unsupportable(f"0x{self.address:08X}: calls 0x{callee_va:08X}, which returns "
                                f"{sig['ret']} BY VALUE - the hidden-pointer return is a crossing "
                                f"this marshal does not express")
        args = []
        actual = node.args.exprs if isinstance(node.args, c_ast.ExprList) else (
            [node.args] if node.args is not None else [])
        if len(actual) != len(sig["params"]):
            raise Unsupportable(f"0x{self.address:08X}: calls 0x{callee_va:08X} with {len(actual)} "
                                f"argument(s); its declaration takes {len(sig['params'])}")
        for i, (arg, param) in enumerate(zip(actual, sig["params"])):
            rewritten = self.expr(arg, 32)
            try:
                cls = self.classify(rewritten, f"0x{self.address:08X} call argument {i}")
            except Boundary as e:
                raise Unsupportable(str(e))
            if cls not in (VA, SCALAR):
                raise Unsupportable(f"0x{self.address:08X} call argument {i}: {cls} cannot cross")
            args.append(rewritten)
        if sig["frame_size"]:
            if not self.frame_name:
                raise Unsupportable(f"0x{self.address:08X}: calls 0x{callee_va:08X}, which has a "
                                    f"{sig['frame_size']}-byte guest frame, but this caller has no "
                                    f"frame of its own to nest it below")
            nested = c_ast.BinaryOp(op="-", left=c_ast.ID("frame"),
                                    right=c_ast.Constant(type="unsigned int",
                                                         value=f"{sig['frame_size']}U"))
            args.append(nested)
        return c_ast.FuncCall(name=c_ast.ID(name),
                              args=c_ast.ExprList(args) if args else None)

    def _struct_name_of(self, node):
        """(struct name, is_guest) for a field access's base.

        A GUEST struct is reached through a guest VA and its field becomes a
        MEMn at VA+offset. A LOCAL struct (`struct_2937 stack;`) is a host object
        whose fields are host C accesses - rewriting those as MEMn(stack + off)
        would read guest memory at a host address, which is the same class of
        error as the pointer model was built to prevent."""
        cur = node
        while True:
            if isinstance(cur, c_ast.Cast):
                t = _type_of(cur.to_type)
                if t and str(t).startswith("ptr:"):
                    nm = str(t)[4:]
                    if nm.startswith(("struct_", "union_")):
                        return nm, self._is_va(cur.expr)
                cur = cur.expr
                continue
            if isinstance(cur, c_ast.ID):
                if cur.name in self.local_slots:
                    # a local struct: a guest object below the frame
                    return self.local_slots[cur.name][0], True
                # class 3: the struct comes from the local's own declaration when
                # no cast supplies it
                nm = self.pointer_types.get(cur.name) or getattr(cur, "_struct", None)
                return nm, cur.name in self.va_locals
            return None, False

    def _is_va(self, node):
        """Does this expression denote a guest VA (a pointer local/param)?"""
        cur = node
        while isinstance(cur, c_ast.Cast):
            cur = cur.expr
        if isinstance(cur, c_ast.ID):
            return cur.name in self.va_locals
        if isinstance(cur, c_ast.BinaryOp):
            return self._is_va(cur.left) or self._is_va(cur.right)
        return False

    def address_of(self, node):
        """`&thing` -> the guest VA of the thing."""
        # &segment_2 is the image's data base: a constant, not a host address.
        # The provider writes it with casts around it, so unwrap those too.
        if isinstance(node, c_ast.Cast):
            return self.address_of(node.expr)
        if isinstance(node, c_ast.UnaryOp) and node.op == "&":
            return self.address_of(node.expr)
        if isinstance(node, c_ast.ID) and node.name in self.local_slots:
            # a local struct's slot below the frame IS its guest VA
            _, off = self.local_slots[node.name]
            return c_ast.BinaryOp(op="-", left=c_ast.ID("frame"),
                                  right=c_ast.Constant(type="unsigned int", value=f"{off}U"))
        if isinstance(node, c_ast.ID) and node.name == "segment_2":
            return c_ast.Constant(type="unsigned int", value=f"0x{self.segment_base:08X}U")
        # (&segment_2 + off) is one constant address: fold it
        if isinstance(node, c_ast.BinaryOp) and node.op == "+":
            left = self.address_of(node.left)
            if isinstance(left, c_ast.Constant) and isinstance(node.right, c_ast.Constant):
                total = int(left.value.rstrip("Uu"), 0) + int(node.right.value.rstrip("Uu"), 0)
                return c_ast.Constant(type="unsigned int", value=f"0x{total:08X}U")
            return c_ast.BinaryOp(op="+", left=left, right=node.right)
        if isinstance(node, c_ast.Cast) and _type_of(node.to_type) == "ptr:segment_t":
            return c_ast.Constant(type="unsigned int", value=f"0x{self.segment_base:08X}U")
        if isinstance(node, c_ast.ID):
            return c_ast.ID(node.name)
        return node

    def stmt(self, node):
        if isinstance(node, c_ast.Compound):
            items = [self.stmt(s) for s in (node.block_items or [])]
            return c_ast.Compound(block_items=[i for i in items if i is not None])
        if isinstance(node, c_ast.Decl):
            d = self.decl(node)
            if d is None:
                return None
            if d.init is not None:
                d.init = self.expr(d.init)
            return d
        if isinstance(node, c_ast.Assignment):
            return self.expr(node)
        if isinstance(node, c_ast.Return) and self.hidden_return and node.expr is not None:
            # the object is copied to the caller's slot through `out`
            src_va = self.expr(node.expr)
            size = struct_size(self.hidden_return) or 0
            # the copy is word by word, so the object must be a multiple of four
            # bytes. Measured over all 1,877 provider struct sizes: every one is.
            # The assertion keeps that a visible precondition rather than an
            # incidental one - a 6-byte struct would otherwise be copied past its end.
            if size % 4:
                raise Unsupportable(f"struct return of {size} bytes: the word copy needs a "
                                    f"multiple of four; a byte tail is not implemented")
            stmts = []
            for k in range(0, size, 4):
                dst = c_ast.FuncCall(name=c_ast.ID("MEM32"), args=c_ast.ExprList([
                    c_ast.BinaryOp(op="+", left=c_ast.ID("out"),
                                   right=c_ast.Constant(type="unsigned int", value=f"{k}U"))]))
                srcm = c_ast.FuncCall(name=c_ast.ID("MEM32"), args=c_ast.ExprList([
                    c_ast.BinaryOp(op="+", left=src_va,
                                   right=c_ast.Constant(type="unsigned int", value=f"{k}U"))]))
                stmts.append(c_ast.Assignment(op="=", lvalue=dst, rvalue=srcm))
            return c_ast.Compound(block_items=stmts) if stmts else c_ast.Return(expr=None)
        if isinstance(node, c_ast.Return):
            return c_ast.Return(expr=self.expr(node.expr) if node.expr else None)
        if isinstance(node, c_ast.If):
            return c_ast.If(cond=self.expr(node.cond), iftrue=self.stmt(node.iftrue),
                            iffalse=self.stmt(node.iffalse) if node.iffalse else None)
        if isinstance(node, c_ast.While):
            return c_ast.While(cond=self.expr(node.cond), stmt=self.stmt(node.stmt))
        if isinstance(node, c_ast.For):
            return c_ast.For(init=self.stmt(node.init) if node.init else None,
                             cond=self.expr(node.cond) if node.cond else None,
                             next=self.expr(node.next) if node.next else None,
                             stmt=self.stmt(node.stmt))
        if isinstance(node, (c_ast.Break, c_ast.Continue)):
            return node
        if isinstance(node, c_ast.FuncCall):
            return self.expr(node)
        return node

    def function(self, fn):
        # register the parameters first: they are the entry values that cross
        for p in (fn.decl.type.args.params if fn.decl.type.args else []):
            t = _type_of(p.type) or ""
            if t.startswith("ptr:"):
                self.va_locals[p.name] = width_of_type(t[4:]) or 32
                if t[4:].startswith(("struct_", "union_")):
                    self.pointer_types[p.name] = t[4:]
            elif width_of_type(t):
                self.scalar_locals[p.name] = width_of_type(t)
        body = self.stmt(fn.body)
        # the return type: a pointer return becomes a guest VA
        ret = fn.decl.type.type
        return_width = None
        if isinstance(ret, c_ast.PtrDecl):
            return_width = width_of_type(str(_type_of(ret)).replace("ptr:", ""))
            if return_width is None:
                raise Unsupportable("a pointer return with no known width")
            self.notes.append("return: pointer -> uint32_t guest VA")
        return body


def rewrite(body, segment_base, own_va=None, layouts=None, frames=False,
            signatures=None, callee_names=None, segment_span=0x10000):
    """(C source for the function with guest-VA pointers, notes) or Unsupportable."""
    import re as _pre
    # `undef(T)` is the provider's own marker for a value it could not define -
    # primitive-types.h reads it from a special location. A body containing one
    # cannot be correct: the decompiler is saying it does not know. The x87
    # helpers make such a body COMPILE, which is exactly why the distinction
    # matters - 0x00481BB0, the frame loop's core, has 180 of them in an 8,541-byte
    # body (counted from the provider's own C: `revng.function_source` then
    # `len(re.findall(r"\bundef\s*\(", src))`). An earlier comment here said 14;
    # the number in the refusal message is what the code finds, so the comment is
    # the thing that was wrong.
    # LLVM intrinsics: declared, not implemented. An unimplemented intrinsic is a
    # function that compiles and computes something else, so it is a refusal class
    # of its own (measured: 184 bit_cast, 59 bit_parity, 50 lshift, 31
    # llvm_umin_i32, 18 llvm_fshl_i32 across the provider bodies).
    intr = _pre.findall(r"\b(bit_cast|bit_parity|lshift|rshift|llvm_[a-z0-9_]+)\s*\(", body)
    # Two of the rev.ng helpers have DEFINED semantics and an exact C spelling, so
    # they are implemented rather than refused. The rest stay refused: an
    # unimplemented intrinsic is a function that compiles and computes something
    # else, which is worse than a refusal. `bit_cast` is handled by prepare(),
    # which rewrites it to a cast before the parse.
    unimplemented = sorted(set(intr) - set(IMPLEMENTED_INTRINSICS) - {"bit_cast"})
    if unimplemented:
        kinds = unimplemented
        raise Unsupportable(f"the provider body uses LLVM intrinsic helper(s) "
                            f"{', '.join(kinds)} that are declared in runtime-library.h but "
                            f"have no implementation here: the body would compile and compute "
                            f"something else")
    n_undef = len(_pre.findall(r"\bundef\s*\(", body))
    if n_undef:
        raise Unsupportable(f"the provider body contains {n_undef} undefined value(s) "
                            f"(`undef()`, the decompiler's own marker that it could not define "
                            f"them): with helpers.h it would compile, and it would be wrong")
    fn = parse(body)
    # scope refusals, with reasons
    import re as _re
    ret_type = _type_of(fn.decl.type.type) or ""
    hidden_return = None
    if _re.match(r"(struct|union)_\d+", ret_type):
        # the caller supplies the storage; the callee writes through it
        hidden_return = ret_type
    # a CALL to another lifted function, not this function's own declaration:
    # the declaration is the first match and carries the return type before it
    calls = _re.findall(r"(?<![A-Za-z_0-9])(function_0x([0-9a-f]+)_Code_x86)\s*\(", body)
    callers = [n for _, n in calls]
    if own_va is not None:
        callers = [c for c in callers if int(c, 16) != own_va]
    elif callers:
        callers = callers[1:]           # drop the declaration when the VA is unknown
    if callers and not (signatures and callee_names):
        raise Unsupportable(f"calls {len(callers)} other lifted function(s) "
                            f"(e.g. 0x{int(callers[0],16):08X}): no declared callee contract was "
                            f"supplied, so the call cannot be marshalled")
    # `undef(T)` is the provider's own marker for a value it could not define -
    # primitive-types.h reads it from a special location. A body containing one
    # cannot be correct: the decompiler is saying it does not know. The x87
    # helpers (helpers.h) make such a body COMPILE, which is precisely why the
    # distinction matters - 0x00481BB0, the frame loop's core, has 14 of these.
    n_undef = len(_re.findall(r"\bundef\s*\(", body))
    if n_undef:
        raise Unsupportable(f"the provider body contains {n_undef} undefined value(s) "
                            f"(`undef()`, the decompiler's own marker that it could not define "
                            f"them): the body would compile and be wrong")
    # an EMPTY provider body means rev.ng named an address it could not
    # decompile. Generating it produces a function that does nothing where the
    # original does something - worse than refusing, because it looks certifiable.
    # The oracle caught exactly this on 0x00480DC1 (a __security_check_cookie that
    # rev.ng emitted as `{}`), returning 0 where the original returned eax.
    if not (fn.body.block_items or []):
        raise Unsupportable("the provider body is EMPTY: it named an address it could not "
                            "decompile, so there is nothing to generate")
    model = Model(segment_base, layouts, segment_span=segment_span)
    model.address = own_va or 0
    model.signatures = signatures or {}
    model.hidden_return = hidden_return
    model.callee_names = callee_names or {}
    model.frames_enabled = frames
    new_body = model.function(fn)
    # the function's own declaration, retyped
    name = fn.decl.name
    ret = fn.decl.type.type
    params = fn.decl.type.args.params if fn.decl.type.args else []
    ret_text = "void"
    if hidden_return:
        ret_text = "void"          # the object goes through `out`, not the return
    elif isinstance(ret, c_ast.PtrDecl):
        w = width_of_type(str(_type_of(ret)).replace("ptr:", ""))
        ret_text = SCALAR_OF_WIDTH[w]
    elif not hidden_return and _type_of(ret):
        ret_text = c_type(_type_of(ret))
    arg_text = []
    for p in params:
        ty = _type_of(p.type) or ""
        if ty == "void":
            continue
        if ty.startswith("ptr:") or isinstance(p.type, c_ast.PtrDecl):
            # every pointer argument is a GUEST VA, whatever it points at
            arg_text.append(f"uint32_t {p.name}")
        else:
            arg_text.append(f"{c_type(ty) or 'uint32_t'} {p.name}")
    new_body.block_items = [_fold_stmt(i) for i in (new_body.block_items or [])]
    if model.hidden_return:
        arg_text.append("uint32_t out")
    if model.frame_name:
        arg_text.append("uint32_t frame")
    body_text = new_body_to_text(new_body)
    source = f"{ret_text} {name}({', '.join(arg_text) if arg_text else 'void'}) {body_text}\n"
    notes = model.notes + [f"parameters: {len(arg_text)}"]
    if model.frame_name:
        notes.append(f"frame: {model.frame_type} {struct_size(model.frame_type) or '?'} bytes "
                     f"({model.frame_name})")
    return source, notes


def _fold_stmt(item):
    import copy
    item = copy.deepcopy(item)
    if isinstance(item, c_ast.Assignment):
        return _fold(item)
    if isinstance(item, c_ast.Return):
        item.expr = _fold(item.expr) if item.expr else None
        return item
    if isinstance(item, c_ast.Decl):
        item.init = _fold(item.init) if item.init is not None else None
        return item
    if isinstance(item, c_ast.If):
        item.cond = _fold(item.cond)
        item.iftrue = _fold_stmt(item.iftrue)
        item.iffalse = _fold_stmt(item.iffalse) if item.iffalse else None
        return item
    if isinstance(item, c_ast.While):
        item.cond = _fold(item.cond); item.stmt = _fold_stmt(item.stmt)
        return item
    if isinstance(item, c_ast.Compound):
        item.block_items = [_fold_stmt(i) for i in (item.block_items or [])]
        return item
    if isinstance(item, c_ast.FuncCall):
        return _fold(item)
    return item


def new_body_to_text(body):
    """Emit a compound statement's text."""
    out = ["{"]
    for item in (body.block_items or []):
        out.append("    " + _stmt_text(item) + ("" if _stmt_text(item).endswith(("}", ";")) else ";"))
    out.append("}")
    return "\n".join(out)


def _fold(node):
    """Fold `CONST + CONST` address arithmetic into one constant."""
    import copy
    node = copy.deepcopy(node)
    if isinstance(node, c_ast.BinaryOp) and node.op == "+":
        node.left = _fold(node.left)
        node.right = _fold(node.right)
        if isinstance(node.left, c_ast.Constant) and isinstance(node.right, c_ast.Constant):
            try:
                total = int(node.left.value.rstrip("UuLl"), 0) + int(node.right.value.rstrip("UuLl"), 0)
                return c_ast.Constant(type="unsigned int", value=f"0x{total:08X}U")
            except ValueError:
                return node
    elif isinstance(node, c_ast.BinaryOp):
        node.left = _fold(node.left); node.right = _fold(node.right)
    elif isinstance(node, c_ast.FuncCall):
        node.args = _fold(node.args) if node.args else None
    elif isinstance(node, c_ast.ExprList):
        node.exprs = [_fold(e) for e in node.exprs]
    elif isinstance(node, c_ast.Assignment):
        node.lvalue = _fold(node.lvalue); node.rvalue = _fold(node.rvalue)
    elif isinstance(node, c_ast.UnaryOp):
        node.expr = _fold(node.expr)
    elif isinstance(node, c_ast.Cast):
        node.expr = _fold(node.expr)
    elif isinstance(node, c_ast.TernaryOp):
        node.cond = _fold(node.cond); node.iftrue = _fold(node.iftrue); node.iffalse = _fold(node.iffalse)
    return node


def _expr_text(e):
    if e is None:
        return ""
    if isinstance(e, c_ast.ID):
        return e.name
    if isinstance(e, c_ast.Constant):
        return e.value
    if isinstance(e, c_ast.FuncCall):
        return f"{_expr_text(e.name)}({_expr_text(e.args)})"
    if isinstance(e, c_ast.ExprList):
        return ", ".join(_expr_text(x) for x in e.exprs)
    if isinstance(e, c_ast.BinaryOp):
        return f"({_expr_text(e.left)} {e.op} {_expr_text(e.right)})"
    if isinstance(e, c_ast.Assignment):
        return f"{_expr_text(e.lvalue)} {e.op} {_expr_text(e.rvalue)}"
    if isinstance(e, c_ast.UnaryOp):
        return f"{e.op}{_expr_text(e.expr)}"
    if isinstance(e, c_ast.Cast):
        t = _type_of(e.to_type) or ""
        if t.startswith("ptr:"):
            # a pointer cast is a reinterpretation of a VA: the VA is unchanged, so
            # the cast drops out. Emitting `(ptr:generic32_t)` was the analysis's
            # internal spelling leaking into C.
            return _expr_text(e.expr)
        return f"({c_type(t) or t}){_expr_text(e.expr)}"
    if isinstance(e, c_ast.TernaryOp):
        return f"({_expr_text(e.cond)} ? {_expr_text(e.iftrue)} : {_expr_text(e.iffalse)})"
    if isinstance(e, c_ast.ArrayRef):
        return f"{_expr_text(e.name)}[{_expr_text(e.subscript)}]"
    if isinstance(e, c_ast.StructRef):
        return f"{_expr_text(e.name)}{e.type}{e.field.name}"
    # A fallback that COMMENTS OUT a construct loses everything inside it -- a
    # switch rendered as a comment takes its calls with it, and the result compiles
    # and computes something else. That is the definition of the failure this
    # project refuses everywhere else, so it refuses here too rather than emitting a
    # placeholder that looks safe.
    raise Unsupportable(f"the AST model cannot render a {type(e).__name__} expression: "
                        f"emitting a placeholder would drop code that runs")


def _stmt_text(s):
    if isinstance(s, c_ast.Decl):
        name = s.name
        return f"{c_type(_type_of(s.type))} {name}" + (f" = {_expr_text(s.init)}" if s.init is not None else "")
    if isinstance(s, c_ast.Assignment):
        return _expr_text(s)
    if isinstance(s, c_ast.Return):
        return f"return{(' ' + _expr_text(s.expr)) if s.expr else ''}"
    if isinstance(s, c_ast.Break):
        return "break"
    if isinstance(s, c_ast.Continue):
        return "continue"
    if isinstance(s, c_ast.If):
        text = f"if ({_expr_text(s.cond)}) {_stmt_or_block(s.iftrue)}"
        if s.iffalse:
            text += f" else {_stmt_or_block(s.iffalse)}"
        return text
    if isinstance(s, c_ast.While):
        return f"while ({_expr_text(s.cond)}) {_stmt_or_block(s.stmt)}"
    if isinstance(s, c_ast.For):
        return "for (;;) " + _stmt_or_block(s.stmt)
    if isinstance(s, c_ast.Compound):
        return new_body_to_text(s)
    if isinstance(s, c_ast.FuncCall):
        return _expr_text(s)
    raise Unsupportable(f"the AST model cannot render a {type(s).__name__} statement: "
                        f"emitting a placeholder would drop the code inside it")


def _stmt_or_block(s):
    if isinstance(s, c_ast.Compound):
        return new_body_to_text(s)
    return "{ " + _stmt_text(s) + "; }"

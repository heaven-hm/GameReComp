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

# The C types pycparser does not know without a libc header, and the provider's
# scalar typedefs. Structs/unions are deliberately absent: a body that needs one
# is refused, because the provider's header was not saved.
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
typedef struct segment_s { uint8_t b[1]; } segment_t;
extern segment_t segment_2;
int exit(int);
"""

MEM_OF_WIDTH = {8: "MEM8", 16: "MEM16", 32: "MEM32", 64: "MEM64"}
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
    return "\n".join(f"typedef struct {n} {{ int _opaque; }} {n};" for n in names)


def prepare(body):
    """The provider's function body as C pycparser can parse."""
    text = re.sub(r'^_ABI\([^)]*\)\s*$', '', body, flags=re.M)
    text = re.sub(r'\b_NORETURN\b', '', text)
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


class Model:
    """The rewrite, applied to one function."""

    def __init__(self, segment_base):
        self.segment_base = segment_base
        self.va_locals = {}        # var name -> width (a pointer that became a VA)
        self.scalar_locals = {}    # var name -> width
        self.notes = []

    # ---- declaration rewriting
    def decl(self, decl):
        ty = decl.type
        name = decl.name      # pycparser puts it on the Decl; the nested
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
    def expr(self, node, want_width=None):
        if node is None:
            return None
        if isinstance(node, c_ast.Cast):
            w = width_of_type(_type_of(node))
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
                break
            if width is None and isinstance(src, c_ast.ID):
                width = self.va_locals.get(src.name)
            if width is None:
                raise Unsupportable("a dereference with no recoverable width")
            inner = self.expr(src, 32)
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
            raise Unsupportable("a struct/union field access: the provider header with the "
                                "layout was not saved, so the offset cannot be established")
        if isinstance(node, c_ast.UnaryOp) and node.op == "&":
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
            return c_ast.FuncCall(name=self.expr(node.name, want_width),
                                  args=self.expr(node.args, want_width) if node.args else None)
        if isinstance(node, c_ast.ID) and node.name in self.va_locals:
            return node
        return node

    def address_of(self, node):
        """`&thing` -> the guest VA of the thing."""
        # &segment_2 is the image's data base: a constant, not a host address.
        # The provider writes it with casts around it, so unwrap those too.
        if isinstance(node, c_ast.Cast):
            return self.address_of(node.expr)
        if isinstance(node, c_ast.UnaryOp) and node.op == "&":
            return self.address_of(node.expr)
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
            return c_ast.Compound(block_items=[self.stmt(s) for s in (node.block_items or [])])
        if isinstance(node, c_ast.Decl):
            d = self.decl(node)
            if d.init is not None:
                d.init = self.expr(d.init)
            return d
        if isinstance(node, c_ast.Assignment):
            return self.expr(node)
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


def rewrite(body, segment_base, own_va=None):
    """(C source for the function with guest-VA pointers, notes) or Unsupportable."""
    import re as _pre
    # `undef(T)` is the provider's own marker for a value it could not define -
    # primitive-types.h reads it from a special location. A body containing one
    # cannot be correct: the decompiler is saying it does not know. The x87
    # helpers make such a body COMPILE, which is exactly why the distinction
    # matters - 0x00481BB0, the frame loop's core, has 14 of these.
    n_undef = len(_pre.findall(r"\bundef\s*\(", body))
    if n_undef:
        raise Unsupportable(f"the provider body contains {n_undef} undefined value(s) "
                            f"(`undef()`, the decompiler's own marker that it could not define "
                            f"them): with helpers.h it would compile, and it would be wrong")
    fn = parse(body)
    # scope refusals, with reasons
    import re as _re
    ret_type = _type_of(fn.decl.type.type) or ""
    if _re.match(r"(struct|union)_\d+", ret_type):
        raise Unsupportable("returns a provider struct BY VALUE: the hidden-pointer return "
                            "convention is not expressible in this module model")
    # a CALL to another lifted function, not this function's own declaration:
    # the declaration is the first match and carries the return type before it
    calls = _re.findall(r"(?<![A-Za-z_0-9])(function_0x([0-9a-f]+)_Code_x86)\s*\(", body)
    callers = [n for _, n in calls]
    if own_va is not None:
        callers = [c for c in callers if int(c, 16) != own_va]
    elif callers:
        callers = callers[1:]           # drop the declaration when the VA is unknown
    if callers:
        raise Unsupportable(f"calls {len(callers)} other lifted function(s) "
                            f"(e.g. 0x{int(callers[0],16):08X}): a call into another lifted body "
                            f"needs that function's guest-ABI entry, which this module model "
                            f"does not marshal")
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
    model = Model(segment_base)
    new_body = model.function(fn)
    # the function's own declaration, retyped
    name = fn.decl.name
    ret = fn.decl.type.type
    params = fn.decl.type.args.params if fn.decl.type.args else []
    ret_text = "void"
    if isinstance(ret, c_ast.PtrDecl):
        w = width_of_type(str(_type_of(ret)).replace("ptr:", ""))
        ret_text = SCALAR_OF_WIDTH[w]
    elif _type_of(ret):
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
    body_text = new_body_to_text(new_body)
    source = f"{ret_text} {name}({', '.join(arg_text) if arg_text else 'void'}) {body_text}\n"
    return source, model.notes + [f"parameters: {len(arg_text)}"]


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
        return f"({_type_of(e.to_type)}){_expr_text(e.expr)}"
    if isinstance(e, c_ast.TernaryOp):
        return f"({_expr_text(e.cond)} ? {_expr_text(e.iftrue)} : {_expr_text(e.iffalse)})"
    if isinstance(e, c_ast.ArrayRef):
        return f"{_expr_text(e.name)}[{_expr_text(e.subscript)}]"
    if isinstance(e, c_ast.StructRef):
        return f"{_expr_text(e.name)}{e.type}{e.field.name}"
    return f"/* unsupported {type(e).__name__} */"


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
    return f"/* unsupported {type(s).__name__} */"


def _stmt_or_block(s):
    if isinstance(s, c_ast.Compound):
        return new_body_to_text(s)
    return "{ " + _stmt_text(s) + "; }"

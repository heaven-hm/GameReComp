"""The structural boundary rule: beside the rewrite, not in the analysis.

Every value crossing from the provider's model into the emitted C must be a GUEST
VA, a PLAIN SCALAR, or a REFUSAL. These tests are one per leaking class seen in
this session, plus one constructed case the session never saw, so the rule is shown
to generalise rather than to cover the known failures.

The four leaks (all the same defect: a value crossing in the source's vocabulary):
  1. a memory operand that is a host local's value, not a guest VA
     (MEM8(var_0) - the two retracted modules);
  2. a plain scalar cast to a pointer, which makes a byte look like an address;
  3. a struct field on a base whose struct never got recovered;
  4. a callee whose signature gained a parameter its callers do not pass.
The constructed fifth: a conditional expression whose two arms are different kinds.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.autorecomp.rename_ast import Boundary, Model, Unsupportable, rewrite

BASE = 0x0049B000
VA = 0x1000


def body_of(text):
    return text


def refuses(text):
    with pytest.raises(Unsupportable):
        rewrite(text, BASE, own_va=VA)


def accepts(text):
    src, _ = rewrite(text, BASE, own_va=VA)
    return src


# ---- class 1: a memory operand that is a host local's value
def test_class1_host_local_as_memory_operand_is_refused():
    refuses("""
void function_0x1000_Code_x86(void) {
    generic8_t var_0 = 1;
    *(generic32_t *) var_0 = 5U;
}
""")


def test_class1_a_constant_address_operand_is_accepted():
    src = accepts("""
void function_0x1000_Code_x86(generic32_t argument_0) {
    *(generic32_t *) (generic32_t) ((generic32_t) &segment_2 + 4944ULL) = argument_0;
}
""")
    assert "MEM32(0x0049C350U)" in src


# ---- class 2: a plain scalar cast to a pointer
def test_class2_scalar_cast_to_a_pointer_is_refused():
    refuses("""
void function_0x1000_Code_x86(void) {
    generic8_t var_0 = 1;
    *(generic32_t *) ((generic32_t *) var_0) = 5U;
}
""")


# ---- class 3: a struct field whose base is not a VA
def test_class3_field_on_a_scalar_base_is_refused():
    refuses("""
void function_0x1000_Code_x86(void) {
    generic32_t var_0 = 1;
    generic32_t var_1 = ((struct_1 *) var_0)->offset_0;
}
""")


# ---- class 4: an argument count that no longer matches (the frame interface)
def test_class4_a_call_with_the_wrong_argument_count_is_refused():
    # the provider body calls another lifted function; the module model has no
    # guest-ABI marshal for that, so it is refused rather than guessed
    refuses("""
void function_0x1000_Code_x86(generic32_t argument_0) {
    generic32_t var_0 = function_0x2000_Code_x86(argument_0);
}
""")


# ---- the constructed fifth: a conditional whose arms are different kinds
def test_constructed_conditional_mixing_a_va_and_a_scalar_is_refused():
    model = Model(BASE)
    import pycparser.c_ast as ast
    cond = ast.TernaryOp(cond=ast.Constant(type="int", value="1"),
                         iftrue=ast.Constant(type="unsigned int", value="0x00400000U"),
                         iffalse=ast.Constant(type="unsigned int", value="7U"))
    with pytest.raises(Boundary):
        model.classify(cond, "constructed")


# ---- and the positive case: a VA crosses
def test_a_constant_address_crosses_as_a_va():
    model = Model(BASE)
    import pycparser.c_ast as ast
    cls = model.classify(ast.Constant(type="unsigned int", value="0x00400000U"), "positive")
    assert cls == "va"



def test_indirect_call_through_provider_type_parses():
    """`((cabifunction_N *) x)()` must PARSE, then be refused on its merits.

    The provider spells an indirect call with a type it never declares. pycparser
    reports the unknown type as ": Invalid expression" with no line, so the body
    was refused for the wrong reason and the message pointed nowhere -- one function
    blocked ten closures behind it. Synthesizing the function type moves the
    refusal to the transform, where the reason is real.
    """
    from tools.autorecomp import rename_ast as R
    body = """
_ABI(Microsoft_x86_cdecl)
void function_0x1000_Code_x86(void) {
    generic32_t *var_0 = (generic32_t *) 0x1000U;
    ((cabifunction_42 *) var_0[12U])();
}
"""
    prelude = R.PRELUDE + R.synthesize_types(body) + R.prepare(body)
    from pycparser import c_parser
    ast = c_parser.CParser().parse(prelude)      # parses
    fns = [e for e in ast.ext if hasattr(e, "body")]
    assert fns, "the body parsed into no function"


def test_bit_cast_is_rewritten_to_a_cast():
    """`bit_cast(T, x)` takes a TYPE as its first argument, so it is not valid C.

    pycparser therefore rejected every body containing one with ": Invalid
    expression" -- no line number, no construct named. It was the single largest
    cause of unparseable bodies (72 of 955), and the provider uses it 203 times.
    A reinterpretation is exactly what a cast is, so the rewrite is exact rather
    than a workaround.
    """
    from tools.autorecomp import rename_ast as R
    out = R.prepare("void f(void){ x = bit_cast(generic8_t *, argument_2); }\n")
    assert "bit_cast" not in out
    assert "((generic8_t *)(argument_2))" in out


def test_bit_cast_nested_and_with_parenthesised_argument():
    """The second argument can nest parens; a one-level regex left 5 behind."""
    from tools.autorecomp import rename_ast as R
    src = ("void f(void){ a = bit_cast(union_1, bit_cast(generic32_t, (x) + (y & -16U))); }\n")
    out = R.prepare(src)
    assert "bit_cast" not in out
    assert out.count("((") >= 2



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

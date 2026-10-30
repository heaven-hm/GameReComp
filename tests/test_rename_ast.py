

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


def test_every_implemented_intrinsic_matches_its_llvm_definition():
    """Each helper we implement must agree with the LLVM semantics it stands in for.

    An unimplemented intrinsic was refused because "it compiles and computes
    something else". An implemented one has the same obligation in the other
    direction: it must compute the SAME thing the provider meant. `llvm.fshl` is
    the one that is easy to get subtly wrong -- a first version split on
    `c >= 32` and disagreed with the definition in 726 of 3456 probed cases while
    looking entirely reasonable. So the definition is executed here, not reasoned
    about.
    """
    import subprocess
    from pathlib import Path
    from tools.autorecomp import rename_ast as R

    for name in ("llvm_fshl_i32", "bit_parity", "lshift",
                 "llvm_smax_i32", "llvm_smin_i32", "llvm_umin_i32",
                 "llvm_umax_i32", "llvm_smin_i16"):
        assert name in R.IMPLEMENTED_INTRINSICS, name

    body = R.IMPLEMENTED_INTRINSICS["llvm_fshl_i32"].strip()
    ref = '''
static uint32_t ref(uint32_t a, uint32_t b, uint32_t c)
{
    c %= 64u;
    if (c == 0u) return a;
    uint64_t x = ((uint64_t)a << 32) | (uint64_t)b;
    return (uint32_t)((x << c) >> 32);
}
int main(void)
{
    uint32_t v[] = {0u, 1u, 0x80000000u, 0xFFFFFFFFu, 0x12345678u, 0xDEADBEEFu};
    for (int i = 0; i < 6; i++)
        for (int j = 0; j < 6; j++)
            for (uint32_t c = 0; c < 96; c++)
                if (llvm_fshl_i32(v[i], v[j], c) != ref(v[i], v[j], c))
                    return 1;
    return 0;
}
'''
    src = "#include <stdint.h>\n" + body + ref
    path = Path("/tmp/intrinsic_check.c")
    path.write_text(src)
    exe = Path("/tmp/intrinsic_check")
    cc = subprocess.run(["cc", "-o", str(exe), str(path)], capture_output=True, text=True)
    assert cc.returncode == 0, cc.stderr
    run = subprocess.run([str(exe)], capture_output=True, text=True)
    assert run.returncode == 0, "the implemented funnel shift disagrees with its definition"


def test_generate_emits_the_definition_of_every_intrinsic_it_calls():
    """A generated module must DEFINE the helpers it calls, not assume them.

    This is the implicit-int defect class exactly: a call to an undeclared function
    compiles, takes its arguments wrongly, and fails somewhere else entirely. The
    generator emitted no definitions until this test existed, so every module using
    an intrinsic was one link away from that failure.
    """
    from tools.autorecomp import rename_ast as R
    from pathlib import Path
    import inspect

    src = inspect.getsource(
        __import__("tools.autorecomp.rename_gen", fromlist=["generate"]).generate)
    assert "intrinsic_definitions" in src, (
        "generate() does not emit the intrinsic definitions it relies on")

    body = "void f(void){ g = bit_parity(x) + lshift(a, b); }\n"
    defs = R.intrinsic_definitions(body)
    assert "bit_parity" in defs and "lshift" in defs
    assert "static inline" in defs

# Windows <-> macOS side-by-side proof (console app)

| Field | Value |
|---|---|
| Original | `windows/arith-calc.exe` SHA-256 `9b59ee613acc7b5a1512f8a5fcc5f65317f0fa79452d01728e1715b63f23d7a6` |
| Windows side | Highball `Games` bottle, engine `x64-crossover26.3-r27`; exit 0 |
| Windows guest stdout SHA-256 | `b6674fd6da06bc9dd36a49a5697efdeb8af7bd354f00b9c6d77359533b572d8f` |
| Native macOS side | rebuilt from the same PE by GameReComp |
| Native guest stdout SHA-256 | `b6674fd6da06bc9dd36a49a5697efdeb8af7bd354f00b9c6d77359533b572d8f` |
| Guest output byte-identical | **True** |

Both sides printed the same guest output block:

```
arith:start
primes<200=46
fib(20)=6765
gcd(1071,462)=21
popcount(0xF0F0F0F0)=16
rotl(0x12345678,8)=878082066
poly=12512
mul=-8369910
div=-18
mod=-141
arith:done
```

This is a **process-level Windows-oracle MATCH for one console app**. It does not establish gameplay, graphics, input or audio equivalence, and it does not generalize to other games. Raw logs are in `windows/arith-calc-wine-stdout.log` and `../END_TO_END_PROOF/batch1/arith-calc/native-run.stdout.log`; the launcher lines in the Wine log are excluded from the guest-output comparison.

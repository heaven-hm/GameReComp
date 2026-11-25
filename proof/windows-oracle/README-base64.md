# Windows <-> macOS oracle proof: base64.exe (scripted stdin)

| Field | Value |
|---|---|
| Original PE SHA-256 | `d805661c60b0b99eab94ad0359ece9b91e7a79fecccc630e92ffdf217c48f879` |
| Input script SHA-256 | `9496a7602af43d866c7f7817732ac149699080a546ed62de1d854ea3625e4cb9` (`1`, `hello world`, `3`) |
| Windows runner | Highball `Games` bottle; exit 0 |
| Native macOS runner | rebuilt from the same PE by GameReComp; exit 0 |
| Encoded result (both) | `aGVsbG8gd29ybGQA` |
| Exit line (both) | `Goodbye!` |
| Verdict | PARTIAL (process-level Windows-oracle match, scripted stdin) |

Commands:
```
highball run -b Games <base64.exe> < input.txt
<rebuilt native> < input.txt
```

This proves that one scripted console scenario produces matching observable output
and exit code on the Windows side and the native macOS side. It is not gameplay,
graphics, audio or input equivalence and does not generalize to other titles.

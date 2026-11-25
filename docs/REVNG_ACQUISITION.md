# rev.ng acquisition: pinned, fail-closed guide

Status: **UNVERIFIED — acquisition guidance only.** This document does not
install anything. Issue #25 stays OPEN until a real revng PASS report exists.
Printing this guide, building the mod, or seeing a help banner is not a
decompilation result and not a PASS.

`tools/revng_probe.py` reported no-core-toolchain-detected on the macOS arm64
research host. Upstream states that rev.ng runs natively on Linux x86-64 only;
other hosts use the `revng/revng` container. This document records *how to
acquire* a pinned rev.ng build and *what counts* as real
`multilifter --lifter revng=<report>` evidence. It does not integrate rev.ng
and does not change any issue state. Run `python3 tools/revng_acquire_guide.py`
for the machine-checkable form; tests are in `tests/test_revng_acquire_guide.py`.

## Authoritative sources

Read from the official documentation and repository on 2026-11-25:

| Purpose | Source |
|---|---|
| Rendered documentation | https://docs.rev.ng |
| Installation procedure (source of truth) | https://github.com/revng/revng/blob/master/share/doc/revng/user-manual/initial-setup.md |
| Repository | https://github.com/revng/revng |
| Official install script (moving `master` channel) | https://rev.ng/downloads/revng-distributable/master/install.sh |
| Official container image | https://hub.docker.com/r/revng/revng |
| Licence text | https://github.com/revng/revng/blob/master/LICENSE |

The documentation source commit recorded at research time is
`1af39407b1826f675ee881faa151a13b3a0a21c9` on `revng/revng` `master`.

## Licence

GPL-2.0 as a whole (the project depends on GPLv2 components, in particular
QEMU); individual files are MIT. Keep rev.ng as a separate external process
and do not link it into the mod DLL. This is a provenance note, not legal
advice.

## Version pins (recorded 2026-11-25)

- Container: `revng/revng@sha256:aaafb2bc57cdc4d6f499b99453a48dfd622ef5611f5eb57927e43b80d03063ac`
  (Docker Hub tags `master` and `latest` both resolved to this digest, pushed
  2026-11-23T13:37:59Z). Use the digest, not `latest`.
- Native archive: `https://revng-website-downloads.s3.fr-par.scw.cloud/binary-archives/public/linux-x86-64/revng-distributable-public-demo/default/none_7361e00740573e8990d13651cedd5314e89e5adc.tar.xz`
  (distributable commit `7361e00740573e8990d13651cedd5314e89e5adc`, 564214476
  bytes, Last-Modified 2026-11-23T12:53:01Z). Upstream publishes no sha256 for
  this archive; compute and record one locally after download.
- The command `curl -L -s https://rev.ng/downloads/revng-distributable/master/install.sh | bash`
  follows a moving channel: it is **NOT a version pin** and appears here only
  so its output can be recognised.

## Host requirements

- Native rev.ng runs on Linux x86-64 only (official `initial-setup.md`).
- macOS, including Apple Silicon/arm64, has no native build: use Docker (or
  colima) with `--platform linux/amd64`.
- Windows is supported through WSL with a Linux x86-64 userland.
- Allow disk/network for the pinned image (~1.17 GB at pin time) or the native
  archive (564214476 bytes).
- No game assets are needed: rev.ng analyses the PE as data. The pinned image
  is linux/amd64; emulation on arm64 is slower but supported.

## Acquisition A — pinned container (recommended on macOS arm64)

```sh
docker pull revng/revng@sha256:aaafb2bc57cdc4d6f499b99453a48dfd622ef5611f5eb57927e43b80d03063ac
# Linux x86-64 host:
docker run --rm -it revng/revng@sha256:aaafb2bc57cdc4d6f499b99453a48dfd622ef5611f5eb57927e43b80d03063ac bash
# macOS / Apple Silicon host (emulated amd64):
docker run --platform linux/amd64 --rm -it revng/revng@sha256:aaafb2bc57cdc4d6f499b99453a48dfd622ef5611f5eb57927e43b80d03063ac bash
# Inside the container, record the tool version and help output:
revng --version | tee revng-version.txt
revng project artifact --help | tee revng-help.txt
```

The `--platform linux/amd64` flag is required on Apple Silicon (official
`initial-setup.md`). Image presence is not proof of a successful lifter run.

## Acquisition B — pinned native archive (Linux x86-64 only)

```sh
curl -fL --retry 3 -o revng-distributable.tar.xz 'https://revng-website-downloads.s3.fr-par.scw.cloud/binary-archives/public/linux-x86-64/revng-distributable-public-demo/default/none_7361e00740573e8990d13651cedd5314e89e5adc.tar.xz'
sha256sum revng-distributable.tar.xz | tee revng-archive.sha256
mkdir -p revng
tar -xJf revng-distributable.tar.xz --strip-components=1 -C revng
cd revng && source ./environment
revng --version | tee revng-version.txt
```

Expect exactly 564214476 bytes and Last-Modified 2026-11-23T12:53:01Z. A
different archive is a different pin; stop and re-review.

## Fail-closed checklist for real `multilifter --lifter revng=<report>` evidence

1. Record host facts (`uname -a`, `docker --version`,
   `docker image inspect <pinned-image>` or `sha256sum` of the archive). A
   probe result of no-core-toolchain-detected is not an installation.
2. Acquire only through the pinned commands; save pull/download output and the
   resolved image digest or archive sha256 in the evidence directory.
3. Show the binary runs from that pin: `revng --version` and
   `revng project artifact --help`; keep the captured output. A help banner
   establishes presence, not decompilation quality.
4. Hash the original PE and each candidate source; record provider version,
   function address, ABI adapter version, test-suite hash and case seeds.
5. Drive the real per-function comparison with
   `tools/lift/difftest.py --project --json <report.json>`; original bytes and
   candidate must both run with resolved dependencies. Faulted, empty or
   untestable comparisons are not PASS.
6. Select with exactly `python3 tools/lift/multilifter.py --lifter
   revng=<report.json> --json <decision.json>`. Require decision `PROMOTE`,
   `chosen_lifter` = `revng`, and zero diverged cases on a real report file.
   Inline JSON, invented verdicts and copied reports are refused.
7. If revng diverges or faults, record the reason and test the PCRecomp
   fallback independently; never label FAILED, BLOCKED or UNRESOLVED as
   verified.
8. Repeat the comparison and selection and compare decision digests;
   determinism is evidence, while correctness stays scoped to driven cases.
9. Feed sealed reports to `tools/issue25_evidence.py`; its CLOSABLE verdict is
   permission to review, not an issue state change. Issue #25 stays OPEN until
   a real revng PASS report exists.
10. Never hand-edit issue state, never emit a GitHub auto-closing keyword, and
    never let this guide stand in for a real provider run.

### Not permitted

- Treat this guide or a probe hit as proof of any installation.
- Write an invented report and feed it to multilifter; only a real difftest
  report file is accepted.
- Treat a build, a help banner, a synthetic report or a single arbitrary seed
  as a PASS verdict.
- Change the state of #25; the guide tool never calls `gh` or the network.

## Limits

This guide is read-only: it runs no installer, container, lifter, `gh` command
or network request, and emits no PASS verdict. The pins record what upstream
published on 2026-11-25; re-verify the digest or archive hash before trusting
a later acquisition. Licence strings are recorded upstream claims, not legal
advice. A CLOSABLE evidence audit is scoped to the reports supplied to it;
this guide cannot establish whole-game equivalence.

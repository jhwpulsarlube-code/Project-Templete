# 툴체인 함정 (CC-RL)

경고·링크 오류가 났을 때, 그리고 `static`/`inline`/매크로/문자열 리터럴/호출 없는 정의를 추가할 때 연다.
host gcc는 이 함정들에서 조용하다. **host PASS ≠ 안전.**
이 표의 CC-RL 동작 주장(W0520177, 인라인 규칙, near 문자열, rlink section GC)은 CC-RL V1.14.00 으로 재측정했고(2026-10-15, 근거 `.scratch/kit-review-findings/measure/12-ccrl-claims/results.md`) V1.16.00 에서도 같은 결과다(2026-10-16, `.scratch/kit-lk-btr-followups/measure/02-ccrl-v1-16-remeasure.md`). 조건이 붙은 행은 조건을 함께 적었다.

| Trap | Fact | Mitigation |
|---|---|---|
| Unused accessor (`static inline` macro) | CC-RL reports `W0520177` for every unused `static` function, with or without `inline`, at any `-O` (a warning; compile exit code stays 0). **GCC `-Wall -Wextra` is silent for `static inline`** but warns for plain `static`. Macro generates 8 accessors, use 6 → 2 warnings; "target dies" only when the project gate pins the warning count | Per-file diagnostic waiver; do not delete spec accessors |
| Called `static` never inlined | A `static` function with **two or more call sites keeps its `call`** at Default, `-Osize` and `-Ospeed`; `-Onothing` (CC-RL has no `-O0`) never inlines. A single-call-site `static` **is** inlined at Default (measured; the `lubx.c` helper has two sites, which is why its `call` survived) | `#pragma inline <func>` directly before the definition forces it at Default/`-Osize`/`-Ospeed` — **not at `-Onothing`**. CC-RL-only pragma — guard with `#if defined (__CCRL__)` or a shared host-gcc TU dies on `-Werror=unknown-pragmas` |
| `va_arg` | Every call produces `W0523082`. **Expected** per spec ([KB 16978923](https://en-support.renesas.com/knowledgeBase/16978923)) | Waive + **pin count so new warnings don't sneak in** |
| near string literals | Some projects' linker directives don't place `.const`. Then rlink puts the unplaced `.const` after `.bss` (RAM region) with `W0561120` (unplaced) and the link fails with `E0562330`/`E0562320`; `F0563100` appears when `.const` lands past the address space. This repo's link uses `-AUTO_SECTION_LAYOUT` and places `.const` at 0x2000, so it does not reproduce here. **Host gcc never sees this.** Layout differs per slot/config | Declare const data **`__far`** (e.g. `__far const char` typedef). **`(__far const char*)` cast is not a fix** — it widens the pointer, not where the literal lives. Use **named far arrays**, not inline literals |
| rlink | **Does not perform section GC.** Bare definition (no caller) → ROM bloat (measured: +29 bytes for one unused function; `-OPtimize=SYmbol_delete` did not remove it; the product link is `-NOOPtimize`) | Wrap both definition AND call site in `#ifdef` probe. Declarations can be unconditional |
| `char` signedness | CC-RL default is **unsigned** (host gcc/x86 is signed) | Use explicit `signed char` when needed |
| Return type mismatch | Declare `bool` function as `int` = C99 UB. Linker succeeds but ABI breaks | Copy declaration from actual defn |

## 보충

- **대형 매크로:** 접근자 매크로를 쓰기 전에 생성되는 심볼 수를 센다. 일부만 쓰면 W0520177이 나므로 전부 쓰거나 면책 개수를 고정한다.
- **`-O` 의존:** 같은 소스가 `-O` 레벨마다 다르게 컴파일된다. 목표 `-O`에서 검증하고 빌드 스크립트에 기록한다. 디버깅용 `-O0` 결과로 커밋하지 않는다.
- **생성기 버전 차이:** 생성된 주변장치 헤더는 프로젝트 버전당 한 번 검토하고, 손으로 쓴 오버라이드를 문서화한다. 생성기 버전(CS+, 코드 생성기 패치)을 기록한다.
- 완료 기준: 경고가 새로 생기면 원인이 설명되거나 면책 개수가 갱신돼 있다.

# ISR 공유 상태와 원자성

ISR 코드, ISR이 공유하는 변수·플래그, 16-bit 값, `/`·`%`를 건드릴 때 연다.
host 테스트는 이 함정들을 잡지 못한다. 목표 빌드와 실기 검증만 게이트다.

| Trap | Fact | Mitigation |
|---|---|---|
| Division | `/` and `%` compile to **separate runtime calls** (no divmod fusion). S1 lib = software path; S2 (MDU) lib wraps calls in `DI`/`POP PSW` → **interrupt-blocking window per call** | Forbidden in ISR/hot path. Decimal conversion: digit-value table + subtraction loop |
| Bit flag write | **Bit-field assignment compiles differently per address context.** Same `sr.bit.x = 0` becomes `clr1 !addr.1` (1 instr) at one site, `and a, !addr` + `mov !addr, a` (RMW = 3 instr) at another — depends on surrounding code. But `reg \|= mask` / `reg &= ~mask` (**constant** mask) → single `set1`/`clr1` at every observed site | Use **const-mask macros** for flag writes. Bit fields read-only. RMW → 3× instructions + **lost atomicity** — ISR sharing that flag bit silently corrupts |
| 16-bit atomicity | `MOVW` is single instruction, but **CC-RL is not obliged to use it.** Measured (V1.14.00 and V1.16.00 give the same asm, `-pack` as in the product flags): `u16` **struct members** via pointer (`[de+off]`) or static direct (`!LOWW(sym+off)`) compile to **2 byte accesses** at every `-O`; a standalone global `u16` compiles to `MOVW`, and so do struct members without `-pack`. The `-O` level does not change which one you get | Never assume "MOVW = atomic." Wrap 16-bit ISR-shared values in PSW save/restore critical section |

## main 쪽 임계구역

ISR 진입 시 RL78은 IE를 지우므로, ISR끼리의 재진입은 ISR이 직접 `EI`를 실행할 때만 생긴다. 일상적인 경우는 main 코드가 접근 도중 인터럽트당하는 것이다.

```c
❌ s_rx_count++;   // CC-RL이 2바이트 접근을 낼 수 있어 둘 사이에 ISR이 끼어든다

✅ PSW 저장 → DI → 접근 → PSW 복원 (무조건 EI 아님)
```

- 임계구역은 **main 쪽에서만** 쓴다. ISR 안에서 CS 매크로를 쓰지 않는다.
- 16-bit 필드와 플래그 비트 RMW는 비원자로 본다.
- ISR이 건드리는 공유 변수를 문서화한다. ISR이 `EI`를 실행하면 선점 가능한 우선순위를 적는다.
- 완료 기준: ISR 공유 변수마다 접근하는 ISR과 main 쪽 보호 방식이 적혀 있다.

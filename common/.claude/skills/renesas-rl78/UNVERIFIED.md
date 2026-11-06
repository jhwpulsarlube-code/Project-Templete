# 미확인 기록 (스킬 갱신 시에만 연다)

이 파일은 SKILL.md와 다른 문서에서 링크하지 않는다. 스킬 내용을 갱신·검증할 때만 읽는다.
실측이나 매뉴얼로 확인되면 해당 문서로 옮기고 여기서 지운다. 확인되지 않은 동안 본 문서에 두지 않는다.

## pitfalls.md §4 — CC-RL 인트린식 이름 (확인 필요)

main 쪽 임계구역 예제에서 쓰던 표기. `isr-and-atomicity.md` 표에 근거가 있는 것은 `__set_psw`뿐이다.
빌드로 확인되면 §4 GOOD 예제에 코드로 되돌린다.

```c
saved = __get_psw(); __DI();
s_rx_count++;
__set_psw(saved);
```

## g16.md — 프로젝트 체크리스트 (Pulsarlube-T_N2)

특정 프로젝트 전용이라 전역 스킬에서 뺐다. 프로젝트 저장소의 문서로 옮기는 것이 맞다. 경로·이름은 해당 프로젝트에서 확인되지 않았다.

## Project Checklist (Pulsarlube-T_N2)

Before first build:

- [ ] Identify generated code location (confirm `gen/` path in project)
- [ ] Add G16 compiler flags to `build/variants/g16/`
- [ ] Create `src/target/isr_*.c` for custom ISR handlers (don't rely on generated)
- [ ] Create `src/target/hw_*.c` for peripheral seams
- [ ] Add G16 exclusion list to `build/rules.mk` (generated files to skip)
- [ ] Document vector table layout (address, handler names)
- [ ] Verify linker script memory layout matches RL78/G16 datasheet

## 생성기 버전 간 주변장치 구조체 차이 예시 (근거 없음)

구 `pitfalls.md` §5의 예시. L13/G16 UART 레이아웃은 실제 생성 헤더로 확인되지 않았다.
`toolchain-traps.md`의 "생성기 버전 차이" 항목은 원칙만 남겼다.

```c
// L13 UART:
typedef struct { uint16_t rx; uint16_t tx; } uart_t;

// G16 UART (generated):
typedef struct { uint8_t rx; uint8_t _pad; uint8_t tx; } uart_t;
```

## g16.md 전체 (근거 없음, 파일 삭제)

구 `g16.md`의 내용. 이 저장소·프로젝트의 G16 실측이나 생성기 출력으로 확인된 항목이 없어 통째로 옮겼다.
확인된 항목만 골라 새 `g16.md`로 되살린다(생성기 배치, 디렉터리 구조, 명명 규칙, 벡터 테이블 형식 등).
`Key difference from L13`의 `drv/gen/` 서술과 `__vector` 지시어 언급은 특히 근거가 없다.

### 1. Code Generator Layout (CS+ G16)

**Generated output location:** `$(GEN_DIR)` (typically `gen/` or `src/gen/`)

| Category | File Pattern | Hand-written Override? |
|----------|------------|--------|
| Peripheral configs | `r_cg_*.c`, `r_cg_*.h` | Exclude from build, own in `src/target/` |
| Vector table | `r_cg_vector_table.c` | Check user sections; may need custom ISR wrapper |
| Startup | `r_cg_startup.c`, `reset.c` | Wrap with `src/target/startup_custom.c` if needed |
| Type definitions | `r_cg_*.h` (read-only reference) | Do not modify; if incompatible, exclude + own |

**G16-specific:** Vector table generation differs from L13. ISR placement may require `__vector` directive.

### 2. Directory Structure

**Standard G16 layout:**
```
src/
├── core/           Pure C (no SFR), host-testable
│   ├── mode.c      Product state machine
│   ├── frame.c     Frame processing
│   └── ...
├── product/        Pure C product policy
│   ├── config.c    Feature flags, tuning tables
│   └── ...
├── target/         SFR, ISR, CC-RL keywords
│   ├── hw_serial.c Seam: UART initialization, TX/RX
│   ├── isr_*.c     ISR handlers (own, not generated)
│   ├── seam_*.c    Other peripheral seams
│   └── linker.x    Linker script (if modified)
└── gen/            Generated (excluded from some builds)
    ├── r_cg_*.c    Generated peripheral drivers (DO NOT EDIT)
    └── r_cg_*.h    Generated headers

build/
├── rules.mk        Build rules, exclusion list, waivers
└── variants/
    └── g16/        G16-specific compiler flags, link options
```

**Key difference from L13:**
- L13: Generated files in `drv/gen/` with flatter structure
- G16: Generated in `gen/` or parallel to src; more header dependencies

### 3. Module Naming Rules (G16 Convention)

**Seam functions (src/target/):**
```c
// Format: hw_<peripheral>_<operation>
hw_serial_init()      // Initialize UART
hw_serial_tx()        // Start TX
hw_serial_rx_get()    // Fetch RX byte

// ISR names:
void serial_rx_isr(void)    // Vector name from link script
void serial_tx_isr(void)
```

**Product/core functions (platform-independent):**
```c
// Format: s_<module>_<operation>
s_frame_process()     // Process one frame
s_mode_set()          // Set device mode
s_tick_inc()          // Increment system tick
```

**File naming:**
```
src/target/hw_serial.c       // Seam implementations
src/target/isr_serial.c      // ISR handlers (hand-written)
src/core/frame.c             // Product logic
src/product/config.c         // Feature config
```

**Avoid:** Mixing `hw_` and `s_` prefixes in same function; use one consistently.

### 4. G16-Specific Issues (To Be Updated)

As you work through the G16 project, record issues here:

- **Interrupt vector layout:** G16 uses different vector table format vs L13. Document once confirmed.
- **SFR register layout:** Some peripherals have different bit positions.
- **Code generator quirks:** E.g., SAU ISR generation, pin assignment.
- **Link script specifics:** Sections, memory layout, constraints.

---

### Resources

- Renesas RL78/G16 Datasheet
- CS+ Code Generator Guide (G16 version)

## 이 저장소 코드에 없는 심볼에 걸린 규칙 (근거 없음)

출처는 G16 계열(`Pulsarlube-T_N2`)로 보이나 G16 코드에서도 `OS_NEAR`·`OS_CRITICAL_EXIT`·`LENIENT_DIAG`·`build/rules.mk` 는 0건이다.
이 저장소(L13)에도 없다. 대응 심볼과 CC-RL 동작이 `.asm`/`.prn` 으로 확인되면 이 저장소의 이름으로 본문에 되살린다.
(`.scratch/kit-review-findings/issues/09-rules-without-matching-symbols.md`)

### CODING_STANDARDS.md §2

- near 함수 포인터 필드는 선언과 정의 **둘 다** `OS_NEAR` 다. 하나만 빠지면 호스트 통과·타깃 실패다.

### isr-and-atomicity.md 표 행

| ISR `OS_CRITICAL_EXIT()` | Restores IE=0 inside ISR (calls `__set_psw(saved)`) | Don't use CS macros in ISR, or document restore intent |

### toolchain-traps.md 표 행

| near function ptr field | Struct field `OS_NEAR` requires **both** function declaration AND definition to be `OS_NEAR`. Skip one → host PASS / target FAIL | Mark hook-table functions `OS_NEAR` on both decl and defn |

### setup.md §4 (제외·면책 목록, `build/rules.mk`)

- 제외 목록 예: 생성된 SAU ISR 을 `src/target/isr_serial.c` 가 소유 → `EXCLUDE_FROM_BUILD += $(GEN_DIR)/r_cg_serial_user.c`. 이 저장소에서는 `cg_src/r_cg_sau_user.c` 가 소유하며 이 구조가 없다.
- 면책 목록 예: `LENIENT_DIAG := -Wno-520177 --lenient-diag-count=2` (W0520177: 매크로가 8개를 만들고 6개를 씀).
- 문서 위치: `build/rules.mk` 또는 `.cmake` 한 곳.

### setup.md §5 (디렉터리 트리)

```
src/core/     Pure C, no SFR or CC-RL keywords → host-testable
src/product/  Pure C product policy (mode tables, etc.) → host-testable
src/target/   SFR, ISR, CC-RL keywords. Only hw_* seam functions exposed to core
```

- 제품 정책을 `target` 에 두면 host 검증이 사라진다. 순수 C 로직은 `product` 로 옮긴다.

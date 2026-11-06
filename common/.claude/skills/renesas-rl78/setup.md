---
name: renesas-rl78-setup
description: Design-phase checklist for RL78 projects — activation model, seam init idempotence, tree structure, exclusion lists
---

# RL78 Design Phase

Run through before writing the first peripheral driver. **Most expensive debt on this project originated here.**

## 1. Activation Model — Define Per Peripheral

**Completion criterion:** For each peripheral, answer all four questions in writing. Store in project design doc or tree comments.

For every new peripheral, ask:
- **Who powers it on?** (external GPIO level, interrupt edge, or request-based hold)
- **Who powers it off?** (expiry, GPIO change, or request cancel)
- **Can SLEEP wake it?** (Yes/No explicit)
- **If not, what's discarded during SLEEP?** (buffered data, timing, state)

**Example:**
```
UART TX:
  ON:  request received (s_tx_put) → set IE, exit SLEEP
  OFF: frame sent (TX-complete ISR) → clr IE
  SLEEP-wake: Yes, TX ISR

TIMER (1ms tick):
  ON:  boot (s_init_tick)
  OFF: never
  SLEEP-wake: No
  Discarded: elapsed ticks during SLEEP (re-sync on wake)
```

**Why:** Absence here means improvising per-slice workarounds. Overlapping mechanisms stack → technical debt.

## 2. Seam Initialization — Idempotence Check

**Completion criterion:** Every seam marked as idempotent or stateful (with documented call-once constraint).

Review `hw_*` init seams:

- **Idempotent seam:** `s_up` guard (or equivalent) ensures safe re-entry. State stored in shadow registers.
  ```c
  static bool s_uart_init_done = false;

  void hw_uart_init(void) {
    if (s_uart_init_done) return;  // idempotent
    // ... config SFR ...
    s_uart_init_done = true;
  }
  ```
  ✅ Can be called mid-state for late requirements.

- **Stateful seam (call-once):** No guard. **Cannot handle requests during state.**
  ```c
  void hw_periph_enable(void) {  // call this exactly once at startup
    // no idempotence check
  }
  ```
  ❌ If enable-request arrives during state transition → lost.

**Fix:** Change stateful → idempotent, or document call-once constraint in header. Activation is enable/disable, not entry hook.

## 3. Seam Shadow Consistency

**Completion criterion:** For each seam with change-only refresh, add paired `*_invalidate()` path.

Review seams with cached hardware state (shadow registers):

- **Change-only refresh:** Updates shadow only if changed.
  ```c
  void hw_mode_set(int mode) {
    if (s_mode_shadow == mode) return;
    s_mode_shadow = mode;
    SFR_MODE = mode;  // hardware
  }
  ```
  ⚠️ If set fails partway → shadow ≠ hardware, forever.

**Fix:** Pair with invalidation:
  ```c
  void hw_mode_invalidate(void) {
    // Force re-sync on next call
    s_mode_shadow = -1;  // sentinel
  }
  ```

## 4. 생성 파일 제외·면책 목록

**완료 기준:** 제외와 면책은 빌드 설정 한 곳에 모으고, 면책은 개수로 고정한다.

생성 파일이 손 작성 로직과 충돌하는 경우를 점검한다.

- 이 저장소에서 생성된 SAU ISR 은 `cg_src/r_cg_sau_user.c` 가 소유한다.
- 면책 목록은 개수를 고정해 새 경고가 보이게 한다(예: W0520177). 시임을 더하면 다시 센다.
- L13 ↔ G16 의 트리 구조·명명 규칙은 프로젝트 저장소에 둔다.

## 5. 시임 호출 순서

- 시임은 앞선 초기화에 의존한다(클럭 → 인터럽트 컨트롤러 → GPIO → 직렬 → 타이머 → ADC 등). 선행 조건을 시임 헤더 주석에 적고, 부팅 순서 테스트로 검증한다.
- **완료 기준:** 시임마다 선행 조건이 문서화돼 있다.

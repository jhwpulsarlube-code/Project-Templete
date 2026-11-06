---
name: renesas-rl78-measurement
description: Verification-phase discipline — timing revalidation, ROM delta opt-in round-trip, hardware bench record, DI-window bounds
---

# RL78 Measurement Discipline

Applied every time you measure real hardware or validate against spec. **Measurement is where assumptions meet silicon.**

## 1. Timing Metrics — Tied to Compiler Flags

**Completion criterion:** Benchmark ID includes compiler `-O` level. Retesting mandatory when flags change.

Every timing number (ISR latency, frame period, wakeup time) is **tied to exact `-O` level used.** When you change optimization:

```
❌ BAD:
Measure: ISR latency = 12µs (no record of flags)
Later: Change to -O3 → ISR latency = 8µs
Claim: "optimization improved latency" (actually a different benchmark)

✅ GOOD:
Measure: [build flags: -O2, -fno-inline] ISR latency = 12µs
Later: Change to -O3 → [retest] ISR latency = 8µs
Compare: Same test, different flags
```

**Before accepting any metric:**
1. Record exact compiler flags (`-O2`, `-O3`, size/speed opts)
2. Record exact code being measured (commit hash or branch)
3. When flags change → re-measure

## 2. ROM Footprint — Opt-In Round-Trip

**Completion criterion:** Feature disabled → enabled → disabled; restored value = original.

ROM cost of conditional features (logging, diagnostics, extra buffers) must be validated via round-trip:

```
Step 1: Measure baseline ROM with feature OFF
  Total ROM = 45000 bytes

Step 2: Enable feature
  Total ROM = 47500 bytes
  Delta = 2500 bytes (claimed cost)

Step 3: Disable feature again
  Total ROM = 45000 bytes ✅
  (must match Step 1 exactly)
```

If Step 3 ≠ Step 1 → conditional logic is broken (dead sections weren't removed, linker GC failed). **Do not trust the claimed delta until round-trip validates.**

**Why:** Build order, link-time visibility, section GC all affect whether a conditional truly removes code. Only round-trip proves it.

## 3. Hardware Benchmark — Record Provenance

**Completion criterion:** Bench result includes: image, test order, date, hardware rev. Retesting re-records all four.

Before marking "PASS" on any hardware measurement:

```
❌ INCOMPLETE:
Test: Frame latency
Result: 50ms ✓ PASS
(Nothing else recorded)

✅ COMPLETE:
Test: Frame latency
Result: 50ms ✓ PASS
Image: git commit abc1234 (branch feature/x, built 2026-09-17 14:30)
Test order: [boot→init→frame1→frame2→...→frame100]
Hardware: RL78/G16 eval board rev 1.2, DIP jumpers: J1=ON J2=OFF
Date: 2026-09-17
```

**Why:** Sequence matters. Example: Frame 1 latency differs from Frame 50 if HW init incomplete. Measuring Frame 1 only = unvalidated claim. Rebooting changes charge on capacitors.

If you later re-measure (new image, different environment):
- Re-run the SAME test sequence
- Re-record all fields
- Compare against prior benchmark at same image

**Do not backfill old benches with new results.** Each result is tied to its provenance.

## 4. Interrupt Disable Window — Bounds Measurement

**Completion criterion:** Measured `DI` window recorded with upper-bound flag and constraining factor (char-time, tick period).

ISR-critical timing (frame timeout, baud synchronization) often depends on the duration hardware interrupts are masked (`DI` instruction window in shared code):

```
Claim: "TX ISR + critical section ≤ 100µs"
Measure: ISR runs 50µs, CS runs 10µs → total 60µs ✓

But if CS code paths vary:
  - Path A: CS = 10µs (toggled inside handler) → total 60µs
  - Path B: CS = 30µs (toggled outside handler) → total 80µs

This is an UPPER BOUND on Path B only if toggle is outside ISR.
If toggle is inside, measurement is tight.
```

**Bounds protocol:**

1. **Mark where `DI` window is measured:**
   ```c
   // DI window = from __di() to __ei() in this ISR
   // If toggle outside ISR, this is upper bound.
   uint16_t t1 = TIMER;
   __di();  // [START window]
   // ... CS critical section ...
   __ei();  // [END window]
   uint16_t t2 = TIMER;
   ```

2. **Constraining factor** (what limits it):
   - Character-time (UART baud sync)
   - Tick period (timer ISR)
   - Frame boundary (CAN message)

3. **Record:**
   ```
   Measurement: DI window ≤ 45µs
   Constraining factor: UART char-time @ 115200 = 87µs
   Toggle: [inside ISR / outside ISR]
   Margin: 87 - 45 = 42µs headroom
   ```

**Why:** Without bounds, you claim a hard metric when it's context-dependent. Later code changes (new `CS_ENTER`) shift the timing. Recording constraints lets you see when re-measurement is needed.

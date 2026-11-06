---
name: renesas-rl78
description: RL78 (L13/G16) + CC-RL — ISR/atomicity traps when editing ISR or shared flags, toolchain traps on warnings or new static/inline/macros/literals, seam and activation design, hardware measurement, generated-code ownership
---

# Renesas RL78 + CC-RL

실기에서 겪은 사실만 적는다. **host gate PASS는 안전이 아니다.** near 포인터, 비트 플래그, 원자성, CC-RL 진단은 host gcc가 못 본다. 아래 작업에 해당하면 먼저 해당 문서를 연다.

| 하는 작업 | 연다 |
|---|---|
| ISR, ISR 공유 변수·플래그, 16-bit 값, `/`·`%` 수정 | [isr-and-atomicity.md](isr-and-atomicity.md) |
| 경고·링크 오류, `static`/`inline`/매크로/문자열 리터럴/호출 없는 정의 추가 | [toolchain-traps.md](toolchain-traps.md) |
| 새 주변장치·시임 설계, 시임 호출 순서 | [setup.md](setup.md) |
| 타이밍·ROM·실기 벤치 수치를 주장 | [measurement.md](measurement.md) |

## 생성 코드 (CS+ 코드 생성기) 소유권

- 생성 파일은 **수정하지 않는다.** 사용자 영역이 있으면 마커 안에서만 고친다. 없으면 **빌드에서 제외**하고 같은 벡터를 손 작성 파일이 소유한다(예: 생성된 SAU ISR에 TX 완료 콜백이 없을 때).
- 재생성이 덮어쓴다고 가정한다. 제외·면책 목록은 빌드 스크립트 한 곳에 둔다. 재생성 뒤에는 제외가 아직 유효한지 확인한다.
- L13과 G16은 생성기 배치와 트리 구조가 다르다.

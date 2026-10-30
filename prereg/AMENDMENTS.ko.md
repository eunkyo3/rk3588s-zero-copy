[English](AMENDMENTS.md)

# 수정 기록

`prereg-v1` 이후 [HYPOTHESES.ko.md](HYPOTHESES.ko.md)와 [PROTOCOL.ko.md](PROTOCOL.ko.md)에 가해진 변경, 그리고 PROTOCOL §14가 나중에 확정한다고 한 모든 값의 추가 전용 기록이다. 기존 항목은 수정하거나 삭제하지 않는다. 영어 항목과 한국어 항목의 내용이 다르면 영어 항목이 우선한다.

각 항목의 형식은 다음과 같다.

```
## A<number> — YYYY-MM-DD — <short title>
- Change:
- Reason:
- Affected data: (runs or results that must be redone, or "none")
- Release: (after freeze-m1 only: amend-A<number>)
```

## A1 — 2026-10-30 — 스파이크 결과: RGA 코어, 컨텍스트 경로, 해제 정책, 디코더 버퍼
- Change: PROTOCOL §14의 다음 값을 확정한다.
  - RGA 코어: `im_opt_t.core = IM_SCHEDULER_RGA3_CORE0 | IM_SCHEDULER_RGA3_CORE1`. 스파이크에서 마스크가 지켜지는 것을 확인했다. RGA3 core 1만 지정하자 부하가 core 1로 옮겨갔다. 이 마스크는 RGA2를 제외한다.
  - 컨텍스트 경로: 늦은 바인딩(§5.1). dma-buf 하나를 컨텍스트 3개 모두에 등록할 수 있으므로 §5.4의 대칭 컨텍스트 대체 경로는 쓰지 않는다.
  - 해제 정책: 한 컨텍스트에서 RKNN 메모리 객체를 해제해도 다른 컨텍스트는 정상 동작하므로 §5.4의 해제 대체 정책은 쓰지 않는다. 종료 시에는 그래도 RKNN 메모리 객체를 먼저 해제한 뒤 fd를 닫는다.
  - C 경로 대체: 없음. C2가 동작한다.
  - 디코더 버퍼 캐시 여부: CPU 캐시됨. 디코더는 `system-dma32` 힙(4 GiB 아래, 캐시됨)에서 할당한다.
- Reason: M1-0 스파이크 결과, [docs/notes/spike.ko.md](../docs/notes/spike.ko.md)
- Affected data: 없음. 아직 측정하지 않았다.
- Release: 없음(`freeze-m1` 이전)

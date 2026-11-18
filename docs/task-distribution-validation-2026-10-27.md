# Agile 태스크 배분 검증 변경 기록

## 범위

- 기준 브랜치: `main`
- 기준 커밋: `9a18c2c551ff0363869673debb6c321731dce5bb`
- PR 대상: `main`
- 관련 TODO: `code-review-todo-2026-10-26.md` P0 #5
- 변경 패키지: `backend/pipeline/domain/agile`

## 문제

LLM 배분 노드는 현재 팀의 미할당 태스크 중 하나를 고르도록 프롬프트했지만, 저장 함수 `assign_task`는 태스크 ID만 조회해 담당자와 상태를 바꿨다. 그 결과 LLM 출력에 팀 밖 태스크 ID, 이미 배분된 태스크 ID, 존재하지 않는 팀원 이름이 포함되면 저장 계층에서 다시 검증되지 않았다. 노드가 받은 클라이언트 제공 `members` 목록은 서버 DB의 팀 소속을 입증하지 못했다.

## 수정 및 리팩터링

- 팀원 선택 근거를 클라이언트 요청의 `members`에서 공유 DB의 `TeamMember` 및 `User` 레코드로 옮겼다. 클라이언트 목록은 하위 호환성을 위해 인자로 유지하지만 권한 판단에는 사용하지 않는다.
- 저장 경계인 `assign_task`에서 태스크 ID, 요청 팀, `unassigned` 상태를 한 번 더 조건으로 검증한다. 업데이트도 동일 조건의 SQL 갱신으로 수행해 검증 이후 상태가 바뀐 태스크를 덮어쓰지 않게 했다.
- 담당자 ID가 요청 팀의 실제 `TeamMember`인지 확인하고, DB에 저장된 이름과 역할로 area 호환성을 검증한다. 입력 이름만으로 사용자를 선택하지 않는다.
- 중복 이름은 LLM 출력의 이름만으로 대상을 특정할 수 없으므로 배분 후보에서 제외한다.
- 노드의 사전 역할 검사는 권한의 원천으로 삼지 않고, 단일 저장 경계가 최종 결정을 수행하도록 중복 검증을 정리했다.

## 영향받는 경로와 회귀 사례

수정 파일:

- `backend/pipeline/domain/agile/nodes/task_distributor.py`
- `backend/pipeline/domain/agile/task_coordinator.py`
- `backend/pipeline/domain/agile/test/test_task_distribution_gate.py`

테스트는 가짜 팀원과 조작된 LLM 배분, 미존재 태스크, 타 팀 태스크, 이미 배분된 태스크, 역할과 area 불일치, 유효한 정상 배분을 다룬다. 유효한 배분은 기존 동작대로 `pending_approval` 상태로 전환된다.

## 검증 근거

- 수정 전 HEAD 사본에서 신규 회귀 테스트 실행: 가짜 팀원 배분 공격이 실패하는 것을 확인했다.
- 현재 Agile 전용 테스트: `10 passed`, `5 subtests passed`.
- `git -c core.whitespace=cr-at-eol diff --check -- backend/pipeline/domain/agile`: 통과.
- 확장 phase6/phase8 테스트는 이전 실행에서 29개 실패했다. 구형 심볼/UI 기대치 및 sandbox의 읽기 전용 DB 오류가 확인되었으며, 이 확장 실패 전체는 이번 기록에서 해결하지 않았다.
- 유료 LLM 및 외부 API 호출은 하지 않았다.

## 경계와 남은 작업

이 변경은 태스크 배분 저장의 팀·상태·담당자 검증을 강화한다. 배분 REST 엔드포인트 요청자의 인증과 팀 권한 확인은 공용 REST 담당 범위의 별도 P0 작업이며 여기서는 변경하지 않았다. 따라서 이 변경만으로 해당 API의 요청자 인가가 해결됐다고 보지 않는다.

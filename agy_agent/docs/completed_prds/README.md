# 📦 완료된 PRD 보관소 (Completed PRDs Archive)

> 수행이 끝난 PRD를 모아두는 공간입니다. **다음 작업의 기획 신호와 뒤섞이지 않도록**
> 활성 워크스페이스 루트의 `PRD.md` 는 완료 시 이곳(또는 각 프로젝트의 `docs/completed_prds/`)으로 이동됩니다.

## 운영 방식

- **자동:** 제품이 최종 완료(`product_gate` 90점 게이트 + 전체 마일스톤 PASS)되면
  `auto_pilot.py` 가 `prd_archive.archive_prd(workspace, reason="final_release")` 를 호출하여
  워크스페이스의 `PRD.md` 를 `docs/completed_prds/PRD_{제품명}_{타임스탬프}.md` 로 이동하고,
  루트에는 "아카이브됨" 스텁만 남깁니다.
- **수동:** `python -c "from prd_archive import archive_prd; archive_prd('/path/to/workspace')"`
- **목록:** `python -c "from prd_archive import list_archived_prds; print(list_archived_prds('/path/to/workspace'))"`

원본은 삭제하지 않고 보존합니다(감사 추적). 루트 스텁에는 원본 경로가 기록됩니다.

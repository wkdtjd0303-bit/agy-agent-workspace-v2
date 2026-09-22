# 📁 Google Drive 대용량 파일 전용 연동 가이드

`!5_agy_agent` 봇은 8~10MB를 초과하는 대용량 파일(릴리즈/디버그 APK, 소스 전체 zip, 대용량 비디오 등)을 대표님의 **공식 30TB Google Drive**에 자동 업로드하고 원클릭 공유 링크를 디스코드에 즉시 전송합니다.

---

## 🛠️ Google Drive 연동 방식

### 1순위: rclone 30TB Google Drive 연동 (현재 활성화됨 ✅)
* 시스템 내장 `rclone`에 `gdrive:` (30TB) 원격 저장소가 등록되어 있어 별도의 JSON 키 없이도 고속 업로드 및 공식 `drive.google.com` 링크 생성이 완벽하게 작동합니다.
* 저장 폴더: Google Drive 내 `AGY_Deliverables/`

### 2순위: Google Cloud 서비스 계정 연동 (선택 사항)
* `credentials/service_account.json` 파일이 존재할 경우 Google Drive v3 API를 통해 무인 업로드를 지원합니다.

---

## 🔒 보안 및 전송 정책 (엄격한 Google Drive 전용)
* `tmpfiles.org`, `catbox/litterbox` 등 신뢰할 수 없는 제3자 임시 사이트로의 파일 공유가 완전히 차단되어 있습니다 (`GDRIVE_ONLY_MODE=true`).
* 모든 파일 공유는 오로지 공식 Google Drive 링크 및 디스코드 직접 첨부로만 안전하게 제공됩니다.

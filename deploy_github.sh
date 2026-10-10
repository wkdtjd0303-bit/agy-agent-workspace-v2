#!/usr/bin/env bash
# ==============================================================================
# agy_agent GitHub One-Click Deployment Script
# Target Remote: https://github.com/wkdtjd0303-bit/agy_agent.git
# ==============================================================================

set -euo pipefail

REPO_DIR="/home/jangsung/Desktop/autocoding/!05_agy_agent"
TARGET_REMOTE="agy_agent"
TARGET_URL="https://github.com/wkdtjd0303-bit/agy_agent.git"
BRANCH="main"

echo "=========================================================="
echo "🚀 agy_agent GitHub 원클릭 배포 파이프라인"
echo "📂 저장소 경로: ${REPO_DIR}"
echo "🎯 대상 원격: ${TARGET_URL} (${BRANCH})"
echo "=========================================================="

cd "${REPO_DIR}"

# 1. 인자 또는 환경변수로 토큰이 전달된 경우
INPUT_TOKEN="${1:-${GITHUB_TOKEN:-}}"

if [ -n "${INPUT_TOKEN}" ]; then
    echo "🔑 제공된 GitHub Token으로 원격 URL 임시 인증 구성 중..."
    AUTH_URL="https://${INPUT_TOKEN}@github.com/wkdtjd0303-bit/agy_agent.git"
    git push "${AUTH_URL}" "${BRANCH}"
    echo "✅ [성공] GitHub 토큰 인증을 통해 배포가 성공적으로 완료되었습니다!"
    exit 0
fi

# 2. 기존 gh / git 자격 증명 헬퍼를 사용한 표준 푸시 시도
echo "📡 기존 Git 자격 증명 및 gh 인증 상태 확인 후 푸시 시도 중..."
if git push "${TARGET_REMOTE}" "${BRANCH}"; then
    echo "✅ [성공] agy_agent 배포가 성공적으로 완료되었습니다!"
    exit 0
else
    echo "----------------------------------------------------------"
    echo "⚠️ [인증 오류 안내]"
    echo "현재 저장된 GitHub Personal Access Token(PAT)이 만료되었거나 무효합니다."
    echo ""
    echo "해결 방법 1 (가장 간편한 방법 - gh CLI 재인증):"
    echo "  $ gh auth login"
    echo "  그 후 다시 아래 명령어를 실행하십시오:"
    echo "  $ ./deploy_github.sh"
    echo ""
    echo "해결 방법 2 (새 GitHub Token 직접 전달):"
    echo "  GitHub (Settings -> Developer Settings -> Personal access tokens)"
    echo "  에서 repo 권한이 있는 토큰(ghp_...)을 발급받은 후 실행하십시오:"
    echo "  $ ./deploy_github.sh <YOUR_NEW_GITHUB_TOKEN>"
    echo "----------------------------------------------------------"
    exit 1
fi

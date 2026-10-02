#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pytest 전역 격리 설정 (Hermetic Test Isolation).

[배경/버그]
`GlobalModelManager.set_role_model()` / `set_role_enabled()` / `set_all_qa_enabled()` 는
디스크의 `state/global_model_config.json` 과 `.env` 를 실제로 저장한다.
따라서 테스트 스위트를 실행하면 **운영 중인 라이브 모델 설정과 .env 가 덮어써지는**
치명적 부작용이 있었다 (예: Coder=gemini/agy, qa2=solar/hermes, qa4~6 활성화로 오염).

[대책]
1. 테스트 수집(collection) 이전인 conftest import 시점에
   `AGY_MODEL_CONFIG_FILE` / `AGY_ENV_FILE` 환경변수를 임시 샌드박스 경로로 지정한다.
   → model_manager 의 `save()` / `_sync_to_env_file()` 은 샌드박스에만 기록한다.
   → `importlib.reload(model_manager)` 로 모듈이 재로드되어도 경로가 유지된다.
2. 실제 라이브 파일을 백업하고, 테스트 종료 시 변조가 감지되면 원복한다 (2차 안전망).
3. 각 테스트 전후로 런타임 config 속성과 GlobalModelManager 메모리 상태를 스냅샷/복원한다.
"""

import os
import json
import atexit
import hashlib
import tempfile
import warnings
from pathlib import Path

import pytest

_BASE_DIR = Path(__file__).resolve().parent.parent
_REAL_CONFIG_FILE = _BASE_DIR / "state" / "global_model_config.json"
_REAL_ENV_FILE = _BASE_DIR / ".env"

_SANDBOX_DIR = tempfile.mkdtemp(prefix="agy_test_model_sandbox_")
_SANDBOX_CONFIG = Path(_SANDBOX_DIR) / "global_model_config.json"
_SANDBOX_ENV = Path(_SANDBOX_DIR) / ".env"

# 반드시 test 모듈이 model_manager 를 import 하기 전에 지정되어야 한다.
os.environ["AGY_MODEL_CONFIG_FILE"] = str(_SANDBOX_CONFIG)
os.environ["AGY_ENV_FILE"] = str(_SANDBOX_ENV)

# [출력 가드 격리] 한국어 강제 재작성은 외부 LLM을 1회 추가 호출하므로 단위 테스트에서는
# 비활성화하여 결정성/속도를 보장한다. (판정 태그 첫 줄 강제 등 결정론적 가드는 그대로 동작)
# 한국어 재작성 경로 자체는 tests/test_output_guard.py 에서 명시적으로 opt-in 하여 검증한다.
os.environ["AGY_OUTPUT_KOREAN_REPAIR"] = "0"


def _seed_sandbox_from_defaults():
    """샌드박스 기준선을 DEFAULT_ROLES 로 고정해 라이브 .env/state 와 무관한 결정적 테스트를 보장한다."""
    import sys

    if str(_BASE_DIR) not in sys.path:
        sys.path.insert(0, str(_BASE_DIR))
    try:
        import model_manager as _mm
        if not _SANDBOX_CONFIG.exists():
            _SANDBOX_CONFIG.parent.mkdir(parents=True, exist_ok=True)
            data = {k: dict(v) for k, v in _mm.DEFAULT_ROLES.items()}
            _SANDBOX_CONFIG.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        # import 실패 시에도 격리 경로 지정 자체는 유지된다.
        pass


_seed_sandbox_from_defaults()


def _read_bytes(path: Path):
    try:
        return path.read_bytes() if path.is_file() else None
    except Exception:
        return None


def _sha(data):
    return hashlib.sha256(data).hexdigest() if data is not None else None


# 실제 라이브 파일 백업 (테스트가 어떤 경로로든 변조할 경우 원복용)
_REAL_CONFIG_BACKUP = _read_bytes(_REAL_CONFIG_FILE)
_REAL_ENV_BACKUP = _read_bytes(_REAL_ENV_FILE)
_REAL_CONFIG_SHA = _sha(_REAL_CONFIG_BACKUP)
_REAL_ENV_SHA = _sha(_REAL_ENV_BACKUP)


def _restore_real_files_if_modified() -> bool:
    """테스트로 인해 실제 라이브 파일이 변조되었다면 백업본으로 원복한다. (원복 발생 시 True)"""
    restored = False
    if _REAL_CONFIG_BACKUP is not None and _sha(_read_bytes(_REAL_CONFIG_FILE)) != _REAL_CONFIG_SHA:
        _REAL_CONFIG_FILE.write_bytes(_REAL_CONFIG_BACKUP)
        restored = True
    if _REAL_ENV_BACKUP is not None and _sha(_read_bytes(_REAL_ENV_FILE)) != _REAL_ENV_SHA:
        _REAL_ENV_FILE.write_bytes(_REAL_ENV_BACKUP)
        restored = True
    return restored


atexit.register(_restore_real_files_if_modified)


def pytest_sessionfinish(session, exitstatus):
    _restore_real_files_if_modified()


_CONFIG_ATTRS = (
    "CODER_MODEL", "CODER_FALLBACK_MODEL", "DEFAULT_AI_ENGINE", "DEFAULT_ENGINE",
    "QA_MODEL", "AUDITOR_MODEL", "COMMANDER_MODEL", "IMAGE_MODEL", "IMAGE_ENGINE",
    "QA2_MODEL", "QA2_ENGINE", "EXTENDED_QA_CONFIGS",
)


@pytest.fixture(autouse=True)
def _isolate_model_manager_state():
    """테스트 1건마다 모델 설정 상태(디스크/메모리/config 런타임 속성)를 격리·원복한다."""
    try:
        import config
        import model_manager as mm
    except Exception:
        yield
        return

    cfg_before = _read_bytes(_SANDBOX_CONFIG)
    env_before = _read_bytes(_SANDBOX_ENV)
    attr_before = {a: getattr(config, a, None) for a in _CONFIG_ATTRS}
    had_attrs = {a: hasattr(config, a) for a in _CONFIG_ATTRS}

    yield

    # 1. 샌드박스 파일 원복
    try:
        if cfg_before is not None:
            _SANDBOX_CONFIG.write_bytes(cfg_before)
        elif _SANDBOX_CONFIG.exists():
            _SANDBOX_CONFIG.unlink()
        if env_before is not None:
            _SANDBOX_ENV.write_bytes(env_before)
        elif _SANDBOX_ENV.exists():
            _SANDBOX_ENV.unlink()
    except Exception:
        pass

    # 2. config 런타임 속성 원복
    for a, value in attr_before.items():
        try:
            if had_attrs[a]:
                setattr(config, a, value)
            elif hasattr(config, a):
                delattr(config, a)
        except Exception:
            pass

    # 3. GlobalModelManager 메모리 상태를 샌드박스 기준선으로 재장전한다.
    #    ※ _roles.clear() 로 비우면, importlib.reload 로 갈라진 구(舊) 클래스가
    #      _initialized=True + 빈 _roles 를 보고 모델을 '' 로 반환하는 순서 의존 버그가 생긴다.
    #      따라서 '비우기' 대신 '샌드박스 파일에서 재초기화'하여 항상 비어있지 않게 유지한다.
    try:
        mm.GlobalModelManager._initialized = False
        mm.GlobalModelManager.initialize()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _guard_real_files_untouched():
    """각 테스트 종료 시 실제 라이브 파일 변조를 즉시 감지/원복하고 경고한다."""
    yield
    if _restore_real_files_if_modified():
        warnings.warn(
            "🚨 테스트 격리 실패: 실제 state/global_model_config.json 또는 .env 가 테스트에 의해 변경되어 "
            "백업본으로 원복했습니다. AGY_MODEL_CONFIG_FILE/AGY_ENV_FILE 격리 경로를 우회하는 코드가 있는지 확인하십시오.",
            RuntimeWarning,
            stacklevel=1,
        )

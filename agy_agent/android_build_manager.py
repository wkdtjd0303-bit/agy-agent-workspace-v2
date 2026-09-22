import os
import shutil
import time
import logging
import asyncio
import re
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple

logger = logging.getLogger("AndroidBuildManager")

class AndroidBuildManager:
    """
    안드로이드 실시간 빌드 엔진
    최신 소스코드를 즉시 컴파일(assembleRelease / assembleDebug)하여
    타임스탬프 APK({AppName}-YYYYMMDD_HHMMSS.apk) 및 최신본({AppName}-latest.apk)을 자동 생성합니다.
    """

    @classmethod
    def find_android_project(cls, workspace: str) -> Optional[str]:
        """
        주어진 워크스페이스 또는 하위 디렉토리에서 gradlew가 위치한 안드로이드 프로젝트 루트를 탐색합니다.
        """
        if not workspace or not os.path.isdir(workspace):
            return None

        # 1. 워크스페이스 루트에 gradlew가 있는 경우
        if os.path.isfile(os.path.join(workspace, "gradlew")):
            return workspace

        # 2. android/ 서브디렉토리에 있는 경우 (React Native / Flutter 등)
        sub_android = os.path.join(workspace, "android")
        if os.path.isdir(sub_android) and os.path.isfile(os.path.join(sub_android, "gradlew")):
            return sub_android

        # 3. 최대 2단계 하위 디렉토리 탐색 (단, 워크스페이스가 다중 프로젝트 허브 루트인 경우 하위 프로젝트로 임의 탈출 방지)
        is_hub_root = (os.path.basename(os.path.abspath(workspace)).lower() == "autocoding")
        for root, dirs, files in os.walk(workspace):
            if is_hub_root and os.path.abspath(root) != os.path.abspath(workspace):
                dirs.clear()
                continue
            dirs[:] = [d for d in dirs if d not in [".git", ".gradle", "build", ".venv", "node_modules"]]
            # 인접 프로젝트 사일로(!) 침범 방지
            if not is_hub_root:
                rel = os.path.relpath(root, workspace)
                if rel != "." and any(part.startswith("!") for part in rel.split(os.sep)):
                    dirs.clear()
                    continue
            depth = len(os.path.relpath(root, workspace).split(os.sep))
            if depth > 2:
                continue
            if "gradlew" in files and any(f.endswith((".gradle", ".gradle.kts")) for f in files):
                return root

        return None

    @classmethod
    def extract_app_name(cls, project_dir: str) -> str:
        """
        settings.gradle(.kts) 또는 디렉토리명에서 앱 이름을 추출합니다.
        """
        # 1. settings.gradle / settings.gradle.kts 에서 rootProject.name 검색
        for s_name in ["settings.gradle.kts", "settings.gradle"]:
            s_path = os.path.join(project_dir, s_name)
            if os.path.isfile(s_path):
                try:
                    with open(s_path, "r", encoding="utf-8", errors="ignore") as f:
                        for line in f:
                            if "rootProject.name" in line:
                                parts = line.split("=")
                                if len(parts) >= 2:
                                    raw_val = parts[1].strip().strip('"').strip("'")
                                    if raw_val:
                                        return raw_val
                except Exception:
                    pass

        # 2. app/build.gradle(.kts) 에서 namespace 또는 applicationId 검색
        for b_name in [os.path.join("app", "build.gradle.kts"), os.path.join("app", "build.gradle")]:
            b_path = os.path.join(project_dir, b_name)
            if os.path.isfile(b_path):
                try:
                    with open(b_path, "r", encoding="utf-8", errors="ignore") as f:
                        for line in f:
                            if "namespace" in line or "applicationId" in line:
                                parts = line.split("=") if "=" in line else line.split()
                                if len(parts) >= 2:
                                    val = parts[-1].strip().strip('"').strip("'")
                                    if val and "." in val:
                                        return val.split(".")[-1].capitalize()
                except Exception:
                    pass

        # 3. 프로젝트 폴더명 기반 기본값
        base_name = os.path.basename(os.path.abspath(project_dir))
        return base_name.replace("!", "").replace("_", "").capitalize() or "App"

    @classmethod
    async def build_apk(
        cls,
        workspace: str,
        build_type: str = "auto",
        timeout_sec: float = 300.0,
        channel_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        최신 소스코드를 바탕으로 즉시 Gradle 빌드를 실행하고,
        신규 생성된 APK에 타임스탬프를 부여하여 반환합니다.
        """
        project_dir = cls.find_android_project(workspace)
        if not project_dir:
            return {
                "success": False,
                "error": f"워크스페이스(`{workspace}`) 내에서 안드로이드 빌드 환경(`gradlew`)을 찾을 수 없습니다.",
                "duration_sec": 0.0
            }

        gradlew_path = os.path.join(project_dir, "gradlew")
        try:
            os.chmod(gradlew_path, 0o755)
        except Exception as e:
            logger.warning(f"chmod +x failed on {gradlew_path}: {e}")

        # 환경 변수 준비
        env = dict(os.environ)
        if "ANDROID_HOME" not in env and "ANDROID_SDK_ROOT" not in env:
            cand_sdk = os.path.expanduser("~/Android/Sdk")
            if os.path.isdir(cand_sdk):
                env["ANDROID_HOME"] = cand_sdk
                env["ANDROID_SDK_ROOT"] = cand_sdk

        build_start_time = time.time()
        tasks_to_try = []
        if build_type == "release":
            tasks_to_try = ["assembleRelease"]
        elif build_type == "debug":
            tasks_to_try = ["assembleDebug"]
        else:
            # auto 모드: Release 우선 시도 후 실패 시 Debug 폴백
            tasks_to_try = ["assembleRelease", "assembleDebug"]

        last_error = ""
        success_task = None
        log_lines: List[str] = []

        for task in tasks_to_try:
            logger.info(f"Executing Gradle build: {gradlew_path} {task} in {project_dir}")
            try:
                proc = await asyncio.create_subprocess_exec(
                    "./gradlew", task, "--no-daemon",
                    cwd=project_dir,
                    env=env,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT
                )
                stdout_data, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
                out_text = stdout_data.decode("utf-8", errors="replace") if stdout_data else ""
                log_lines = out_text.splitlines()

                if proc.returncode == 0:
                    success_task = task
                    logger.info(f"Gradle build task '{task}' succeeded!")
                    if channel_id:
                        try:
                            from autonomy_guard import AutonomySafetyLayer
                            AutonomySafetyLayer.record_progress(channel_id, "gradle_build_success", f"{task} completed")
                        except Exception:
                            pass
                    break
                else:
                    last_error = f"Gradle task '{task}' failed with exit code {proc.returncode}"
                    tail_err = "\n".join(log_lines[-15:])
                    logger.warning(f"{last_error}:\n{tail_err}")
            except asyncio.TimeoutError:
                last_error = f"Gradle 빌드 시간 초과 ({timeout_sec:.0f}초 초과)"
                logger.error(last_error)
                if proc:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                break
            except asyncio.CancelledError:
                logger.info("Gradle build cancelled. Terminating build process...")
                if proc:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                raise
            except Exception as e:
                last_error = f"Gradle 실행 중 예외 발생: {e}"
                logger.error(last_error)

        duration = time.time() - build_start_time

        if not success_task:
            error_lines = [line for line in log_lines if line.strip().startswith("e: ") or "Unresolved reference" in line or "Compilation error" in line]
            if error_lines:
                tail_summary = "\n".join(error_lines[:10])
            else:
                tail_summary = "\n".join(log_lines[-12:]) if log_lines else last_error
            return {
                "success": False,
                "error": f"빌드 실패 ({last_error})\n```text\n{tail_summary}\n```",
                "duration_sec": duration,
                "log_tail": tail_summary
            }

        # 신규 빌드된 APK 파일 탐색 (build_start_time 이후 생성/수정된 파일 우선)
        candidate_apks: List[Tuple[str, float, int]] = []
        search_dirs = [
            project_dir,
            os.path.join(project_dir, "app", "build", "outputs", "apk"),
            os.path.join(project_dir, "build", "outputs", "apk"),
            workspace
        ]

        for s_dir in search_dirs:
            if not os.path.isdir(s_dir):
                continue
            for root, _, files in os.walk(s_dir):
                for f in files:
                    if f.lower().endswith(".apk") and not f.lower().endswith("-unaligned.apk"):
                        p = os.path.join(root, f)
                        try:
                            mtime = os.path.getmtime(p)
                            sz = os.path.getsize(p)
                            candidate_apks.append((p, mtime, sz))
                        except Exception:
                            pass

        if not candidate_apks:
            return {
                "success": False,
                "error": "Gradle 빌드는 성공했으나 생성된 `.apk` 산출물을 디스크에서 찾지 못했습니다.",
                "duration_sec": duration
            }

        # 1. 방금 빌드된 APK (빌드 시작 시각 - 10초 이후) 필터
        fresh_apks = [c for c in candidate_apks if c[1] >= (build_start_time - 10.0)]
        target_list = fresh_apks if fresh_apks else candidate_apks

        # 최신 수정 시간 순 정렬
        target_list.sort(key=lambda x: x[1], reverse=True)
        best_apk_path, best_mtime, best_sz = target_list[0]

        app_name = cls.extract_app_name(project_dir)
        now_ts = datetime.now().strftime("%Y%m%d_%H%M%S")

        # 헌법 제7조: 자동 타임스탬프({AppName}-YYYYMMDD_HHMMSS.apk) 및 최신본({AppName}-latest.apk) 보존
        timestamped_name = f"{app_name}-{now_ts}.apk"
        timestamped_path = os.path.join(workspace, timestamped_name)
        latest_name = f"{app_name}-latest.apk"
        latest_path = os.path.join(workspace, latest_name)

        try:
            # 워크스페이스 루트로 복사
            if os.path.abspath(best_apk_path) != os.path.abspath(timestamped_path):
                shutil.copy2(best_apk_path, timestamped_path)
            shutil.copy2(timestamped_path, latest_path)
            final_apk_path = timestamped_path
            final_apk_name = timestamped_name
            final_sz = os.path.getsize(timestamped_path)
        except Exception as e:
            logger.warning(f"Failed to copy timestamped APK: {e}")
            final_apk_path = best_apk_path
            final_apk_name = os.path.basename(best_apk_path)
            final_sz = best_sz

        # Clean up older timestamped APKs to prevent storage bloat (keep latest 5)
        try:
            cls.cleanup_old_apks(workspace, app_name, keep_count=5)
        except Exception:
            pass

        if channel_id:
            try:
                from autonomy_guard import AutonomySafetyLayer
                AutonomySafetyLayer.record_progress(channel_id, "apk_ready", final_apk_name)
            except Exception:
                pass

        return {
            "success": True,
            "apk_path": final_apk_path,
            "apk_name": final_apk_name,
            "apk_size": final_sz,
            "apk_size_mb": final_sz / (1024 * 1024),
            "app_name": app_name,
            "duration_sec": duration,
            "build_type": "release" if "release" in success_task.lower() else "debug",
            "timestamped_name": timestamped_name,
            "latest_name": latest_name,
            "project_dir": project_dir
        }

    @classmethod
    async def run_preflight_compile_check(
        cls,
        workspace: str,
        timeout_sec: float = 120.0
    ) -> Dict[str, Any]:
        """
        [컴파일 사전 비행 점검(Pre-Flight Check) 게이트]
        Coder 턴 완료 직후, 전체 APK 빌드(assembleDebug/assembleRelease) 이전에
        './gradlew compileDebugSources' 또는 단위 테스트 컴파일을 1차 조기 실행하여 문법/참조 오류를 검증합니다.
        컴파일 에러 발생 시 QA으로 넘기지 않고 Coder 단계에서 즉시 Fail-Fast 자가수정을 유도합니다.
        """
        project_dir = cls.find_android_project(workspace)
        if not project_dir:
            return {
                "is_android": False,
                "passed": True,
                "duration_sec": 0.0,
                "message": "안드로이드 프로젝트가 아니므로 컴파일 사전 비행 점검을 생략합니다."
            }

        gradlew_path = os.path.join(project_dir, "gradlew")
        try:
            os.chmod(gradlew_path, 0o755)
        except Exception as e:
            logger.warning(f"chmod +x failed on {gradlew_path}: {e}")

        env = dict(os.environ)
        if "ANDROID_HOME" not in env and "ANDROID_SDK_ROOT" not in env:
            cand_sdk = os.path.expanduser("~/Android/Sdk")
            if os.path.isdir(cand_sdk):
                env["ANDROID_HOME"] = cand_sdk
                env["ANDROID_SDK_ROOT"] = cand_sdk

        start_time = time.time()
        logger.info(f"Executing Pre-Flight Compile Check: ./gradlew compileDebugSources in {project_dir}")
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                "./gradlew", "compileDebugSources", "--no-daemon",
                cwd=project_dir,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT
            )
            stdout_data, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
            duration = time.time() - start_time
            out_text = stdout_data.decode("utf-8", errors="replace") if stdout_data else ""
            log_lines = out_text.splitlines()

            if proc.returncode == 0:
                logger.info(f"Pre-Flight Compile Check succeeded in {duration:.1f}s")
                return {
                    "is_android": True,
                    "passed": True,
                    "duration_sec": duration,
                    "message": "사전 비행 컴파일 점검(compileDebugSources) 성공"
                }
            else:
                error_lines = [
                    line for line in log_lines
                    if line.strip().startswith("e: ") or "Unresolved reference" in line or "Compilation error" in line or "error:" in line.lower()
                ]
                error_summary = "\n".join(error_lines[:15]) if error_lines else "\n".join(log_lines[-15:])
                logger.warning(f"Pre-Flight Compile Check failed with exit code {proc.returncode}:\n{error_summary}")
                return {
                    "is_android": True,
                    "passed": False,
                    "duration_sec": duration,
                    "exit_code": proc.returncode,
                    "error": error_summary,
                    "log_tail": "\n".join(log_lines[-15:])
                }
        except asyncio.TimeoutError:
            duration = time.time() - start_time
            if proc:
                try:
                    proc.kill()
                except Exception:
                    pass
            logger.error(f"Pre-Flight Compile Check timed out after {timeout_sec}s")
            return {
                "is_android": True,
                "passed": False,
                "duration_sec": duration,
                "error": f"컴파일 사전 점검 시간 초과 ({timeout_sec:.0f}초 초과)"
            }
        except asyncio.CancelledError:
            if proc:
                try:
                    proc.kill()
                except Exception:
                    pass
            raise
        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"Pre-Flight Compile Check exception: {e}")
            return {
                "is_android": True,
                "passed": False,
                "duration_sec": duration,
                "error": f"컴파일 사전 점검 실행 오류: {e}"
            }

    @classmethod
    def cleanup_old_apks(cls, workspace: str, app_name: str, keep_count: int = 5) -> int:
        """
        Retains the latest `keep_count` timestamped APKs and deletes older ones
        to prevent workspace storage exhaustion during prolonged Mode 3 autonomous cycles.
        """
        if not workspace or not os.path.isdir(workspace):
            return 0
        
        timestamped_apks = []
        pattern = re.compile(rf"^{re.escape(app_name)}-\d{{8}}_\d{{6}}\.apk$", re.IGNORECASE)
        try:
            for f in os.listdir(workspace):
                if pattern.match(f):
                    full_p = os.path.join(workspace, f)
                    if os.path.isfile(full_p):
                        try:
                            mtime = os.path.getmtime(full_p)
                            timestamped_apks.append((full_p, mtime))
                        except Exception:
                            pass
        except Exception:
            return 0

        # Sort newest first
        timestamped_apks.sort(key=lambda x: x[1], reverse=True)
        removed = 0
        if len(timestamped_apks) > keep_count:
            to_remove = timestamped_apks[keep_count:]
            for p, _ in to_remove:
                try:
                    os.remove(p)
                    removed += 1
                    logger.info(f"[AndroidBuildManager] Cleaned up old APK: {os.path.basename(p)}")
                except Exception as e:
                    logger.warning(f"[AndroidBuildManager] Failed to remove old APK {p}: {e}")
        return removed

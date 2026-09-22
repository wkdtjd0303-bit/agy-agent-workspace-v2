import os
import logging
import asyncio
from typing import Optional, Dict, Any

logger = logging.getLogger("GDriveUploader")

# Candidate paths for Google Drive service account or credentials
DEFAULT_CRED_PATHS = [
    os.getenv("GDRIVE_SERVICE_ACCOUNT_PATH", ""),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "credentials", "service_account.json"),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "credentials", "credentials.json"),
    os.path.expanduser("~/.config/gdrive/service_account.json"),
    os.path.expanduser("~/.config/gdrive/credentials.json"),
]

GDRIVE_FOLDER_ID = os.getenv("GDRIVE_FOLDER_ID", "")  # Optional designated Drive folder ID
# Default True: strictly enforce Google Drive only (no tmpfiles.org or catbox leakage)
GDRIVE_ONLY_MODE = os.getenv("GDRIVE_ONLY_MODE", "true").lower() in ("true", "1", "yes")

class GoogleDriveUploader:
    """
    Handles automatic uploading of deliverables (e.g. APKs, ZIPs, Videos)
    strictly to Google Drive (30TB rclone remote or Google Drive API).
    Ensures safe, permanent, and official Google Drive share links
    without routing through third-party temporary file hosts (tmpfiles, litterbox).
    """
    _service = None
    _auth_type = None

    @classmethod
    def get_credentials_path(cls) -> Optional[str]:
        for p in DEFAULT_CRED_PATHS:
            if p and os.path.isfile(p):
                return p
        return None

    @classmethod
    def get_drive_service(cls):
        """Initializes and returns Google Drive v3 API service."""
        if cls._service is not None:
            return cls._service

        cred_path = cls.get_credentials_path()
        if not cred_path:
            return None

        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build

            scopes = ["https://www.googleapis.com/auth/drive.file", "https://www.googleapis.com/auth/drive"]
            creds = service_account.Credentials.from_service_account_file(cred_path, scopes=scopes)
            cls._service = build("drive", "v3", credentials=creds)
            cls._auth_type = "service_account"
            logger.info(f"Google Drive API initialized with service account from: {cred_path}")
            return cls._service
        except Exception as e:
            logger.warning(f"Failed to initialize Google Drive service from {cred_path}: {e}")
            return None

    @classmethod
    async def _get_rclone_remote(cls) -> Optional[str]:
        """Checks if rclone is installed and configured with a Google Drive remote."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "rclone", "listremotes", "--timeout", "5s", "--contimeout", "3s",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=6.0)
            remotes = [r.strip() for r in stdout.decode("utf-8", errors="replace").splitlines() if r.strip()]
            
            # Prioritize remotes named 'gdrive:' or containing 'drive' / 'google'
            for r in remotes:
                r_clean = r.rstrip(":")
                if r_clean.lower() == "gdrive":
                    return r if r.endswith(":") else f"{r}:"
            for r in remotes:
                r_clean = r.rstrip(":")
                if any(k in r_clean.lower() for k in ("gdrive", "drive", "google")):
                    return r if r.endswith(":") else f"{r}:"
            if remotes:
                r = remotes[0]
                return r if r.endswith(":") else f"{r}:"
        except Exception:
            pass
        return None

    @classmethod
    async def _upload_via_rclone(
        cls,
        remote: str,
        file_path: str,
        file_name: str,
        file_size_mb: float
    ) -> Dict[str, Any]:
        """Uploads file using user's logged-in rclone Google Drive account with retries and adaptive timeouts."""
        remote_normalized = remote if remote.endswith(":") else f"{remote}:"
        target_dir = f"{remote_normalized}AGY_Deliverables"
        logger.info(f"Uploading {file_name} to Google Drive via rclone ({target_dir})...")

        # Dynamic timeout based on file size: minimum 90s, plus 4s per MB for transient network jitter
        upload_timeout = max(90.0, file_size_mb * 4.0 + 45.0)

        # 1. Copy file to Google Drive with up to 3 retries and optimized rclone chunk settings
        copy_success = False
        last_err = ""
        for attempt in range(1, 4):
            copy_proc = None
            try:
                cmd = [
                    "rclone", "copyto", file_path, f"{target_dir}/{file_name}",
                    "--drive-chunk-size", "32M",
                    "--drive-upload-cutoff", "32M",
                    "--timeout", "60s",
                    "--contimeout", "20s",
                    "--retries", "3",
                    "--low-level-retries", "10",
                ]
                copy_proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                _, copy_err = await asyncio.wait_for(copy_proc.communicate(), timeout=upload_timeout)
                if copy_proc.returncode == 0:
                    copy_success = True
                    break
                last_err = copy_err.decode("utf-8", errors="replace")
                logger.warning(f"rclone copy attempt {attempt} failed: {last_err}")
                await asyncio.sleep(2.0)
            except asyncio.TimeoutError:
                last_err = f"rclone copy timed out (> {upload_timeout:.0f}s)"
                logger.warning(f"rclone copy attempt {attempt} timed out.")
                if copy_proc:
                    try:
                        copy_proc.kill()
                    except Exception:
                        pass
                await asyncio.sleep(2.0)
            except Exception as ce:
                last_err = str(ce)
                logger.warning(f"rclone copy exception attempt {attempt}: {ce}")
                await asyncio.sleep(2.0)

        if not copy_success:
            logger.error(f"rclone upload failed completely for {file_name}: {last_err}")
            # Fallback to Service Account if available
            service = cls.get_drive_service()
            if service:
                return await cls._upload_to_gdrive(service, file_path, file_name, file_size_mb)
            if not GDRIVE_ONLY_MODE:
                return await cls._upload_to_cloud_fallback(file_path, file_name, file_size_mb)
            return {
                "success": False,
                "error": f"Google Drive(rclone) 업로드 실패: {last_err or '전송 오류'}. 로컬 경로: {file_path}"
            }

        # 2. Get public shareable link via rclone link
        share_url = ""
        try:
            link_proc = await asyncio.create_subprocess_exec(
                "rclone", "link", f"{target_dir}/{file_name}",
                "--timeout", "30s", "--contimeout", "15s",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            link_out, _ = await asyncio.wait_for(link_proc.communicate(), timeout=30.0)
            raw_link = link_out.decode("utf-8", errors="replace").strip()
            if raw_link.startswith("http://") or raw_link.startswith("https://"):
                share_url = raw_link
        except Exception as le:
            logger.warning(f"rclone link failed: {le}")

        # 3. Fallback: query file ID via rclone lsjson if rclone link didn't return URL
        if not share_url:
            try:
                import json
                ls_proc = await asyncio.create_subprocess_exec(
                    "rclone", "lsjson", f"{target_dir}/{file_name}",
                    "--timeout", "30s", "--contimeout", "15s",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                ls_out, _ = await asyncio.wait_for(ls_proc.communicate(), timeout=30.0)
                items = json.loads(ls_out.decode("utf-8", errors="replace"))
                if items and isinstance(items, list) and items[0].get("ID"):
                    file_id = items[0]["ID"]
                    share_url = f"https://drive.google.com/open?id={file_id}"
            except Exception as lse:
                logger.warning(f"rclone lsjson ID lookup failed: {lse}")

        if share_url:
            return {
                "success": True,
                "provider": "Google Drive (30TB)",
                "file_name": file_name,
                "size_mb": file_size_mb,
                "view_url": share_url,
                "download_url": share_url
            }

        # If link generation failed but file copied, provide instructions
        return {
            "success": True,
            "provider": "Google Drive (30TB)",
            "file_name": file_name,
            "size_mb": file_size_mb,
            "view_url": "https://drive.google.com",
            "download_url": "https://drive.google.com",
            "note": f"Google Drive 'AGY_Deliverables/{file_name}'에 정상 저장되었습니다."
        }

    @classmethod
    async def upload_file(
        cls,
        file_path: str,
        display_name: Optional[str] = None,
        folder_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Uploads a file to Google Drive and sets public read permissions.
        Strict Google Drive Policy:
          1. rclone Google Drive (30TB connected remote)
          2. Google Drive API (Service Account JSON)
        Third-party temporary hosts are strictly blocked.
        """
        if not os.path.isfile(file_path):
            return {"success": False, "error": f"File not found: {file_path}"}

        file_name = display_name or os.path.basename(file_path)
        file_size_bytes = os.path.getsize(file_path)
        file_size_mb = file_size_bytes / (1024 * 1024)

        # 1. Check for user-authenticated rclone remote (Google Drive)
        rclone_remote = await cls._get_rclone_remote()
        if rclone_remote:
            return await cls._upload_via_rclone(rclone_remote, file_path, file_name, file_size_mb)

        # 2. Check for service account Google Drive API
        service = cls.get_drive_service()
        if service:
            return await cls._upload_to_gdrive(service, file_path, file_name, file_size_mb, folder_id)

        # 3. Third-party fallback only if explicitly permitted (Default: False)
        if not GDRIVE_ONLY_MODE:
            return await cls._upload_to_cloud_fallback(file_path, file_name, file_size_mb)

        return {
            "success": False,
            "error": "Google Drive 연동(rclone 또는 Service Account)이 설정되지 않았습니다. 파일은 로컬에 안전하게 저장되어 있습니다: " + file_path
        }

    @classmethod
    async def _upload_to_gdrive(
        cls,
        service,
        file_path: str,
        file_name: str,
        file_size_mb: float,
        folder_id: Optional[str] = None
    ) -> Dict[str, Any]:
        from googleapiclient.http import MediaFileUpload

        def _do_upload():
            file_metadata = {"name": file_name}
            target_folder = folder_id or GDRIVE_FOLDER_ID
            if target_folder:
                file_metadata["parents"] = [target_folder]

            media = MediaFileUpload(file_path, resumable=True)
            drive_file = service.files().create(
                body=file_metadata,
                media_body=media,
                fields="id, name, webViewLink, webContentLink"
            ).execute()

            file_id = drive_file.get("id")

            # Set public link permission (Anyone with link can read/download)
            try:
                service.permissions().create(
                    fileId=file_id,
                    body={"role": "reader", "type": "anyone"},
                    fields="id"
                ).execute()
            except Exception as perm_err:
                logger.warning(f"Could not set public permission on Drive file {file_id}: {perm_err}")

            view_link = drive_file.get("webViewLink") or f"https://drive.google.com/file/d/{file_id}/view?usp=sharing"
            download_link = drive_file.get("webContentLink") or view_link

            return {
                "success": True,
                "provider": "Google Drive (API)",
                "file_id": file_id,
                "file_name": file_name,
                "size_mb": file_size_mb,
                "view_url": view_link,
                "download_url": download_link
            }

        api_upload_timeout = max(90.0, file_size_mb * 4.0 + 30.0)
        try:
            loop = asyncio.get_event_loop()
            result = await asyncio.wait_for(
                loop.run_in_executor(None, _do_upload),
                timeout=api_upload_timeout
            )
            logger.info(f"Successfully uploaded {file_name} to Google Drive: {result.get('view_url', '')}")
            return result
        except asyncio.TimeoutError:
            logger.error(f"Google Drive API upload timed out (> {api_upload_timeout:.0f}s) for {file_name}")
            if not GDRIVE_ONLY_MODE:
                return await cls._upload_to_cloud_fallback(file_path, file_name, file_size_mb)
            return {
                "success": False,
                "error": f"Google Drive API 업로드 타임아웃({api_upload_timeout:.0f}초 초과). 로컬 파일: {file_path}"
            }
        except Exception as e:
            logger.exception(f"Google Drive API upload failed for {file_name}: {e}")
            if not GDRIVE_ONLY_MODE:
                return await cls._upload_to_cloud_fallback(file_path, file_name, file_size_mb)
            return {
                "success": False,
                "error": f"Google Drive API 업로드 실패: {e}. 로컬 파일: {file_path}"
            }

    @classmethod
    async def _upload_to_cloud_fallback(
        cls,
        file_path: str,
        file_name: str,
        file_size_mb: float
    ) -> Dict[str, Any]:
        """
        Temporary 3rd-party cloud fallback, only called if GDRIVE_ONLY_MODE=false.
        """
        if GDRIVE_ONLY_MODE:
            return {
                "success": False,
                "error": "Google Drive 전용 모드가 활성화되어 외부 임시 스토리지 업로드가 차단되었습니다."
            }
        logger.info(f"Using direct cloud upload fallback for large file: {file_name} ({file_size_mb:.1f}MB)")
        try:
            cmd = f'curl --silent --max-time 60 -F "reqtype=fileupload" -F "time=72h" -F "fileToUpload=@{file_path}" https://litterbox.catbox.moe/resources/internals/api.php'
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=65.0)
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                except Exception:
                    pass
                return {
                    "success": False,
                    "error": "외부 임시 스토리지 업로드 타임아웃 (> 65s)"
                }

            url = stdout.decode("utf-8", errors="replace").strip()
            if url.startswith("http://") or url.startswith("https://"):
                return {
                    "success": True,
                    "provider": "Cloud Storage (Litterbox)",
                    "file_name": file_name,
                    "size_mb": file_size_mb,
                    "view_url": url,
                    "download_url": url
                }
        except Exception as e:
            logger.debug(f"litterbox fallback failed: {e}")

        return {
            "success": False,
            "error": "모든 클라우드 업로드 방식이 실패했습니다. Google Drive(rclone) 설정을 확인해주세요."
        }

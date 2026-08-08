"""Google Drive client used by Agent 1 (Clip Fetch) and the orchestrator.

Uses a service account (recommended for GitHub Actions automation). The service
account must be granted access to the shared "Movie Clips" folder tree.

Auth options:
  - GOOGLE_APPLICATION_CREDENTIALS: path to a service-account JSON file, or
  - GOOGLE_DRIVE_SERVICE_ACCOUNT: base64/JSON payload of a service-account key.
"""

from __future__ import annotations

import base64
import io
import json
import os
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload

from .config import getenv
from .logger import get_logger

SCOPES = ["https://www.googleapis.com/auth/drive"]

logger = get_logger("drive")


class DriveClient:
    def __init__(self) -> None:
        self.service = self._build_service()

    # -- auth --------------------------------------------------------------
    @staticmethod
    def _load_credentials():
        env_json = getenv("GOOGLE_DRIVE_SERVICE_ACCOUNT")
        file_path = getenv("GOOGLE_APPLICATION_CREDENTIALS")
        if env_json:
            try:
                payload = json.loads(env_json)
            except json.JSONDecodeError:
                # Support base64-encoded payload too.
                payload = json.loads(
                    base64.b64decode(env_json).decode("utf-8")
                )
            return service_account.Credentials.from_service_account_info(
                payload, scopes=SCOPES
            )
        if file_path and os.path.exists(file_path):
            return service_account.Credentials.from_service_account_file(
                file_path, scopes=SCOPES
            )
        raise RuntimeError(
            "No Google Drive credentials. Set GOOGLE_DRIVE_SERVICE_ACCOUNT or "
            "GOOGLE_APPLICATION_CREDENTIALS."
        )

    def _build_service(self):
        creds = self._load_credentials()
        if creds.expired:
            creds.refresh(Request())
        return build("drive", "v3", credentials=creds, cache_discovery=False)

    # -- folder helpers -----------------------------------------------------
    def find_folder(self, path: str, root_id: str | None = None) -> str | None:
        """Resolve a '/'-joined folder path (e.g. 'Movie Clips/Ready') to a folder ID."""
        current = root_id
        for segment in [s for s in path.split("/") if s]:
            folder_id = self._find_child_folder(current, segment)
            if folder_id is None:
                logger.warning("Folder not found: %s under %s", segment, current)
                return None
            current = folder_id
        return current

    def _find_child_folder(self, parent_id: str | None, name: str) -> str | None:
        q = f"name = '{self._escape(name)}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
        if parent_id:
            q += f" and '{parent_id}' in parents"
        results = (
            self.service.files()
            .list(q=q, fields="files(id, name)", pageSize=10)
            .execute()
        )
        files = results.get("files", [])
        return files[0]["id"] if files else None

    @staticmethod
    def _escape(name: str) -> str:
        return name.replace("\\", "\\\\").replace("'", "\\'")

    # -- file listing -------------------------------------------------------
    def list_files_in_folder(self, folder_id: str) -> list[dict[str, Any]]:
        """List non-folder files in a folder, oldest first."""
        q = (
            f"'{folder_id}' in parents and trashed = false and "
            f"mimeType != 'application/vnd.google-apps.folder'"
        )
        items: list[dict[str, Any]] = []
        page_token = None
        while True:
            result = (
                self.service.files()
                .list(
                    q=q,
                    fields="nextPageToken, files(id, name, mimeType, createdTime, size)",
                    pageSize=100,
                    pageToken=page_token,
                )
                .execute()
            )
            items.extend(result.get("files", []))
            page_token = result.get("nextPageToken")
            if not page_token:
                break
        items.sort(key=lambda f: f.get("createdTime", ""))
        return items

    # -- download -----------------------------------------------------------
    def download_file(self, file_id: str, dest_path: str) -> str:
        """Download a file to dest_path, returning the local path."""
        request = self.service.files().get_media(fileId=file_id)
        os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
        with open(dest_path, "wb") as fh:
            downloader = MediaIoBaseDownload(fh, request, chunksize=256 * 1024)
            done = False
            while not done:
                status, done = downloader.next_chunk()
                if status:
                    logger.info("Download %d%%", int(status.progress() * 100))
        return dest_path

    # -- move ---------------------------------------------------------------
    def move_file(self, file_id: str, source_folder_id: str, target_folder_id: str) -> None:
        """Move a file between folders (used to Ready -> Processing -> Uploaded)."""
        try:
            self.service.files().update(
                fileId=file_id,
                addParents=target_folder_id,
                removeParents=source_folder_id,
                fields="id, parents",
            ).execute()
            logger.info("Moved file %s to folder %s", file_id, target_folder_id)
        except HttpError as exc:
            logger.error("Move failed for %s: %s", file_id, exc)
            raise

    def get_metadata(self, file_id: str) -> dict[str, Any]:
        result = (
            self.service.files()
            .get(fileId=file_id, fields="id, name, mimeType, size, createdTime, parents")
            .execute()
        )
        return result

    def file_exists(self, file_id: str) -> bool:
        try:
            self.service.files().get(fileId=file_id, fields="id").execute()
            return True
        except HttpError:
            return False


def service_account_id() -> str | None:
    """Return the service account email (for sharing diagnostics)."""
    env_json = getenv("GOOGLE_DRIVE_SERVICE_ACCOUNT")
    if env_json:
        try:
            payload = json.loads(env_json)
            return payload.get("client_email")
        except Exception:
            return None
    return None

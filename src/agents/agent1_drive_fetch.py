"""Agent 1 - Google Drive Clip Fetch Agent.

Responsibilities:
  1. Check the `Movie Clips / Ready` folder.
  2. Identify available new movie clips.
  3. Pick the next clip to process.
  4. Move it to the `Processing` folder.
  5. Download it locally for the upload pipeline.

Only this agent talks to Google Drive for clip sourcing.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from ..common.config import BASE_DIR, load_settings
from ..common.drive import DriveClient
from ..common.logger import get_logger

logger = get_logger("agent1_drive_fetch")

_DRYRUN_COUNTER: dict[str, int] = {"n": 0}


class DriveFetchAgent:
    def __init__(self, dry_run: bool = False) -> None:
        self.settings = load_settings()
        self.dry_run = dry_run
        self.drive = DriveClient() if not dry_run else None
        self.folder_paths = self.settings.get("drive", {}).get("folder_paths", {})
        root_id = self.settings.get("drive", {}).get("root_folder_id") or None
        self._root_id = root_id
        self._folder_ids: dict[str, str] = {}

    def _folder_id(self, key: str) -> str:
        if key in self._folder_ids:
            return self._folder_ids[key]
        if self.dry_run:
            self._folder_ids[key] = f"dryrun-{key}"
            return self._folder_ids[key]
        path = self.folder_paths.get(key)
        if not path:
            raise RuntimeError(f"Drive folder path missing for: {key}")
        folder_id = self.drive.find_folder(path, self._root_id)
        if not folder_id:
            raise RuntimeError(f"Drive folder not found: {path}")
        self._folder_ids[key] = folder_id
        return folder_id

    def _folder_id(self, key: str) -> str:
        if key in self._folder_ids:
            return self._folder_ids[key]
        path = self.folder_paths.get(key)
        if not path:
            raise RuntimeError(f"Drive folder path missing for: {key}")
        folder_id = self.drive.find_folder(path, self._root_id)
        if not folder_id:
            raise RuntimeError(f"Drive folder not found: {path}")
        self._folder_ids[key] = folder_id
        return folder_id

    def ready_folder_id(self) -> str:
        return self._folder_id("ready")

    def processing_folder_id(self) -> str:
        return self._folder_id("processing")

    def uploaded_folder_id(self) -> str:
        return self._folder_id("uploaded")

    # -- main API -----------------------------------------------------------
    def list_ready_clips(self) -> list[dict[str, Any]]:
        """List all clips currently available in the Ready folder."""
        ready_id = self.ready_folder_id()
        files = self.drive.list_files_in_folder(ready_id)
        logger.info("Ready folder contains %d clip(s)", len(files))
        return files

    def fetch_next_clip(self, exclude_ids: set[str] | None = None) -> dict[str, Any] | None:
        """Pick the next available clip and return its job payload.

        Returns None when the Ready folder is empty. Clips whose drive id is in
        exclude_ids (already uploaded) are skipped.
        """
        exclude_ids = exclude_ids or set()
        if self.dry_run:
            local_path = self._dry_run_fake_clip()
            n = 0
            while True:
                n += 1
                candidate_id = f"dryrun-clip-{n:04d}"
                if candidate_id not in exclude_ids:
                    break
            _DRYRUN_COUNTER["n"] = n
            names = [
                "The Dark Knight - Joker Interrogation Scene.mp4",
                "Avengers Endgame - Captain America Mjolnir Scene.mp4",
                "Interstellar - Docking Scene.mp4",
                "Gladiator - Are You Not Entertained.mp4",
                "The Matrix - Bullet Time Scene.mp4",
            ]
            name = names[(n - 1) % len(names)]
            return {
                "drive_file_id": f"dryrun-clip-{n:04d}",
                "drive_file_name": name,
                "mime_type": "video/mp4",
                "local_path": local_path,
                "size_bytes": 10485760,
            }

        files = self.list_ready_clips()
        if not files:
            logger.info("No clips in Ready folder; nothing to fetch.")
            return None

        # Skip clips that are not video files or were already uploaded.
        candidates = [
            f for f in files
            if self._is_video(f) and f["id"] not in exclude_ids
        ]
        if not candidates:
            logger.warning("No new video clips found in Ready folder.")
            return None

        clip = candidates[0]
        logger.info("Selected clip: %s (%s)", clip["name"], clip["id"])

        try:
            moved = self._move_to_processing(clip)
            if not moved:
                return None
        except Exception as exc:
            logger.error("Could not move clip to Processing: %s", exc)
            raise

        local_path = self._download(clip)
        return {
            "drive_file_id": clip["id"],
            "drive_file_name": clip["name"],
            "mime_type": clip.get("mimeType"),
            "local_path": local_path,
            "size_bytes": clip.get("size"),
        }

    # -- internals ----------------------------------------------------------
    @staticmethod
    def _is_video(file: dict[str, Any]) -> bool:
        mime = file.get("mimeType", "")
        name = file.get("name", "").lower()
        video_mimes = (
            "video/",
            "application/vnd.google-apps.shortcut",
        )
        video_exts = (".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".mpg", ".mpeg")
        if mime.startswith(video_mimes):
            return True
        return name.endswith(video_exts)

    def _move_to_processing(self, clip: dict[str, Any]) -> bool:
        ready_id = self.ready_folder_id()
        processing_id = self.processing_folder_id()
        self.drive.move_file(clip["id"], ready_id, processing_id)
        logger.info("Moved %s to Processing folder", clip["name"])
        return True

    def _download(self, clip: dict[str, Any]) -> str:
        downloads_dir = Path(BASE_DIR) / self.settings.get("app", {}).get(
            "downloads_dir", "downloads"
        )
        downloads_dir.mkdir(parents=True, exist_ok=True)
        safe_name = self._safe_filename(clip["name"])
        local_path = str(downloads_dir / safe_name)
        self.drive.download_file(clip["id"], local_path)
        logger.info("Downloaded %s -> %s", clip["name"], local_path)
        return local_path

    def ensure_local_clip(self, drive_file_id: str, drive_file_name: str, local_path: str) -> str:
        """Ensure a previously-fetched clip exists locally, re-downloading it if the
        file was lost between CI runs (each GitHub Actions run has a fresh checkout).
        """
        if local_path and os.path.exists(local_path):
            return local_path
        downloads_dir = Path(BASE_DIR) / self.settings.get("app", {}).get(
            "downloads_dir", "downloads"
        )
        downloads_dir.mkdir(parents=True, exist_ok=True)
        safe_name = self._safe_filename(drive_file_name)
        dest = str(downloads_dir / safe_name)
        self.drive.download_file(drive_file_id, dest)
        logger.info("Re-downloaded %s -> %s", drive_file_name, dest)
        return dest

    @staticmethod
    def _safe_filename(name: str) -> str:
        name = re.sub(r"[^\w.\-]", "_", name)
        return name.strip("_")

    def _dry_run_fake_clip(self) -> str:
        """Create a tiny local placeholder video for dry-run simulation."""
        downloads_dir = Path(BASE_DIR) / self.settings.get("app", {}).get(
            "downloads_dir", "downloads"
        )
        downloads_dir.mkdir(parents=True, exist_ok=True)
        path = downloads_dir / "dryrun_clip.mp4"
        if not path.exists():
            # Minimal valid-ish MP4-ish bytes; only used to simulate file IO.
            path.write_bytes(b"\x00\x00\x00\x18ftypmp42dryrun")
        return str(path)


def fetch_agent() -> DriveFetchAgent:
    """Construct Agent 1 (used by orchestrator)."""
    return DriveFetchAgent()

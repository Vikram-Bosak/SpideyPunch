"""Agent 4 - Facebook Upload Agent.

Uploads each clip as a Facebook Reel/video using the Facebook Graph API
(Page access token). Produces the public Facebook URL and verifies success.
"""

from __future__ import annotations

import os
import time
from typing import Any

import requests

from ..common.config import getenv, load_settings
from ..common.logger import get_logger

logger = get_logger("agent4_facebook")


class FacebookUploadAgent:
    def __init__(self) -> None:
        self.settings = load_settings()
        self.page_id = getenv("FACEBOOK_PAGE_ID")
        self.access_token = getenv("FACEBOOK_PAGE_ACCESS_TOKEN")
        if not (self.page_id and self.access_token):
            raise RuntimeError(
                "Facebook credentials missing. Set FACEBOOK_PAGE_ID and "
                "FACEBOOK_PAGE_ACCESS_TOKEN."
            )
        self.api_version = self.settings.get("facebook", {}).get(
            "api_version", "v21.0"
        )
        self.graph_url = f"https://graph.facebook.com/{self.api_version}"
        self._chunk_size = 10 * 1024 * 1024  # 10 MB resumable upload chunks

    def upload_reel(
        self,
        local_path: str,
        caption: str,
        privacy: str = "PUBLIC",
    ) -> str:
        """Upload a video/reel to the Facebook page and return its public URL.

        The /video_reels endpoint uses the resumable upload protocol, so the
        file is uploaded in three phases: start (create session), transfer
        (stream the bytes), finish (finalize the reel).
        """
        privacy = self.settings.get("facebook", {}).get("privacy", privacy)
        endpoint = f"{self.graph_url}/{self.page_id}/video_reels"
        file_size = os.path.getsize(local_path)

        # Phase 1: start the upload session.
        start_params = {
            "upload_phase": "start",
            "file_size": file_size,
            "description": caption,
            "privacy": f'{{"value": "{privacy}"}}',
            "access_token": self.access_token,
        }
        resp = requests.post(endpoint, data=start_params, timeout=120)
        if resp.status_code != 200:
            raise RuntimeError(f"Facebook upload session start failed: {resp.text}")
        session = resp.json()
        video_id = session.get("video_id")
        upload_session_id = session.get("upload_session_id")
        if not (video_id and upload_session_id):
            raise RuntimeError(f"Facebook upload session missing ids: {session}")

        # Phase 2: transfer the file in chunks.
        offset = int(session.get("start_offset", 0))
        end_offset = int(session.get("end_offset", file_size))
        with open(local_path, "rb") as fh:
            fh.seek(offset)
            while offset < file_size:
                chunk = fh.read(self._chunk_size)
                if not chunk:
                    break
                transfer_params = {
                    "upload_phase": "transfer",
                    "upload_session_id": upload_session_id,
                    "start_offset": offset,
                    "access_token": self.access_token,
                }
                resp = requests.post(
                    endpoint,
                    data=transfer_params,
                    files={"video_file_chunk": chunk},
                    timeout=600,
                )
                if resp.status_code != 200:
                    raise RuntimeError(
                        f"Facebook upload transfer failed at offset {offset}: {resp.text}"
                    )
                result = resp.json()
                offset = int(result.get("start_offset", offset + len(chunk)))
                end_offset = int(result.get("end_offset", end_offset))
                logger.info("Facebook upload progress: %d/%d", min(offset, file_size), file_size)

        # Phase 3: finish the upload session.
        finish_params = {
            "upload_phase": "finish",
            "upload_session_id": upload_session_id,
            "video_id": video_id,
            "access_token": self.access_token,
        }
        resp = requests.post(endpoint, data=finish_params, timeout=120)
        if resp.status_code != 200:
            raise RuntimeError(f"Facebook upload finish failed: {resp.text}")

        self._wait_for_processing(video_id)
        # Reel URL is most reliable when the reel is public.
        public_url = f"https://www.facebook.com/reel/{video_id}"
        logger.info("Facebook upload OK: %s", public_url)
        return public_url

    def _wait_for_processing(self, video_id: str, max_seconds: int = 300) -> str:
        """Poll video processing status until the video is ready/public."""
        url = f"{self.graph_url}/{video_id}"
        params = {"fields": "status", "access_token": self.access_token}
        deadline = time.time() + max_seconds
        last_status = "pending"
        while time.time() < deadline:
            resp = requests.get(url, params=params, timeout=30)
            if resp.status_code == 200:
                body = resp.json()
                status_obj = body.get("status") or {}
                last_status = str(status_obj)
                progress = status_obj.get("processing_progress")
                if progress is not None:
                    logger.info("Facebook processing progress: %s", progress)
                if status_obj.get("video_status") in ("ready", "published"):
                    break
            else:
                logger.warning("Status check failed: %s", resp.text)
            time.sleep(15)
        logger.info("Facebook processing final status: %s", last_status)
        return video_id

    def verify(self, video_id: str) -> bool:
        """Verify the video is reachable and public."""
        url = f"{self.graph_url}/{video_id}"
        params = {
            "fields": "id,status",
            "access_token": self.access_token,
        }
        try:
            resp = requests.get(url, params=params, timeout=30)
            return resp.status_code == 200
        except Exception as exc:  # noqa: BLE001
            logger.error("Facebook verify error: %s", exc)
            return False


def upload_to_facebook(
    local_path: str,
    seo: dict[str, Any],
) -> str:
    """Convenience entry point: returns the public Facebook URL."""
    agent = FacebookUploadAgent()
    caption = seo.get("facebook_caption", "")
    return agent.upload_reel(local_path=local_path, caption=caption)

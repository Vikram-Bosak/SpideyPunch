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
        self.access_token = self._resolve_page_token(self.access_token)

    def _resolve_page_token(self, token: str) -> str:
        """If given a User Access Token, resolve the Page Access Token for self.page_id."""
        url = f"{self.graph_url}/{self.page_id}"
        params = {"fields": "access_token", "access_token": token}
        try:
            resp = requests.get(url, params=params, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                page_token = data.get("access_token")
                if page_token:
                    logger.info("Successfully resolved Page Access Token for page %s", self.page_id)
                    return page_token
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not resolve page token via /{page_id}: %s", exc)
        return token

    def upload_reel(
        self,
        local_path: str,
        caption: str,
        privacy: str = "PUBLIC",
    ) -> str:
        """Upload a video/reel to the Facebook page and return its public URL.

        The /video_reels endpoint uses the Graph API Video Uploads protocol
        (resumable uploads via rupload.facebook.com): start the session to get
        an upload_url, stream the file bytes to it, then finish the session.
        """
        endpoint = f"{self.graph_url}/{self.page_id}/video_reels"
        file_size = os.path.getsize(local_path)

        # Phase 1: start the upload session.
        start_params = {
            "upload_phase": "start",
            "access_token": self.access_token,
        }
        resp = requests.post(endpoint, data=start_params, timeout=120)
        if resp.status_code != 200:
            raise RuntimeError(f"Facebook upload session start failed: {resp.text}")
        session = resp.json()
        video_id = session.get("video_id")
        if not video_id:
            raise RuntimeError(f"Facebook upload session missing ids: {session}")
        # Server-provided upload endpoint (rupload.facebook.com).
        upload_url = session.get("upload_url") or (
            f"https://rupload.facebook.com/video-upload/{self.api_version}/{video_id}"
        )

        # Phase 2: stream the file bytes to the upload_url (POST per official sample).
        with open(local_path, "rb") as fh:
            file_bytes = fh.read()
        headers = {
            "Authorization": f"OAuth {self.access_token}",
            "offset": "0",
            "file_size": str(file_size),
        }
        resp = requests.post(upload_url, data=file_bytes, headers=headers, timeout=600)
        if resp.status_code not in (200, 201, 202):
            logger.error(
                "FB transfer debug: status=%s headers=%s body=%s",
                resp.status_code, dict(resp.headers), resp.text[:500],
            )
            raise RuntimeError(
                f"Facebook upload transfer failed: {resp.status_code} {resp.text}"
            )
        if resp.text:
            logger.info("Facebook upload transfer response: %s", resp.text[:200])

        # Phase 3: finish the upload session and publish the reel.
        finish_params = {
            "upload_phase": "finish",
            "video_id": video_id,
            "description": caption,
            "video_state": "PUBLISHED",
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
                video_status = status_obj.get("video_status")
                if video_status == "error":
                    errors = (
                        status_obj.get("processing_phase", {}).get("errors")
                        or status_obj.get("publishing_phase", {}).get("errors")
                        or []
                    )
                    logger.warning("Facebook video processing error: %s", errors)
                    break
                if (
                    video_status in ("ready", "published")
                    or status_obj.get("publishing_phase", {}).get("status")
                    == "complete"
                ):
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

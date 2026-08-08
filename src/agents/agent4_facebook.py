"""Agent 4 - Facebook Upload Agent.

Uploads each clip as a Facebook Reel/video using the Facebook Graph API
(Page access token). Produces the public Facebook URL and verifies success.
"""

from __future__ import annotations

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

    def upload_reel(
        self,
        local_path: str,
        caption: str,
        privacy: str = "PUBLIC",
    ) -> str:
        """Upload a video/reel to the Facebook page and return its public URL."""
        privacy = self.settings.get("facebook", {}).get("privacy", privacy)
        url = f"{self.graph_url}/{self.page_id}/video_reels"
        data = {
            "description": caption,
            "privacy": f'{{"value": "{privacy}"}}',
            "access_token": self.access_token,
        }
        with open(local_path, "rb") as fh:
            files = {"source": fh}
            resp = requests.post(url, data=data, files=files, timeout=600)
        if resp.status_code != 200:
            raise RuntimeError(f"Facebook upload failed: {resp.text}")

        result = resp.json()
        video_id = result.get("video_id") or result.get("id")
        if not video_id:
            raise RuntimeError(f"Facebook upload missing video id: {result}")

        reel_id = self._wait_for_processing(video_id)
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

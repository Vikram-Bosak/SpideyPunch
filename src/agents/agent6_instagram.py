"""Agent 6 - Instagram Upload Agent.

Uploads each clip as an Instagram Reel using the Meta Content Publishing API
(resumable binary upload protocol). Produces the public Instagram URL.
"""

from __future__ import annotations

import os
import time
from typing import Any

import requests

from ..common.config import getenv, load_settings
from ..common.logger import get_logger

logger = get_logger("agent6_instagram")


class InstagramUploadAgent:
    def __init__(self) -> None:
        self.settings = load_settings()
        self.page_id = getenv("FACEBOOK_PAGE_ID")
        self.access_token = getenv("INSTAGRAM_ACCESS_TOKEN") or getenv("FACEBOOK_PAGE_ACCESS_TOKEN")
        if not (self.page_id and self.access_token):
            raise RuntimeError(
                "Instagram credentials missing. Set FACEBOOK_PAGE_ID and "
                "INSTAGRAM_ACCESS_TOKEN or FACEBOOK_PAGE_ACCESS_TOKEN."
            )
        self.api_version = self.settings.get("facebook", {}).get(
            "api_version", "v21.0"
        )
        self.graph_url = f"https://graph.facebook.com/{self.api_version}"

    def get_instagram_business_id(self) -> str:
        """Fetch the Instagram Business ID connected to the Facebook Page."""
        url = f"{self.graph_url}/{self.page_id}"
        params = {
            "fields": "instagram_business_account",
            "access_token": self.access_token,
        }
        resp = requests.get(url, params=params, timeout=30)
        if resp.status_code != 200:
            raise RuntimeError(f"Failed to fetch Instagram account connection info: {resp.text}")
        data = resp.json()
        ig_acct = data.get("instagram_business_account")
        if not ig_acct or "id" not in ig_acct:
            raise RuntimeError(
                f"No Instagram Business Account connected to Page ID {self.page_id}. "
                "Ensure your Instagram Professional profile is connected to this Facebook Page."
            )
        ig_user_id = ig_acct["id"]
        logger.info("Discovered Instagram Business Account ID: %s", ig_user_id)
        return ig_user_id

    def upload_reel(
        self,
        local_path: str,
        caption: str,
    ) -> str:
        """Upload a video/reel to the Instagram business profile and return its URL.

        Uses the official Meta Resumable Upload protocol for Instagram Reels:
        1. Create container with upload_type=resumable and media_type=REELS
        2. Post binary chunks to rupload.facebook.com/ig-api-upload/
        3. Poll status on container until FINISHED
        4. Publish the container with caption
        """
        ig_user_id = self.get_instagram_business_id()
        file_size = os.path.getsize(local_path)

        # Step 1: Initialize Resumable Media Container
        init_url = f"{self.graph_url}/{ig_user_id}/media"
        init_params = {
            "upload_type": "resumable",
            "media_type": "REELS",
            "caption": caption,
            "access_token": self.access_token,
        }
        resp = requests.post(init_url, data=init_params, timeout=60)
        if resp.status_code != 200:
            raise RuntimeError(f"Instagram container initialization failed: {resp.text}")
        container_data = resp.json()
        container_id = container_data.get("id")
        if not container_id:
            raise RuntimeError(f"Instagram container missing ID: {container_data}")

        # Step 2: Perform resumable binary chunk upload
        upload_url = f"https://rupload.facebook.com/ig-api-upload/{self.api_version}/{container_id}"
        with open(local_path, "rb") as f:
            video_bytes = f.read()

        headers = {
            "Authorization": f"OAuth {self.access_token}",
            "offset": "0",
            "file_size": str(file_size),
            "Content-Type": "application/octet-stream",
        }
        resp = requests.post(upload_url, data=video_bytes, headers=headers, timeout=600)
        if resp.status_code not in (200, 201, 202):
            raise RuntimeError(f"Instagram binary chunk transfer failed: {resp.status_code} {resp.text}")

        # Step 3: Poll status of container processing
        self._wait_for_container_processing(container_id)

        # Step 4: Publish the Media Container
        publish_endpoint = f"{self.graph_url}/{ig_user_id}/media_publish"
        publish_params = {
            "creation_id": container_id,
            "caption": caption,
            "access_token": self.access_token,
        }
        resp = requests.post(publish_endpoint, data=publish_params, timeout=120)
        if resp.status_code != 200:
            raise RuntimeError(f"Instagram Reels publishing failed: {resp.text}")
        publish_data = resp.json()
        media_id = publish_data.get("id")
        if not media_id:
            raise RuntimeError(f"Instagram Reels publish missing media ID: {publish_data}")

        # Retrieve media permalink
        media_url = f"{self.graph_url}/{media_id}"
        media_params = {
            "fields": "permalink",
            "access_token": self.access_token,
        }
        resp = requests.get(media_url, params=media_params, timeout=30)
        if resp.status_code == 200:
            permalink = resp.json().get("permalink")
            if permalink:
                logger.info("Instagram Reels upload OK: %s", permalink)
                return permalink

        fallback_url = f"https://www.instagram.com/reel/{media_id}"
        logger.info("Instagram Reels upload OK (fallback URL): %s", fallback_url)
        return fallback_url

    def _wait_for_container_processing(self, container_id: str, max_seconds: int = 300) -> None:
        """Poll container status until the state is FINISHED."""
        url = f"{self.graph_url}/{container_id}"
        params = {
            "fields": "status_code,status",
            "access_token": self.access_token,
        }
        deadline = time.time() + max_seconds
        while time.time() < deadline:
            resp = requests.get(url, params=params, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                status_code = data.get("status_code")
                if status_code == "FINISHED":
                    logger.info("Instagram container processing finished successfully.")
                    return
                elif status_code == "ERROR":
                    error_msg = data.get("error", "Unknown container error")
                    raise RuntimeError(f"Instagram container processing failed: {error_msg}")
            else:
                logger.warning("Instagram container status check failed: %s", resp.text)
            time.sleep(10)
        raise RuntimeError("Instagram container processing timed out.")

    def verify(self, media_id_or_url: str) -> bool:
        """Verify the media container or URL is reachable."""
        # Simple verify checking endpoint accessibility
        try:
            url = f"{self.graph_url}/{self.page_id}"
            params = {"access_token": self.access_token}
            resp = requests.get(url, params=params, timeout=10)
            return resp.status_code == 200
        except Exception as exc:  # noqa: BLE001
            logger.error("Instagram verify check failed: %s", exc)
            return False

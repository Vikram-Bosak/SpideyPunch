"""Agent 3 - YouTube Upload Agent.

Uploads each clip as a YouTube Short using the YouTube Data API v3 with OAuth2
(client id + secret + refresh token). Produces the public Shorts URL and
verifies the upload succeeded.
"""

from __future__ import annotations

import os
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from ..common.config import getenv, load_settings
from ..common.logger import get_logger

logger = get_logger("agent3_youtube")

YOUTUBE_API_SERVICE_NAME = "youtube"
YOUTUBE_API_VERSION = "v3"


class YouTubeUploadAgent:
    def __init__(self) -> None:
        self.settings = load_settings()
        self.client_id = getenv("YOUTUBE_CLIENT_ID")
        self.client_secret = getenv("YOUTUBE_CLIENT_SECRET")
        self.refresh_token = getenv("YOUTUBE_REFRESH_TOKEN")
        if not (self.client_id and self.client_secret and self.refresh_token):
            raise RuntimeError(
                "YouTube credentials missing. Set YOUTUBE_CLIENT_ID, "
                "YOUTUBE_CLIENT_SECRET, YOUTUBE_REFRESH_TOKEN."
            )
        self.youtube = self._build_client()

    def _build_client(self):
        creds = Credentials(
            token=None,
            refresh_token=self.refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=self.client_id,
            client_secret=self.client_secret,
        )
        creds.refresh(Request())
        return build(
            YOUTUBE_API_SERVICE_NAME,
            YOUTUBE_API_VERSION,
            credentials=creds,
            cache_discovery=False,
        )

    def upload_short(
        self,
        local_path: str,
        title: str,
        description: str,
        tags: list[str],
        category_id: str = "24",
        privacy_status: str = "public",
    ) -> str:
        """Upload a video as a Short and return the public URL."""
        title = self._enforce_title_limit(title)

        body = {
            "snippet": {
                "title": title,
                "description": description,
                "tags": tags,
                "categoryId": category_id,
                "defaultLanguage": "en",
                "defaultAudioLanguage": "en",
            },
            "status": {
                "privacyStatus": privacy_status,
                "selfDeclaredMadeForKids": False,
            },
        }

        media = MediaFileUpload(local_path, resumable=True)
        request = self.youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
        )
        response = self._upload_resumable(request)
        video_id = response.get("id")
        if not video_id:
            raise RuntimeError("YouTube upload completed but no video id returned.")
        url = f"https://www.youtube.com/shorts/{video_id}"
        logger.info("YouTube upload OK: %s", url)
        return url

    def _upload_resumable(self, request) -> dict[str, Any]:
        response: dict[str, Any] | None = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                logger.info("YouTube upload %d%%", int(status.progress() * 100))
        return response

    def get_video_status(self, video_id: str) -> str:
        """Return upload/processing status of a video id."""
        try:
            result = self.youtube.videos().list(
                part="status", id=video_id
            ).execute()
            items = result.get("items", [])
            if not items:
                return "missing"
            return items[0]["status"].get("uploadStatus", "unknown")
        except HttpError as exc:
            logger.error("Could not verify video status: %s", exc)
            return "error"

    def verify_public(self, video_id: str) -> bool:
        status = self.get_video_status(video_id)
        return status in ("processed", "uploaded")

    @staticmethod
    def _enforce_title_limit(title: str, limit: int = 100) -> str:
        return title if len(title) <= limit else title[: limit - 1].rstrip() + "…"


def upload_to_youtube(
    local_path: str,
    seo: dict[str, Any],
    privacy_status: str | None = None,
) -> str:
    """Convenience entry point: returns the public YouTube URL."""
    agent = YouTubeUploadAgent()
    settings_yt = agent.settings.get("youtube", {})
    privacy = privacy_status or settings_yt.get("privacy_status", "public")
    return agent.upload_short(
        local_path=local_path,
        title=seo.get("youtube_title", ""),
        description=seo.get("youtube_description", ""),
        tags=seo.get("tags", []),
        category_id=seo.get("category_id", settings_yt.get("category_id", "24")),
        privacy_status=privacy,
    )

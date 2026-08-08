"""Agent 5 - Discord Reporting Agent.

Sends a detailed final status report to a Discord channel via a webhook after
each upload job completes.
"""

from __future__ import annotations

from typing import Any

import requests

from ..common.config import getenv, load_settings
from ..common.logger import get_logger
from ..common.time_utils import format_ts

logger = get_logger("agent5_discord")

EMBED_COLOR_SUCCESS = 0x22C55E
EMBED_COLOR_FAILURE = 0xEF4444
EMBED_COLOR_WARNING = 0xF59E0B


class DiscordReportingAgent:
    def __init__(self) -> None:
        self.settings = load_settings()
        webhook_env = self.settings.get("discord", {}).get(
            "webhook_url_env", "DISCORD_WEBHOOK_URL"
        )
        self.webhook_url = getenv(webhook_env)
        self.report_title = self.settings.get("discord", {}).get(
            "report_title", "Hollywood Movie Clips - Upload Report"
        )

    def available(self) -> bool:
        return bool(self.webhook_url)

    def send_report(self, report: dict[str, Any]) -> bool:
        """Send a rich embed report. Returns True on success."""
        if not self.webhook_url:
            logger.warning("Discord webhook not configured; skipping report.")
            return False

        status = report.get("overall_status", "unknown")
        color = EMBED_COLOR_SUCCESS
        if status == "failed":
            color = EMBED_COLOR_FAILURE
        elif status == "partial":
            color = EMBED_COLOR_WARNING

        embed = {
            "title": self.report_title,
            "color": color,
            "description": self._build_description(report),
            "fields": self._build_fields(report),
            "timestamp": report.get("reported_at") or "",
        }

        payload = {"embeds": [embed]}
        try:
            resp = requests.post(self.webhook_url, json=payload, timeout=30)
            if resp.status_code in (200, 204):
                logger.info("Discord report sent successfully.")
                return True
            logger.error("Discord webhook responded %s: %s", resp.status_code, resp.text)
            return False
        except Exception as exc:  # noqa: BLE001
            logger.error("Discord report failed: %s", exc)
            return False

    # -- embed builders ------------------------------------------------------
    def _build_description(self, report: dict[str, Any]) -> str:
        video_num = report.get("video_number")
        movie = report.get("movie_title") or "N/A"
        status = report.get("overall_status", "unknown").upper()
        lines = [
            f"**Video #{video_num}**",
            f"Movie: {movie}",
            f"Status: {status}",
        ]
        return "\n".join(lines)

    def _build_fields(self, report: dict[str, Any]) -> list[dict[str, str]]:
        def tick(value: bool | None) -> str:
            return "YES" if value else ("NO" if value is False else "UNKNOWN")

        def youtube_lines() -> str:
            entry = report.get("youtube") or {}
            return "\n".join([
                f"Status: {tick(entry.get('success'))}",
                f"Public URL: {entry.get('url') or 'N/A'}",
            ])

        def facebook_lines() -> str:
            entry = report.get("facebook") or {}
            return "\n".join([
                f"Status: {tick(entry.get('success'))}",
                f"Public URL: {entry.get('url') or 'N/A'}",
            ])

        fields = [
            {"name": "YouTube", "value": youtube_lines(), "inline": True},
            {"name": "Facebook", "value": facebook_lines(), "inline": True},
            {
                "name": "Google Drive",
                "value": f"Source: {report.get('source_file') or 'N/A'}\n"
                         f"Moved to Uploaded: {tick(report.get('moved_to_uploaded'))}",
                "inline": False,
            },
            {
                "name": "Upload Time",
                "value": report.get("upload_time") or "N/A",
                "inline": True,
            },
            {
                "name": "Workflow Status",
                "value": report.get("workflow_status") or "N/A",
                "inline": True,
            },
            {
                "name": "GitHub Actions Run",
                "value": report.get("actions_run_url") or "N/A",
                "inline": False,
            },
        ]
        errors = report.get("errors")
        if errors:
            fields.append(
                {"name": "Errors", "value": "\n".join(errors) or "N/A", "inline": False}
            )
        return fields


def send_discord_report(report: dict[str, Any]) -> bool:
    """Convenience entry point used by the orchestrator."""
    return DiscordReportingAgent().send_report(report)

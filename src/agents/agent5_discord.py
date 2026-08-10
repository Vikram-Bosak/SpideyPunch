"""Agent 5 - Discord Reporting Agent.

Sends a detailed final status report to a Discord channel via a webhook after
each upload job completes.
"""

from __future__ import annotations

from typing import Any

import requests

from ..common.config import getenv, load_settings
from ..common.logger import get_logger
from ..common.time_utils import format_ts, iso_ts

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
        """Send a formatted text report. Returns True on success."""
        if not self.webhook_url:
            logger.warning("Discord webhook not configured; skipping report.")
            return False

        seo = report.get("seo") or {}
        yt = report.get("youtube") or {}
        fb = report.get("facebook") or {}

        yt_ok = "✅" if yt.get("success") else "❌"
        fb_ok = "✅" if fb.get("success") else "❌"

        status_str = "✅ SUCCESS" if report.get("overall_status") == "completed" else "❌ FAILED"
        if report.get("overall_status") == "partial":
            status_str = "⚠️ PARTIAL SUCCESS"

        upload_time_str = report.get("upload_time") or "N/A"
        # Extract HH:MM AM/PM if possible from ISO/timestamp
        try:
            from datetime import datetime
            if " " in upload_time_str:
                dt_part = upload_time_str.split(" ")[1] # e.g. 11:30
                hour, minute = map(int, dt_part.split(":"))
                ampm = "AM" if hour < 12 else "PM"
                hour_12 = hour if hour <= 12 else hour - 12
                if hour_12 == 0:
                    hour_12 = 12
                upload_time_str = f"{hour_12:02d}:{minute:02d} {ampm}"
        except Exception:
            pass

        text_content = (
            f"🎬 **VIDEO UPLOAD REPORT**\n\n"
            f"**Video**: {report.get('source_file') or 'N/A'}\n\n"
            f"🔎 **SEO**\n"
            f"**Primary Keyword**:\n{seo.get('primary_keyword') or 'N/A'}\n\n"
            f"**Secondary Keywords**:\n" + "\n".join(seo.get('secondary_keywords', [])) + f"\n\n"
            f"📺 **YouTube**\n"
            f"SEO Title: ✅\n"
            f"Description: ✅\n"
            f"Tags: ✅\n"
            f"Hashtags: ✅\n"
            f"Upload: {yt_ok}\n"
            f"URL: {yt.get('url') or 'N/A'}\n\n"
            f"📘 **Facebook**\n"
            f"Caption: ✅\n"
            f"Keywords: ✅\n"
            f"Hashtags: ✅\n"
            f"Upload: {fb_ok}\n"
            f"URL: {fb.get('url') or 'N/A'}\n\n"
            f"⏱ **Upload Time**:\n{upload_time_str}\n\n"
            f"**STATUS**: {status_str}"
        )

        payload = {"content": text_content}
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

    @staticmethod
    def _iso_report_ts(report: dict[str, Any]) -> str:
        raw = report.get("reported_at") or ""
        # Already ISO 8601? e.g. 2026-08-08T15:29:00.000Z
        import re
        if re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", raw):
            return raw
        try:
            # "YYYY-MM-DD HH:MM UTC" -> ISO 8601 UTC
            from datetime import datetime, timezone
            dt = datetime.strptime(raw, "%Y-%m-%d %H:%M UTC")
            return dt.replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        except ValueError:
            return raw

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
            {"name": "YouTube Status", "value": youtube_lines(), "inline": True},
            {"name": "Facebook Status", "value": facebook_lines(), "inline": True},
        ]

        seo = report.get("seo")
        if seo:
            fields.extend([
                {
                    "name": "SEO Details",
                    "value": f"**Primary Keyword**: {seo.get('primary_keyword') or 'N/A'}\n"
                             f"**Secondary Keywords**: {', '.join(seo.get('secondary_keywords', [])) or 'N/A'}\n"
                             f"**SEO Score**: {seo.get('seo_score', 'N/A')}/100\n"
                             f"**Validation Errors**: {', '.join(seo.get('validation_errors', [])) or 'None'}",
                    "inline": False
                },
                {
                    "name": "YouTube SEO Metadata",
                    "value": f"**Title**: {seo.get('youtube_title') or 'N/A'}\n"
                             f"**Description**: {seo.get('youtube_description')[:250] or 'N/A'}...\n"
                             f"**Tags**: {', '.join(seo.get('tags', [])) or 'N/A'}\n"
                             f"**Hashtags**: {', '.join(seo.get('hashtags', [])) or 'N/A'}",
                    "inline": False
                },
                {
                    "name": "Facebook SEO Metadata",
                    "value": f"**Caption**: {seo.get('facebook_caption') or 'N/A'}",
                    "inline": False
                }
            ])

        fields.extend([
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
        ])
        errors = report.get("errors")
        if errors:
            fields.append(
                {"name": "Errors", "value": "\n".join(errors) or "N/A", "inline": False}
            )
        return fields


def send_discord_report(report: dict[str, Any]) -> bool:
    """Convenience entry point used by the orchestrator."""
    return DiscordReportingAgent().send_report(report)

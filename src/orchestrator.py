"""Orchestrator.

Runs the full pipeline for one upload slot per invocation:

    Ready folder
        -> Agent 1 (Drive Fetch)
        -> Agent 2 (SEO)
        -> Upload Queue & Scheduler
            -> Agent 3 (YouTube)  and  Agent 4 (Facebook)
        -> Upload Verification
        -> Move to Uploaded folder
        -> Agent 5 (Discord Report)

It is safe to invoke repeatedly (e.g. every 15 min from GitHub Actions cron).
State is persisted so each run resumes partial work: a successful YouTube upload
is never repeated if Facebook later fails.
"""

from __future__ import annotations

import os
from typing import Any

from .common import state as state_lib
from .common.config import getenv, load_schedule, load_settings
from .common.logger import get_logger
from .common.state import new_job
from .common.time_utils import (
    apply_jitter,
    format_ts,
    is_due,
    now_in_tz,
    random_jitter,
)

logger = get_logger("orchestrator")


class Orchestrator:
    def __init__(self, dry_run: bool = False) -> None:
        self.settings = load_settings()
        self.schedule = load_schedule()
        self.dry_run = dry_run
        self.max_retries = int(
            self.settings.get("app", {}).get("max_retries_per_platform", 3)
        )
        self.actions_run_url = self._build_actions_url()
        self._agent1 = None
        self._agent2 = None
        self._agent3 = None
        self._agent4 = None
        self._agent5 = None

    # -- lazy agent construction --------------------------------------------
    def _drive_fetch(self):
        if self._agent1 is None:
            from .agents.agent1_drive_fetch import DriveFetchAgent
            self._agent1 = DriveFetchAgent(dry_run=self.dry_run)
        return self._agent1

    def _seo(self):
        if self._agent2 is None:
            from .agents.agent2_seo import SeoAgent
            self._agent2 = SeoAgent()
        return self._agent2

    def _youtube(self):
        if self._agent3 is None:
            from .agents.agent3_youtube import YouTubeUploadAgent
            self._agent3 = YouTubeUploadAgent()
        return self._agent3

    def _facebook(self):
        if self._agent4 is None:
            from .agents.agent4_facebook import FacebookUploadAgent
            self._agent4 = FacebookUploadAgent()
        return self._agent4

    def _discord(self):
        if self._agent5 is None:
            from .agents.agent5_discord import DiscordReportingAgent
            self._agent5 = DiscordReportingAgent()
        return self._agent5

    @staticmethod
    def _build_actions_url() -> str:
        server = getenv("GITHUB_SERVER_URL", "https://github.com")
        repo = getenv("GITHUB_REPOSITORY", "")
        run_id = getenv("GITHUB_RUN_ID", "")
        if repo and run_id:
            return f"{server}/{repo}/actions/runs/{run_id}"
        return os.environ.get("GITHUB_ACTIONS_RUN_URL", "N/A (local run)")

    # -- main ---------------------------------------------------------------
    def run_once(self) -> int:
        state = state_lib.get_state()
        now = now_in_tz()
        today = now.date().isoformat()
        force = getenv("_FORCE_SLOT") == "1"
        logger.info("Orchestrator run at %s", format_ts(now))

        # Compute (and persist) today's jittered slot times, if not yet done.
        self._ensure_daily_times(state, today)

        # 1) Resume an in-flight job from today first (retries / partial).
        job = self._find_resumable_job(state, today, force)
        if job is None:
            # 2) Otherwise pick the next due slot.
            slot = self._find_due_slot(state, now, today)
            if slot is None:
                logger.info("No upload slot due right now; next scheduled run will check.")
                state_lib.persist(state)
                return 0
            job = self._start_new_job(state, slot, today)
            if job is None:
                # No clip available -> report (once per slot per day), but this
                # is a normal condition (Ready folder empty), not a pipeline failure.
                self._report_no_clip(state, slot, today)
                state_lib.persist(state)
                return 0

        self._process_job(state, job, today, force)
        state_lib.persist(state)
        return 0

    # -- daily jittered schedule --------------------------------------------
    def _ensure_daily_times(self, state: dict[str, Any], today: str) -> None:
        """Persist today's per-slot upload times: base USA peak time + random jitter.

        Jitter is generated once per day per slot (1-15 min), so the actual
        upload time differs every day but stays inside the USA high-traffic window.
        """
        daily = state.setdefault("daily_times", {})
        if today in daily:
            return
        jitter_low, jitter_high = self.schedule.get("jitter_minutes", [1, 15])
        times: dict[str, str] = {}
        for slot in self.schedule.get("slots", []):
            video_number = str(slot.get("video_number", 0))
            base = slot.get("time", "00:00")
            jitter = random_jitter(jitter_low, jitter_high)
            times[video_number] = apply_jitter(base, jitter)
            logger.info("Slot %s base %s + jitter %d min -> %s",
                        video_number, base, jitter, times[video_number])
        daily[today] = times
        state_lib.persist(state)

    def _slot_time(self, state: dict[str, Any], today: str, video_number: int) -> str:
        daily = state.get("daily_times", {}).get(today, {})
        return daily.get(str(video_number)) or "00:00"

    # -- scheduling ---------------------------------------------------------
    def _find_resumable_job(self, state: dict[str, Any], today: str, force: bool = False) -> dict[str, Any] | None:
        # Resume today's in-flight jobs first, then recover stale in-flight jobs
        # from previous days (e.g. a run that was killed mid-upload). Without this,
        # a partially-processed clip would stay stuck in the Processing folder.
        for job in state.get("jobs", []):
            if job.get("final_status") in ("completed", "failed", "partial"):
                continue
            if force:
                return job
            yt, fb = job["youtube"], job["facebook"]
            if yt["status"] == "pending" or fb["status"] == "pending":
                return job
            if yt["status"] == "failed" and yt["retries"] < self.max_retries:
                return job
            if fb["status"] == "failed" and fb["retries"] < self.max_retries:
                return job
        return None

    def _find_due_slot(self, state: dict[str, Any], now, today: str) -> dict[str, Any] | None:
        force = getenv("_FORCE_SLOT") == "1"
        for slot in self.schedule.get("slots", []):
            video_number = int(slot.get("video_number", 0))
            existing = state_lib.find_job_by_number(state, video_number)
            if existing and existing.get("date") == today:
                if existing.get("final_status") in ("completed", "failed", "partial"):
                    continue
            if force:
                return slot
            if is_due(now, self._slot_time(state, today, video_number)):
                return slot
        return None

    def _start_new_job(self, state: dict[str, Any], slot: dict[str, Any], today: str) -> dict[str, Any] | None:
        video_number = int(slot.get("video_number", 0))
        existing = state_lib.find_job_by_number(state, video_number)
        if existing and existing.get("date") == today:
            if existing.get("final_status") not in ("completed", "failed", "partial"):
                return existing

        logger.info("Slot %s due: starting new job.", video_number)
        exclude_ids = {
            j.get("drive_file_id")
            for j in state.get("jobs", [])
            if j.get("final_status") in ("completed", "partial")
        }
        try:
            clip = self._drive_fetch().fetch_next_clip(exclude_ids=exclude_ids)
        except Exception as exc:  # noqa: BLE001
            logger.error("Agent 1 (Drive Fetch) failed: %s", exc)
            self._report_drive_error(slot, today, str(exc))
            return None
        if clip is None:
            return None

        # Agent 2 - SEO metadata (American English).
        try:
            seo = self._seo().analyze(clip)
        except Exception as exc:  # noqa: BLE001
            logger.error("Agent 2 (SEO) failed: %s", exc)
            self._report_seo_error(slot, clip, today, str(exc))
            return None

        job = new_job(
            video_number=video_number,
            drive_file_id=clip["drive_file_id"],
            drive_file_name=clip["drive_file_name"],
            local_path=clip["local_path"],
        )
        job["date"] = today
        job["movie_title"] = seo.get("movie_title")
        job["seo"] = seo
        job["started_at"] = format_ts(now_in_tz())
        job["max_retries"] = self.max_retries
        slot_time = self._slot_time(state, today, video_number)
        job["youtube"]["slot_time"] = slot_time
        job["facebook"]["slot_time"] = slot_time
        state_lib.add_job(state, job)
        return job

    # -- job processing -----------------------------------------------------
    def _process_job(self, state: dict[str, Any], job: dict[str, Any], today: str, force: bool = False) -> None:
        now = now_in_tz()
        slot_time = job["youtube"].get("slot_time") or "00:00"
        # Jobs resumed from a previous day (stale in-flight jobs) should upload
        # as soon as possible rather than waiting for their old slot time.
        stale = job.get("date") not in (None, today)
        job["date"] = today

        yt = job["youtube"]
        fb = job["facebook"]

        # Each CI run has a fresh checkout, so re-download the clip if the
        # local file was lost between runs (needed for resumable retries).
        if not self.dry_run and (
            yt["status"] in ("pending", "failed") or fb["status"] in ("pending", "failed")
        ):
            try:
                job["local_path"] = self._drive_fetch().ensure_local_clip(
                    job["drive_file_id"], job["drive_file_name"], job.get("local_path") or ""
                )
            except Exception as exc:  # noqa: BLE001
                logger.error("Could not re-fetch clip %s from Drive: %s", job["drive_file_id"], exc)

        # YouTube
        if yt["status"] in ("pending", "failed") and (
            force or stale or is_due(now, slot_time) or yt.get("retry_due")
        ) and yt["retries"] < self.max_retries:
            self._upload_youtube(job)

        # Facebook
        fb_time = job["facebook"].get("slot_time") or slot_time
        if fb["status"] in ("pending", "failed") and (
            force or stale or is_due(now, fb_time) or fb.get("retry_due")
        ) and fb["retries"] < self.max_retries:
            self._upload_facebook(job)

        # Verification
        self._verify_uploads(job)

        # Completion handling
        yt_ok = yt["status"] == "success"
        fb_ok = fb["status"] == "success"
        yt_exhausted = yt["status"] == "failed"
        fb_exhausted = fb["status"] == "failed"

        if yt_ok and fb_ok:
            self._finalize_success(state, job)
        elif (yt_ok and fb_exhausted) or (fb_ok and yt_exhausted):
            self._finalize_partial(state, job)
        elif yt_exhausted and fb_exhausted:
            self._finalize_failed(state, job)

    def _upload_youtube(self, job: dict[str, Any]) -> None:
        yt = job["youtube"]
        logger.info("Uploading to YouTube: %s", job["drive_file_name"])
        if self.dry_run:
            yt["status"] = "success"
            yt["url"] = "https://www.youtube.com/shorts/DRYRUN"
            yt["error"] = None
            yt["last_attempt"] = format_ts(now_in_tz())
            return
        try:
            url = self._youtube().upload_short(
                local_path=job["local_path"],
                title=job["seo"]["youtube_title"],
                description=job["seo"]["youtube_description"],
                tags=job["seo"]["tags"],
                category_id=job["seo"]["category_id"],
            )
            yt["status"] = "success"
            yt["url"] = url
            yt["error"] = None
            yt["retry_due"] = False
            yt["last_attempt"] = format_ts(now_in_tz())
        except Exception as exc:  # noqa: BLE001
            self._mark_failed(yt, str(exc), "YouTube")

    def _upload_facebook(self, job: dict[str, Any]) -> None:
        fb = job["facebook"]
        logger.info("Uploading to Facebook: %s", job["drive_file_name"])
        if self.dry_run:
            fb["status"] = "success"
            fb["url"] = "https://www.facebook.com/reel/DRYRUN"
            fb["error"] = None
            fb["last_attempt"] = format_ts(now_in_tz())
            return
        try:
            url = self._facebook().upload_reel(
                local_path=job["local_path"],
                caption=job["seo"]["facebook_caption"],
            )
            fb["status"] = "success"
            fb["url"] = url
            fb["error"] = None
            fb["retry_due"] = False
            fb["last_attempt"] = format_ts(now_in_tz())
        except Exception as exc:  # noqa: BLE001
            self._mark_failed(fb, str(exc), "Facebook")

    def _mark_failed(self, entry: dict[str, Any], error: str, platform: str) -> None:
        entry["retries"] = entry.get("retries", 0) + 1
        entry["error"] = error
        entry["last_attempt"] = format_ts(now_in_tz())
        entry["retry_due"] = True
        logger.error("%s upload attempt %d failed: %s", platform, entry["retries"], error)
        if entry["retries"] >= self.max_retries:
            entry["status"] = "failed"
            entry["retry_due"] = False
        else:
            entry["status"] = "pending"

    def _verify_uploads(self, job: dict[str, Any]) -> None:
        yt = job["youtube"]
        fb = job["facebook"]
        if yt["status"] == "success" and yt.get("url"):
            try:
                video_id = yt["url"].rstrip("/").split("/")[-1]
                if not self.dry_run and not self._youtube().verify_public(video_id):
                    logger.warning("YouTube verification inconclusive for %s", video_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("YouTube verification skipped: %s", exc)
        if fb["status"] == "success" and fb.get("url"):
            video_id = fb["url"].rstrip("/").split("/")[-1]
            if not self.dry_run and not self._facebook().verify(video_id):
                logger.warning("Facebook verification inconclusive for %s", video_id)

    # -- finalization -------------------------------------------------------
    def _move_to_uploaded(self, job: dict[str, Any]) -> bool:
        if job.get("moved_to_uploaded"):
            return True
        if self.dry_run:
            job["moved_to_uploaded"] = True
            return True
        try:
            drive = self._drive_fetch().drive
            drive.move_file(
                job["drive_file_id"],
                self._drive_fetch().processing_folder_id(),
                self._drive_fetch().uploaded_folder_id(),
            )
            job["moved_to_uploaded"] = True
            logger.info("Moved %s to Uploaded folder.", job["drive_file_name"])
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error("CRITICAL: failed to move %s to Uploaded: %s", job["drive_file_name"], exc)
            return False

    def _finalize_success(self, state: dict[str, Any], job: dict[str, Any]) -> None:
        moved = self._move_to_uploaded(job)
        job["final_status"] = "completed"
        job["completed_at"] = format_ts(now_in_tz())
        report = self._build_report(job, "completed", moved)
        self._send_report(report)

    def _finalize_partial(self, state: dict[str, Any], job: dict[str, Any]) -> None:
        # Only move to Uploaded when the primary (YouTube) succeeded.
        moved = self._move_to_uploaded(job) if job["youtube"]["status"] == "success" else False
        job["final_status"] = "partial"
        job["completed_at"] = format_ts(now_in_tz())
        report = self._build_report(job, "partial", moved)
        self._send_report(report)

    def _finalize_failed(self, state: dict[str, Any], job: dict[str, Any]) -> None:
        job["final_status"] = "failed"
        job["completed_at"] = format_ts(now_in_tz())
        report = self._build_report(job, "failed", False)
        self._send_report(report)

    # -- reports ------------------------------------------------------------
    def _build_report(self, job: dict[str, Any], status: str, moved: bool) -> dict[str, Any]:
        errors: list[str] = []
        for platform in ("youtube", "facebook"):
            entry = job[platform]
            if entry.get("error"):
                errors.append(f"{platform.title()}: {entry['error']}")
        return {
            "video_number": job["video_number"],
            "movie_title": job.get("movie_title"),
            "overall_status": status,
            "youtube": {
                "success": job["youtube"]["status"] == "success",
                "url": job["youtube"].get("url"),
            },
            "facebook": {
                "success": job["facebook"]["status"] == "success",
                "url": job["facebook"].get("url"),
            },
            "source_file": job["drive_file_name"],
            "moved_to_uploaded": moved,
            "upload_time": job.get("started_at"),
            "workflow_status": status.upper(),
            "actions_run_url": self.actions_run_url,
            "errors": errors,
            "reported_at": format_ts(now_in_tz()),
        }

    def _send_report(self, report: dict[str, Any]) -> None:
        if self.dry_run:
            logger.info("DRY RUN: would send Discord report: %s", report)
            return
        self._discord().send_report(report)

    def _report_no_clip(self, state: dict[str, Any], slot: dict[str, Any], today: str) -> None:
        video_number = int(slot.get("video_number", 0))
        reported = state.setdefault("reported_events", {})
        if reported.get(f"{today}:no_clip:{video_number}"):
            logger.info("No-clip already reported for slot %s today.", video_number)
            return
        reported[f"{today}:no_clip:{video_number}"] = True
        report = {
            "video_number": video_number,
            "movie_title": "N/A",
            "overall_status": "failed",
            "youtube": {"success": False, "url": None},
            "facebook": {"success": False, "url": None},
            "source_file": "N/A",
            "moved_to_uploaded": False,
            "upload_time": format_ts(now_in_tz()),
            "workflow_status": "NO CLIP AVAILABLE",
            "actions_run_url": self.actions_run_url,
            "errors": ["Google Drive Ready folder is empty; no clip to upload."],
            "reported_at": format_ts(now_in_tz()),
        }
        self._send_report(report)

    def _report_drive_error(self, slot: dict[str, Any], today: str, error: str) -> None:
        report = {
            "video_number": int(slot.get("video_number", 0)),
            "movie_title": "N/A",
            "overall_status": "failed",
            "youtube": {"success": False, "url": None},
            "facebook": {"success": False, "url": None},
            "source_file": "N/A",
            "moved_to_uploaded": False,
            "upload_time": format_ts(now_in_tz()),
            "workflow_status": "DRIVE FETCH FAILED",
            "actions_run_url": self.actions_run_url,
            "errors": [f"Google Drive fetch failed: {error}"],
            "reported_at": format_ts(now_in_tz()),
        }
        self._send_report(report)

    def _report_seo_error(self, slot: dict[str, Any], clip: dict[str, Any], today: str, error: str) -> None:
        report = {
            "video_number": int(slot.get("video_number", 0)),
            "movie_title": "N/A",
            "overall_status": "failed",
            "youtube": {"success": False, "url": None},
            "facebook": {"success": False, "url": None},
            "source_file": clip.get("drive_file_name", "N/A"),
            "moved_to_uploaded": False,
            "upload_time": format_ts(now_in_tz()),
            "workflow_status": "SEO ANALYSIS FAILED",
            "actions_run_url": self.actions_run_url,
            "errors": [f"Movie clip analysis & SEO failed: {error}"],
            "reported_at": format_ts(now_in_tz()),
        }
        self._send_report(report)

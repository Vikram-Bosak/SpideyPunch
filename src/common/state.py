"""Workflow state management.

State lives in state/workflow_state.json. It tracks every clip job so that:
- the same Google Drive file is never uploaded twice (duplicate prevention),
- retry counters are persisted between GitHub Actions runs,
- partial failures (YouTube ok, Facebook failed) resume without re-uploading the
  successful platform.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .config import load_state, save_state


def _platform_default() -> dict[str, Any]:
    return {
        "status": "pending",        # pending | uploading | success | failed
        "url": None,
        "error": None,
        "retries": 0,
        "last_attempt": None,
    }


def new_job(video_number: int, drive_file_id: str, drive_file_name: str,
            local_path: str) -> dict[str, Any]:
    """Create a fresh job record for one clip."""
    return {
        "video_number": video_number,
        "drive_file_id": drive_file_id,
        "drive_file_name": drive_file_name,
        "local_path": local_path,
        "movie_title": None,
        "seo": None,
        "youtube": _platform_default(),
        "facebook": _platform_default(),
        "instagram": _platform_default(),
        "moved_to_uploaded": False,
        "started_at": None,
        "completed_at": None,
        "final_status": None,       # completed | partial | failed
    }


def get_state() -> dict[str, Any]:
    return load_state()


def persist(state: dict[str, Any]) -> None:
    save_state(state)


def find_job_by_file(state: dict[str, Any], drive_file_id: str) -> dict[str, Any] | None:
    for job in state.get("jobs", []):
        if job.get("drive_file_id") == drive_file_id:
            return job
    return None


def find_job_by_number(state: dict[str, Any], video_number: int, date: str | None = None) -> dict[str, Any] | None:
    for job in state.get("jobs", []):
        if job.get("video_number") == video_number:
            if date is None or job.get("date") == date:
                return job
    return None


def add_job(state: dict[str, Any], job: dict[str, Any]) -> None:
    jobs = state.setdefault("jobs", [])
    # Replace an existing entry with the same drive file id, if any.
    for idx, existing in enumerate(jobs):
        if existing.get("drive_file_id") == job["drive_file_id"]:
            jobs[idx] = job
            return
    jobs.append(job)


def reset_platform_if_pending(job: dict[str, Any], platform: str) -> None:
    """Re-raise a failed platform back to pending for retry (bounded by retries)."""
    entry = job[platform]
    if entry["status"] == "failed" and entry["retries"] >= job.get("max_retries", 0):
        return
    if entry["status"] in ("failed",):
        entry["status"] = "pending"


def copy_job(job: dict[str, Any]) -> dict[str, Any]:
    return deepcopy(job)

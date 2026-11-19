"""Saving the download queue between runs (no Qt).

Only the yt-dlp arguments are stored, not the command prefix that launches yt-dlp, so a
restored job uses whatever yt-dlp the app is set up with when it starts again.
"""
from __future__ import annotations

from .runner import DONE, FINISHED

VERSION = 1


def job_record(job) -> dict:
    return {
        "args": list(job.args),
        "label": job.label,
        "url": getattr(job, "url", ""),
        "folder": getattr(job, "folder", ""),
        "archive": bool(getattr(job, "is_archive", False)),
        "exclusive": bool(getattr(job, "exclusive", False)),
        # Jobs stopped only because the app was closing come back as Queued.
        "status": "Queued" if getattr(job, "resume", False) else job.status,
        "last_error": getattr(job, "last_error", ""),
    }


def dump(jobs) -> dict:
    """Everything still worth seeing after a restart: all jobs except plain "Done"."""
    return {"version": VERSION,
            "jobs": [job_record(j) for j in jobs if j.status != DONE and getattr(j, "args", None)]}


def load(data) -> list[dict]:
    """Records to recreate. Running jobs come back as Queued so they resume (yt-dlp
    continues .part files and skips what the download archive already lists)."""
    if not isinstance(data, dict) or data.get("version") != VERSION:
        return []
    out = []
    for rec in data.get("jobs") or []:
        if not isinstance(rec, dict) or not isinstance(rec.get("args"), list) or not rec["args"]:
            continue
        if not all(isinstance(a, str) for a in rec["args"]):
            continue
        status = rec.get("status")
        status = status if status in FINISHED else "Queued"
        out.append({
            "args": rec["args"],
            "label": str(rec.get("label") or ""),
            "url": str(rec.get("url") or ""),
            "folder": str(rec.get("folder") or ""),
            "archive": bool(rec.get("archive")),
            "exclusive": bool(rec.get("exclusive")),
            "status": status,
            "last_error": str(rec.get("last_error") or ""),
        })
    return out

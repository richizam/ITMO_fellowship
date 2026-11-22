"""Background jobs: building a dossier takes a few seconds to a minute (external APIs,
LLM), so the web UI starts a job and polls its progress."""
from __future__ import annotations

import logging
import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime

from app.pipeline import CandidateInput, build_dossier

log = logging.getLogger(__name__)
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="dossier")
_lock = threading.Lock()


@dataclass
class Job:
    id: str
    label: str
    status: str = "queued"  # queued | running | done | error
    steps: list[str] = field(default_factory=list)
    dossier_id: str | None = None
    error: str = ""
    started: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict:
        return {"id": self.id, "label": self.label, "status": self.status, "steps": self.steps,
                "dossier_id": self.dossier_id, "error": self.error}


_jobs: dict[str, Job] = {}


def submit(inp: CandidateInput, label: str) -> Job:
    job = Job(id=uuid.uuid4().hex[:12], label=label)
    with _lock:
        _jobs[job.id] = job
    _pool.submit(_run, job, inp)
    return job


def get(job_id: str) -> Job | None:
    return _jobs.get(job_id)


def _run(job: Job, inp: CandidateInput) -> None:
    job.status = "running"
    try:
        d = build_dossier(inp, progress=job.steps.append)
        job.dossier_id = d.id
        job.status = "done"
    except Exception as e:  # report the failure to the UI instead of losing it in a thread
        log.error("dossier job %s failed:\n%s", job.id, traceback.format_exc())
        job.error = f"{type(e).__name__}: {e}"
        job.status = "error"

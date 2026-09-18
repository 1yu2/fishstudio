import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy.dialects import postgresql

from app.db.models import GenerationJob
from app.services import job_event_service, job_service
from app.services.storage_backend import ObjectStorage


def test_fair_lease_query_partitions_by_project_owner():
    class Result:
        def scalars(self):
            return self

        def all(self):
            return []

    class Session:
        async def execute(self, statement):
            self.sql = str(statement.compile(dialect=postgresql.dialect()))
            return Result()

        async def flush(self):
            pass

    session = Session()
    asyncio.run(job_service.lease_jobs(session, limit=5, worker_id="worker-a"))
    assert "row_number() OVER (PARTITION BY series_projects.user_id" in session.sql
    assert "generation_jobs.priority DESC" in session.sql


def test_dead_letter_sets_terminal_timestamp_and_event():
    class Session:
        def __init__(self):
            self.rows = []

        def add(self, row):
            self.rows.append(row)

        async def flush(self):
            pass

    job = GenerationJob(
        id="job-1", project_id="project-1", operation="generate_video",
        idempotency_key="key", status="running", attempt=3, max_attempts=3,
        progress=20,
    )
    session = Session()
    asyncio.run(job_service.mark_failed(
        session, job, error_code="provider_error", error="boom", retryable=True
    ))
    assert job.status == "failed"
    assert job.dead_lettered_at is not None
    assert any(getattr(row, "event", "") == "job.dead_lettered" for row in session.rows)


def test_job_event_contract_contains_required_context():
    row = SimpleNamespace(
        id=7, event="job.running", project_id="p1", run_id="r1", step_id="s1",
        job_id="j1", entity_type="shot", entity_id="shot-1", status="running",
        progress=40, payload={}, occurred_at=datetime(2026, 9, 18, tzinfo=timezone.utc),
    )
    payload = job_event_service.event_payload(row)
    assert set(("event", "project_id", "run_id", "step_id", "job_id", "entity_id", "progress", "occurred_at")) <= payload.keys()


def test_local_storage_keeps_legacy_file_and_checksum(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTIFACT_STORAGE_BACKEND", "local")
    path = Path(tmp_path) / "artifact.bin"
    path.write_bytes(b"fishstudio")
    backend, checksum = ObjectStorage().promote_file(path, "legacy/artifact.bin")
    assert backend == "local"
    assert checksum == "ab1cafb66b2e4e27c9a9e8a319d627bf9bef88f3c5d679ac7932663083e1668c"
    assert path.read_bytes() == b"fishstudio"


def test_s3_storage_promotes_temporary_object_atomically(tmp_path, monkeypatch):
    calls = []

    class Client:
        def upload_file(self, source, bucket, key, ExtraArgs=None):
            calls.append(("upload", bucket, key, ExtraArgs))

        def copy_object(self, **kwargs):
            calls.append(("copy", kwargs))

        def delete_object(self, **kwargs):
            calls.append(("delete", kwargs))

    monkeypatch.setenv("ARTIFACT_STORAGE_BACKEND", "s3")
    storage = ObjectStorage()
    monkeypatch.setattr(storage, "_client", lambda: Client())
    path = Path(tmp_path) / "artifact.bin"
    path.write_bytes(b"fishstudio")
    backend, _ = storage.promote_file(path, "videos/final.mp4", "video/mp4")
    assert backend == "s3"
    assert calls[0][0] == "upload" and calls[0][2].startswith(".tmp/")
    assert calls[1][0] == "copy" and calls[1][1]["Key"] == "videos/final.mp4"
    assert calls[2][0] == "delete"

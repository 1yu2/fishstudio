"""产物输入血缘：补充审计 metadata，失败不阻断生成任务。"""

import hashlib
import logging

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import async_object_session

from app.db.models import Artifact, GenerationJob

logger = logging.getLogger(__name__)


async def attach_lineage(artifact: Artifact, job: GenerationJob) -> None:
    try:
        params = job.params or {}
        trace = params.get("trace") or {}
        refs = []
        for key, role in (
            ("first_frame", "first_frame"),
            ("last_frame", "last_frame"),
            ("reference_images", "reference"),
            ("reference_artifact_ids", "reference"),
            ("source_image", "source_image"),
        ):
            values = params.get(key) or []
            for ref in values if isinstance(values, list) else [values]:
                if isinstance(ref, str) and ref:
                    refs.append((role, ref))
        session = async_object_session(artifact)
        matches = {}
        if session is not None and refs:
            keys = {ref.removeprefix("/storage/") for _, ref in refs}
            # 保存点保证查询失败不会把主事务留在 aborted 状态。
            async with session.begin_nested():
                rows = await session.scalars(
                    select(Artifact)
                    .where(
                        Artifact.project_id == job.project_id,
                        or_(Artifact.id.in_(keys), Artifact.storage_key.in_(keys)),
                    )
                    .order_by(Artifact.created_at, Artifact.id)
                )
                for row in rows:
                    matches.setdefault(row.storage_key, row)
                    matches[row.id] = row
        inputs = []
        for role, ref in refs:
            row = matches.get(ref.removeprefix("/storage/"))
            item = {"role": role, "ref": row.storage_key if row else ref}
            if row:
                item["artifact_id"] = row.id
            if item not in inputs:
                inputs.append(item)
        artifact.artifact_metadata = {
            **(artifact.artifact_metadata or {}),
            "lineage": {
                "job_id": job.id,
                "operation": job.operation,
                "run_id": trace.get("run_id") or job.id,
                "request_id": trace.get("request_id") or job.id,
                "model_invocation_id": params.get("model_invocation_id"),
                "inputs": inputs,
                "prompt_sha256": hashlib.sha256((params.get("prompt") or "").encode()).hexdigest(),
                "prompt_version": trace.get("prompt_version"),
            },
        }
    except Exception:
        logger.warning("产物血缘记录失败（已忽略）", exc_info=True)

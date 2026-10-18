"""Artifact 存储抽象：本地兼容 + S3/MinIO 临时对象原子晋升。"""

from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path
from typing import Optional


class ObjectStorage:
    def __init__(self) -> None:
        self.backend = os.getenv("ARTIFACT_STORAGE_BACKEND", "local").strip().lower()
        self.bucket = os.getenv("S3_BUCKET", "fishstudio")

    def _client(self):
        import boto3

        return boto3.client(
            "s3",
            endpoint_url=os.getenv("S3_ENDPOINT") or None,
            aws_access_key_id=os.getenv("S3_ACCESS_KEY") or None,
            aws_secret_access_key=os.getenv("S3_SECRET_KEY") or None,
            region_name=os.getenv("S3_REGION", "us-east-1"),
        )

    def promote_file(self, path: Path, storage_key: str, mime_type: str = "") -> tuple[str, str]:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
        checksum = digest.hexdigest()
        if self.backend not in {"s3", "minio"}:
            return "local", checksum
        client = self._client()
        temporary = f".tmp/{uuid.uuid4().hex}"
        extra = {"Metadata": {"sha256": checksum}}
        if mime_type:
            extra["ContentType"] = mime_type
        client.upload_file(str(path), self.bucket, temporary, ExtraArgs=extra)
        client.copy_object(
            Bucket=self.bucket,
            Key=storage_key,
            CopySource={"Bucket": self.bucket, "Key": temporary},
            MetadataDirective="COPY",
            TaggingDirective="REPLACE",
            Tagging="lifecycle=managed",
        )
        client.delete_object(Bucket=self.bucket, Key=temporary)
        lifecycle_days = int(os.getenv("S3_LIFECYCLE_DAYS", "0") or 0)
        if lifecycle_days > 0:
            try:
                client.put_bucket_lifecycle_configuration(
                    Bucket=self.bucket,
                    LifecycleConfiguration={
                        "Rules": [
                            {
                                "ID": "fishstudio-managed-artifacts",
                                "Status": "Enabled",
                                "Filter": {"Tag": {"Key": "lifecycle", "Value": "managed"}},
                                "Expiration": {"Days": lifecycle_days},
                                "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 1},
                            }
                        ]
                    },
                )
            except Exception:
                # 部分托管 S3 账号不允许改 bucket policy，不阻断已经完成的晋升。
                pass
        return self.backend, checksum

    def signed_url(self, storage_key: str, *, expires_seconds: int = 900) -> Optional[str]:
        if self.backend not in {"s3", "minio"}:
            return None
        return self._client().generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": storage_key},
            ExpiresIn=expires_seconds,
        )


storage = ObjectStorage()

from __future__ import annotations

from pathlib import Path
from typing import Any
import hashlib
import json

from control_plane.settings import AppConfig

try:
    import boto3
except ModuleNotFoundError:  # pragma: no cover
    boto3 = None


class ArtifactStore:
    def __init__(self, config: AppConfig):
        self.config = config
        self.local_root = Path(".omega-cas")
        self.local_root.mkdir(parents=True, exist_ok=True)

    def _s3_client(self):
        if boto3 is None:
            return None
        return boto3.client(
            "s3",
            endpoint_url=self.config.omega.cas_endpoint,
            aws_access_key_id=self.config.omega.cas_access_key,
            aws_secret_access_key=self.config.omega.cas_secret_key,
        )

    def put_bundle(self, namespace: str, payload: dict[str, Any]) -> str:
        encoded = json.dumps(payload, sort_keys=True, indent=2).encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        object_name = f"{namespace}/{digest}.json"

        client = self._s3_client()
        if client is not None:
            try:
                existing = {bucket["Name"] for bucket in client.list_buckets().get("Buckets", [])}
                if self.config.omega.cas_bucket not in existing:
                    client.create_bucket(Bucket=self.config.omega.cas_bucket)
                client.put_object(
                    Bucket=self.config.omega.cas_bucket,
                    Key=object_name,
                    Body=encoded,
                    ContentType="application/json",
                )
                return f"s3://{self.config.omega.cas_bucket}/{object_name}"
            except Exception:
                pass

        local_path = self.local_root / namespace / f"{digest}.json"
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(encoded)
        return str(local_path.resolve())

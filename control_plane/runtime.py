from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

import redis

from control_plane.redis_keys import WORKER_HEARTBEATS
from control_plane.settings import AppConfig, load_config


def build_parser(description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--profile", default=os.getenv("OMEGA_PROFILE", "dev-macos"))
    return parser


def load_runtime(profile: str) -> tuple[AppConfig, Any]:
    config = load_config(profile)
    client = redis.Redis.from_url(config.omega.redis, decode_responses=True)
    return config, client


def ensure_stream_group(client: Any, stream: str, group: str) -> None:
    try:
        client.xgroup_create(stream, group, id="0", mkstream=True)
    except redis.ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def infer_repo_path(source_path: str, test_path: str) -> Path:
    return Path(os.path.commonpath([Path(source_path).resolve(), Path(test_path).resolve()]))


def update_worker_heartbeat(
    client: Any,
    worker_id: str,
    ttl_seconds: int,
) -> None:
    now = time.time()
    client.zadd(WORKER_HEARTBEATS, {worker_id: now})
    client.zremrangebyscore(WORKER_HEARTBEATS, 0, now - ttl_seconds)


def parse_stream_message(message: dict[str, Any]) -> dict[str, Any]:
    payload = message.get("payload", "{}")
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    return json.loads(payload)

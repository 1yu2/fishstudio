"""Deployment write-only 环境密钥存储。"""

from __future__ import annotations

import os
import re
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
ENV_FILE = BACKEND_DIR / ".env"
ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")


def write_env_credential(reference: str, value: str) -> None:
    if not reference.startswith("env:"):
        raise ValueError("当前仅支持写入 env: 类型的 credential_ref")
    key = reference.split(":", 1)[1]
    if not ENV_NAME.fullmatch(key):
        raise ValueError("credential_ref 环境变量名无效")
    if not value.strip():
        raise ValueError("API Key 不能为空")
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    pattern = re.compile(rf"^{re.escape(key)}\s*=")
    output: list[str] = []
    replaced = False
    for line in lines:
        if pattern.match(line):
            output.append(f"{key}={value.strip()}")
            replaced = True
        else:
            output.append(line)
    if not replaced:
        output.append(f"{key}={value.strip()}")
    ENV_FILE.write_text("\n".join(output) + "\n", encoding="utf-8")
    os.environ[key] = value.strip()

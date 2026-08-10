"""t8-web 前端构建占位测试。

- 无 docker（如 hardened 沙箱内）→ 仅做文件存在性断言，docker 构建步骤跳过；
- 宿主有 docker → 执行 `docker build .sandbox/web.Dockerfile`，
  容器内跑 npm ci + vitest + vite build，全绿才通过（验收 #1）。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# 前端交付物清单（缺失即失败）
REQUIRED_FILES = [
    "frontend/package.json",
    "frontend/package-lock.json",
    "frontend/vite.config.ts",
    "frontend/tsconfig.json",
    "frontend/index.html",
    "frontend/src/main.tsx",
    "frontend/src/App.tsx",
    "frontend/src/theme.css",
    "frontend/src/lib/scriptGraph.ts",
    "frontend/src/lib/progress.ts",
    "frontend/src/store/useGraphStore.ts",
    "frontend/src/components/nodes/ActNode.tsx",
    "frontend/src/components/nodes/SceneNode.tsx",
    "frontend/src/components/nodes/EventNode.tsx",
    "frontend/src/components/nodes/NpcNode.tsx",
    "frontend/src/components/nodes/ClueNode.tsx",
    "frontend/src/components/NodeDrawer.tsx",
    "frontend/src/components/Legend.tsx",
    "frontend/src/components/ProgressBand.tsx",
    "frontend/public/campaign.json",
    "frontend/public/progress.jsonl",
    "frontend/tests/scriptGraph.test.ts",
    "frontend/tests/store.test.ts",
    "frontend/tests/progress.test.ts",
    "frontend/tests/components.test.ts",
    ".sandbox/web.Dockerfile",
]


def test_web_frontend_deliverables_exist() -> None:
    missing = [p for p in REQUIRED_FILES if not (ROOT / p).is_file()]
    assert not missing, f"缺失前端交付物: {missing}"


def test_web_package_lock_exists() -> None:
    """package-lock.json 由宿主 npm install 生成，容器 npm ci 依赖它复现。"""
    lock = ROOT / "frontend/package-lock.json"
    assert lock.is_file(), "frontend/package-lock.json 缺失（宿主先跑 npm install）"
    assert b"@xyflow/react" in lock.read_bytes()


def test_web_dockerfile_build() -> None:  # pragma: no cover - 宿主专用
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("docker 不可用（hardened 沙箱内）——跳过容器构建")
    result = subprocess.run(
        [
            "docker", "build",
            "--progress=plain",  # BuildKit 默认把 RUN 输出流到 stderr，plain 模式进 stdout
            "-f", str(ROOT / ".sandbox/web.Dockerfile"),
            "-t", "tindalos-web:check",
            str(ROOT),
        ],
        capture_output=True,
        text=True,
        timeout=1800,
    )
    combined = (result.stdout or "") + (result.stderr or "")
    tail = combined[-4000:]
    assert result.returncode == 0, f"docker build 失败:\n{tail}"
    # 全缓存命中时 BuildKit 可能不回放 RUN 输出——returncode 0 即足够；
    # 有输出时校验容器内确实完成了 vitest+vite build 标记。
    if combined.strip():
        assert "web build OK" in combined, f"容器内未完成 vitest+vite build 校验:\n{tail}"

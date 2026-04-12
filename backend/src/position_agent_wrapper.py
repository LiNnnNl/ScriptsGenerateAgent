"""
position_agent_wrapper.py

subprocess 封装层，调用 position_agent_standalone.py 生成真实 3D 坐标文件。
若场景缺少必要的资源文件（scene_export / position_template），静默跳过。
"""

import json
import os
import re
import sys
import subprocess
from pathlib import Path

# 资源目录和独立脚本路径（相对于本文件）
_SRC_DIR = Path(__file__).parent
_BACKEND_DIR = _SRC_DIR.parent
RESOURCES_DIR = _BACKEND_DIR / "resources"
STANDALONE_PATH = _BACKEND_DIR / "position_agent_standalone.py"


def _normalize_scene_key(raw: str) -> str:
    """归一化场景键，兼容空格/下划线/大小写差异。"""
    return re.sub(r"[^a-z0-9]", "", (raw or "").lower())


def _resolve_scene_resource_file(folder: Path, scene_id: str) -> Path:
    """
    在资源目录中解析场景文件：
    1) 先尝试精确匹配 <scene_id>.json
    2) 再按归一化键模糊匹配（Space Station == SpaceStation）
    """
    exact = folder / f"{scene_id}.json"
    if exact.exists():
        return exact

    wanted = _normalize_scene_key(scene_id)
    if not wanted:
        return exact

    for candidate in folder.glob("*.json"):
        if _normalize_scene_key(candidate.stem) == wanted:
            return candidate
    return exact


def _force_scene_key(output_path: str, scene_id: str) -> None:
    """将点位 JSON 最外层的场景 key 强制替换为用户选择的 scene_id。"""
    try:
        with open(output_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or len(data) != 1:
            return
        current_key = next(iter(data))
        if current_key == scene_id:
            return
        data[scene_id] = data.pop(current_key)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def run_position_agent(
    script_path: str,
    scene_id: str,
    output_dir: str,
    output_filename: str,
) -> dict:
    """
    调用 position_agent_standalone.py 为指定剧本生成位置坐标文件。

    Returns:
        {"ok": True, "output_path": "..."}                     — 成功
        {"ok": False, "skip": True, "error": "缺少资源文件"}   — 静默跳过
        {"ok": False, "error": "..."}                          — 执行失败
    """
    scene_export_dir = RESOURCES_DIR / "scene_exports"
    template_dir = RESOURCES_DIR / "position_templates"
    scene_export = _resolve_scene_resource_file(scene_export_dir, scene_id)
    template_path = _resolve_scene_resource_file(template_dir, scene_id)

    if not scene_export.exists() or not template_path.exists():
        return {
            "ok": False,
            "skip": True,
            "error": (
                "缺少场景资源文件（"
                f"scene_exports/{scene_id}.json 或 position_templates/{scene_id}.json；"
                "已尝试空格/下划线/大小写兼容匹配）"
            )
        }

    output_path = str(Path(output_dir) / output_filename)

    # 通过 sys.argv 传参，用 runpy 启动 standalone，同时在启动前给 SSL context 加上
    # OP_IGNORE_UNEXPECTED_EOF（Python 3.12+），避免火山引擎 ARK 不发 close_notify 时报错。
    script_args = [
        str(STANDALONE_PATH),
        "--deepseek-api-key", os.getenv("API_KEY", ""),
        "--api-url",          (os.getenv("BASE_URL", "").rstrip("/") + "/chat/completions"),
        "--model",            os.getenv("MODEL", "deepseek-chat"),
        "--no-force-json-response",
        "--scene-export-path",        str(scene_export),
        "--script-file-path",         script_path,
        "--positions-template-path",  str(template_path),
        "--output-path",              output_path,
    ]
    inline = (
        "import ssl, sys, runpy\n"
        "_orig = ssl.create_default_context\n"
        "def _patched(*a, **kw):\n"
        "    ctx = _orig(*a, **kw)\n"
        "    ctx.options |= getattr(ssl, 'OP_IGNORE_UNEXPECTED_EOF', 0x80)\n"
        "    return ctx\n"
        # urllib.request 实际调用的是 ssl._create_default_https_context，
        # 两个都要打补丁才能覆盖到
        "ssl.create_default_context = _patched\n"
        "ssl._create_default_https_context = _patched\n"
        f"sys.argv = {repr(script_args)}\n"
        f"runpy.run_path({repr(str(STANDALONE_PATH))}, run_name='__main__')\n"
    )
    cmd = [sys.executable, "-c", inline]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
        )
        stdout = result.stdout.strip()
        if stdout:
            try:
                agent_result = json.loads(stdout)
            except json.JSONDecodeError:
                agent_result = None
            if isinstance(agent_result, dict) and agent_result.get("ok"):
                _force_scene_key(output_path, scene_id)
            return agent_result or {"ok": False, "error": "position_agent_standalone 返回非 JSON 内容"}
        stderr = result.stderr.strip()
        return {
            "ok": False,
            "error": stderr or f"position_agent_standalone 退出码 {result.returncode}"
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "position_agent_standalone 执行超时（>300s）"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

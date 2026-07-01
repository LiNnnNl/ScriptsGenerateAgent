#!/usr/bin/env python3
"""Run end-to-end script generation eval cases through the Flask API."""

from __future__ import annotations

import argparse
import concurrent.futures
import html
import json
import shutil
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


ROOT = Path(__file__).resolve().parents[1]
EVAL_DIR = ROOT / "eval"
DEFAULT_CASES = EVAL_DIR / "test_cases.json"
BACKEND_OUTPUTS = ROOT / "backend" / "outputs"


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def slug(value: str) -> str:
    allowed = []
    for ch in value.lower().replace("：", "_").replace(" ", "_"):
        allowed.append(ch if ch.isalnum() or ch in ("_", "-") else "_")
    text = "".join(allowed).strip("_")
    return text or "case"


def load_characters(names: Iterable[str]) -> List[Dict[str, Any]]:
    names = list(names or [])
    if not names:
        return []
    resources = load_json(ROOT / "backend" / "resources" / "characters_resource.json")
    by_name = {item.get("name"): item for item in resources if isinstance(item, dict)}
    missing = [name for name in names if name not in by_name]
    if missing:
        raise ValueError(f"角色不存在于 characters_resource.json: {', '.join(missing)}")
    return [by_name[name] for name in names]


def build_payload(case: Dict[str, Any]) -> Dict[str, Any]:
    scene_pool = case.get("scene_pool") or []
    selected_scene = case.get("scene_id") or (scene_pool[0] if scene_pool else "")
    return {
        "custom_characters": load_characters(case.get("character_names") or []),
        "scene_id": selected_scene,
        "scene_pool": scene_pool,
        "act_scenes": case.get("act_scenes") or scene_pool[:1],
        "creative_idea": case["creative_idea"],
        "required_character_count": case.get("character_count", 2),
        "act_count": case.get("act_count", 1),
        "direct_mode": bool(case.get("direct_mode", False)),
    }


def stream_generate(base_url: str, payload: Dict[str, Any], timeout: int) -> List[Dict[str, Any]]:
    url = base_url.rstrip("/") + "/api/generate"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    events: List[Dict[str, Any]] = []
    with urllib.request.urlopen(request, timeout=timeout) as response:
        for raw_line in response:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                events.append({"type": "parse_error", "raw": line})
    return events


def copy_backend_file(filename: Optional[str], json_dir: Path, prefix: str) -> Optional[str]:
    if not filename:
        return None
    src = BACKEND_OUTPUTS / filename
    if not src.exists():
        return None
    dest_name = f"{prefix}__{filename}"
    shutil.copy2(src, json_dir / dest_name)
    return dest_name


def read_copied_json(json_dir: Path, filename: Optional[str]) -> Any:
    if not filename:
        return None
    path = json_dir / filename
    if not path.exists():
        return None
    try:
        return load_json(path)
    except Exception:
        return None


def summarize_script(script: Any) -> Dict[str, Any]:
    if not isinstance(script, list):
        return {"act_count": 0, "beat_count": 0, "acts": []}
    acts = []
    beat_count = 0
    for index, act in enumerate(script, start=1):
        if not isinstance(act, dict):
            continue
        info = act.get("scene information") or {}
        beats = act.get("scene") or []
        beat_count += len(beats) if isinstance(beats, list) else 0
        preview = []
        if isinstance(beats, list):
            for beat in beats[:6]:
                if not isinstance(beat, dict):
                    continue
                speaker = beat.get("speaker") or beat.get("character") or ""
                content = beat.get("content") or beat.get("action") or beat.get("motion_description") or ""
                preview.append({"speaker": str(speaker), "content": str(content)})
        acts.append({
            "index": index,
            "where": info.get("where", ""),
            "what": info.get("what", ""),
            "beat_count": len(beats) if isinstance(beats, list) else 0,
            "preview": preview,
        })
    return {"act_count": len(acts), "beat_count": beat_count, "acts": acts}


def run_case(case: Dict[str, Any], base_url: str, run_json_dir: Path, timeout: int) -> Dict[str, Any]:
    case_id = slug(case["id"])
    prefix = f"{case_id}"
    payload = build_payload(case)
    write_json(run_json_dir / f"{prefix}__request.json", payload)

    started = time.perf_counter()
    started_at = datetime.now().isoformat(timespec="seconds")
    events: List[Dict[str, Any]] = []
    error = None
    try:
        events = stream_generate(base_url, payload, timeout)
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        error = str(exc)
    elapsed = time.perf_counter() - started

    success_event = next((event for event in reversed(events) if event.get("type") == "success"), None)
    error_event = next((event for event in reversed(events) if event.get("type") == "error"), None)
    event_file = f"{prefix}__events.ndjson"
    with (run_json_dir / event_file).open("w", encoding="utf-8") as f:
        for event in events:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    files: Dict[str, Optional[str]] = {}
    if success_event:
        files = {
            "script": copy_backend_file(success_event.get("filename"), run_json_dir, prefix),
            "camera_script": copy_backend_file(success_event.get("camera_script_filename"), run_json_dir, prefix),
            "actors_profile": copy_backend_file(success_event.get("actors_profile_filename"), run_json_dir, prefix),
            "position_plan": copy_backend_file(success_event.get("position_plan_filename"), run_json_dir, prefix),
            "position_detail": copy_backend_file(success_event.get("position_detail_filename"), run_json_dir, prefix),
        }

    script_data = read_copied_json(run_json_dir, files.get("script"))
    result = {
        "id": case["id"],
        "name": case["name"],
        "creative_idea": case["creative_idea"],
        "status": "success" if success_event else "error",
        "started_at": started_at,
        "elapsed_seconds": round(elapsed, 2),
        "title": (success_event or {}).get("title"),
        "session_id": (success_event or {}).get("session_id"),
        "estimated_duration": (success_event or {}).get("estimated_duration"),
        "error": error or (error_event or {}).get("message"),
        "files": files,
        "event_file": event_file,
        "script_summary": summarize_script(script_data),
    }
    write_json(run_json_dir / f"{prefix}__summary.json", result)
    return result


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def json_link(filename: Optional[str], label: str) -> str:
    if not filename:
        return ""
    return f'<a href="../json/{esc(filename)}">{esc(label)}</a>'


def render_html(run_dir: Path, results: List[Dict[str, Any]]) -> None:
    html_dir = run_dir / "html"
    html_dir.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total = len(results)
    passed = sum(1 for item in results if item["status"] == "success")
    rows = []
    cards = []
    for item in results:
        status_class = "ok" if item["status"] == "success" else "bad"
        links = " ".join([
            json_link(item["files"].get("script"), "剧本JSON"),
            json_link(item["files"].get("camera_script"), "镜头JSON"),
            json_link(item["files"].get("actors_profile"), "角色档案"),
            json_link(item["event_file"], "事件流"),
        ])
        rows.append(
            "<tr>"
            f"<td>{esc(item['name'])}</td>"
            f"<td><span class=\"pill {status_class}\">{esc(item['status'])}</span></td>"
            f"<td>{esc(item['elapsed_seconds'])}s</td>"
            f"<td>{esc(item.get('title') or '')}</td>"
            f"<td>{links}</td>"
            "</tr>"
        )
        summary = item["script_summary"]
        act_blocks = []
        for act in summary.get("acts", []):
            previews = "".join(
                f"<li><b>{esc(p.get('speaker'))}</b>{': ' if p.get('speaker') else ''}{esc(p.get('content'))}</li>"
                for p in act.get("preview", [])
            )
            act_blocks.append(
                "<section class=\"act\">"
                f"<h4>第 {esc(act.get('index'))} 幕 · {esc(act.get('where'))} · {esc(act.get('beat_count'))} beats</h4>"
                f"<p>{esc(act.get('what'))}</p>"
                f"<ul>{previews}</ul>"
                "</section>"
            )
        cards.append(
            "<article class=\"case-card\">"
            f"<header><h3>{esc(item['name'])}</h3><span class=\"pill {status_class}\">{esc(item['status'])}</span></header>"
            f"<p class=\"idea\">{esc(item['creative_idea'])}</p>"
            f"<div class=\"meta\">耗时 {esc(item['elapsed_seconds'])}s · 标题 {esc(item.get('title') or '未生成')} · 幕数 {esc(summary.get('act_count'))} · beat {esc(summary.get('beat_count'))}</div>"
            f"{'<p class=\"error\">' + esc(item.get('error')) + '</p>' if item.get('error') else ''}"
            f"<nav>{links}</nav>"
            + "".join(act_blocks)
            + "</article>"
        )
    page = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ScriptsGenerateAgent Eval</title>
  <style>
    :root {{ color-scheme: light; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    body {{ margin: 0; background: #f6f7f9; color: #20242a; }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 28px; }}
    h1 {{ margin: 0 0 8px; font-size: 28px; }}
    .sub {{ color: #667085; margin-bottom: 24px; }}
    .summary {{ display: flex; gap: 12px; margin-bottom: 22px; flex-wrap: wrap; }}
    .stat {{ background: white; border: 1px solid #d9dee7; border-radius: 8px; padding: 12px 16px; min-width: 120px; }}
    .stat b {{ display: block; font-size: 22px; }}
    table {{ width: 100%; border-collapse: collapse; background: white; border: 1px solid #d9dee7; border-radius: 8px; overflow: hidden; margin-bottom: 24px; }}
    th, td {{ padding: 10px 12px; border-bottom: 1px solid #edf0f4; text-align: left; vertical-align: top; }}
    th {{ background: #eef2f7; font-size: 13px; color: #475467; }}
    a {{ color: #1d5fd1; text-decoration: none; margin-right: 10px; }}
    .case-card {{ background: white; border: 1px solid #d9dee7; border-radius: 8px; padding: 18px; margin-bottom: 18px; }}
    .case-card header {{ display: flex; justify-content: space-between; gap: 12px; align-items: center; }}
    .case-card h3 {{ margin: 0; font-size: 20px; }}
    .idea {{ color: #344054; }}
    .meta {{ color: #667085; font-size: 14px; margin: 8px 0 12px; }}
    .pill {{ display: inline-block; border-radius: 999px; padding: 3px 9px; font-size: 12px; font-weight: 700; }}
    .pill.ok {{ color: #067647; background: #dcfae6; }}
    .pill.bad {{ color: #b42318; background: #fee4e2; }}
    .act {{ border-top: 1px solid #edf0f4; margin-top: 14px; padding-top: 12px; }}
    .act h4 {{ margin: 0 0 6px; font-size: 15px; }}
    .act p {{ margin: 0 0 8px; color: #475467; }}
    .act ul {{ margin: 0; padding-left: 20px; }}
    .act li {{ margin: 4px 0; }}
    .error {{ color: #b42318; background: #fff1f0; border: 1px solid #ffccc7; padding: 10px; border-radius: 6px; }}
  </style>
</head>
<body>
  <main>
    <h1>ScriptsGenerateAgent Eval</h1>
    <div class="sub">生成时间：{esc(generated_at)} · 结果目录：{esc(str(run_dir))}</div>
    <section class="summary">
      <div class="stat"><b>{passed}/{total}</b>成功</div>
      <div class="stat"><b>{esc(round(sum(i['elapsed_seconds'] for i in results), 2))}s</b>总耗时</div>
    </section>
    <table>
      <thead><tr><th>测试题</th><th>状态</th><th>耗时</th><th>标题</th><th>文件</th></tr></thead>
      <tbody>{''.join(rows)}</tbody>
    </table>
    {''.join(cards)}
  </main>
</body>
</html>
"""
    (html_dir / "index.html").write_text(page, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run script generation eval cases.")
    parser.add_argument("--cases", default=str(DEFAULT_CASES), help="Path to eval cases JSON.")
    parser.add_argument("--base-url", default="http://127.0.0.1:5001", help="Backend base URL.")
    parser.add_argument("--case-id", action="append", help="Only run selected case id. Can be repeated.")
    parser.add_argument("--concurrency", type=int, default=1, help="Number of cases to run at the same time.")
    parser.add_argument("--timeout", type=int, default=1800, help="HTTP timeout per case in seconds.")
    args = parser.parse_args()

    cases = load_json(Path(args.cases))
    if args.case_id:
        selected = set(args.case_id)
        cases = [case for case in cases if case.get("id") in selected]
    if not cases:
        raise SystemExit("没有可运行的测试题。")

    run_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = EVAL_DIR / "res" / run_stamp
    json_dir = run_dir / "json"
    json_dir.mkdir(parents=True, exist_ok=True)

    results = []
    suite_started = time.perf_counter()
    concurrency = max(1, min(args.concurrency, len(cases)))
    if concurrency == 1:
        for index, case in enumerate(cases, start=1):
            print(f"[{index}/{len(cases)}] {case['name']} ...", flush=True)
            result = run_case(case, args.base_url, json_dir, args.timeout)
            results.append(result)
            print(f"  -> {result['status']} {result['elapsed_seconds']}s {result.get('title') or result.get('error') or ''}", flush=True)
    else:
        print(f"并发数: {concurrency}", flush=True)
        by_id = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
            future_to_case = {}
            for index, case in enumerate(cases, start=1):
                print(f"[{index}/{len(cases)}] queued {case['name']}", flush=True)
                future = executor.submit(run_case, case, args.base_url, json_dir, args.timeout)
                future_to_case[future] = case
            for future in concurrent.futures.as_completed(future_to_case):
                case = future_to_case[future]
                try:
                    result = future.result()
                except Exception as exc:
                    result = {
                        "id": case["id"],
                        "name": case["name"],
                        "creative_idea": case["creative_idea"],
                        "status": "error",
                        "started_at": datetime.now().isoformat(timespec="seconds"),
                        "elapsed_seconds": 0,
                        "title": None,
                        "session_id": None,
                        "estimated_duration": None,
                        "error": str(exc),
                        "files": {},
                        "event_file": None,
                        "script_summary": summarize_script(None),
                    }
                by_id[result["id"]] = result
                print(f"  -> {result['name']} {result['status']} {result['elapsed_seconds']}s {result.get('title') or result.get('error') or ''}", flush=True)
        results = [by_id.get(case["id"]) for case in cases if by_id.get(case["id"])]

    suite = {
        "run_id": run_stamp,
        "base_url": args.base_url,
        "started_at": run_stamp,
        "elapsed_seconds": round(time.perf_counter() - suite_started, 2),
        "results": results,
    }
    write_json(json_dir / "summary.json", suite)
    render_html(run_dir, results)
    print(f"HTML: {run_dir / 'html' / 'index.html'}")
    print(f"JSON: {json_dir}")
    return 0 if all(item["status"] == "success" for item in results) else 1


if __name__ == "__main__":
    sys.exit(main())

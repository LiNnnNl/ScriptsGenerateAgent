#!/usr/bin/env python3
"""Export and reapply shot_description/actions for every storyboard event."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

FIELDS = ("shot_description", "actions")


def read_json(path: Path):
    with path.open(encoding="utf-8") as file:
        return json.load(file)


def write_json(path: Path, data) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
        file.write("\n")
    temporary.replace(path)


def events_by_id(containers: list, collection_key: str) -> dict[str, dict]:
    if not isinstance(containers, list):
        raise ValueError(f"剧本顶层必须包含数组：{collection_key}")
    events = {}
    for container in containers:
        for event in container.get(collection_key, []):
            shot_id = event.get("shot_id")
            if not isinstance(shot_id, str) or not shot_id:
                raise ValueError("发现缺少有效 shot_id 的镜头，无法安全匹配")
            if shot_id in events:
                raise ValueError(f"shot_id 重复：{shot_id}")
            events[shot_id] = event
    return events


def export_fields(source: Path, output: Path) -> int:
    script = read_json(source)
    playback = events_by_id(script.get("playback_episode"), "scene")
    storyboard = events_by_id(script.get("scenes"), "events")
    if playback.keys() != storyboard.keys():
        raise ValueError("playback_episode 与 scenes 的镜头 ID 不一致")
    records = []
    for scene_index, episode in enumerate(script["playback_episode"]):
        for event_index, event in enumerate(episode.get("scene", [])):
            records.append({
                "scene_index": scene_index,
                "event_index": event_index,
                "shot_id": event["shot_id"],
                "shot_description": event.get("shot_description"),
                "actions": event.get("actions"),
                "original_fields_present": {key: key in event for key in FIELDS},
            })
    write_json(output, {"format_version": 1, "shots": records})
    return len(records)


def apply_fields(source: Path, fields_path: Path) -> int:
    script = read_json(source)
    playback = events_by_id(script.get("playback_episode"), "scene")
    storyboard = events_by_id(script.get("scenes"), "events")
    export = read_json(fields_path)
    if export.get("format_version") != 1 or not isinstance(export.get("shots"), list):
        raise ValueError("字段文件格式不受支持")

    records = {}
    for record in export["shots"]:
        shot_id = record.get("shot_id")
        if not isinstance(shot_id, str) or shot_id in records:
            raise ValueError(f"字段文件中的 shot_id 无效或重复：{shot_id}")
        records[shot_id] = record
    if records.keys() != playback.keys() or playback.keys() != storyboard.keys():
        raise ValueError("字段文件、playback_episode 与 scenes 的镜头 ID 集合不一致")

    for shot_id, record in records.items():
        for key in FIELDS:
            value = record.get(key)
            if key == "shot_description" and value is not None and not isinstance(value, str):
                raise ValueError(f"{shot_id}.{key} 必须是字符串或 null")
            if key == "actions" and value is not None and not isinstance(value, list):
                raise ValueError(f"{shot_id}.{key} 必须是数组或 null")
    for shot_id, record in records.items():
        playback[shot_id]["shot_description"] = record["shot_description"]
        playback[shot_id]["actions"] = record["actions"]
        storyboard[shot_id]["shot_description"] = record["shot_description"]

    write_json(source, script)
    return len(records)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    extract = commands.add_parser("extract", help="导出可编辑的镜头字段 JSON")
    extract.add_argument("source", type=Path)
    extract.add_argument("--output", type=Path)
    apply = commands.add_parser("apply", help="将镜头字段写回原剧本 JSON")
    apply.add_argument("source", type=Path)
    apply.add_argument("fields", type=Path)
    args = parser.parse_args()

    if args.command == "extract":
        output = args.output or args.source.with_name(args.source.stem + ".shot_fields.json")
        count = export_fields(args.source, output)
        print(f"已导出 {count} 个镜头：{output}")
    else:
        count = apply_fields(args.source, args.fields)
        print(f"已回填 {count} 个镜头的 shot_description/actions：{args.source}")


if __name__ == "__main__":
    main()

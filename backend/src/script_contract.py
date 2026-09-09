"""Final screenplay contract. See docs/script_contract.md for precedence and exceptions."""
from __future__ import annotations

import copy
import json
import math
import re
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from .resource_loader import POSTURE_TRANSITION_TARGETS

Text = Annotated[str, Field(min_length=1, pattern=r"\S")]
TrackID = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")]
PositionID = Annotated[str, Field(pattern=r"^Position [1-9][0-9]*$")]
State = Literal["standing", "sitting", "kneeling", "squatting"]
Probability = Annotated[float, Field(ge=0, le=1)]
MANDARIN = {"version": 1, "default_track": "zh_hans_cmn", "tracks": [{
    "id": "zh_hans_cmn", "label": "中文-普通话", "content_language": "zh-Hans",
    "speech_language": "cmn-Hans-CN", "tts_prompt_suffix": "普通话。",
}]}
CAMERA_FIELDS = {"shot", "shot_type", "shot_blend", "Follow", "follow", "camera", "target",
                 "target_position", "target_anchor", "motion_enabled", "motion_preset",
                 "play_motion_on_activate", "motion_start_delay", "motion_reset_on_replay", "motion_sequence"}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Track(StrictModel):
    id: TrackID
    label: Text
    content_language: Text
    speech_language: Text
    tts_prompt_suffix: Annotated[str, Field(min_length=1, max_length=80)]


class LanguageConfig(StrictModel):
    version: Literal[1]
    default_track: Text
    tracks: Annotated[list[Track], Field(min_length=1, max_length=8)]


class SceneInfo(StrictModel):
    who: list[Text]
    where: str
    what: Text
    emotionLibrary: str
    language_config: LanguageConfig | None = None


class Position(StrictModel):
    character: Text
    position: PositionID


class InitialPosition(Position):
    state: State


class Interaction(StrictModel):
    # Parameter names are defined by the selected object's action, checked below.
    model_config = ConfigDict(extra="allow", strict=True, allow_inf_nan=False)
    target: str
    action: str
    delay: Annotated[float, Field(ge=0)] = 0.0


Interactions = Interaction | Annotated[list[Interaction], Field(min_length=1)]


class Action(StrictModel):
    character: Text
    state: State
    action: str
    motion_detail: str = ""
    then_interact: Interactions | None = Field(default=None, alias="then-interact")


class Move(StrictModel):
    character: Text
    destination: PositionID
    then_interact: Interactions | None = Field(default=None, alias="then-interact")


class Emotion(StrictModel):
    character: Text
    emotion: str
    emotionStyle: str


class Event(StrictModel):
    event_index: Annotated[int, Field(ge=0)]
    current_position: list[Position] = Field(alias="current position")
    emotion: str | Annotated[list[Emotion], Field(min_length=1)]
    confidence: Probability
    reason: Text
    shot_description: str
    speaker: str | None = None
    content: str | None = None
    actions: list[Action] | None = None
    move: Annotated[list[Move], Field(min_length=1)] | None = None
    duration: Annotated[float, Field(gt=0)] | None = None
    language_track: Text | None = None
    content_variants: dict[str, Text] | None = None
    then_interact: Interactions | None = Field(default=None, alias="then-interact")


class Act(StrictModel):
    scene_information: SceneInfo = Field(alias="scene information")
    initial_position: list[InitialPosition] = Field(alias="initial position")
    scene: Annotated[list[Event], Field(min_length=1)]


SCRIPT_ADAPTER = TypeAdapter(Annotated[list[Act], Field(min_length=1)])


def issue(path, code, message, candidates=None):
    result = {"path": path, "code": code, "message": message}
    if candidates is not None:
        result["candidates"] = sorted(candidates)
    return result


def read_catalogs(resource_loader):
    """Only repository-local catalogs; Unity paths never become runtime dependencies."""
    root = resource_loader.resource_dir
    emotions_path = root / "emotion_libraries.json"
    emotions = json.loads(emotions_path.read_text(encoding="utf-8-sig")) if emotions_path.exists() else {}
    interactions = {}
    for path in sorted((root / "interactions").glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        interactions[data["scene"]] = {obj["id"]: obj for obj in data["objects"]}
    camera_targets = {}
    for path in sorted((root / 'camera_targets').glob('*.json')):
        data = json.loads(path.read_text(encoding='utf-8-sig'))
        camera_targets[data['scene']] = {obj['id']: obj for obj in data['objects']}
    return {"emotions": emotions.get("libraries", {}), "interactions": interactions, 'camera_targets': camera_targets}


def normalize_script(script, resource_loader=None):
    """Fill derivable metadata only. Invalid populated values remain errors, except empty catalogs."""
    result = copy.deepcopy(script)
    warnings = []
    catalogs = read_catalogs(resource_loader) if resource_loader else None
    if not isinstance(result, list):
        return result, warnings
    for si, act in enumerate(result):
        if not isinstance(act, dict):
            continue
        info = act.get("scene information")
        if not isinstance(info, dict):
            continue
        info.setdefault("emotionLibrary", "")
        config = info.get("language_config")
        positions, states = {}, {}
        for pos in act.get("initial position", []) if isinstance(act.get("initial position"), list) else []:
            if isinstance(pos, dict) and isinstance(pos.get("character"), str):
                pos.setdefault("state", "standing")
                positions[pos["character"]] = pos.get("position")
                states[pos["character"]] = pos.get("state")
        for ei, event in enumerate(act.get("scene", []) if isinstance(act.get("scene"), list) else []):
            if not isinstance(event, dict):
                continue
            event.setdefault("event_index", ei)
            event.setdefault("current position", [{"character": c, "position": p} for c, p in positions.items()])
            event.setdefault("confidence", 0.0)
            event.setdefault("reason", "未提供情绪判断" if event.get("speaker") else "空台词")
            event.setdefault("emotion", "normal")
            event.setdefault("shot_description", "")
            if "move" in event and isinstance(event["move"], dict):
                event["move"] = [event["move"]]
            if "move" not in event:
                event.setdefault("actions", [])
                if event.get("speaker") == "":
                    event.setdefault("content", "无台词")
                    event.setdefault("duration", 5.0)
            if event.get("speaker") and isinstance(config, dict):
                tracks = config.get("tracks")
                if isinstance(tracks, list) and len(tracks) == 1 and isinstance(tracks[0], dict):
                    track_id = tracks[0].get("id")
                    if isinstance(track_id, str) and track_id:
                        event.setdefault("language_track", track_id)
                        if isinstance(event.get("content"), str):
                            event.setdefault("content_variants", {track_id: event["content"]})
            if isinstance(event.get("duration"), str):
                match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(s|秒)?", event["duration"].strip())
                if match:
                    event["duration"] = float(match[1])
            for action in event.get("actions", []) if isinstance(event.get("actions"), list) else []:
                if isinstance(action, dict):
                    character = action.get("character")
                    action.setdefault("state", states.get(character, "standing") if isinstance(character, str) else "standing")
                    if isinstance(character, str) and isinstance(action.get("action"), str) and action["action"] in POSTURE_TRANSITION_TARGETS:
                        states[character] = POSTURE_TRANSITION_TARGETS[action["action"]]
            for move in event.get("move", []) if isinstance(event.get("move"), list) else []:
                if isinstance(move, dict) and isinstance(move.get("character"), str):
                    positions[move["character"]] = move.get("destination")
            # Camera-only fields are intermediates, never part of script.json.
            for field in CAMERA_FIELDS | {"current_position", "intensity"}:
                event.pop(field, None)
        for key in ("position_descriptions", "position_plan", "position_detail", "title", "_direct_position_repair_applied"):
            act.pop(key, None)
        if catalogs is not None:
            library_name = info.get("emotionLibrary")
            library = catalogs["emotions"].get(library_name, {}) if isinstance(library_name, str) else {}
            if not catalogs["emotions"] and isinstance(info.get("emotionLibrary"), str):
                info["emotionLibrary"] = ""
                warnings.append(issue(f"$[{si}].scene information.emotionLibrary", "RESOURCE_LIBRARY_EMPTY", "表情库目录为空，名称已清空"))
            for ei, event in enumerate(act.get("scene", []) if isinstance(act.get("scene"), list) else []):
                if not isinstance(event, dict):
                    continue
                emotion = event.get("emotion")
                if not library_name or not catalogs["emotions"] or (library and not library.get("emotions")):
                    if isinstance(emotion, str):
                        event["emotion"] = ""
                    elif isinstance(emotion, list):
                        for item in emotion:
                            if isinstance(item, dict) and isinstance(item.get("emotion"), str):
                                item["emotion"] = ""
                    if library_name:
                        warnings.append(issue(f"$[{si}].scene[{ei}].emotion", "RESOURCE_LIBRARY_EMPTY", "情绪名称库为空，名称已清空"))
                if isinstance(emotion, list) and (not library_name or not catalogs["emotions"] or (library and not library.get("styles"))):
                    for item in emotion:
                        if isinstance(item, dict) and isinstance(item.get("emotionStyle"), str):
                            item["emotionStyle"] = ""
                    if library_name:
                        warnings.append(issue(f"$[{si}].scene[{ei}].emotion", "RESOURCE_LIBRARY_EMPTY", "情绪切换方式库为空，名称已清空"))
                if not resource_loader.actions:
                    for action in event.get("actions", []) or []:
                        if isinstance(action, dict) and isinstance(action.get("action"), str):
                            action["action"] = ""
                            warnings.append(issue(f"$[{si}].scene[{ei}].actions", "RESOURCE_LIBRARY_EMPTY", "动作库为空"))
                objects = catalogs["interactions"].get(info.get("where"), {}) if isinstance(info.get("where"), str) else {}
                for owner, label in interaction_owners(event):
                    for entry in interaction_list(owner.get("then-interact")):
                        if isinstance(entry, dict) and not objects:
                            for key in ('target', 'action'):
                                if isinstance(entry.get(key), str):
                                    entry[key] = ""
                            warnings.append(issue(f"$[{si}].scene[{ei}].{label}", "RESOURCE_LIBRARY_EMPTY", "场景交互库为空，目标和动作已清空"))
    return result, warnings


def interaction_owners(event):
    yield event, "then-interact"
    for field in ("actions", "move"):
        for i, item in enumerate(event.get(field, []) if isinstance(event.get(field), list) else []):
            if isinstance(item, dict):
                yield item, f"{field}[{i}].then-interact"


def interaction_list(value):
    return value if isinstance(value, list) else [value] if isinstance(value, dict) else []


def normalize_camera_resources(camera, script, loader):
    result = copy.deepcopy(camera)
    catalogs = read_catalogs(loader)['camera_targets']
    for act, cs in zip(script, result.get('scenes', [])):
        targets = catalogs.get(act['scene information']['where'], {})
        if not targets:
            for event in cs['events']:
                if event['shot'] == 'object':
                    event['target'] = ''
                    if 'target_anchor' in event:
                        event['target_anchor'] = ''
    return result


def validate_script(script, resource_loader=None, *, final=True, act_count=None, required_names=(), character_count=None):
    errors, warnings = [], []
    try:
        SCRIPT_ADAPTER.validate_python(script)
    except ValidationError as exc:
        for err in exc.errors(include_input=False, include_url=False):
            errors.append(issue("$." + ".".join(map(str, err["loc"])), "SCHEMA", err["msg"]))
        return {"valid": False, "errors": errors, "warnings": warnings}
    # Optional means absent, not explicit null. Guard before semantic traversal.
    for si, act in enumerate(script):
        for ei, event in enumerate(act['scene']):
            for key, value in event.items():
                if value is None:
                    errors.append(issue(f'$[{si}].scene[{ei}].{key}', 'NULL_FIELD', '字段存在时不得为 null'))
            for owner, label in interaction_owners(event):
                if 'then-interact' in owner and owner['then-interact'] is None:
                    errors.append(issue(f'$[{si}].scene[{ei}].{label}', 'NULL_FIELD', '可选交互不能为 null'))
    if errors:
        return {'valid': False, 'errors': errors, 'warnings': warnings}
    catalogs = read_catalogs(resource_loader) if resource_loader else {"emotions": {}, "interactions": {}}

    def require(ok, path, code, message, candidates=None):
        if not ok:
            errors.append(issue(path, code, message, candidates))

    def selection(value, candidates, path):
        if candidates:
            require(value in candidates, path, "RESOURCE_VALUE", "必须从合法候选中选择", candidates)
        elif value == "":
            warnings.append(issue(path, "RESOURCE_LIBRARY_EMPTY", "取值库为空，保留空字符串"))
        else:
            require(False, path, "UNVERIFIED_VALUE", "取值库为空，未经验证名称必须清空")

    names = set()
    require(act_count is None or len(script) == act_count, "$", "ACT_COUNT", f"幕数必须为 {act_count}")
    language_configs = [act["scene information"].get("language_config") for act in script]
    enabled_configs = [config for config in language_configs if config is not None]
    require(not enabled_configs or len(enabled_configs) == len(script), "$", "LANGUAGE_CONFIG",
            "启用多语言后所有幕都必须声明相同的 language_config")
    if enabled_configs:
        canonical = json.dumps(enabled_configs[0], sort_keys=True, ensure_ascii=False)
        require(all(json.dumps(config, sort_keys=True, ensure_ascii=False) == canonical for config in enabled_configs),
                "$", "LANGUAGE_CONFIG", "所有幕的 language_config 必须完全一致")
    for si, act in enumerate(script):
        prefix = f"$[{si}]"
        info = act["scene information"]
        who = set(info["who"])
        names.update(who)
        require(len(who) == len(info["who"]), prefix, "DUPLICATE_CHARACTER", "who 不得重复")
        if resource_loader:
            selection(info["where"], {s.id for s in resource_loader.scenes}, prefix + ".scene information.where")
        library_name = info["emotionLibrary"]
        if library_name:
            selection(library_name, catalogs["emotions"], prefix + ".scene information.emotionLibrary")
        library = catalogs["emotions"].get(library_name, {})
        supported_characters = set(library.get("target_characters", []))
        if supported_characters:
            require(who <= supported_characters, prefix + ".scene information.emotionLibrary",
                    "EMOTION_CHARACTER", "表情库不支持本幕全部角色", supported_characters)
        config = info.get("language_config")
        ids = set()
        if "language_config" in info and config is None:
            require(False, prefix + ".scene information.language_config", "NULL_FIELD", "可选字段存在时不得为 null")
        if config:
            tracks = config["tracks"]
            ids = {track["id"] for track in tracks}
            require(len(ids) == len(tracks), prefix, "LANGUAGE_TRACK", "语言轨道 id 不得重复")
            require(config["default_track"] in ids, prefix, "LANGUAGE_TRACK", "默认轨道不存在", ids)
            for track in tracks:
                for field in ("content_language", "speech_language"):
                    require(bool(re.fullmatch(r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*", track[field])), prefix, "LANGUAGE_TAG", f"{field} 必须为语言标签")
                suffix = track["tts_prompt_suffix"]
                require(not re.search(r"[\r\n{}\[\]]|https?://|www\.|api[ _-]?key|密钥|system\s*prompt|系统提示|你是", suffix, re.I),
                        prefix + ".scene information.language_config", "TTS_SUFFIX", "tts_prompt_suffix 只能包含简短语言/发音要求")

        def positions(entries, path):
            result = {p["character"]: p["position"] for p in entries}
            require(len(result) == len(entries) and set(result) == who, path, "CHARACTER_COVERAGE", "必须恰好覆盖 who 中全部角色", who)
            require(len(set(result.values())) == len(result), path, "POSITION_OCCUPIED", "不同角色不能共用位置")
            return result

        current = positions(act["initial position"], prefix + ".initial position")
        states = {p["character"]: p["state"] for p in act["initial position"]}
        for ei, event in enumerate(act["scene"]):
            path = f"{prefix}.scene[{ei}]"
            for key, value in event.items():
                require(value is not None, path + "." + key, "NULL_FIELD", "字段存在时不得为 null")
            require(event["event_index"] == ei, path, "EVENT_INDEX", "event_index 必须从 0 连续递增")
            snapshot = positions(event["current position"], path + ".current position")
            require(snapshot == current, path + ".current position", "POSITION_CONTINUITY", "必须等于初始位置或上个事件移动后的快照")
            if final:
                require(bool(event["shot_description"].strip()), path, "EMPTY_DESCRIPTION", "最终 shot_description 不得为空")
            moving = "move" in event
            speaking = bool(event.get("speaker"))
            if speaking:
                require(event["speaker"] in who, path + ".speaker", "CHARACTER_REFERENCE", "说话人必须在 who 中", who)
                require(isinstance(event.get("content"), str) and bool(event["content"].strip()), path + ".content", "REQUIRED", "说话事件必须有非空台词")
            elif not moving:
                require(event.get("speaker") == "" and isinstance(event.get("content"), str), path, "SILENT_EVENT", "无说话人事件必须有空 speaker 和字符串 content")
                require("duration" in event and event.get("actions") == [], path, "SILENT_EVENT", "无说话人事件必须有正数 duration 和 actions=[]")
            if not moving:
                require("actions" in event, path, "REQUIRED", "非移动事件必须有 actions")
            elif not speaking:
                require("speaker" not in event and "content" not in event, path, "MOVE_FIELDS", "纯移动不含 speaker/content")
            if moving:
                require("actions" not in event, path, "MOVE_FIELDS", "移动事件不含 actions")
            has_language_fields = "language_track" in event or "content_variants" in event
            if config and speaking:
                require("language_track" in event and "content_variants" in event, path,
                        "LANGUAGE_REQUIRED", "启用多语言配置后每句对白必须包含轨道和全部译本")
            if has_language_fields:
                require(speaking and bool(config), path, "LANGUAGE_EVENT", "语言字段只用于启用语言配置的说话事件")
                track = event.get("language_track")
                require(track in ids, path, "LANGUAGE_TRACK", "事件轨道不存在", ids)
                variants = event.get("content_variants", {})
                require(set(variants) == ids, path, "LANGUAGE_VARIANTS", "译本必须恰好覆盖全部已发布轨道", ids)
                require(track in variants and event.get("content") == variants.get(track), path, "LANGUAGE_CONTENT", "content 必须等于当前轨道译本")
            emotions = event["emotion"]
            assignments = emotions if isinstance(emotions, list) else [{"emotion": emotions}]
            assigned = set()
            for assignment in assignments:
                if "character" in assignment:
                    c = assignment["character"]
                    require(c in who and c not in assigned, path + ".emotion", "CHARACTER_REFERENCE", "情绪角色必须在场且不得重复", who)
                    assigned.add(c)
                    if library_name:
                        selection(assignment["emotionStyle"], library.get("styles", []), path + ".emotionStyle")
                    else:
                        require(assignment["emotionStyle"] == "", path + ".emotionStyle", "EMOTION_DISABLED", "未选择表情库时必须为空")
                expression = assignment["emotion"]
                if ":" not in expression:
                    if library_name:
                        selection(expression, library.get("emotions", []), path + ".emotion")
                    else:
                        require(expression == "", path + ".emotion", "EMOTION_DISABLED", "未选择表情库时必须为空")
                else:
                    require(bool(library_name), path + ".emotion", "EMOTION_DISABLED", "复合情绪必须先选择表情库")
                    parts = expression.split(",")
                    weights, emotion_names = [], set()
                    for part in parts:
                        match = re.fullmatch(r"\s*([^:,\s]+)\s*:\s*(\d+(?:\.\d+)?)\s*", part)
                        require(bool(match), path + ".emotion", "EMOTION_WEIGHT", "格式为 emotion:weight，用逗号分隔")
                        if match:
                            name, weight = match[1], float(match[2])
                            selection(name, library.get("emotions", []), path + ".emotion")
                            require(name not in emotion_names and 0 <= weight <= 1, path, "EMOTION_WEIGHT", "情绪不重复，权重在 [0,1]")
                            emotion_names.add(name)
                            weights.append(weight)
                    require(math.isclose(sum(weights), 1, abs_tol=1e-6), path, "EMOTION_WEIGHT", "权重总和必须为 1")
            for ai, action in enumerate(event.get("actions", [])):
                c = action["character"]
                ap = f"{path}.actions[{ai}]"
                require(c in who, ap, "CHARACTER_REFERENCE", "动作角色不在场", who)
                require(action["state"] == states.get(c), ap, "POSTURE_CONTINUITY", "state 必须为执行动作前的姿态")
                if resource_loader:
                    candidates = {a.action_id for a in resource_loader.actions if a.is_compatible_with_state(action["state"])}
                    if resource_loader.actions and not candidates:
                        require(False, ap + '.action', 'NO_COMPATIBLE_ACTION', '动作库非空，但当前姿态没有可用动作', [])
                    else:
                        selection(action["action"], candidates, ap + ".action")
                if action["action"] in POSTURE_TRANSITION_TARGETS:
                    states[c] = POSTURE_TRANSITION_TARGETS[action["action"]]
            movers = set()
            for move in event.get("move", []):
                c = move["character"]
                require(c in who and c not in movers, path + ".move", "CHARACTER_REFERENCE", "移动角色必须在场且不重复", who)
                require(states.get(c) == "standing", path + ".move", "POSTURE_MOVE", "移动前必须先站起")
                movers.add(c)
                current[c] = move["destination"]
            require(len(set(current.values())) == len(current), path + ".move", "POSITION_OCCUPIED", "移动后不得重占位置")
            objects = catalogs["interactions"].get(info["where"], {})
            for owner, label in interaction_owners(event):
                for entry in interaction_list(owner.get("then-interact")):
                    ip = path + "." + label
                    selection(entry["target"], objects, ip + ".target")
                    actions = {a["name"]: a for a in objects.get(entry["target"], {}).get("actions", [])}
                    selection(entry["action"], actions, ip + ".action")
                    if entry["action"] not in actions:
                        continue
                    parameters = actions[entry["action"]].get("parameters", {})
                    require(set(entry) <= {"target", "action", "delay"} | set(parameters), ip, "INTERACTION_PARAMETER", "出现未定义的交互参数")
                    types = {"number": (int, float), "integer": (int,), "boolean": (bool,), "string": (str,)}
                    for key, spec in parameters.items():
                        require(not spec.get("required") or key in entry, ip + "." + key, "REQUIRED", "缺少交互参数")
                        if key not in entry:
                            continue
                        value = entry[key]
                        ok = type(value) in types.get(spec.get("type"), ())
                        require(ok, ip + "." + key, "PARAMETER_TYPE", "交互参数类型错误")
                        if ok and type(value) in (int, float):
                            require(math.isfinite(value) and spec.get("min", -math.inf) <= value <= spec.get("max", math.inf), ip, "PARAMETER_RANGE", "交互参数超出范围")
                        if "enum" in spec:
                            require(value in spec["enum"], ip, "PARAMETER_ENUM", "交互参数不在枚举中", spec["enum"])
    require(set(required_names) <= names, "$", "REQUIRED_CHARACTERS", "缺少指定角色", required_names)
    require(not character_count or len(names) == character_count, "$", "CHARACTER_COUNT", f"角色总数必须为 {character_count}")
    return {"valid": not errors, "errors": errors, "warnings": warnings}


def validate_bundle(script, camera, actors, loader, plans=None, details=None):
    """Cross-file checks against the exact final script, not an earlier draft."""
    from .schema import validate_camera_script
    errors, warnings = [], []
    camera_result = validate_camera_script(camera)
    if not camera_result['valid']:
        return {'valid': False, 'errors': [issue('camera_script', 'CAMERA_SCHEMA', str(e)) for e in camera_result['errors']], 'warnings': []}
    def check(ok, path, message):
        if not ok:
            errors.append(issue(path, 'BUNDLE_REFERENCE', message))
    camera_scenes = camera['scenes']
    check(len(camera_scenes) == len(script), 'camera_script.scenes', '幕数必须与剧本一致')
    for si, (act, cs) in enumerate(zip(script, camera_scenes)):
        check(cs['shot_index'] == si, f'camera_script.scenes[{si}]', 'shot_index 必须匹配幕索引')
        check(len(cs['events']) == len(act['scene']), f'camera_script.scenes[{si}]', '镜头事件数量必须匹配剧本')
        for ei, (event, ce) in enumerate(zip(act['scene'], cs['events'])):
            path = f'camera_script.scenes[{si}].events[{ei}]'
            check(ce['event_index'] == ei, path, 'event_index 不匹配')
            pos = {p['character']: p['position'] for p in event['current position']}
            if ce['shot'] != 'object':
                check(ce['target'] in pos and pos.get(ce['target']) == ce['target_position'], path, '镜头目标与事件开始位置不匹配')
            else:
                targets = read_catalogs(loader)['camera_targets'].get(act['scene information']['where'], {})
                if targets:
                    check(ce['target'] in targets, path, '物体镜头目标必须来自当前场景目标库')
                    if ce.get('target_anchor'):
                        check(ce['target_anchor'] in targets.get(ce['target'], {}).get('anchors', []), path, '物体镜头锚点必须属于选定目标')
                else:
                    check(ce['target'] == '' and not ce.get('target_anchor'), path, '空库时物体目标与锚点必须清空')
                    warnings.append(issue(path + '.target', 'RESOURCE_LIBRARY_EMPTY', '物体镜头目标库为空'))
            check(ce['shot_type'] in loader.camera_list, path, '景别不在 CameraLib 中')
            if ce['motion_enabled']:
                cam_def = loader.camera_list.get(ce['shot_type'], {})
                check(ce['motion_preset'] == cam_def.get('DefaultMotionPreset'), path, '运镜预设必须为当前景别已注册的默认预设')
            check(ce['shot_description'] == event['shot_description'], path, '镜头描述与主剧本不一致')
    expected = {name for act in script for name in act['scene information']['who']}
    if not isinstance(actors, list) or any(not isinstance(a, dict) for a in actors):
        check(False, 'actors_profile', '必须为对象数组')
    else:
        check(len(actors) == len(expected) and {a.get('name') for a in actors} == expected, 'actors_profile', '演员档案必须恰好覆盖剧本角色')
        required = {'name', 'age', 'gender', 'gameobject_name', 'appearance', 'acting_style', 'traits', 'background'}
        models = {c.gameobject_name for c in loader.characters if c.gameobject_name}
        for i, actor in enumerate(actors):
            check(required <= actor.keys(), f'actors_profile[{i}]', '演员字段不完整')
            check(actor.get('gameobject_name') in models, f'actors_profile[{i}].gameobject_name', '模型映射必须来自角色资源库')
            check(actor.get('age') is None or type(actor.get('age')) is int and actor['age'] >= 0, f'actors_profile[{i}].age', '年龄必须为非负整数或 null')
            for field in ('name', 'gender', 'gameobject_name', 'acting_style', 'background'):
                check(isinstance(actor.get(field), str), f'actors_profile[{i}].{field}', '必须为字符串')
            appearance = actor.get('appearance')
            check(isinstance(appearance, dict) and all(isinstance(appearance.get(k), str) for k in ('height', 'body_type', 'hair', 'face')),
                  f'actors_profile[{i}].appearance', 'appearance 必须有四个字符串字段')
            check(isinstance(actor.get('traits'), list) and all(isinstance(t, str) for t in actor['traits']), f'actors_profile[{i}].traits', 'traits 必须为字符串数组')
    for label, document in (('position_plan', plans), ('position_detail', details)):
        if document is None:
            continue
        scenes = document.get('scenes', [document]) if isinstance(document, dict) else []
        check(len(scenes) == len(script), label, '位置文件必须覆盖全部幕')
        for si, (act, data) in enumerate(zip(script, scenes)):
            path = f'{label}[{si}]'
            check(data.get('where') == act['scene information']['where'], path, '位置文件场景不匹配')
            required_positions = {(p['character'], p['position']) for p in act['initial position']}
            required_positions.update((m['character'], m['destination']) for e in act['scene'] for m in e.get('move', []))
            actual = []
            for group in data.get('groups', []):
                if label == 'position_plan':
                    actual.extend((p.get('character'), p.get('position_id')) for p in group.get('positions', []))
                else:
                    actual.append((group.get('character'), group.get('position_id')))
            actual.extend((p.get('character'), p.get('position_id')) for p in data.get('singles', []))
            check(required_positions <= set(actual), path, '位置文件遗漏初始位置或移动目的地')
            check(len(actual) == len(set(actual)), path, '位置规划条目重复')
            scene_info = loader.load_scene_info(act['scene information']['where']) or {}
            regions = {r['name']: r for r in scene_info.get('regions', [])}
            layout_path = loader.resource_dir / 'cinematography' / 'LayoutLib.json'
            layouts = {item['layout']: item for item in json.loads(layout_path.read_text(encoding='utf-8-sig'))['layout_library']}
            for item in data.get('groups', []) + data.get('singles', []):
                check(item.get('region') in regions, path, 'region 必须来自本幕场景锚点库')
                if 'layout' in item:
                    check(item['layout'] in layouts, path, 'layout 必须来自 LayoutLib')
                    if 'positions' in item and item['layout'] in layouts:
                        spec = layouts[item['layout']]
                        check(spec['min_people'] <= len(item['positions']) <= spec['max_people'], path, '分组人数超出布局范围')
                if item.get('neartarget'):
                    region = regions.get(item.get('region'), {})
                    anchors = {a['name'] for a in region.get('anchors', []) + region.get('scene_markers', [])}
                    check(item['neartarget'] in anchors, path, 'neartarget 必须在所属区域锚点中')
                lookat = item.get('lookat')
                if label == 'position_plan' and 'positions' in item:
                    check(isinstance(lookat, dict) and lookat.get('mode') in ('center', 'target'), path, '分组 lookat.mode 必须为 center/target')
                    if isinstance(lookat, dict) and lookat.get('mode') == 'target':
                        check(lookat.get('target_character') in {p.get('character') for p in item['positions']}, path, 'lookat.target_character 必须属于该组')
                else:
                    allowed = {'center'} | {p[1] for p in actual} | {a['name'] for region in regions.values() for a in region.get('anchors', []) + region.get('scene_markers', [])}
                    check(isinstance(lookat, str) and lookat in allowed, path, 'lookat 必须为 center、Position 或本场景锚点')
    return {'valid': not errors, 'errors': errors, 'warnings': warnings}

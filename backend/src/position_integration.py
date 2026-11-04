"""Native position-agent.2 integration; published releases remain immutable.

The old screenplay is an editorial input only. Native blocking states and
placements are the sole spatial authority, including for camera targeting.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time
import zipfile

CONFIG_PATH = Path(__file__).resolve().parents[1] / 'position_agent.json'
FILES = ('script.json', 'blocking_plan.json', 'spatial_solution.json', 'attention_plan.json')


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, path)


def settings():
    path = Path(os.getenv('POSITION_AGENT_CONFIG', str(CONFIG_PATH)))
    return read(path) if path.is_file() else {'enabled': False}


def enabled():
    return settings().get('enabled') is True


def configure_models():
    """Use the uploaded credential only for its intended provider."""
    config = settings()
    if not config.get('enabled'):
        return
    from dotenv import dotenv_values
    values = dotenv_values(Path(config['home']) / config['env_file'])
    key = values.get('GLM_API_KEY')
    if not key:
        raise ValueError('新版模型配置缺少 GLM_API_KEY。')
    os.environ.update(API_KEY=key, BASE_URL='https://open.bigmodel.cn/api/paas/v4',
        MODEL=config['model'], SIMPLE_MODEL=config['model'], FALLBACK_MODEL=config['model'],
        CINEMATOGRAPHY_MODEL=config['model'], MODEL_MAX_TOKENS='16384')


def scene_directory(scene_id):
    config = settings()
    relative = config.get('scenes', {}).get(scene_id)
    if not relative:
        raise ValueError(f'场景 {scene_id} 尚未提供新版场景包，无法进行站位规划。')
    home = Path(config['home']).resolve()
    path = (home / relative).resolve()
    path.relative_to(home)
    if len(list(path.glob('*_scene_grid.json'))) != 1:
        raise ValueError(f'场景 {scene_id} 的网格文件缺失或不唯一。')
    return path


def check_request(params):
    if not enabled():
        return
    ids = params.get('scene_pool') or [params.get('scene_id')]
    if not isinstance(ids, list) or not ids or not all(isinstance(x, str) for x in ids):
        raise ValueError('请选择已提供新版场景包的场景。')
    for scene_id in ids:
        scene_directory(scene_id)


def exported_scene_info(scene_id):
    """Read-only camera view of the same Unity export used by the planner."""
    directory = scene_directory(scene_id)
    grid = read(next(directory.glob('*_scene_grid.json')))
    objects = grid['objects']
    targets_path = directory / f'{scene_id}_camera_targets.json'
    targets = read(targets_path).get('targets', []) if targets_path.is_file() else []
    markers = []
    seen = set()
    for target in targets:
        name = target.get('lookup_target') or target.get('target_id') or target['name']
        if name in seen:
            raise ValueError(f'新版镜头目标名称重复：{name}')
        seen.add(name)
        xyz = target.get('default_look_target') or target['position']
        markers.append({'name': name, 'position': dict(zip(('x', 'y', 'z'), xyz))})
    regions = []
    for region in grid['regions']:
        anchors = []
        for obj in objects:
            if obj.get('region') not in (region['id'], region['name']):
                continue
            position = obj.get('position') or (obj.get('bounds') or {}).get('center')
            if position:
                anchors.append({'name': obj['name'], 'position': position})
        regions.append({'name': region['name'], 'description': region.get('description', region['name']),
                        'anchors': anchors, 'scene_markers': [], 'spatial_relations': []})
    # Camera targets are global objects, not invented placement anchors.
    if markers:
        regions.append({'name': '镜头目标', 'description': 'Unity 导出的物体镜头目标',
                        'anchors': [], 'scene_markers': markers, 'spatial_relations': []})
    return {'where': scene_id, 'regions': regions}


def authored_contexts(text, act_count):
    """Keep original scene instructions, including opening and global constraints."""
    if act_count == 1:
        return {0: text}
    try:
        value = json.loads(text)
        if isinstance(value, list) and len(value) == act_count:
            return {i: json.dumps(scene, ensure_ascii=False) for i, scene in enumerate(value)}
    except (ValueError, TypeError):
        pass
    markers = list(re.finditer(r'(?m)^\s*(?:第\s*[0-9一二三四五六七八九十百]+\s*幕|Act\s*\d+|Scene\s*\d+)[^\n]*', text, re.I))
    if len(markers) != act_count:
        raise ValueError('新版多幕直接模式需要明确的“第1幕、第2幕…”分隔，以保留每幕原始空间约束。')
    prefix = text[:markers[0].start()]
    return {i: prefix + text[m.start():markers[i + 1].start() if i + 1 < len(markers) else len(text)] for i, m in enumerate(markers)}


def narrative_input(scene, script_id, authored_context=''):
    """Keep all dialogue; make legacy execution-only moves explicit narrative."""
    from .script_contract import CAMERA_FIELDS
    result = copy.deepcopy(scene)
    result['script_id'] = script_id
    result['scene information'].setdefault('what', '')
    if authored_context:
        result['scene information']['what'] = str(result['scene information'].get('what', '')) + '\n原始本幕叙事与约束：\n' + authored_context
    result['scene information']['what'] += '\n走位规则：换镜头、左右构图、转身与注视改变不等于平移。只有明确标注移动的事件允许人物改变世界坐标，移动后位置持续沿用。'
    rows = [re.sub(r'^\s*(?:镜头\s*\d+|SHOT\s*\d+|S\d+)[：:、.\s]*', '', line, flags=re.I)
            for line in authored_context.splitlines() if re.match(r'^\s*(?:镜头\s*\d+|SHOT\s*\d+|S\d+)[：:、.\s]', line, re.I)]
    if len(rows) != len(result['scene']):
        rows = []
    first_shot = re.search(r'(?mi)^\s*(?:镜头\s*\d+|SHOT\s*\d+|S\d+)[：:、.\s]', authored_context)
    opening = authored_context[:first_shot.start()].strip() if first_shot else ''
    result.pop('initial position', None)
    for key in ('position_descriptions', 'position_plan', 'position_detail', 'title', '_direct_position_repair_applied'):
        result.pop(key, None)
    for index, event in enumerate(result['scene']):
        event['event_id'] = f'{script_id}_E{index + 1:04d}'
        event['event_index'] = index
        event.pop('current position', None)
        moves = event.pop('move', [])
        for move in moves:
            interaction = move.get('then-interact') or {}
            interactions = interaction if isinstance(interaction, list) else [interaction]
            if any(item.get('target') or item.get('action') for item in interactions):
                raise ValueError(f'第 {index + 1} 个事件包含移动后物体交互，需要新版执行接口适配，不能直接丢弃。')
        description = event.get('shot_description', '').strip()
        narrative = rows[index] if rows else description
        if moves and not description:
            raise ValueError(f'第 {index + 1} 个事件只有旧位置编号，缺少可供新版规划的移动描述。')
        if not event.get('content') and not event.get('speaker'):
            event['speaker'] = ''
            event['content'] = narrative or '无台词'
            event.setdefault('duration', 5.0)
        if narrative:
            performance = list(event.get('performance') or [])
            names = [m['character'] for m in moves] if moves else [event.get('speaker')] if event.get('speaker') else []
            for name in names:
                performance.append({'character': name, 'motion_detail': narrative})
            if performance:
                event['performance'] = performance
        moving_names = {m['character'] for m in moves}
        performance = event.setdefault('performance', [])
        for name in result['scene information']['who']:
            if index == 0 and opening:
                # Opening-facing validation accepts only the first source and
                # its annotations, not scene-level synopsis or later dialogue.
                performance.append({'character': name, 'motion_detail': opening})
            rule = (f'{name}仅在本事件执行上述明确移动，停稳后新位置持续到后续事件。' if name in moving_names else
                    f'{name}在本事件不发生平移；已有位置时保持上一事件结束的世界坐标。可以转身或改变注视，不能因换镜头重新站位。')
            performance.append({'character': name, 'motion_detail': rule})
        for key in CAMERA_FIELDS:
            event.pop(key, None)
        event.setdefault('actions', [])
    return [result]


def validate_motion_continuity(native, editorial):
    states, placements = validate_native_documents(native['documents'])
    def positions(state_id):
        return {a['character']: placements[a['placement_id']]['position'] if a['presence'] == 'present' else None
                for a in states[state_id]['actors']}
    def same(left, right):
        if left is None or right is None:
            return left is right
        return math.dist(left, right) <= 0.001
    events = native['documents']['script.json'][0]['scene']
    if len(events) != len(editorial['scene']):
        raise ValueError('新版事件数与原始编辑稿不一致。')
    pending, boundaries = {}, []
    for index, (event, original) in enumerate(zip(events, editorial['scene'])):
        movers = {m['character'] for m in original.get('move', [])}
        initial = positions(event['blocking_state_id'])
        current = initial
        preceding = dict(pending)
        leading = True
        for transition in event.get('blocking_transitions', []):
            before = leading and (transition['start_when'] == 'before_event' or
                                  transition['start_when'] == 'after_previous_transition')
            if not before:
                leading = False
                if any(same(start, current.get(c)) for c, start in pending.items()):
                    raise ValueError(f'第 {index} 个事件的指定移动未在下一事件正文前完成。')
                pending = {}
            after = positions(transition['to_state_id'])
            for character in set(current) | set(after):
                allowed = movers | (set(pending) if before else set())
                if character not in allowed and not same(current.get(character), after.get(character)):
                    raise ValueError(f'第 {index + 1} 个事件中 {character} 未被安排移动，但新版坐标发生变化，拒绝发布。')
            current = after
        if any(same(start, current.get(c)) for c, start in pending.items()):
            raise ValueError(f'第 {index} 个事件的指定移动未在下一事件正文前完成。')
        if preceding:
            boundaries.append({'source_event_index': index - 1, 'transition_event_index': index,
                               'characters': sorted(preceding), 'start_when': 'before_event'})
        pending = {}
        for character in movers:
            if same(initial.get(character), current.get(character)):
                following = events[index + 1].get('blocking_transitions', []) if index + 1 < len(events) else []
                next_movers = {m['character'] for m in editorial['scene'][index + 1].get('move', [])} if following else set()
                if not original.get('speaker') and character not in next_movers and not event.get('blocking_transitions') and following and following[0]['start_when'] == 'before_event':
                    pending[character] = current.get(character)
                else:
                    raise ValueError(f'第 {index + 1} 个事件中 {character} 的指定移动没有落实到坐标，拒绝发布。')
    if pending:
        raise ValueError('结尾指定移动没有落实到坐标。')
    return boundaries


def audit_authored_distances(native):
    """Report measurable two-person distance mismatches, without rewriting a release."""
    scene = native['documents']['script.json'][0]
    who = scene['scene information']['who']
    if len(who) != 2:
        return []
    states, placements = validate_native_documents(native['documents'])
    digits = dict(zip('零一二三四五六七八九', range(10)))
    digits['两'] = 2
    pattern = re.compile(r'(?:相隔|相距)\s*(约|大约)?\s*([0-9]+(?:\.[0-9]+)?|[零一二两三四五六七八九十]+)\s*米(半)?')
    def amount(value, half):
        if re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', value):
            number = float(value)
        elif value in digits:
            number = digits[value]
        elif '十' in value:
            parts = value.split('十')
            if len(parts) != 2 or any(p and p not in digits for p in parts):
                return None
            number = digits.get(parts[0], 1) * 10 + digits.get(parts[1], 0)
        else:
            return None
        return number + (0.5 if half else 0)
    context = native['source'][0]['scene information'].get('what', '').split('原始本幕叙事与约束：\n')[-1]
    first = re.search(r'(?mi)^\s*(?:镜头\s*\d+|SHOT\s*\d+|S\d+)[：:、.\s]', context)
    opening = context[:first.start()] if first else ''
    checks = [(0, opening, False)]
    checks.extend((i, e.get('content', ''), True) for i, e in enumerate(native['source'][0]['scene']) if not e.get('speaker'))
    warnings = []
    boundaries = {b['source_event_index']: b['transition_event_index']
                  for b in validate_motion_continuity(native, native['editorial'])} if 'editorial' in native else {}
    for index, text, after in checks:
        if not ('两人' in text or all(name in text for name in who)):
            continue
        for match in pattern.finditer(text):
            expected = amount(match[2], match[3])
            if expected is None or expected <= 0:
                continue
            event = scene['scene'][index]
            state = event_state(event, states) if after else states[event['blocking_state_id']]
            if after and event.get('blocking_transitions'):
                state = states[event['blocking_transitions'][-1]['to_state_id']]
            elif after and index in boundaries:
                state = event_state(scene['scene'][boundaries[index]], states)
            by_name = {a['character']: placements[a['placement_id']]['position'] for a in state['actors'] if a['presence'] == 'present'}
            if not all(name in by_name for name in who):
                continue
            actual = math.dist([by_name[who[0]][0], by_name[who[0]][2]], [by_name[who[1]][0], by_name[who[1]][2]])
            distance_policy = native['documents']['blocking_plan.json'].get('distance_policy')
            tolerance = ((distance_policy['approx_tolerance_m'] if match[1] else distance_policy['exact_tolerance_m'])
                         if distance_policy else max(0.5, expected * 0.2) if match[1] else 0.1)
            if abs(actual - expected) > tolerance + 1e-6:
                warnings.append({'path': f'scene[{index}]', 'code': 'AUTHORED_DISTANCE_MISMATCH',
                    'message': f'{who[0]}与{who[1]}的原文间距为{expected:g}米，规划结果约{actual:.2f}米；距离要求未满足，需复核或重新规划。',
                    'expected_m': expected, 'actual_m': round(actual, 3), 'tolerance_m': tolerance})
    return warnings


def event_state(event, states):
    """State at the start of performance, after before_event transitions."""
    state_id = event['blocking_state_id']
    before = False
    for transition in event.get('blocking_transitions', []):
        when = transition['start_when']
        if when == 'before_event' or (when == 'after_previous_transition' and before):
            state_id = transition['to_state_id']
            before = True
        else:
            before = False
    return states[state_id]


def validate_native_documents(documents, source=None):
    """Cross references, continuity, coordinates, facing, and unchanged narration."""
    script, plan, spatial, attention = [documents[name] for name in FILES]
    if not isinstance(script, list) or len(script) != 1:
        raise ValueError('新版站位任务必须恰好包含一场。')
    scene = script[0]
    scene_id = scene['scene information']['where']
    if any(d.get('scene_id') != scene_id for d in (plan, spatial, attention)):
        raise ValueError('新版交付的场景引用不一致。')
    script_id = scene.get('script_id')
    if not script_id or any(d.get('parent_script', {}).get('script_id') != script_id for d in (plan, spatial, attention)):
        raise ValueError('新版交付的剧本引用不一致。')
    if spatial.get('parent_blocking_plan', {}).get('plan_id') != plan.get('plan_id') or attention.get('parent_blocking_plan', {}).get('plan_id') != plan.get('plan_id'):
        raise ValueError('走位计划父引用不一致。')
    if attention.get('parent_spatial_solution', {}).get('solution_id') != spatial.get('solution_id'):
        raise ValueError('注视计划空间引用不一致。')
    states = {s['blocking_state_id']: s for s in plan['blocking_states']}
    placements = {p['placement_id']: p for p in spatial['placements']}
    if not states or len(states) != len(plan['blocking_states']) or len(placements) != len(spatial['placements']):
        raise ValueError('新版状态或点位编号缺失、重复。')
    who = set(scene['scene information']['who'])
    for placement in placements.values():
        for field in ('position', 'rotation'):
            values = placement.get(field)
            if not isinstance(values, list) or len(values) != 3 or any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
                raise ValueError(f'点位 {placement["placement_id"]} 的 {field} 无效。')
        if placement.get('root_rotation_space') != 'body_facing_world' or placement.get('model_yaw_correction') != 0:
            raise ValueError('身体朝向必须遵循新版世界朝向约定，补偿为 0。')
        if not 0 <= placement['rotation'][1] < 360:
            raise ValueError('身体朝向超出 0–360 度范围。')
    for state in states.values():
        actors = state['actors']
        if {a['character'] for a in actors} != who or len(actors) != len(who):
            raise ValueError('走位状态必须标明全部人物在场或离场。')
        for actor in actors:
            pid = actor.get('placement_id')
            if actor['presence'] == 'present':
                if pid not in placements or placements[pid]['character'] != actor['character']:
                    raise ValueError('人物点位引用不匹配。')
            elif actor['presence'] != 'absent' or pid is not None:
                raise ValueError('离场人物不得占据点位。')
    carry = None
    for index, event in enumerate(scene['scene']):
        state = event.get('blocking_state_id')
        if state not in states or (carry is not None and state != carry):
            raise ValueError(f'第 {index + 1} 个事件的走位状态不连续。')
        for offset, transition in enumerate(event.get('blocking_transitions', [])):
            if transition.get('from_state_id') != state or transition.get('to_state_id') not in states:
                raise ValueError('走位转换引用无效。')
            if transition.get('start_when') not in ('before_event', 'after_dialogue', 'after_actions', 'after_previous_transition') or (offset == 0 and transition['start_when'] == 'after_previous_transition'):
                raise ValueError('走位转换时机无效。')
            state = transition['to_state_id']
        carry = state
        actors = [a for a in event_state(event, states)['actors'] if a['presence'] == 'present']
        present = {a['character'] for a in actors}
        if event.get('speaker') and event['speaker'] not in present:
            raise ValueError(f'第 {index + 1} 个事件说话人尚未入场。')
        from .resource_loader import POSTURE_TRANSITION_TARGETS
        poses = {'stand': 'standing', 'sit': 'sitting', 'kneel': 'kneeling', 'crouch': 'squatting'}
        action_states = {a['character']: poses.get(a.get('pose'), a.get('pose')) for a in actors}
        for action in event.get('actions', []):
            if action['character'] not in present or (action.get('state') and action['state'] != action_states.get(action['character'])):
                raise ValueError(f'第 {index + 1} 个事件动作姿态与新版站位不一致，需要重新规划表演。')
            if action.get('action') in POSTURE_TRANSITION_TARGETS:
                action_states[action['character']] = POSTURE_TRANSITION_TARGETS[action['action']]
    indices = [e['event_index'] for e in attention['events']]
    if len(indices) != len(set(indices)) or any(type(i) is not int or i < 0 or i >= len(scene['scene']) for i in indices):
        raise ValueError('注视计划事件索引无效。')
    for event in attention['events']:
        active = {a['character'] for a in event_state(scene['scene'][event['event_index']], states)['actors'] if a['presence'] == 'present'}
        actors = event.get('actors', [])
        if len({a['character'] for a in actors}) != len(actors) or any(a['character'] not in active for a in actors):
            raise ValueError('注视计划引用了离场人物或重复人物。')
        for actor in actors:
            target = actor.get('target', {})
            if target.get('type') == 'character' and target.get('target_character') not in active:
                raise ValueError('注视目标人物不在场。')
    if source is not None:
        original = source[0]
        if len(original['scene']) != len(scene['scene']):
            raise ValueError('站位模块改变了源事件数量。')
        for before, after in zip(original['scene'], scene['scene']):
            for key, value in before.items():
                if after.get(key) != value:
                    raise ValueError(f'站位模块改变了源剧本字段：{key}')
    return states, placements


def verify_delivery(root, source=None):
    root = Path(root).resolve()
    final = read(root / 'final_result.json')
    if not final.get('delivery', {}).get('available') or not final.get('capabilities', {}).get('can_validate_in_unity'):
        issues = '; '.join(str(i.get('message', i.get('code'))) for i in final.get('open_issues', []))
        raise ValueError(f'新版站位尚未通过交付检查（{final.get("status")}）：{issues[:1500]}。任务 {root.parent.name} 已保留，可继续诊断。')
    documents = {}
    for name in FILES:
        reference = final['delivery']['files'][name]
        path = (root / reference['path']).resolve()
        path.relative_to(root)
        if hashlib.sha256(path.read_bytes()).hexdigest() != reference['sha256']:
            raise ValueError(f'新版交付文件校验失败：{name}')
        documents[name] = read(path)
    validate_native_documents(documents, source)
    return final, documents


def run_position(source, scene_id, job_id, progress=None):
    config = settings()
    home = Path(config['home'])
    folder = home / 'jobs' / job_id
    folder.mkdir(parents=True, exist_ok=True)
    source_path = folder / 'source.json'
    if source_path.exists() and read(source_path) != source:
        raise ValueError('站位任务编号已绑定其他输入。')
    if not source_path.exists():
        save(source_path, source)
    root = folder / 'run'
    if not (root / 'final_result.json').exists():
        command = [str(home / 'venv/bin/python'), '-m', 'agent_runner', 'run', '--out', str(root),
                   '--script', str(source_path), '--scene', str(scene_directory(scene_id)),
                   '--env-file', str(home / config['env_file']), '--setting-policy', 'strict',
                   '--model', config['model'],
                   '--max-calls', str(config['max_calls']), '--max-total-tokens', str(config['max_total_tokens']),
                   '--max-run-seconds', str(config['max_run_seconds'])]
        if config.get('distance_policy') is not None:
            command.extend(['--distance-policy', json.dumps(config['distance_policy'])])
        if config.get('max_turns') is not None:
            command.extend(['--max-turns', str(config['max_turns'])])
        binding = {'release': config['release'], 'model': config['model'],
                   'distance_policy': config.get('distance_policy')}
        with (folder / 'worker.log').open('ab') as log:
            process = subprocess.Popen(command, cwd=home / config['release'], stdout=log, stderr=log)
            save(folder / 'worker.json', {**binding, 'pid': process.pid, 'state': 'running'})
            started = time.monotonic()
            while True:
                try:
                    code = process.wait(timeout=15)
                    break
                except subprocess.TimeoutExpired:
                    if progress:
                        progress('info', 'position_running', f'📍 新版站位规划运行中，已用时 {int(time.monotonic()-started)} 秒；任务 {job_id}')
            save(folder / 'worker.json', {**binding, 'pid': process.pid, 'state': 'finished', 'exit_code': code})
    if not (root / 'final_result.json').exists():
        raise ValueError(f'站位任务 {job_id} 未生成结果，请检查该任务日志。')
    final, documents = verify_delivery(root, source)
    command = [str(home / 'venv/bin/python'), '-m', 'agent_runner', 'verify-release', '--out', str(root)]
    checked = subprocess.run(command, cwd=home / config['release'], capture_output=True, text=True, timeout=90)
    if checked.returncode:
        raise ValueError(f'站位任务 {job_id} 的正式交付版本校验失败。')
    return {'root': str(root), 'final': final, 'documents': documents, 'source': source}


def camera_view(native, scene_info):
    docs = native['documents']
    states, placements = validate_native_documents(docs)
    view = copy.deepcopy(docs['script.json'][0])
    regions = {r['id']: r['name'] for r in read(next(scene_directory(view['scene information']['where']).glob('*_scene_grid.json')))['regions']}
    singles = [{'position_id': p['placement_id'], 'character': p['character'], 'region': regions[p['region_id']],
                'neartarget': '', 'lookat': 'center', 'position': p['position'], 'rotation': p['rotation']} for p in placements.values()]
    for event in view['scene']:
        event['current position'] = [{'character': a['character'], 'position': a['placement_id']}
                                     for a in event_state(event, states)['actors'] if a['presence'] == 'present']
    view['initial position'] = copy.deepcopy(view['scene'][0]['current position'])
    return view, {'where': scene_info['where'], 'groups': [], 'singles': singles}


def run_cinematography(script, scene, resource_dir, output_dir, timestamp, act_scene_map=None, progress_callback=None, source_context=None):
    from .cinematography import _build_camera_script, _normalise_shot_blend, _normalise_shot_contract
    from .cinematography.client import LLMJsonClient
    from .cinematography.shot_planning_stage import ShotPlanningStage
    from .cinematography.camera_planning_stage import CameraPlanningStage
    from .schema import validate_camera_script
    from .script_contract import normalize_script
    output_dir = Path(output_dir)
    camera_lib = read(Path(resource_dir) / 'cinematography/CameraLib.json')
    client = LLMJsonClient()
    enriched, views, native_scenes = [], [], []
    for index, original in enumerate(script):
        scene_id = (act_scene_map or {}).get(index, scene).id
        scene_directory(scene_id)
        stages = output_dir / 'CinematographyStages' / f'act_{index + 1:03d}'
        stages.mkdir(parents=True, exist_ok=True)
        stage1 = ShotPlanningStage(script_json=copy.deepcopy(original), llm_client=client,
            output_path=stages/'shot_descriptions.json', stage_output_dir=stages, progress_callback=progress_callback)
        draft = stage1.run()['script_with_shot_description']
        if isinstance(draft, list):
            draft = draft[0]
        draft['scene information']['where'] = scene_id
        normalized = normalize_script([draft])[0][0]
        source = narrative_input(normalized, f'S{timestamp}_A{index + 1:03d}', (source_context or {}).get(index, ''))
        job_id = f'script-{timestamp}-act-{index + 1}'
        if progress_callback:
            progress_callback('info', 'position_start', f'📍 第 {index + 1} 幕：开始新版走位、身体朝向与注视规划。')
        native = run_position(source, scene_id, job_id, progress_callback)
        native['editorial'] = copy.deepcopy(draft)
        validate_motion_continuity(native, draft)
        info = exported_scene_info(scene_id)
        view, context = camera_view(native, info)
        for event, previous in zip(view['scene'], draft['scene']):
            for key in ('shot', 'target', 'target_anchor', 'camera', 'shot_type', 'shot_blend', 'Follow'):
                if key in previous:
                    event[key] = previous[key]
            if not event.get('speaker'):
                event.setdefault('shot', 'scene')
        stage3 = CameraPlanningStage(script_json=view, scene_info_json=info, camera_lib_json=camera_lib,
            position_plan_json=context, position_detail_json=None, llm_client=client,
            output_dir=stages, stage_output_dir=stages, progress_callback=progress_callback,
            authoritative_snapshots=True)
        planned = stage3.run()['script_with_camera_plan']
        if isinstance(planned, list):
            planned = planned[0]
        # Frozen release text cannot change after planning approval.
        for target, frozen in zip(planned['scene'], native['documents']['script.json'][0]['scene']):
            target['shot_description'] = frozen['shot_description']
        _normalise_shot_blend(planned)
        _normalise_shot_contract(planned)
        enriched.append(draft)
        views.append(planned)
        native_scenes.append(native)
    camera = _build_camera_script(views, camera_lib)
    report = validate_camera_script(camera)
    if not report['valid']:
        raise ValueError('新版镜头格式校验失败：' + json.dumps(report['errors'], ensure_ascii=False))
    filename = f'camera_script_{timestamp}.json'
    save(output_dir / filename, camera)
    plan_name, detail_name = f'blocking_plan_{timestamp}.json', f'spatial_solution_{timestamp}.json'
    for name, key in ((plan_name, 'blocking_plan.json'), (detail_name, 'spatial_solution.json')):
        rows = [item['documents'][key] for item in native_scenes]
        save(output_dir/name, rows[0] if len(rows) == 1 else {'schema_version': 'position-scenes.1', 'scenes': rows})
    return {'ok': True, 'enriched_script': enriched, 'camera_script_filename': filename,
            'position_plan_filename': plan_name, 'position_detail_filename': detail_name, 'native_scenes': native_scenes}


def validate_camera_targets(camera, native_scenes):
    errors = []
    if len(camera['scenes']) != len(native_scenes):
        return [{'path': 'camera_script', 'code': 'BUNDLE_REFERENCE', 'message': '镜头幕数不一致'}]
    for index, (camera_scene, native) in enumerate(zip(camera['scenes'], native_scenes)):
        if 'editorial' in native:
            validate_motion_continuity(native, native['editorial'])
        states, _ = validate_native_documents(native['documents'], native['source'])
        scene = native['documents']['script.json'][0]
        targets = {m['name'] for r in exported_scene_info(scene['scene information']['where'])['regions'] for m in r['scene_markers']}
        if len(camera_scene['events']) != len(scene['scene']):
            errors.append({'path': f'camera_script[{index}]', 'code': 'BUNDLE_REFERENCE', 'message': '镜头事件数量不一致'})
        for event, ce in zip(scene['scene'], camera_scene['events']):
            present = {a['character']: a['placement_id'] for a in event_state(event, states)['actors'] if a['presence'] == 'present'}
            valid = ce['shot_description'] == event['shot_description']
            if ce['shot'] == 'character':
                valid = valid and ce['target'] in present and ce['target_position'] == present[ce['target']]
            if ce['shot'] == 'object':
                valid = valid and ce['target'] in targets
            if not valid:
                errors.append({'path': f'camera_script[{index}].events[{ce["event_index"]}]', 'code': 'BUNDLE_REFERENCE', 'message': '镜头与新版人物点位或场景目标不一致'})
    return errors


def publish_artifacts(output_dir, timestamp, native_scenes, camera):
    """Package byte-identical releases with per-act camera files and audit gates."""
    output_dir = Path(output_dir)
    names = {'attention_plan': f'attention_plan_{timestamp}.json', 'position_report': f'position_report_{timestamp}.json',
             'position_bundle': f'position_bundle_{timestamp}.zip'}
    attentions, reports = [], []
    camera_errors = validate_camera_targets(camera, native_scenes)
    if camera_errors:
        raise ValueError('镜头与新版交付引用校验失败：' + json.dumps(camera_errors, ensure_ascii=False))
    with zipfile.ZipFile(output_dir/names['position_bundle'], 'w', zipfile.ZIP_DEFLATED) as archive:
        for index, native in enumerate(native_scenes):
            root = Path(native['root'])
            final, documents = verify_delivery(root, native['source'])
            prefix = f'act_{index + 1:03d}/'
            for name in FILES + ('latest.json',):
                archive.write(root/'delivery'/name, prefix+'delivery/'+name)
            for name in ('final_result.json', 'run_result.json', 'RESULT.md', 'tasks.json'):
                if (root/name).is_file():
                    archive.write(root/name, prefix+name)
            for path in (root/'preview').glob('*'):
                if path.is_file():
                    archive.write(path, prefix+'preview/'+path.name)
            camera_scene = copy.deepcopy(camera['scenes'][index])
            camera_scene['shot_index'] = 0
            camera_bytes = json.dumps({'scenes': [camera_scene]}, ensure_ascii=False, indent=2).encode('utf-8')
            archive.writestr(prefix+'delivery/camera_script.json', camera_bytes)
            attentions.append(documents['attention_plan.json'])
            reports.append({'act_index': index, 'status': final['status'], 'capabilities': final['capabilities'],
                            'quality_gates': final['quality_gates'], 'open_issues': final['open_issues'],
                            'body_facing_quality': final.get('body_facing_quality'), 'input_binding': final['input_binding'],
                            'distance_validation': documents['spatial_solution.json'].get('distance_validation'),
                            'authoring_warnings': audit_authored_distances(native),
                            'camera_sha256': hashlib.sha256(camera_bytes).hexdigest(),
                            'camera_references_valid': True,
                            'movement_boundary_mapping': validate_motion_continuity(native, native['editorial']) if 'editorial' in native else [],
                            'movement_continuity_checked': 'editorial' in native})
        report = {'schema_version': 'script-position-report.1', 'scenes': reports,
                  'camera_target_position': 'spatial_solution.placements[].placement_id; after before_event transitions',
                  'execution': 'Unity 验证通过前不可作为已验收的动画执行方案。'}
        archive.writestr('integration_report.json', json.dumps(report, ensure_ascii=False, indent=2))
    save(output_dir/names['attention_plan'], attentions[0] if len(attentions) == 1 else {'schema_version': 'position-scenes.1', 'scenes': attentions})
    save(output_dir/names['position_report'], report)
    for index, native in enumerate(native_scenes):
        sheet = native['final'].get('visualization', {}).get('contact_sheet')
        if sheet:
            path = Path(native['root']) / sheet['path']
            if hashlib.sha256(path.read_bytes()).hexdigest() != sheet['sha256']:
                raise ValueError('布局预览校验失败。')
            name = f'position_preview_{timestamp}_{index + 1}.png'
            import shutil
            shutil.copyfile(path, output_dir/name)
            names[f'position_preview_{index + 1}'] = name
    return names

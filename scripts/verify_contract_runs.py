"""Two real-model acceptance runs. No mocks and no production resource edits."""
import argparse
import asyncio
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from dotenv import load_dotenv
load_dotenv(ROOT / 'backend' / '.env')
from src.autogen_pipeline import run_autogen_pipeline
from src.resource_loader import ResourceLoader
from src.script_contract import MANDARIN, validate_script, validate_bundle


async def run(case):
    loader = ResourceLoader()
    chosen = ([c for c in loader.characters if c.gameobject_name in ('F01_WithCamera', 'F02_WithCamera')]
              if case == 'normal' else loader.characters[:2])
    a, b = [c.name for c in chosen]
    params = {'scene_id': 'Auditorium', 'required_character_count': 2,
              'custom_characters': [{'name': c.name, 'gender': c.gender, 'gameobject_name': c.gameobject_name,
                                     'background': c.background} for c in chosen]}
    original = None
    if case == 'normal':
        params.update(act_count=2, creative_idea=(
            f'{a}与{b}在礼堂排练一场迟到的告别。只生成两幕，每幕3到5个事件，台词短而自然。'
            '必须包含Sit Down坐下动作。必须包含Stand Up站起动作。必须包含纯移动事件。'
            '必须包含边走边说事件。必须包含普通对白。必须包含无说话人事件（speaker为空、content为无台词）。'
            '移动前必须站立。不同角色独立站位，'
            '每一幕自己初始化姿态。默认普通话。不要交互物体。'))
    else:
        config = copy.deepcopy(MANDARIN)
        config['tracks'].append({'id': 'en_us', 'label': 'English-英语', 'content_language': 'en',
                                 'speech_language': 'en-US', 'tts_prompt_suffix': '英语。'})
        original = [{'scene information': {'who': [a, b], 'where': 'Auditorium', 'what': '排练后一起离开礼堂',
                      'emotionLibrary': '', 'language_config': config},
                     'initial position': [{'character': a, 'position': 'Position 1', 'state': 'standing'},
                                          {'character': b, 'position': 'Position 2', 'state': 'sitting'}],
                     'position_descriptions': {'Position 1': '礼堂前排左侧', 'Position 2': '礼堂前排右侧',
                                               'Position 3': '礼堂中间走道左侧', 'Position 4': '礼堂中间走道右侧'},
                     'scene': [
                         {'speaker': a, 'content': '我们该走了。', 'language_track': 'zh_hans_cmn',
                          'content_variants': {'zh_hans_cmn': '我们该走了。', 'en_us': 'We should go.'},
                          'emotion': [{'character': a, 'emotion': '', 'emotionStyle': ''}], 'actions': []},
                         {'speaker': b, 'content': '等我站起来。', 'actions': [{'character': b, 'state': 'sitting',
                          'action': 'Stand Up', 'motion_detail': 'Rises slowly from the seat.',
                          'then-interact': {'target': '', 'action': '', 'delay': 0.2}}],
                          'language_track': 'zh_hans_cmn',
                          'content_variants': {'zh_hans_cmn': '等我站起来。', 'en_us': 'Let me stand up.'}},
                         {'move': [{'character': a, 'destination': 'Position 3',
                                    'then-interact': {'target': '', 'action': '', 'delay': 0}}]},
                         {'speaker': b, 'content': '走吧。', 'move': [{'character': b, 'destination': 'Position 4'}],
                          'language_track': 'zh_hans_cmn',
                          'content_variants': {'zh_hans_cmn': '走吧。', 'en_us': "Let's go."}},
                         {'speaker': '', 'content': '无台词', 'duration': 3, 'actions': [],
                          'shot_description': '礼堂的座椅排列整齐，走道尽头亮着柔和的灯。'},
                     ]}]
        params.update(act_count=1, direct_mode=True, creative_idea=json.dumps(original, ensure_ascii=False))
    destination = ROOT / 'test_outputs' / 'contract_acceptance' / case
    destination.mkdir(parents=True, exist_ok=True)
    (destination / 'request.json').write_text(json.dumps(params, ensure_ascii=False, indent=2), encoding='utf-8')
    events = []
    class Bridge:
        def put_event(self, event):
            events.append(event)
            if event.get('message'):
                print(event['message'], flush=True)
    try:
        await run_autogen_pipeline(Bridge(), loader, params)
    finally:
        (destination / 'events.json').write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding='utf-8')
    success = next((e for e in reversed(events) if e.get('type') == 'success'), None)
    if not success:
        raise RuntimeError('No validated success event')
    documents = {}
    for name, field in [('script', 'filename'), ('camera', 'camera_script_filename'), ('actors', 'actors_profile_filename'),
                        ('plan', 'position_plan_filename'), ('detail', 'position_detail_filename'), ('validation', 'validation_filename')]:
        source = ROOT / 'outputs' / success[field]
        documents[name] = json.loads(source.read_text(encoding='utf-8'))
        (destination / source.name).write_bytes(source.read_bytes())
    report = validate_script(documents['script'], loader, act_count=params['act_count'], required_names=[a, b], character_count=2)
    bundle = validate_bundle(documents['script'], documents['camera'], documents['actors'], loader, documents['plan'], documents['detail'])
    assert report['valid'], report['errors']
    assert bundle['valid'], bundle['errors']
    if original:
        expected = [(e.get('speaker'), e.get('content')) for act in original for e in act['scene']]
        actual = [(e.get('speaker'), e.get('content')) for act in documents['script'] for e in act['scene']]
        assert actual == expected, 'Direct-mode dialogue or event order changed'
        assert documents['script'][0]['scene'][0]['content_variants'] == original[0]['scene'][0]['content_variants']
    beats = [e for act in documents['script'] for e in act['scene']]
    coverage = {'speaking': any(e.get('speaker') and 'move' not in e for e in beats),
                'moving': any('move' in e and 'speaker' not in e for e in beats),
                'walk_and_talk': any('move' in e and e.get('speaker') for e in beats),
                'silent': any(e.get('speaker') == '' for e in beats)}
    assert all(coverage.values()), coverage
    if case == 'normal':
        action_ids = {a['action'] for e in beats for a in e.get('actions', [])}
        assert {'Sit Down', 'Stand Up'} <= action_ids, action_ids
    acceptance = {'case': case, 'valid': True, 'coverage': coverage, 'validation': report, 'bundle': bundle, 'success': success}
    (destination / 'acceptance.json').write_text(json.dumps(acceptance, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'case': case, 'valid': True, 'coverage': coverage, 'folder': str(destination)}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--case', choices=['normal', 'direct'], required=True)
    asyncio.run(run(parser.parse_args().case))

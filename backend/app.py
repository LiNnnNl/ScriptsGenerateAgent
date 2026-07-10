"""
ScriptAgent Web UI
使用 Flask 提供简单的 Web 界面
"""

import os

# ── SSL 修复：使用 certifi 的 CA 证书，修复 "unable to get local issuer certificate" ──
import certifi
os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["SSL_CERT_DIR"] = ""

from flask import Flask, request, jsonify, send_file, Response, stream_with_context, send_from_directory
from flask_cors import CORS
import json
import logging
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from src.resource_loader import ResourceLoader
from src.autogen_bridge import AutoGenStreamBridge
from src.autogen_pipeline import run_autogen_pipeline
from src.director_word_pipeline import run_director_word_pipeline
from src.prompt_renderers.character_generation import (
    build_character_generation_prompt,
    character_generation_system_prompt,
)
from src import registry as _registry
from src.word_exporter import export_script_to_word

# 加载环境变量
load_dotenv()

# ── 日志配置 ──
_log_dir = Path(__file__).parent.parent / "logs"
_log_dir.mkdir(exist_ok=True)
_log_file = _log_dir / f"app_{datetime.now().strftime('%Y%m%d')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(_log_file, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False  # 支持中文
_repo_dir = Path(__file__).resolve().parent.parent
_frontend_dir = _repo_dir / "frontend"


class _ScriptPrefixMiddleware:
    """Allow the app to sit behind a `/script` URL prefix."""

    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        path_info = environ.get("PATH_INFO", "")
        if path_info == "/script":
            query_string = environ.get("QUERY_STRING", "")
            location = "/script/"
            if query_string:
                location = f"{location}?{query_string}"
            start_response(
                "308 Permanent Redirect",
                [
                    ("Location", location),
                    ("Content-Type", "text/plain; charset=utf-8"),
                    ("Content-Length", "0"),
                ],
            )
            return [b""]
        elif path_info.startswith("/script/"):
            environ["PATH_INFO"] = path_info[len("/script"):]
        return self.wsgi_app(environ, start_response)


app.wsgi_app = _ScriptPrefixMiddleware(app.wsgi_app)

# 启用CORS（跨域资源共享）
_cors_origins = os.getenv("CORS_ORIGINS", "*")
_cors_origins = [
    origin.strip()
    for origin in _cors_origins.split(",")
    if origin.strip()
] or "*"
CORS(app, resources={
    r"/api/*": {
        "origins": _cors_origins,
        "methods": ["GET", "POST", "PATCH", "OPTIONS"],
        "allow_headers": ["Content-Type"]
    }
})

# 初始化资源加载器
resource_loader = ResourceLoader()


def _serve_frontend_asset(asset_path='index.html'):
    target = (_frontend_dir / asset_path).resolve()
    try:
        target.relative_to(_frontend_dir.resolve())
    except ValueError:
        return jsonify({'success': False, 'error': 'Invalid frontend asset path'}), 400

    if target.is_file():
        return send_from_directory(str(_frontend_dir), asset_path)
    return send_from_directory(str(_frontend_dir), 'index.html')


@app.route('/api/health', methods=['GET'])
def health_check():
    """部署探活：前后端分离后，后端只承诺 API 可用。"""
    return jsonify({
        'success': True,
        'service': 'script-agent-api',
        'time': datetime.now().isoformat(timespec='seconds'),
    })


@app.route('/api/shot_types', methods=['GET'])
def get_shot_types():
    """返回合法的 shot_type 和 shot_blend 枚举值"""
    from src.schema import VALID_SHOT_TYPE, VALID_SHOT_BLEND
    return jsonify({
        'success': True,
        'shot_types': sorted(VALID_SHOT_TYPE),
        'shot_blends': sorted(VALID_SHOT_BLEND),
    })


@app.route('/api/actions', methods=['GET'])
def get_actions():
    """返回动作库列表（按 compatible_states 分组）"""
    try:
        groups = {}
        for action in resource_loader.actions:
            for state in (action.compatible_states or ['standing']):
                groups.setdefault(state, []).append({
                    'trigger': action.action_id,
                    'description': action.description,
                    'state': state,
                })
        return jsonify({'success': True, 'data': groups})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/styles', methods=['GET'])
def get_styles():
    """获取所有可用画风"""
    try:
        styles = resource_loader.get_available_styles()
        return jsonify({
            'success': True,
            'data': styles
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/scenes', methods=['GET'])
def get_all_scenes():
    """获取所有场景"""
    try:
        scenes = resource_loader.get_all_scenes()
        scenes_data = []
        for scene in scenes:
            raw = resource_loader.load_scene_info(scene.id)
            regions = []
            if raw:
                for r in raw.get("regions", []):
                    regions.append({
                        "name": r["name"],
                        "description": r.get("description", ""),
                        "anchors": [a["name"] for a in r.get("anchors", [])],
                        "markers": [m["name"] for m in r.get("scene_markers", [])],
                    })
            scenes_data.append({
                'id': scene.id,
                'name': scene.name,
                'description': scene.description,
                'regions': regions,
            })
        return jsonify({'success': True, 'data': scenes_data})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/scenes/<style_tag>', methods=['GET'])
def get_scenes(style_tag):
    """根据画风获取场景列表"""
    try:
        scenes = resource_loader.get_scenes_by_style(style_tag)
        scenes_data = [
            {
                'id': scene.id,
                'name': scene.name,
                'description': scene.description,
                'positions': scene.valid_positions,
                'camera_groups': scene.camera_groups
            }
            for scene in scenes
        ]
        return jsonify({
            'success': True,
            'data': scenes_data
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


_CHAR_DISPLAY_FIELDS = (
    'name', 'gender', 'age', 'gameobject_name',
    'appearance', 'acting_style', 'traits', 'background',
)

@app.route('/api/characters', methods=['GET'])
def get_all_characters():
    """获取所有角色（只返回标准展示字段，过滤引擎专用字段）"""
    try:
        char_file = resource_loader.resource_dir / 'characters_resource.json'
        with open(char_file, 'r', encoding='utf-8-sig') as f:
            raw = json.load(f)
        characters = []
        for c in raw:
            if not isinstance(c, dict) or not (c.get('name') or '').strip():
                continue
            entry = {}
            for k in _CHAR_DISPLAY_FIELDS:
                entry[k] = c.get(k, [] if k == 'traits' else ({} if k == 'appearance' else ''))
            characters.append(entry)
        return jsonify({'success': True, 'data': characters})
    except Exception as e:
        logger.error("get_all_characters 失败: %s", e)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/characters', methods=['POST'])
def add_character():
    """永久添加角色到角色库"""
    try:
        data = request.json
        name = (data.get('name') or '').strip()
        if not name:
            return jsonify({'success': False, 'error': '角色名称不能为空'}), 400

        description = (data.get('description') or '').strip()

        char_file = resource_loader.resource_dir / 'characters_resource.json'
        with open(char_file, 'r', encoding='utf-8-sig') as f:
            characters = json.load(f)

        if any((c.get('name') or '') == name for c in characters if isinstance(c, dict)):
            return jsonify({'success': False, 'error': f'角色「{name}」已存在于角色库中'}), 400

        new_char = {
            "name": name,
            "gender": (data.get('gender') or '未知').strip(),
            "ip": (data.get('ip') or '自定义').strip(),
            "manufacturer": "用户创建",
            "background": (data.get('background') or description or f"用户自定义角色：{name}").strip(),
            "Faction": (data.get('Faction') or '未知').strip(),
            "personality_traits": (data.get('personality_traits') or description or '性格由AI自由发挥').strip(),
            "role_position": "未知",
            "important_relationships": []
        }

        characters.append(new_char)

        with open(char_file, 'w', encoding='utf-8') as f:
            json.dump(characters, f, ensure_ascii=False, indent=2)

        return jsonify({'success': True, 'data': new_char})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/characters/<style_tag>', methods=['GET'])
def get_characters(style_tag):
    """根据画风获取角色列表（旧接口保留）"""
    try:
        characters = resource_loader.get_characters_by_style(style_tag)
        characters_data = [
            {
                'id': char.id,
                'name': char.name,
                'description': char.description,
                'personality': char.personality
            }
            for char in characters
        ]
        return jsonify({
            'success': True,
            'data': characters_data
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/generate_characters', methods=['POST'])
def generate_characters():
    """使用 AI 生成角色档案 JSON（按指定格式）"""
    import os
    import httpx
    from openai import OpenAI

    data = request.json or {}
    scene_id = data.get('scene_id', '')
    character_count = int(data.get('character_count', 2))
    creative_idea = (data.get('creative_idea') or '').strip()
    partial_characters = data.get('partial_characters', [])

    # 场景池（多场景）：角色跨幕复用，参考整池概述作为环境参考；缺省回退单 scene_id
    scene_pool_ids = data.get('scene_pool') or []
    if not isinstance(scene_pool_ids, list):
        scene_pool_ids = []
    scene_pool_ids = [str(s).strip() for s in scene_pool_ids if str(s).strip()]
    if not scene_pool_ids and scene_id:
        scene_pool_ids = [scene_id]

    # 获取场景名称描述（单场景=单条；多场景=整池概述）
    _scenes_by_id = {sc.id: sc for sc in resource_loader.get_all_scenes()}
    _pool_descs = [
        f"{_scenes_by_id[sid].name}：{_scenes_by_id[sid].description}"
        for sid in scene_pool_ids if sid in _scenes_by_id
    ]
    if len(_pool_descs) > 1:
        scene_desc = "（多场景，角色需跨场景通用）\n" + "\n".join(f"- {d}" for d in _pool_descs)
    elif _pool_descs:
        scene_desc = _pool_descs[0]
    else:
        scene_desc = scene_id

    # 构建已指定角色说明
    specified = [c for c in partial_characters if (c.get('name') or '').strip()]
    char_instructions = ''
    if specified:
        char_instructions = '\n\n已指定角色（必须包含，完善其档案）：\n'
        for c in specified:
            char_instructions += f"- {c['name'].strip()}"
            if (c.get('description') or '').strip():
                char_instructions += f"：{c['description'].strip()}"
            char_instructions += '\n'
        remaining = character_count - len(specified)
        if remaining > 0:
            char_instructions += f'\n另需自由创作 {remaining} 位新角色。'

    # 读取可用角色模型列表（有 gameobject_name 的条目）
    available_models = []
    try:
        char_file = resource_loader.resource_dir / 'characters_resource.json'
        with open(char_file, 'r', encoding='utf-8-sig') as _f:
            _char_data = json.load(_f)
        for _c in _char_data:
            gname = (_c.get('gameobject_name') or '').strip()
            if gname:
                _app = _c.get('appearance') or {}
                _traits = _c.get('traits') or []
                available_models.append({
                    'gameobject_name': gname,
                    'ref_name': _c.get('name', ''),
                    'gender': _c.get('gender', ''),
                    'traits': ', '.join(_traits) if isinstance(_traits, list) else str(_traits),
                    'body_type': (_app.get('body_type') or '')[:60] if isinstance(_app, dict) else '',
                    'background': (_c.get('background') or '')[:60],
                })
    except Exception as _e:
        logger.warning("读取角色模型列表失败: %s", _e)

    # 构建模型选择说明
    if available_models:
        model_list_str = '\n'.join(
            f"  - gameobject_name: \"{m['gameobject_name']}\""
            f"  参考形象: {m['ref_name']}({m['gender']})"
            f"  | 特质: {m['traits']}"
            f"  | 外形: {m['body_type']}"
            for m in available_models
        )
        model_instruction = (
            f"\n\n## 可用角色模型列表\n"
            f"请为每个角色从下列模型中选择外形和气质最契合的一个，"
            f"将其 gameobject_name 填入对应字段。若无合适模型则留空字符串。\n"
            + model_list_str
        )
    else:
        model_instruction = "\n\n注意：当前暂无可用角色模型，gameobject_name 字段留空字符串。"

    prompt = build_character_generation_prompt(
        character_count=character_count,
        scene_desc=scene_desc,
        creative_idea=creative_idea,
        char_instructions=char_instructions,
        model_instruction=model_instruction,
    )

    client = OpenAI(
        api_key=os.getenv('API_KEY'),
        base_url=os.getenv('BASE_URL', 'https://ark.cn-beijing.volces.com/api/v3'),
        timeout=120,
        max_retries=0,
        http_client=httpx.Client(trust_env=False),
    )
    model_name = os.getenv('MODEL', 'doubao-seed-2-0-lite-260215')

    try:
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": character_generation_system_prompt},
                {"role": "user", "content": prompt}
            ],
            max_tokens=4000,
            temperature=0.8
        )
        raw = response.choices[0].message.content.strip()

        # 清理可能的 markdown 代码块包裹
        cleaned = raw
        if cleaned.startswith('```'):
            cleaned = cleaned.split('\n', 1)[1] if '\n' in cleaned else ''
            if '```' in cleaned:
                cleaned = cleaned[:cleaned.rfind('```')].rstrip()

        # 截取第一个 [ 到最后一个 ]
        start = cleaned.find('[')
        end = cleaned.rfind(']')
        if start != -1 and end != -1:
            cleaned = cleaned[start:end + 1]

        characters = json.loads(cleaned)
        if not isinstance(characters, list):
            raise ValueError('AI 输出不是数组')

        # 规范化：确保每个角色对象严格符合标准字段，缺失字段补空字符串
        valid_gobj_names = {m['gameobject_name'] for m in available_models}

        def normalize_char(c):
            rels = c.get('important_relationships') or []
            if not isinstance(rels, list):
                rels = []
            norm_rels = [
                {
                    "object": str(r.get('object') or ''),
                    "relationship": str(r.get('relationship') or '')
                }
                for r in rels if isinstance(r, dict)
            ]
            # gameobject_name 只保留在可用列表中的值，其余清空
            gobj = str(c.get('gameobject_name') or '').strip()
            if valid_gobj_names and gobj not in valid_gobj_names:
                gobj = ''
            return {
                "name":                    str(c.get('name') or ''),
                "gender":                  str(c.get('gender') or ''),
                "ip":                      str(c.get('ip') or ''),
                "manufacturer":            str(c.get('manufacturer') or ''),
                "background":              str(c.get('background') or ''),
                "Faction":                 str(c.get('Faction') or ''),
                "personality_traits":      str(c.get('personality_traits') or ''),
                "role_position":           str(c.get('role_position') or ''),
                "important_relationships": norm_rels,
                "gameobject_name":         gobj,
            }

        characters = [normalize_char(c) for c in characters if isinstance(c, dict)]

        # 保存文件
        timestamp = int(datetime.now().timestamp())
        filename = f"characters_{timestamp}.json"
        output_dir = Path('outputs')
        output_dir.mkdir(exist_ok=True)
        with open(output_dir / filename, 'w', encoding='utf-8') as f:
            json.dump(characters, f, ensure_ascii=False, indent=2)

        return jsonify({'success': True, 'data': characters, 'filename': filename})

    except (json.JSONDecodeError, ValueError) as e:
        logger.error("generate_characters JSON 解析失败: %s | raw=%s", e, raw[:300])
        return jsonify({'success': False, 'error': f'AI 输出格式错误: {str(e)}'}), 500
    except Exception as e:
        logger.error("generate_characters 失败: %s", e)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/generate', methods=['POST'])
def generate_script():
    """生成剧本（流式输出，AutoGen 多 Agent 版）"""

    def generate():
        bridge = AutoGenStreamBridge()
        bridge.run_in_thread(
            run_autogen_pipeline(bridge, resource_loader, request.json)
        )
        yield from bridge.flask_generator()

    return Response(stream_with_context(generate()), mimetype='application/x-ndjson')


@app.route('/api/generate_director_word', methods=['POST'])
def generate_director_word():
    """导演 Word 模式：只调用 DirectorAgent，生成可读分镜剧本并导出 Word。"""

    def generate():
        bridge = AutoGenStreamBridge()
        bridge.run_in_thread(
            run_director_word_pipeline(bridge, resource_loader, request.json or {})
        )
        yield from bridge.flask_generator()

    return Response(stream_with_context(generate()), mimetype='application/x-ndjson')


@app.route('/api/script_content/<filename>', methods=['GET'])
def get_script_content(filename):
    """返回生成的剧本 JSON 内容（供前端编辑器加载）"""
    try:
        filepath = Path('outputs') / filename
        if not filepath.exists() or filepath.suffix != '.json':
            return jsonify({'success': False, 'error': '文件不存在'}), 404
        with open(filepath, 'r', encoding='utf-8') as f:
            content = json.load(f)
        return jsonify({'success': True, 'data': content})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/character_image/<gameobject_name>', methods=['GET'])
def character_image(gameobject_name):
    """返回角色模型预览图（Images/<gameobject_name>.png）"""
    images_dir = resource_loader.resource_dir / 'Images'
    # 只允许字母数字下划线，防止路径穿越
    import re
    if not re.fullmatch(r'[\w\-]+', gameobject_name):
        return ('', 404)
    for ext in ('png', 'jpg', 'jpeg', 'webp'):
        fname = f'{gameobject_name}.{ext}'
        if (images_dir / fname).exists():
            return send_from_directory(str(images_dir), fname)
    return ('', 404)


@app.route('/api/download/<filename>', methods=['GET'])
def download_file(filename):
    """下载生成的脚本文件"""
    try:
        filepath = Path('outputs') / filename
        if not filepath.exists():
            return jsonify({
                'success': False,
                'error': '文件不存在'
            }), 404
        
        return send_file(
            filepath,
            as_attachment=True,
            download_name=filename,
            mimetype='application/json'
        )
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/position_plan/<session_id>', methods=['GET'])
def get_position_plan(session_id):
    """返回某次会话的 position_plan JSON（含 anchor 名称映射，供前端做 Position N → 锚点名 显示）"""
    try:
        data = _registry.load_registry()
        session = data.get("sessions", {}).get(session_id)
        if not session:
            return jsonify({'success': False, 'error': '会话不存在'}), 404

        fname = session.get("files", {}).get("position_plan")
        if not fname:
            return jsonify({'success': False, 'error': '无 position_plan 文件'}), 404

        fpath = Path("outputs") / fname
        if not fpath.exists():
            return jsonify({'success': False, 'error': '文件不存在'}), 404

        with open(fpath, 'r', encoding='utf-8') as f:
            plan = json.load(f)
        return jsonify({'success': True, 'data': plan})
    except Exception as e:
        logger.error("get_position_plan 失败: %s", e)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/history', methods=['GET'])
def get_history():
    """返回历史生成会话列表（按时间倒序）"""
    try:
        sessions = _registry.list_sessions_desc()
        return jsonify({'success': True, 'data': sessions})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/history/<session_id>/label', methods=['PATCH'])
def update_history_label(session_id):
    """更新历史会话的自定义标签"""
    try:
        data = request.json or {}
        label = str(data.get('label', ''))
        ok = _registry.update_label(session_id, label)
        if not ok:
            return jsonify({'success': False, 'error': '会话不存在'}), 404
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/download_session/<session_id>', methods=['GET'])
def download_session_zip(session_id):
    """打包下载某次生成会话的所有输出文件（剧本/角色档案/位置规划/摄影脚本等）"""
    import io, zipfile
    try:
        data = _registry.load_registry()
        session = data.get("sessions", {}).get(session_id)
        if not session:
            return jsonify({'success': False, 'error': '会话不存在'}), 404

        files_info = session.get("files", {})
        word_export = session.get("word_export")

        output_dir = Path("outputs")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            # 收集所有文件
            keys = ['script', 'actors_profile', 'position_plan', 'position_detail', 'camera_script']
            for key in keys:
                fname = files_info.get(key)
                if fname:
                    fpath = output_dir / fname
                    if fpath.exists():
                        zf.write(fpath, fname)
            # word 导出（如果存在）
            if word_export:
                fpath = output_dir / word_export
                if fpath.exists():
                    zf.write(fpath, word_export)

        buf.seek(0)
        return send_file(
            buf,
            as_attachment=True,
            download_name=f"session_{session_id}.zip",
            mimetype='application/zip',
        )
    except Exception as e:
        logger.error("download_session_zip 失败: %s", e)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/download_word/<filename>', methods=['GET'])
def download_word(filename):
    """将剧本 JSON 导出为 Word 文档并下载（只含对话和镜头描述）"""
    try:
        output_dir = Path('outputs')
        json_path = output_dir / filename
        if not json_path.exists() or json_path.suffix != '.json':
            return jsonify({'success': False, 'error': '剧本文件不存在'}), 404

        # 提取时间戳用于 docx 文件名和 session_id
        stem = json_path.stem  # e.g. script_1746400000
        ts = stem.split('_', 1)[-1] if '_' in stem else stem
        docx_filename = f"script_{ts}.docx"
        docx_path = output_dir / docx_filename

        if not docx_path.exists():
            with open(json_path, 'r', encoding='utf-8') as f:
                script_data = json.load(f)
            export_script_to_word(script_data, docx_path)
            _registry.update_word_export(ts, docx_filename)

        return send_file(
            docx_path,
            as_attachment=True,
            download_name=docx_filename,
            mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        )
    except Exception as e:
        logger.error("download_word 失败: %s", e)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/', defaults={'asset_path': 'index.html'}, methods=['GET'])
@app.route('/<path:asset_path>', methods=['GET'])
def serve_frontend(asset_path):
    """Serve the static frontend so `/script/*` can point at this backend."""
    if asset_path.startswith('api/'):
        return jsonify({'success': False, 'error': 'Not Found'}), 404
    return _serve_frontend_asset(asset_path)


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001)

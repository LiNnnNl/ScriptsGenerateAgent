"""
ScriptAgent Web UI
使用 Flask 提供简单的 Web 界面
"""

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

# 启用CORS（跨域资源共享）
CORS(app, resources={
    r"/api/*": {
        "origins": "*",
        "methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["Content-Type"]
    }
})

# 初始化资源加载器
resource_loader = ResourceLoader()


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
    from openai import OpenAI

    data = request.json or {}
    scene_id = data.get('scene_id', '')
    character_count = int(data.get('character_count', 2))
    creative_idea = (data.get('creative_idea') or '').strip()
    partial_characters = data.get('partial_characters', [])

    # 获取场景名称描述
    scene_desc = scene_id
    for scene in resource_loader.get_all_scenes():
        if scene.id == scene_id:
            scene_desc = f"{scene.name}：{scene.description}"
            break

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

    # 严格格式模板（每个字段必须存在，不知道的留空字符串）
    format_example = json.dumps([
        {
            "name": "天命人",
            "gender": "男",
            "ip": "黑神话：悟空",
            "manufacturer": "游戏科学",
            "background": "重走西游路的小猴子，背负着收集大圣六根、复活齐天大圣的宿命。虽一言不发，却在九九八十一难中磨砺成神。",
            "Faction": "花果山 / 寻根人",
            "personality_traits": "坚毅, 灵动, 沉默寡言",
            "role_position": "棍法宗师 / 法术全才",
            "important_relationships": [
                {"object": "弥勒/小弥勒", "relationship": "引路者 / 幕后观察者"},
                {"object": "二郎神", "relationship": "宿命的对手 / 意志的考验者"}
            ],
            "gameobject_name": "WuKong_Model_01"
        }
    ], ensure_ascii=False, indent=2)

    prompt = (
        f"请为以下场景创作 {character_count} 位角色的完整档案。\n\n"
        f"场景：{scene_desc}\n"
        + (f"创作灵感：{creative_idea}\n" if creative_idea else '')
        + char_instructions
        + model_instruction
        + f"\n\n请严格按照以下 JSON 数组格式输出。"
          f"每位角色必须包含下列全部字段，不知道的字段留空字符串 \"\"，"
          f"important_relationships 不知道的留空数组 []。"
          f"直接输出 JSON 数组，不要有 ```json 包裹或任何说明文字：\n\n"
        + format_example
        + f"\n\n要求：\n"
          f"- 输出恰好 {character_count} 位角色\n"
          f"- 每个角色对象必须且只能包含以上 10 个字段，字段名大小写完全一致\n"
          f"- gameobject_name 必须从「可用角色模型列表」中选取，填写列表中存在的值；无合适的则留空字符串\n"
          f"- important_relationships 中每条必须包含 object 和 relationship 两个字段\n"
          f"- 不知道的字段填空字符串，不要省略字段\n"
          f"- background 要有故事性，至少 30 字\n"
          f"- personality_traits 使用逗号分隔的词语\n"
          f"- 直接输出 JSON 数组，不加任何前缀后缀"
    )

    client = OpenAI(
        api_key=os.getenv('API_KEY'),
        base_url=os.getenv('BASE_URL', 'https://ark.cn-beijing.volces.com/api/v3')
    )
    model_name = os.getenv('MODEL', 'doubao-seed-2-0-lite-260215')

    try:
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": "你是一位专业的角色设计师，擅长为影视、游戏创作有深度的角色档案。"},
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
    """下载生成的脚本文件（优先 outputs/，其次 resources/）"""
    try:
        filepath = Path('outputs') / filename
        if not filepath.exists():
            filepath = Path('resources') / filename
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


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)


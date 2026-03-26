"""
ScriptAgent Web UI
使用 Flask 提供简单的 Web 界面
"""

from flask import Flask, request, jsonify, send_file, Response, stream_with_context
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


@app.route('/api/characters', methods=['GET'])
def get_all_characters():
    """获取所有角色"""
    try:
        char_file = Path('resources/characters_resource.json')
        with open(char_file, 'r', encoding='utf-8-sig') as f:
            characters = json.load(f)
        return jsonify({'success': True, 'data': characters})
    except Exception as e:
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

        char_file = Path('resources/characters_resource.json')
        with open(char_file, 'r', encoding='utf-8-sig') as f:
            characters = json.load(f)

        if any(c['name'] == name for c in characters):
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


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)


"""
ScriptAgent Web UI
使用 Flask 提供简单的 Web 界面
"""

from flask import Flask, render_template, request, jsonify, send_file
import json
import os
from pathlib import Path
from dotenv import load_dotenv
from src.resource_loader import ResourceLoader
from src.director_ai import DirectorAI
from src.json_generator import ScriptJSONGenerator

# 加载环境变量
load_dotenv()

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False  # 支持中文

# 初始化资源加载器
resource_loader = ResourceLoader()


@app.route('/')
def index():
    """主页"""
    return render_template('index.html')


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
                'positions': scene.valid_positions
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


@app.route('/api/characters/<style_tag>', methods=['GET'])
def get_characters(style_tag):
    """根据画风获取角色列表"""
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
    """生成剧本"""
    try:
        data = request.json
        
        # 获取参数
        character_ids = data.get('character_ids', [])
        scene_id = data.get('scene_id')
        plot_outline = data.get('plot_outline', '')
        
        # 验证配置
        validation = resource_loader.validate_configuration(character_ids, scene_id)
        
        if not validation['valid']:
            return jsonify({
                'success': False,
                'error': '配置验证失败',
                'details': validation['errors']
            }), 400
        
        # 获取资源对象
        characters = validation['characters']
        scene = validation['scene']
        
        # 检查 API Key
        api_key = os.getenv('DEEPSEEK_API_KEY')
        if not api_key:
            return jsonify({
                'success': False,
                'error': '未配置 DEEPSEEK_API_KEY，请在 .env 文件中设置'
            }), 500
        
        # 初始化导演AI
        director = DirectorAI(resource_loader)
        
        # 生成剧本
        ai_script = director.generate_script(
            characters=characters,
            scene=scene,
            plot_outline=plot_outline
        )
        
        # 检查AI输出
        if 'error' in ai_script:
            return jsonify({
                'success': False,
                'error': 'AI生成失败',
                'details': ai_script
            }), 500
        
        # 验证AI输出
        validation = director.validate_script_output(ai_script, scene)
        
        # 生成最终JSON
        generator = ScriptJSONGenerator(characters, scene)
        final_json = generator.generate_final_json(
            ai_script,
            plot_outline[:100] + "..."
        )
        
        # 验证规范
        spec_validation = ScriptJSONGenerator.validate_against_spec(final_json)
        
        if not spec_validation['valid']:
            return jsonify({
                'success': False,
                'error': 'JSON规范验证失败',
                'details': spec_validation['errors']
            }), 500
        
        # 保存文件
        output_dir = Path('outputs')
        output_dir.mkdir(exist_ok=True)
        
        import time
        filename = f"script_{int(time.time())}.json"
        filepath = output_dir / filename
        
        generator.export_to_file(final_json, str(filepath))
        
        return jsonify({
            'success': True,
            'data': {
                'script': final_json,
                'filename': filename,
                'warnings': validation.get('warnings', []) + spec_validation.get('warnings', [])
            }
        })
        
    except Exception as e:
        import traceback
        return jsonify({
            'success': False,
            'error': str(e),
            'traceback': traceback.format_exc()
        }), 500


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


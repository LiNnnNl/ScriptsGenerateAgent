"""
ScriptAgent Web UI
使用 Flask 提供简单的 Web 界面
"""

from flask import Flask, request, jsonify, send_file, Response, stream_with_context
from flask_cors import CORS
import json
import os
import time
from pathlib import Path
from dotenv import load_dotenv
from src.resource_loader import ResourceLoader
from src.director_ai import DirectorAI
from src.json_generator import ScriptJSONGenerator

# 加载环境变量
load_dotenv()

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False  # 支持中文

# 启用CORS（跨域资源共享）
CORS(app, resources={
    r"/api/*": {
        "origins": "*",  # 允许所有来源，生产环境应该指定具体域名
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
    """生成剧本（流式输出）"""
    
    def generate():
        try:
            data = request.json
            
            # 获取参数
            character_ids = data.get('character_ids', [])
            scene_id = data.get('scene_id')
            creative_idea = data.get('creative_idea', '').strip()
            
            # 使用用户的创作想法（如果提供）
            plot_outline = creative_idea if creative_idea else ''
            
            # 发送日志：开始验证
            yield json.dumps({
                'type': 'log',
                'level': 'info',
                'message': '📋 开始验证配置...'
            }) + '\n'
            
            # 验证配置
            validation = resource_loader.validate_configuration(character_ids, scene_id)
            
            if not validation['valid']:
                yield json.dumps({
                    'type': 'error',
                    'message': '配置验证失败',
                    'details': validation['errors']
                }) + '\n'
                return
            
            yield json.dumps({
                'type': 'log',
                'level': 'success',
                'message': '✅ 配置验证通过'
            }) + '\n'
            
            # 获取资源对象
            characters = validation['characters']
            scene = validation['scene']
            
            yield json.dumps({
                'type': 'log',
                'level': 'info',
                'message': f'🎭 已加载 {len(characters)} 个角色，场景: {scene.name}'
            }) + '\n'
            
            # 检查 API Key
            api_key = os.getenv('DEEPSEEK_API_KEY')
            if not api_key:
                yield json.dumps({
                    'type': 'error',
                    'message': '未配置 DEEPSEEK_API_KEY，请在 .env 文件中设置'
                }) + '\n'
                return
            
            # 初始化导演AI
            yield json.dumps({
                'type': 'log',
                'level': 'info',
                'message': '🤖 初始化导演 AI...'
            }) + '\n'
            
            director = DirectorAI(resource_loader)
            
            yield json.dumps({
                'type': 'log',
                'level': 'success',
                'message': '✅ 导演 AI 初始化完成'
            }) + '\n'
            
            # 构建上下文提示
            yield json.dumps({
                'type': 'log',
                'level': 'info',
                'message': '📝 正在构建 AI 上下文（角色性格、场景点位、可用动作）...'
            }) + '\n'
            
            if creative_idea:
                yield json.dumps({
                    'type': 'thinking',
                    'message': f'创作想法: "{creative_idea[:80]}..."'
                }) + '\n'
                yield json.dumps({
                    'type': 'log',
                    'level': 'info',
                    'message': f'💭 AI 将根据你的想法创作剧本'
                }) + '\n'
            else:
                yield json.dumps({
                    'type': 'thinking',
                    'message': f'准备让 {len(characters)} 个角色在 {scene.name} 中自由发挥...'
                }) + '\n'
                yield json.dumps({
                    'type': 'log',
                    'level': 'info',
                    'message': f'💭 AI 将完全自由创作剧情'
                }) + '\n'
            
            # 生成剧本
            yield json.dumps({
                'type': 'log',
                'level': 'info',
                'message': '🎬 正在调用 DeepSeek AI 生成剧本...'
            }) + '\n'
            
            yield json.dumps({
                'type': 'thinking',
                'message': 'AI 正在思考角色对白、走位和动作...'
            }) + '\n'
            
            ai_script = director.generate_script(
                characters=characters,
                scene=scene,
                plot_outline=plot_outline
            )
            
            # 检查AI输出
            if 'error' in ai_script:
                yield json.dumps({
                    'type': 'error',
                    'message': 'AI 生成失败',
                    'details': ai_script
                }) + '\n'
                return
            
            yield json.dumps({
                'type': 'log',
                'level': 'success',
                'message': '✅ AI 剧本生成完成'
            }) + '\n'
            
            # 验证AI输出
            yield json.dumps({
                'type': 'log',
                'level': 'info',
                'message': '🔍 验证 AI 输出（检查位置ID、动作ID、状态兼容性）...'
            }) + '\n'
            
            validation = director.validate_script_output(ai_script, scene)
            
            if not validation['valid']:
                for error in validation['errors']:
                    yield json.dumps({
                        'type': 'log',
                        'level': 'warning',
                        'message': f'⚠️  验证问题: {error}'
                    }) + '\n'
            else:
                yield json.dumps({
                    'type': 'log',
                    'level': 'success',
                    'message': '✅ AI 输出验证通过'
                }) + '\n'
            
            # 生成最终JSON
            yield json.dumps({
                'type': 'log',
                'level': 'info',
                'message': '🔄 转换为最终 JSON 格式...'
            }) + '\n'
            
            generator = ScriptJSONGenerator(characters, scene)
            
            # 生成剧情概述
            if creative_idea:
                plot_summary = creative_idea[:100] + ("..." if len(creative_idea) > 100 else "")
            else:
                plot_summary = f"{len(characters)}个角色在{scene.name}的场景"
            
            final_json = generator.generate_final_json(
                ai_script,
                plot_summary
            )
            
            # 验证规范
            yield json.dumps({
                'type': 'log',
                'level': 'info',
                'message': '📋 验证 JSON 规范符合性...'
            }) + '\n'
            
            spec_validation = ScriptJSONGenerator.validate_against_spec(final_json)
            
            if not spec_validation['valid']:
                yield json.dumps({
                    'type': 'error',
                    'message': 'JSON 规范验证失败',
                    'details': spec_validation['errors']
                }) + '\n'
                return
            
            yield json.dumps({
                'type': 'log',
                'level': 'success',
                'message': '✅ JSON 规范验证通过'
            }) + '\n'
            
            # 保存文件
            yield json.dumps({
                'type': 'log',
                'level': 'info',
                'message': '💾 保存文件...'
            }) + '\n'
            
            output_dir = Path('outputs')
            output_dir.mkdir(exist_ok=True)
            
            filename = f"script_{int(time.time())}.json"
            filepath = output_dir / filename
            
            generator.export_to_file(final_json, str(filepath))
            
            # 收集警告
            all_warnings = validation.get('warnings', []) + spec_validation.get('warnings', [])
            
            # 发送成功消息
            yield json.dumps({
                'type': 'success',
                'filename': filename,
                'warnings': all_warnings
            }) + '\n'
            
        except Exception as e:
            import traceback
            yield json.dumps({
                'type': 'error',
                'message': str(e),
                'details': traceback.format_exc()
            }) + '\n'
    
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


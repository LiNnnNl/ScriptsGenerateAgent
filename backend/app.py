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
            "gender": "未知",
            "ip": "自定义",
            "manufacturer": "用户创建",
            "background": description if description else f"用户自定义角色：{name}",
            "Faction": "未知",
            "personality_traits": description if description else "性格由AI自由发挥",
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
    """生成剧本（流式输出）"""
    
    def generate():
        try:
            data = request.json
            
            # 获取参数
            custom_characters_input = data.get('custom_characters', [])
            scene_id = data.get('scene_id')
            creative_idea = data.get('creative_idea', '').strip()

            # 使用用户的创作想法（如果提供）
            plot_outline = creative_idea if creative_idea else ''

            # 验证场景
            scene = resource_loader.get_scene_by_id(scene_id)
            if not scene:
                yield json.dumps({'type': 'error', 'message': f'场景不存在: {scene_id}'}) + '\n'
                return

            # 构建角色列表
            if custom_characters_input:
                characters = resource_loader.build_custom_characters(custom_characters_input)
                yield json.dumps({
                    'type': 'log',
                    'level': 'success',
                    'message': f'✅ 已构建 {len(characters)} 个自定义角色'
                }) + '\n'
            else:
                characters = []
                yield json.dumps({
                    'type': 'log',
                    'level': 'info',
                    'message': '💭 未指定角色，AI 将自由创作'
                }) + '\n'
            
            # 检查 API Key
            api_key = os.getenv('API_KEY')
            if not api_key:
                yield json.dumps({
                    'type': 'error',
                    'message': '未配置 API_KEY，请在 .env 文件中设置'
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
                    'message': f'创作想法: "{creative_idea[:80]}{"..." if len(creative_idea) > 80 else ""}"'
                }) + '\n'
                yield json.dumps({
                    'type': 'log',
                    'level': 'info',
                    'message': f'💭 AI 将根据你的想法创作剧本'
                }) + '\n'
            else:
                yield json.dumps({
                    'type': 'thinking',
                    'message': f'准备在 {scene.name} 中自由发挥...'
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
            elif characters:
                plot_summary = f"{len(characters)}个角色在{scene.name}的场景"
            else:
                plot_summary = f"AI自由创作：{scene.name}"
            
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

            # 提取剧本中出现的角色，生成 actors_profile.json
            actor_names = []
            seen = set()
            if isinstance(ai_script, list):
                for scene_obj in ai_script:
                    info = scene_obj.get('scene information', {})
                    for name in info.get('who', []):
                        if name and name not in seen:
                            seen.add(name)
                            actor_names.append(name)

            # 读取角色库原始数据
            char_file_raw = Path('resources/characters_resource.json')
            with open(char_file_raw, 'r', encoding='utf-8-sig') as f:
                all_chars_raw = json.load(f)
            char_map = {c['name']: c for c in all_chars_raw}

            # 自定义角色索引（按名称）
            custom_char_map = {
                (item.get('name') or '').strip(): item
                for item in custom_characters_input
                if (item.get('name') or '').strip()
            }

            actors_profile = []
            for name in actor_names:
                if name in char_map:
                    actors_profile.append(char_map[name])
                elif name in custom_char_map:
                    item = custom_char_map[name]
                    desc = (item.get('description') or '').strip()
                    actors_profile.append({
                        "name": name,
                        "gender": "未知",
                        "ip": "自定义",
                        "manufacturer": "用户创建",
                        "background": desc if desc else f"用户自定义角色：{name}",
                        "Faction": "未知",
                        "personality_traits": desc if desc else "性格由AI自由发挥",
                        "role_position": "未知",
                        "important_relationships": []
                    })
                else:
                    actors_profile.append({
                        "name": name,
                        "gender": "未知",
                        "ip": "AI创作",
                        "manufacturer": "AI生成",
                        "background": f"AI自由创作角色：{name}",
                        "Faction": "未知",
                        "personality_traits": "由AI自由发挥",
                        "role_position": "未知",
                        "important_relationships": []
                    })

            actors_profile_filename = f"actors_profile_{int(time.time())}.json"
            actors_filepath = output_dir / actors_profile_filename
            with open(actors_filepath, 'w', encoding='utf-8') as f:
                json.dump(actors_profile, f, ensure_ascii=False, indent=2)

            yield json.dumps({
                'type': 'log',
                'level': 'success',
                'message': f'✅ 已生成角色档案：{len(actors_profile)} 位演员'
            }) + '\n'

            # 收集警告
            all_warnings = validation.get('warnings', []) + spec_validation.get('warnings', [])

            # 发送成功消息
            yield json.dumps({
                'type': 'success',
                'filename': filename,
                'actors_profile_filename': actors_profile_filename,
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


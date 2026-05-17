"""
直接运行 AutoGen Pipeline 脚本
用法: python run_pipeline.py [--scene_id XXX] [--characters 张三,李四] [--creative_idea "剧情..."]
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
from src.resource_loader import ResourceLoader
from src.autogen_bridge import AutoGenStreamBridge
from src.autogen_pipeline import run_autogen_pipeline

load_dotenv()


def main():
    parser = argparse.ArgumentParser(description="ScriptAgent Pipeline Runner")
    parser.add_argument('--scene_id', type=str, default='scene_school_rooftop')
    parser.add_argument('--characters', type=str, default='',
                        help='格式: 名称1:描述1,名称2:描述2')
    parser.add_argument('--creative_idea', type=str, default='关于信任与成长的故事')
    parser.add_argument('--style', type=str, default='崩坏：星穹铁道')
    parser.add_argument('--output_dir', type=str, default='outputs')
    parser.add_argument('--enable_cinematography', action='store_true', default=False)
    
    args = parser.parse_args()
    
    # 解析角色
    custom_characters = []
    if args.characters:
        for char_str in args.characters.split(','):
            parts = char_str.split(':', 1)
            name = parts[0].strip()
            desc = parts[1].strip() if len(parts) > 1 else ''
            if name:
                custom_characters.append({
                    'name': name,
                    'description': desc,
                    'gender': '未知',
                    'background': desc,
                    'personality_traits': desc
                })
    
    request_data = {
        'custom_characters': custom_characters,
        'scene_id': args.scene_id,
        'creative_idea': args.creative_idea,
        'style': args.style,
        'enable_cinematography': args.enable_cinematography,
    }
    
    print("=" * 60)
    print("ScriptAgent - 全流程剧本生成")
    print("=" * 60)
    print(f"\n场景: {args.scene_id}")
    print(f"画风: {args.style}")
    print(f"角色: {args.characters or '由AI自由创作'}")
    print(f"创意: {args.creative_idea}")
    print(f"摄影: {'开启' if args.enable_cinematography else '关闭'}")
    print()
    
    resource_loader = ResourceLoader()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(exist_ok=True)
    
    print("开始生成剧本...\n")
    
    # 实时输出事件
    success_file = None
    actors_file = None
    position_plan_file = None
    position_detail_file = None
    
    bridge = AutoGenStreamBridge()
    bridge.run_in_thread(run_autogen_pipeline(bridge, resource_loader, request_data))
    
    for line in bridge.flask_generator():
        if line.strip():
            try:
                event = json.loads(line)
            except:
                print(line.rstrip())
                continue
            
            msg_type = event.get('type')
            if msg_type == 'thinking_chunk':
                # 流式思考输出，实时显示
                text = event.get('text', '')
                if text:
                    print(text, end='', flush=True)
            elif msg_type == 'thinking_done':
                print()  # 换行
            elif msg_type == 'log':
                level = event.get('level', 'info')
                message = event.get('message', '')
                prefix = {'info': '📋', 'success': '✅', 'warning': '⚠️', 'error': '❌'}.get(level, '📋')
                print(f"{prefix} {message}")
            elif msg_type == 'success':
                success_file = event.get('filename')
                actors_file = event.get('actors_profile_filename')
                position_plan_file = event.get('position_plan_filename')
                position_detail_file = event.get('position_detail_filename')
                print(f"\n{'='*60}")
                print(f"✅ 剧本生成成功!")
                print(f"📄 剧本: {success_file}")
                print(f"👥 演员档案: {actors_file}")
                if position_plan_file:
                    print(f"📍 位置规划: {position_plan_file}")
                if position_detail_file:
                    print(f"📍 位置详情: {position_detail_file}")
                print(f"{'='*60}")
            elif msg_type == 'error':
                print(f"\n❌ 生成失败: {event.get('message', '未知错误')}")
    
    # 复制到项目根目录的 output 文件夹
    if success_file:
        project_output = Path(__file__).parent.parent / 'output'
        project_output.mkdir(exist_ok=True)
        
        import shutil
        for src_name, dst_name in [
            (success_file, success_file),
            (actors_file, actors_file) if actors_file else (None, None),
            (position_plan_file, position_plan_file) if position_plan_file else (None, None),
            (position_detail_file, position_detail_file) if position_detail_file else (None, None),
        ]:
            if src_name:
                src = output_dir / src_name
                dst = project_output / src_name
                if src.exists():
                    shutil.copy(src, dst)
                    print(f"📁 已复制到: {dst}")


if __name__ == "__main__":
    main()

"""
ScriptAgent主程序
提供命令行接口和完整的剧本生成流程
"""

import argparse
import json
import sys
from pathlib import Path
from dotenv import load_dotenv
from src.resource_loader import ResourceLoader
from src.director_ai import DirectorAI
from src.json_generator import ScriptJSONGenerator

# 加载环境变量
load_dotenv()


def interactive_mode(resource_loader: ResourceLoader):
    """交互式模式 - 引导用户一步步配置"""
    
    print("\n" + "="*60)
    print("欢迎使用 ScriptAgent - AI剧本生成系统")
    print("="*60 + "\n")
    
    # 显示资源摘要
    print(resource_loader.get_resource_summary())
    print("\n" + "="*60 + "\n")
    
    # 步骤1: 选择画风
    print("【步骤1】选择画风")
    styles = resource_loader.get_available_styles()
    for idx, style in enumerate(styles, 1):
        print(f"{idx}. {style}")
    
    while True:
        try:
            choice = int(input("\n请选择画风编号: "))
            if 1 <= choice <= len(styles):
                selected_style = styles[choice - 1]
                break
            else:
                print("无效选择，请重新输入")
        except ValueError:
            print("请输入数字")
    
    print(f"\n✓ 已选择画风: {selected_style}\n")
    
    # 步骤2: 选择场景
    print("【步骤2】选择场景")
    scenes = resource_loader.get_scenes_by_style(selected_style)
    for idx, scene in enumerate(scenes, 1):
        print(f"{idx}. {scene.name} - {scene.description}")
    
    while True:
        try:
            choice = int(input("\n请选择场景编号: "))
            if 1 <= choice <= len(scenes):
                selected_scene = scenes[choice - 1]
                break
            else:
                print("无效选择，请重新输入")
        except ValueError:
            print("请输入数字")
    
    print(f"\n✓ 已选择场景: {selected_scene.name}\n")
    
    # 显示场景点位
    print("场景可用点位:")
    for pos in selected_scene.valid_positions:
        sittable = " [可坐]" if pos.get('is_sittable', False) else ""
        print(f"  - {pos['id']}{sittable}: {pos['description']}")
    print()
    
    # 步骤3: 选择角色
    print("【步骤3】选择角色（可多选）")
    characters = resource_loader.get_characters_by_style(selected_style)
    for idx, char in enumerate(characters, 1):
        print(f"{idx}. {char.name} - {char.description}")
        print(f"   性格: {char.personality}")
    
    selected_chars = []
    while True:
        choice_str = input("\n请输入角色编号（多个用逗号分隔，如: 1,2,3）: ")
        try:
            choices = [int(c.strip()) for c in choice_str.split(',')]
            if all(1 <= c <= len(characters) for c in choices):
                selected_chars = [characters[c-1] for c in choices]
                break
            else:
                print("部分编号无效，请重新输入")
        except ValueError:
            print("请输入有效的数字（用逗号分隔）")
    
    print(f"\n✓ 已选择角色: {', '.join([c.name for c in selected_chars])}\n")
    
    # 步骤4: 输入创作想法（可选）
    print("【步骤4】输入创作想法（可选）")
    print("请输入剧情主题、走向或想法（直接回车跳过，由AI自由创作）:")
    print("例如：关于信任与背叛的故事 / 两人从争吵到和解 / 紧张压抑的氛围\n")
    
    creative_idea = input("> ").strip()
    
    if creative_idea:
        print(f"\n✓ 创作想法已录入，AI 将根据你的想法创作剧本\n")
    else:
        print(f"\n✓ 将由 AI 完全自由创作剧情\n")
    
    # 验证配置
    validation = resource_loader.validate_configuration(
        [c.id for c in selected_chars],
        selected_scene.id
    )
    
    if not validation["valid"]:
        print("\n❌ 配置验证失败:")
        for error in validation["errors"]:
            print(f"  - {error}")
        return None
    
    print("\n✓ 配置验证通过\n")
    
    return {
        "style": selected_style,
        "scene": selected_scene,
        "characters": selected_chars,
        "creative_idea": creative_idea
    }


def generate_script(config: dict, api_key: str, output_file: str, base_url: str = None):
    """生成剧本并保存"""
    
    print("="*60)
    print("开始生成剧本...")
    print("="*60 + "\n")
    
    # 初始化资源加载器
    resource_loader = ResourceLoader()
    
    # 初始化导演AI
    print("正在初始化导演AI...")
    director = DirectorAI(resource_loader, api_key=api_key, base_url=base_url)
    
    # 生成剧本
    print("正在生成剧本（这可能需要一些时间）...\n")
    ai_script = director.generate_script(
        characters=config["characters"],
        scene=config["scene"],
        plot_outline=config.get("creative_idea", "")
    )
    
    # 检查是否有错误
    if "error" in ai_script:
        print(f"\n❌ AI生成失败:")
        print(f"  错误: {ai_script['error']}")
        if "raw_response" in ai_script:
            print(f"\n原始响应:\n{ai_script['raw_response']}")
        return False
    
    # 验证AI输出
    print("正在验证AI输出...")
    validation = director.validate_script_output(ai_script, config["scene"])
    
    if not validation["valid"]:
        print("\n⚠️  AI输出验证发现问题:")
        for error in validation["errors"]:
            print(f"  - {error}")
        print("\n继续生成最终JSON...")
    
    if validation["warnings"]:
        print("\n⚠️  警告:")
        for warning in validation["warnings"]:
            print(f"  - {warning}")
    
    # 生成最终JSON
    print("\n正在生成最终JSON...")
    generator = ScriptJSONGenerator(config["characters"], config["scene"])
    
    # 生成剧情概述
    creative_idea = config.get("creative_idea", "")
    if creative_idea:
        plot_summary = creative_idea[:100] + ("..." if len(creative_idea) > 100 else "")
    else:
        plot_summary = f"{len(config['characters'])}个角色在{config['scene'].name}的场景"
    
    final_json = generator.generate_final_json(
        ai_script, 
        plot_summary
    )
    
    # 验证JSON规范
    print("正在验证JSON规范...")
    spec_validation = ScriptJSONGenerator.validate_against_spec(final_json)
    
    if not spec_validation["valid"]:
        print("\n❌ JSON规范验证失败:")
        for error in spec_validation["errors"]:
            print(f"  - {error}")
        return False
    
    if spec_validation["warnings"]:
        print("\n⚠️  JSON规范警告:")
        for warning in spec_validation["warnings"]:
            print(f"  - {warning}")
    
    # 保存文件
    print(f"\n正在保存到 {output_file}...")
    generator.export_to_file(final_json, output_file)
    
    print("\n" + "="*60)
    print(f"✓ 剧本生成成功！")
    print(f"✓ 已保存到: {output_file}")
    print("="*60 + "\n")
    
    return True


def main():
    parser = argparse.ArgumentParser(description="ScriptAgent - AI剧本生成系统")
    
    parser.add_argument(
        "--mode",
        choices=["interactive", "config"],
        default="interactive",
        help="运行模式: interactive(交互式) 或 config(配置文件)"
    )
    
    parser.add_argument(
        "--config",
        type=str,
        help="配置文件路径 (JSON格式)"
    )
    
    parser.add_argument(
        "--api-key",
        type=str,
        help="API Key (也可在 .env 文件中设置 API_KEY)"
    )

    parser.add_argument(
        "--base-url",
        type=str,
        help="API Base URL (也可在 .env 文件中设置 BASE_URL)"
    )
    
    parser.add_argument(
        "--output",
        type=str,
        default="output_script.json",
        help="输出文件路径"
    )
    
    args = parser.parse_args()
    
    # 检查API Key
    import os
    api_key = args.api_key or os.getenv("API_KEY")
    base_url = args.base_url or os.getenv("BASE_URL")

    if not api_key:
        print("❌ 错误: 未提供API Key")
        print("请在 .env 文件中设置 API_KEY 或使用 --api-key 参数")
        sys.exit(1)
    
    # 初始化资源加载器
    try:
        resource_loader = ResourceLoader()
    except Exception as e:
        print(f"❌ 资源加载失败: {e}")
        sys.exit(1)
    
    # 根据模式运行
    if args.mode == "interactive":
        # 交互式模式
        config = interactive_mode(resource_loader)
        if not config:
            print("\n❌ 配置失败，程序退出")
            sys.exit(1)
        
        # 生成剧本
        success = generate_script(config, api_key, args.output, base_url)
        sys.exit(0 if success else 1)
    
    elif args.mode == "config":
        # 配置文件模式
        if not args.config:
            print("❌ 错误: 配置文件模式需要 --config 参数")
            sys.exit(1)
        
        # 读取配置文件
        try:
            with open(args.config, 'r', encoding='utf-8') as f:
                config_data = json.load(f)
        except Exception as e:
            print(f"❌ 配置文件读取失败: {e}")
            sys.exit(1)
        
        # 解析配置
        character_ids = config_data.get("character_ids", [])
        scene_id = config_data.get("scene_id")
        creative_idea = config_data.get("creative_idea", "")
        
        # 获取资源对象
        characters = [resource_loader.get_character_by_id(cid) for cid in character_ids]
        characters = [c for c in characters if c]  # 过滤None
        
        scene = resource_loader.get_scene_by_id(scene_id)
        
        if not scene or not characters:
            print("❌ 错误: 配置文件中的角色或场景ID无效")
            sys.exit(1)
        
        # 验证配置
        validation = resource_loader.validate_configuration(character_ids, scene_id)
        if not validation["valid"]:
            print("\n❌ 配置验证失败:")
            for error in validation["errors"]:
                print(f"  - {error}")
            sys.exit(1)
        
        config = {
            "characters": characters,
            "scene": scene,
            "creative_idea": creative_idea
        }
        
        # 生成剧本
        success = generate_script(config, api_key, args.output, base_url)
        sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()


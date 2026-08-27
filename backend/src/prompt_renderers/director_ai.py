"""Render legacy DirectorAI prompts from pure prompt files."""

from typing import List

from ..prompt_files.stash.director_ai_context_stash import director_ai_context_prompt
from ..prompt_files.stash.director_ai_generate_user_stash import director_ai_generate_user_prompt
from ..prompt_utils import render_prompt
from ..resource_loader import Character, ResourceLoader, Scene
from .action_info import render_action_info


def build_director_ai_context_prompt(
    resource_loader: ResourceLoader,
    characters: List[Character],
    scene: Scene,
    plot_outline: str,
    required_character_count: int = 0,
) -> str:
    shot_types = resource_loader.shot_types
    shot_types_str = "、".join(f'"{item}"' for item in shot_types) if shot_types else '"全景"、"中景"、"中近景"、"近景"、"仰拍镜头"、"俯拍镜头"'

    total_count = required_character_count if required_character_count > 0 else len(characters)
    if total_count == 0:
        total_count = 2
    extra_count = max(0, total_count - len(characters))

    char_info = "## 角色配置\n\n"
    if characters:
        char_info += f"本场景共需要 **{total_count}** 位角色"
        if extra_count == 0:
            char_info += f"，以下 {len(characters)} 位角色已全部指定，**不得出现任何其他角色**。\n\n"
        else:
            char_info += f"，其中 {len(characters)} 位已指定，另需 AI 自行创作 **{extra_count}** 位新角色。\n\n"
        char_info += "### 已指定角色\n\n"
        for char in characters:
            char_info += f"#### {char.name}\n"
            char_info += f"- gameobject_name: {char.gameobject_name}\n"
            char_info += f"- 背景: {char.description}\n"
            char_info += f"- 性格: {char.personality}\n\n"
    else:
        char_info += f"本场景共需要 **{total_count}** 位角色，全部由 AI 自由创作。\n\n"

    scene_info = "## 场景信息\n\n"
    scene_info += f"### {scene.name} (ID: {scene.id})\n"
    scene_info += f"- 描述: {scene.description}\n\n"
    scene_info += "#### 可用点位:\n"
    for pos in scene.valid_positions:
        sittable = " [可坐]" if pos.get('is_sittable', False) else ""
        group_tag = f" [组{pos['camera_group']}]" if pos.get('camera_group') else ""
        scene_info += f"- **{pos['id']}**{sittable}{group_tag}: {pos['description']}\n"
    if scene.camera_groups:
        scene_info += "\n#### 镜头分组（同一镜头只能拍摄同组点位内的角色）:\n"
        for group in scene.camera_groups:
            scene_info += f"- **{group['id']}组 - {group['name']}**: {', '.join(group['position_ids'])}\n"

    action_info = render_action_info(resource_loader)

    if plot_outline and plot_outline.strip():
        plot_info = f"""## 创作要求

用户的创作想法：
{plot_outline}

请根据以上创作想法，结合角色性格、场景环境和可用动作，创作一段剧本。

**注意：**
- 充分利用角色的性格特点，让对白符合人物设定
- 利用场景的空间点位，设计合理的走位和互动
- 选择合适的动作，让表演生动有张力
- 围绕用户的创作想法展开，但可以适当发挥
- 每个角色都要有适当的戏份和表现机会

"""
    else:
        plot_info = """## 剧情要求

请根据以上角色性格、场景环境和可用动作，自由创作一段剧情。

**要求：**
- 充分利用角色的性格特点，让对白符合人物设定
- 利用场景的空间点位，设计合理的走位和互动
- 选择合适的动作，让表演生动有张力
- 剧情要有冲突、转折或情感变化
- 每个角色都要有适当的戏份和表现机会

"""

    if characters and extra_count == 0:
        char_count_rule = f"1. **角色数量（最高优先级）**: 剧本中出现的角色总数必须恰好为 **{total_count}** 位，即上文指定的 {', '.join(c.name for c in characters)}，**绝对不得引入任何其他角色**。"
    elif characters and extra_count > 0:
        char_count_rule = f"1. **角色数量（最高优先级）**: 剧本中出现的角色总数必须恰好为 **{total_count}** 位：指定角色 {', '.join(c.name for c in characters)} 必须全部出现，另外还需自由创作 {extra_count} 位新角色。"
    else:
        char_count_rule = f"1. **角色数量（最高优先级）**: 剧本中出现的角色总数必须恰好为 **{total_count}** 位，全部由 AI 自由创作，但数量严格固定。"

    return render_prompt(
        director_ai_context_prompt,
        char_info=char_info,
        scene_info=scene_info,
        action_info=action_info,
        plot_info=plot_info,
        char_count_rule=char_count_rule,
        shot_types_str=shot_types_str,
    )

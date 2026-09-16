"""Render AutoGen agent prompts from pure prompt files."""

from typing import Dict, List, Optional

from ..prompt_files.fixed_dialogues import fixed_dialogues_prompt
from ..prompt_files.user_constraints import user_constraints_prompt
from ..prompt_utils import render_prompt
from ..resource_loader import Character, ResourceLoader, Scene
from ..script_style_skill import build_script_style_context
from ..agents.character_voice import build_system_message as _build_character_voice_system_message
from ..agents.character_bios import build_system_message as _build_character_bios_system_message
from ..agents.concept import build_system_message as _build_concept_system_message
from ..agents.concept_pitch import build_system_message as _build_concept_pitch_system_message
from ..agents.contract_repair import build_system_message as _build_contract_repair_system_message
from ..agents.critic import build_system_message as _build_critic_system_message
from ..agents.dialogue import build_system_message as _build_dialogue_system_message
from ..agents.director import build_system_message as _build_director_package_system_message
from ..agents.director_word import build_system_message as _build_director_word_package_system_message
from ..agents.meeting_summary import build_system_message as _build_meeting_summary_system_message
from ..agents.narrative_arch import build_system_message as _build_narrative_arch_system_message
from ..agents.position import build_system_message as _build_position_system_message
from ..agents.revision import build_system_message as _build_revision_system_message
from ..agents.story_ir import build_system_message as _build_story_ir_system_message
from ..agents.shot_plan import build_system_message as _build_shot_plan_system_message
from ..agents.synopsis import build_system_message as _build_synopsis_system_message
from ..agents.title import build_system_message as build_title_system_message
from ..agents.treatment import build_system_message as _build_treatment_system_message
from ..agents.validation import build_system_message as _build_validation_system_message
from .action_info import render_action_info as _render_action_info


def _build_stage_common_context(
    characters: List[Character],
    scene: Scene,
    required_character_count: int = 0,
) -> str:
    total_count = required_character_count if required_character_count > 0 else len(characters)
    if total_count == 0:
        total_count = 2
    extra_count = max(0, total_count - len(characters))

    lines = [
        f"场景：{scene.name} (ID: {scene.id})",
        f"场景描述：{scene.description}",
        f"角色总数要求：{total_count}",
    ]
    if characters:
        lines.append("已指定角色（必须保留完整姓名，包括括号内编号，不得缩写）：")
        for char in characters:
            lines.append(f"- {char.name}｜背景：{char.description}｜性格：{char.personality}")
        if extra_count > 0:
            lines.append(f"还需新增角色数量：{extra_count}")
    else:
        lines.append("未指定角色，全部由 AI 自由创作。")
    return "\n".join(lines)


def _append_user_constraints(
    user_constraints: Optional[List[str]] = None,
    fixed_dialogues: Optional[List[dict]] = None,
) -> str:
    parts = []
    if user_constraints:
        constraints = "\n".join(f"- {item}" for item in user_constraints)
        parts.append(render_prompt(user_constraints_prompt, constraints=constraints).strip())
    if fixed_dialogues:
        dialogues = "\n".join(f"- **{item['speaker']}**：{item['content']}" for item in fixed_dialogues)
        parts.append(render_prompt(fixed_dialogues_prompt, dialogues=dialogues).strip())
    return "\n".join(parts) if parts else ""


def _render_character_info(characters: List[Character], total_count: int, extra_count: int) -> str:
    char_info = "## 角色配置\n\n"
    if characters:
        char_info += f"本场景共需要 **{total_count}** 位角色"
        if extra_count == 0:
            char_info += f"，以下 {len(characters)} 位角色已全部指定，**不得出现任何其他角色**。\n\n"
        else:
            char_info += f"，其中 {len(characters)} 位已指定，另需 AI 自行创作 **{extra_count}** 位新角色。\n\n"
        char_info += "### 已指定角色\n\n"
        char_info += ('JSON 中 who、speaker 和所有 character 引用必须逐字使用以下完整姓名，'
                      '包括空格和括号内编号；不得缩写或用 gameobject_name 代替。'
                      '对白 content 中的自然称呼不受此限制，不要为补全姓名改写对白。\n\n')
        for char in characters:
            char_info += f"#### {char.name}\n"
            char_info += f"- gameobject_name: {char.gameobject_name}\n"
            char_info += f"- 背景: {char.description}\n"
            char_info += f"- 性格: {char.personality}\n\n"
    else:
        char_info += f"本场景共需要 **{total_count}** 位角色，全部由 AI 自由创作。\n\n"
    return char_info


def _render_scene_info(
    scene: Scene,
    resource_loader: ResourceLoader,
    act_count: int,
    act_scene_map: Optional[Dict[int, Scene]] = None,
) -> str:
    region_note = (
        "> **重要说明**：区域内的锚点（anchors）和场景标记（scene_markers）是场景中"
        "**标志性物体（雕像、树木、石柱等）的坐标**，**不是角色可以站立的位置**。"
        "编剧只需根据戏剧意图为每个站位选择合适的区域名称；"
        "角色的具体坐标由摄影指导智能体自动计算。\n\n"
    )

    def render_regions(sc: Scene) -> str:
        raw = resource_loader.load_scene_info(sc.id)
        if not (raw and raw.get("regions")):
            return ""
        text = ""
        for region in raw["regions"]:
            markers = [item["name"] for item in region.get("scene_markers", [])]
            text += f"**{region['name']}**\n"
            text += f"- {region['description']}\n"
            text += f"- 区域内标志性物体：{'、'.join(markers) if markers else '无'}\n\n"
        return text

    if act_scene_map:
        scene_info = "## 场景信息（多场景）\n\n本剧本各幕发生在不同场景，请严格按下表安排：\n\n"
        for index in range(act_count):
            sc = act_scene_map.get(index) or scene
            scene_info += f"- **第 {index + 1} 幕** → {sc.name} (ID: {sc.id})：{sc.description}\n"
        scene_info += "\n### 各场景可用区域（Regions）\n\n" + region_note
        rendered_ids = set()
        for index in range(act_count):
            sc = act_scene_map.get(index) or scene
            if sc.id in rendered_ids:
                continue
            rendered_ids.add(sc.id)
            region_text = render_regions(sc)
            if region_text:
                scene_info += f"#### {sc.name} (ID: {sc.id})\n\n{region_text}"
        return scene_info

    scene_info = f"## 场景信息\n\n### {scene.name} (ID: {scene.id})\n"
    scene_info += f"- 描述: {scene.description}\n\n"
    region_text = render_regions(scene)
    if region_text:
        scene_info += "### 可用区域（Regions）\n\n" + region_note + region_text
    return scene_info


def build_director_system_message(
    characters: List[Character],
    scene: Scene,
    resource_loader: ResourceLoader,
    required_character_count: int = 0,
    act_count: int = 3,
    user_constraints: Optional[List[str]] = None,
    direct_mode: bool = False,
    act_scene_map: Optional[Dict[int, Scene]] = None,
    script_style_guide: Optional[str] = None,
) -> str:
    total_count = required_character_count if required_character_count > 0 else len(characters)
    if total_count == 0:
        total_count = 2
    extra_count = max(0, total_count - len(characters))

    shot_types = resource_loader.shot_types
    shot_types_str = "、".join(f'"{item}"' for item in shot_types) if shot_types else '"全景"、"中景"、"中近景"、"近景"、"仰拍镜头"、"俯拍镜头"'

    if characters and extra_count == 0:
        char_count_rule = f"1. **角色数量（最高优先级）**: 剧本中出现的角色总数必须恰好为 **{total_count}** 位，即 {', '.join(c.name for c in characters)}，**绝对不得引入任何其他角色**。"
    elif characters and extra_count > 0:
        char_count_rule = f"1. **角色数量（最高优先级）**: 剧本中出现的角色总数必须恰好为 **{total_count}** 位：指定角色 {', '.join(c.name for c in characters)} 必须全部出现，另外还需自由创作 {extra_count} 位新角色。"
    else:
        char_count_rule = f"1. **角色数量（最高优先级）**: 剧本中出现的角色总数必须恰好为 **{total_count}** 位，全部由 AI 自由创作，但数量严格固定。"

    act_count_rule = f"0. **幕数（最高优先级）**: 输出 JSON 数组必须恰好包含 **{act_count}** 个场景对象（即 {act_count} 幕），不多不少。"
    if act_scene_map:
        act_scene_lines = "；".join(f"第 {index + 1} 幕 = {(act_scene_map.get(index) or scene).name}" for index in range(act_count))
        act_count_rule += (
            "\n   **每幕场景（多场景，最高优先级）**：各幕剧情必须发生在指定场景，"
            "该幕站位只能选所属场景「可用区域」里的区域（场景标识由系统按幕自动写入 `where`，你无需也不要自行填写）。"
            f"幕-场景对应：{act_scene_lines}。"
        )

    return _build_director_package_system_message(
        direct_mode=direct_mode,
        char_info=_render_character_info(characters, total_count, extra_count),
        scene_info=_render_scene_info(scene, resource_loader, act_count, act_scene_map),
        action_info=_render_action_info(resource_loader),
        act_count_rule=act_count_rule,
        char_count_rule=char_count_rule,
        shot_types_str=shot_types_str,
        user_constraints=(_append_user_constraints(user_constraints) + "\n") if user_constraints else "",
        video_style_guide=script_style_guide or build_script_style_context(),
    )


def build_director_word_system_message(
    characters: List[Character],
    scene: Scene,
    resource_loader: ResourceLoader,
    required_character_count: int = 0,
    act_count: int = 3,
    user_constraints: Optional[List[str]] = None,
    act_scene_map: Optional[Dict[int, Scene]] = None,
    script_style_guide: Optional[str] = None,
) -> str:
    total_count = required_character_count if required_character_count > 0 else len(characters)
    if total_count == 0:
        total_count = 2
    extra_count = max(0, total_count - len(characters))

    shot_types = resource_loader.shot_types
    shot_types_str = "、".join(f'"{item}"' for item in shot_types) if shot_types else '"全景"、"中景"、"中近景"、"近景"、"仰拍镜头"、"俯拍镜头"'

    if characters and extra_count == 0:
        char_count_rule = f"1. **角色数量（最高优先级）**: 剧本中出现的角色总数必须恰好为 **{total_count}** 位，即 {', '.join(c.name for c in characters)}，**绝对不得引入任何其他角色**。"
    elif characters and extra_count > 0:
        char_count_rule = f"1. **角色数量（最高优先级）**: 剧本中出现的角色总数必须恰好为 **{total_count}** 位：指定角色 {', '.join(c.name for c in characters)} 必须全部出现，另外还需自由创作 {extra_count} 位新角色。"
    else:
        char_count_rule = f"1. **角色数量（最高优先级）**: 剧本中出现的角色总数必须恰好为 **{total_count}** 位，全部由 AI 自由创作，但数量严格固定。"

    act_count_rule = f"0. **幕数（最高优先级）**: 输出 JSON 数组必须恰好包含 **{act_count}** 个场景对象（即 {act_count} 幕），不多不少。"
    if act_scene_map:
        act_scene_lines = "；".join(f"第 {index + 1} 幕 = {(act_scene_map.get(index) or scene).name}" for index in range(act_count))
        act_count_rule += (
            "\n   **每幕场景（多场景，最高优先级）**：各幕剧情必须发生在指定场景，"
            f"幕-场景对应：{act_scene_lines}。"
        )

    return _build_director_word_package_system_message(
        char_info=_render_character_info(characters, total_count, extra_count),
        scene_info=_render_scene_info(scene, resource_loader, act_count, act_scene_map),
        action_info=_render_action_info(resource_loader),
        act_count_rule=act_count_rule,
        char_count_rule=char_count_rule,
        shot_types_str=shot_types_str,
        user_constraints=(_append_user_constraints(user_constraints) + "\n") if user_constraints else "",
        video_style_guide=script_style_guide or build_script_style_context(),
    )


def build_concept_system_message(characters: List[Character], scene: Scene, required_character_count: int = 0) -> str:
    return _build_concept_system_message(_build_stage_common_context(characters, scene, required_character_count))


def build_synopsis_system_message() -> str:
    return _build_synopsis_system_message()


def build_character_bios_system_message() -> str:
    return _build_character_bios_system_message()


def build_treatment_system_message(act_count: int = 3, script_style_guide: Optional[str] = None) -> str:
    return _build_treatment_system_message(
        act_count,
        script_style_guide or build_script_style_context(),
    )


def build_meeting_summary_system_message(script_style_guide: Optional[str] = None) -> str:
    return _build_meeting_summary_system_message(script_style_guide or build_script_style_context())


def build_shot_plan_system_message() -> str:
    return _build_shot_plan_system_message()


def build_story_ir_system_message() -> str:
    return _build_story_ir_system_message()


def build_critic_system_message(
    user_constraints: Optional[List[str]] = None,
    fixed_dialogues: Optional[List[dict]] = None,
    script_style_guide: Optional[str] = None,
) -> str:
    return _build_critic_system_message(
        _append_user_constraints(user_constraints, fixed_dialogues),
        script_style_guide or build_script_style_context(),
    )


def build_dialogue_system_message(
    user_constraints: Optional[List[str]] = None,
    fixed_dialogues: Optional[List[dict]] = None,
    script_style_guide: Optional[str] = None,
) -> str:
    return _build_dialogue_system_message(
        _append_user_constraints(user_constraints, fixed_dialogues),
        script_style_guide or build_script_style_context(),
    )


def build_revision_system_message() -> str:
    return _build_revision_system_message()


def build_contract_repair_system_message() -> str:
    return _build_contract_repair_system_message()


def build_concept_pitch_system_message(
    characters: List[Character],
    scene: Scene,
    required_character_count: int = 0,
    script_style_guide: Optional[str] = None,
) -> str:
    return _build_concept_pitch_system_message(
        _build_stage_common_context(characters, scene, required_character_count),
        script_style_guide or build_script_style_context(),
    )


def build_character_voice_system_message(script_style_guide: Optional[str] = None) -> str:
    return _build_character_voice_system_message(script_style_guide or build_script_style_context())


def build_narrative_arch_system_message(script_style_guide: Optional[str] = None) -> str:
    return _build_narrative_arch_system_message(script_style_guide or build_script_style_context())


def build_validation_system_message() -> str:
    return _build_validation_system_message()


def build_position_agent_system_message(scene: Scene) -> str:
    positions_info = ""
    for pos in scene.valid_positions:
        sittable = " [可坐]" if pos.get('is_sittable', False) else ""
        group_tag = f" [镜头组{pos['camera_group']}]" if pos.get('camera_group') else ""
        positions_info += f"- **{pos['id']}**{sittable}{group_tag}: {pos['description']}\n"

    camera_groups_info = ""
    if scene.camera_groups:
        camera_groups_info = "\n#### 镜头分组（同一对白片段内所有角色必须属于同一镜头组）:\n"
        for group in scene.camera_groups:
            camera_groups_info += f"- **{group['id']}组 - {group['name']}**: {', '.join(group['position_ids'])}\n"

    return _build_position_system_message(
        scene_name=scene.name,
        scene_id=scene.id,
        positions_info=positions_info,
        camera_groups_info=camera_groups_info,
    )

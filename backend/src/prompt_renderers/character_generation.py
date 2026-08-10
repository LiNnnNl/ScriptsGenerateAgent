"""Render character generation prompts from pure prompt files."""

import json

from ..prompt_files.character_generation_system import character_generation_system_prompt
from ..prompt_files.character_generation_user import character_generation_user_prompt
from ..prompt_utils import render_prompt


def build_character_generation_prompt(
    character_count: int,
    scene_desc: str,
    creative_idea: str,
    char_instructions: str,
    model_instruction: str,
) -> str:
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
                {"object": "二郎神", "relationship": "宿命的对手 / 意志的考验者"},
            ],
            "gameobject_name": "WuKong_Model_01",
        }
    ], ensure_ascii=False, indent=2)
    return render_prompt(
        character_generation_user_prompt,
        character_count=character_count,
        scene_desc=scene_desc,
        creative_idea_block=f"创作灵感：{creative_idea}\n" if creative_idea else "",
        char_instructions=char_instructions,
        model_instruction=model_instruction,
        format_example=format_example,
    )

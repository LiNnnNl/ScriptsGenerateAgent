"""Script tone skill for optional emotional/story bias rules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List


@dataclass(frozen=True)
class ScriptTone:
    tone_id: str
    name: str
    guidance: str


_TONES: List[ScriptTone] = [
    ScriptTone(
        tone_id="warm",
        name="温情",
        guidance=(
            "以理解、陪伴、修复关系和细腻善意为主要情绪走向。冲突可以存在，"
            "但结尾或关键转折应留下温暖、释然、被接住的感觉；避免把温情写成空洞说教。"
        ),
    ),
    ScriptTone(
        tone_id="melancholy",
        name="伤感",
        guidance=(
            "以失落、遗憾、错过、孤独或无法挽回的情绪为主要底色。表达应克制具体，"
            "通过细节、沉默、动作和未说出口的话制造余味；避免直接堆砌悲伤形容词。"
        ),
    ),
    ScriptTone(
        tone_id="comedy",
        name="喜剧",
        guidance=(
            "以误会、反差、节奏错位、语言包袱或人物弱点制造轻松效果。喜剧应服务人物和情节，"
            "可以荒诞但不能让角色突然降智；结尾可轻快、反转或带一点讽刺。"
        ),
    ),
]

_TONE_BY_ID = {tone.tone_id: tone for tone in _TONES}


class ScriptToneSkill:
    """Optional deterministic tone selector controlled by user buttons."""

    def available_tones(self) -> List[Dict]:
        return [{"tone_id": tone.tone_id, "name": tone.name} for tone in _TONES]

    def resolve(self, requested_tone_id: str = "") -> Dict:
        tone_id = (requested_tone_id or "").strip()
        tone = _TONE_BY_ID.get(tone_id)
        if not tone:
            return {
                "explicit": False,
                "source": "not_selected",
                "selected": {
                    "tone_id": "unspecified",
                    "name": "未指定",
                    "guidance": "用户未指定剧情倾向；不要额外强加温情、伤感、喜剧等情绪模板，按剧本风格和创作灵感自然发展。",
                },
            }
        return {
            "explicit": True,
            "source": "user_button",
            "selected": {
                "tone_id": tone.tone_id,
                "name": tone.name,
                "guidance": tone.guidance,
            },
        }

    def render_context(self, requested_tone_id: str = "") -> str:
        result = self.resolve(requested_tone_id)
        selected = result["selected"]
        return (
            "## ScriptToneSkill 剧情倾向规则\n\n"
            "剧情倾向由用户按钮决定。所有 Agent 必须遵守本节固定规则，"
            "但不得让情绪倾向覆盖用户明确剧情、剧本风格和技术格式要求。\n\n"
            f"### 当前选择\n- tone_id: {selected['tone_id']}\n"
            f"- 剧情倾向: {selected['name']}\n"
            f"- 是否用户按钮指定: {'是' if result['explicit'] else '否'}\n\n"
            f"{selected['guidance']}"
        )


def build_script_tone_context(requested_tone_id: str = "") -> str:
    return ScriptToneSkill().render_context(requested_tone_id)

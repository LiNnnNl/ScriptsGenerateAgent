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
        guidance="""### 温情
- 温情来自人物在具体困境中的理解、陪伴、克制和行动，不来自旁白宣布“爱与希望”。
- 保留真实冲突和人物边界；善意可以缓解关系，但不得自动抹平伤害、责任与现实代价。
- 优先用微小动作、习惯细节、未说尽的话和前后呼应制造“被看见、被接住”的感觉。
- 结尾可以温暖、释然或带遗憾，只需留下情感连接，不强制和解、团圆或治愈所有问题。
- 禁止连续说教、集体表态、突然忏悔和为催泪而安排无因果牺牲。""",
    ),
    ScriptTone(
        tone_id="melancholy",
        name="伤感",
        guidance="""### 伤感
- 先明确失去、错过或无法两全的具体对象，让伤感建立在人物选择和现实限制上，而不是抽象悲伤气氛。
- 通过物件变化、动作停顿、空间距离、对话缺口和日常细节承载情绪；少用哭喊与悲伤形容词直接命名感受。
- 人物即使无力也应保有选择和尊严，禁止为了虐而让角色反复误会、拒绝沟通或突然遭遇无因果灾难。
- 保留明暗对比和短暂轻松，使低落有层次；不要让每个场景、每句对白都维持同一悲伤强度。
- 结尾可以遗憾、开放或克制告别，但必须回应核心关系或选择，留下余味而非仅靠死亡、分手制造冲击。""",
    ),
    ScriptTone(
        tone_id="comedy",
        name="喜剧",
        guidance="""### 喜剧
- 先建立人物认真追求的目标，再用信息差、性格缺陷、身份反差、规则碰撞或行动失控制造喜剧压力。
- 笑点必须同时暴露人物、推进局面或回收前文信息；删除只为抖机灵、与剧情无关的插科打诨。
- 使用铺垫—升级—兑现—回扣形成节奏，同一种包袱不要连续重复；重要笑点前后留出反应空间。
- 荒诞可以升级，但人物必须按自身认知认真行动，禁止突然降智、强行误会或凭空巧合解决问题。
- 可自嘲、讽刺处境和权力关系，避免把弱势身份、身体特征或创伤本身当作唯一笑点。
- 结尾可用最大回扣、局势反转或余波收束；喜剧不等于必须圆满，也不能消解用户要求保留的真实代价。""",
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
            "它只调节情绪表达、细节选择和收束余味，不改变内容形态。"
            "优先级固定为：用户明确剧情与硬约束 > 剧本风格 > 剧情倾向；"
            "不得让剧情倾向覆盖用户剧情、风格结构或技术格式要求。\n\n"
            f"### 当前选择\n- tone_id: {selected['tone_id']}\n"
            f"- 剧情倾向: {selected['name']}\n"
            f"- 是否用户按钮指定: {'是' if result['explicit'] else '否'}\n\n"
            f"{selected['guidance']}"
        )


def build_script_tone_context(requested_tone_id: str = "") -> str:
    return ScriptToneSkill().render_context(requested_tone_id)

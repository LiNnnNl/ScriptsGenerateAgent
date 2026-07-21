"""Script style skill for selecting genre/platform rules used by creative agents."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List


@dataclass(frozen=True)
class ScriptStyle:
    style_id: str
    name: str
    keywords: tuple[str, ...]
    guidance: str


_COMMON_ANTI_BIAS = """### 通用执行边界
- 优先级固定为：用户明确剧情与硬约束 > 用户选择的剧本风格 > 剧情倾向 > 模型默认偏好；低优先级规则不得篡改高优先级内容。
- 风格决定内容形态、节奏、冲突密度、语言方式与收束逻辑，不等于套用固定题材、角色职业或现成桥段。
- 不为追求“完整”擅自添加大团圆、善恶有报、全员和解或完整人生弧光；也不为追求“爆款”机械堆叠反转、热梗和情绪刺激。
- 每个关键事件必须能由人物目标、已知信息或前序行动解释；风格化不能以角色降智、因果断裂和设定冲突为代价。
- 示例只用于理解写法，不得复刻示例作品、人物、台词或情节。
- 无论选择何种风格，都必须遵守当前 Agent 要求的 JSON、字段、幕数、角色、场景与技术约束。"""


_STYLES: List[ScriptStyle] = [
    ScriptStyle(
        style_id="douyin_short",
        name="抖音短视频",
        keywords=("抖音", "短视频", "快手", "小红书", "短片", "爆款", "网感"),
        guidance="""### 抖音短视频
#### 创作目标
用最少铺垫建立一个清晰看点，让观众迅速理解“谁遇到了什么异常、冲突或情绪瞬间”，并在短篇幅内获得一次明确的情绪回报。

#### 执行规则
- 开篇第一个节拍直接呈现异常、欲望、冲突、悬念或强视觉动作；禁止从背景介绍和日常寒暄起笔。
- 只保留一条核心表达：一个冲突、一个观察或一种情绪。人物信息随行动自然暴露，不补写与当前看点无关的身世和成长线。
- 每个后续节拍必须升级局面、改变理解或兑现前文信息；删除重复解释、同义对白和无推进动作。
- “网感”来自准确口语、当下处境和节奏，不靠生硬热梗、网络黑话或夸张表情堆砌。
- 反转必须有前置线索且改变观众理解；若题材更适合治愈、讽刺、悬念或氛围收束，不得强行反转。
- 结尾在高信息点及时停止，可采用回扣、反差、开放、讽刺、余味或动作定格；不强制补齐完整人生结局。""",
    ),
    ScriptStyle(
        style_id="vertical_drama",
        name="竖屏短剧",
        keywords=("竖屏短剧", "短剧", "爽文", "甜宠", "虐渣", "逆袭", "打脸", "赘婿", "重生"),
        guidance="""### 竖屏短剧
#### 创作目标
围绕高压人物关系持续制造“目标受阻—局势升级—阶段兑现—新问题出现”的追更动力，让冲突直观、情绪明确，同时保证因果和人物动机成立。

#### 执行规则
- 开场尽快把主角置于不可回避的关系冲突或利益危机中，并明确主角当下要争取或守住什么。
- 每一幕至少完成一次有效变化：权力关系改变、秘密暴露、承诺被打破、目标推进或代价升级；不能只靠争吵制造假进展。
- 爽点必须由主角的选择、能力、准备或代价换来；禁止凭空出现证据、救兵、身份和反派降智。
- 人设可以鲜明，但主要角色必须有稳定欲望与行为逻辑；反派也需要可理解的利益驱动，避免纯工具人。
- 对白短、直接、有攻防和潜台词，重要信息分批揭示；减少旁白解释和角色互相复述已知事实。
- 幕尾钩子必须改变下一幕的问题，可用新证据、新身份、新代价、关系倒戈或未完成行动；禁止用无关误会和虚假惊吓硬切。
- 结局服从用户指定题材和剧情倾向。可以逆袭、和解、开放或付出代价，不默认所有竖屏短剧都必须大团圆。""",
    ),
    ScriptStyle(
        style_id="film",
        name="电影",
        keywords=("电影", "影片", "影院", "长片", "cinema", "film"),
        guidance="""### 电影
#### 创作目标
让人物选择推动一条完整而有层次的因果链，使外部事件、人物关系和主题表达在关键转折处彼此作用，最终形成可通过画面和行动感知的整体体验。

#### 执行规则
- 尽早建立主角欲望、现实阻力和潜在代价；后续转折必须迫使人物重新选择，而不只是增加事件数量。
- 人物弧光通过选择及其后果呈现。允许人物不成长、失败或坚持错误，但变化与不变化都必须有充分铺垫。
- 优先使用动作、空间、物件、停顿和关系变化传达信息；对白承担交锋与潜台词，不替画面解释主题。
- 场景必须至少完成叙事推进、关系变化、信息揭示或情绪转折之一；兼具多项更佳，纯氛围场景也要服务整体节奏。
- 节奏服从类型：喜剧、悬疑、动作、现实题材可以采用不同密度；“电影感”不等于缓慢、晦涩、长对白或故作留白。
- 意象和伏笔应在后文产生意义，不堆砌无回收的象征；主题从人物困境中生长，不由角色直接总结。
- 结局回应核心选择与主题，可以圆满、遗憾、悲剧或开放；开放结局仍需完成主要情感或主题问题的表达。""",
    ),
    ScriptStyle(
        style_id="standup",
        name="脱口秀",
        keywords=("脱口秀", "单口喜剧", "stand-up", "standup", "吐槽大会"),
        guidance="""### 脱口秀
#### 创作目标
围绕一个清晰、具体且带个人立场的核心观点组织单人表达，通过真实观察、预期偏差和连续升级的笑点，让观众既认出生活经验，也听见表达者独特的口吻。

#### 执行规则
- 开场迅速给出主题、态度或第一个有效笑点；禁止用“大家好，今天聊聊”之类空泛报幕消耗篇幅。
- 每个段落采用“具体事实或经历—个人解释—意外角度或包袱”的推进方式，抽象观点必须落到可感知细节。
- 笑点来源可以是夸张、类比、误导、回扣、自嘲和观察反差，但必须服务同一主题，不拼贴互不相关的段子。
- 整体按“铺垫—升级—兑现—回扣”形成节奏，逐步提高荒谬度或观点深度，并在后段回收前文关键词或意象。
- 避免连续使用相同句式和同一种反转，不要把每句话都写成孤立的一句话段子。
- 保持稳定的第一人称口吻和立场。允许冒犯表达者自己或权力关系，避免以弱势身份本身作为唯一笑点。
- 不虚构传统影视冲突线、反派和大团圆；需要结构化剧本字段时，仍以单一说话者的观点推进组织各节拍。
- 结尾优先使用最强回扣、观点翻转或简洁金句收束，不额外添加说教式升华。""",
    ),
]

_STYLE_BY_ID = {style.style_id: style for style in _STYLES}


class ScriptStyleSkill:
    """Deterministic skill used by agents to select style-specific writing rules."""

    def available_styles(self) -> List[Dict]:
        return [
            {
                "style_id": style.style_id,
                "name": style.name,
                "keywords": list(style.keywords),
            }
            for style in _STYLES
        ]

    def select_by_id(self, style_id: str) -> Dict:
        style = _STYLE_BY_ID.get((style_id or "").strip())
        if not style:
            return self._unspecified_result(source="idea_auto")
        return {
            "explicit": True,
            "source": "user_button",
            "selected": {
                "style_id": style.style_id,
                "name": style.name,
                "matched_keywords": [],
                "guidance": style.guidance,
            },
            "all_matches": [],
            "common_rules": _COMMON_ANTI_BIAS,
        }

    def resolve(self, user_request: str, requested_style_id: str = "") -> Dict:
        style_id = (requested_style_id or "").strip()
        if style_id and style_id != "auto":
            if style_id in _STYLE_BY_ID:
                return self.select_by_id(style_id)
        return self.detect(user_request, source="idea_auto")

    def _unspecified_result(self, source: str) -> Dict:
        return {
            "explicit": False,
            "source": source,
            "selected": {
                "style_id": "unspecified",
                "name": "未明确指定",
                "matched_keywords": [],
                "guidance": "用户未明确指定视频风格；使用通用反偏置规则，不默认写大团圆或善恶闭环。",
            },
            "all_matches": [],
            "common_rules": _COMMON_ANTI_BIAS,
        }

    def detect(self, user_request: str, source: str = "idea_auto") -> Dict:
        text = (user_request or "").lower()
        matches = []
        for style in _STYLES:
            matched_keywords = [kw for kw in style.keywords if kw.lower() in text]
            if matched_keywords:
                matches.append({
                    "style_id": style.style_id,
                    "name": style.name,
                    "matched_keywords": matched_keywords,
                    "guidance": style.guidance,
                })

        if matches:
            selected = matches[0]
            explicit = True
        else:
            return self._unspecified_result(source=source)

        return {
            "explicit": explicit,
            "source": source,
            "selected": selected,
            "all_matches": matches,
            "common_rules": _COMMON_ANTI_BIAS,
        }

    def render_context(self, user_request: str = "", requested_style_id: str = "") -> str:
        result = self.resolve(user_request, requested_style_id=requested_style_id)
        selected = result["selected"]
        source_label = "用户按钮选择" if result.get("source") == "user_button" else "创作灵感自动识别"
        return (
            "## ScriptStyleSkill 剧本风格规则\n\n"
            "剧本风格已由导演在流程入口统一识别并锁定。"
            "所有 Agent 必须直接遵守本节固定规则，不得重新判断、改写或切换为其他风格。\n\n"
            f"### 当前识别\n- style_id: {selected['style_id']}\n"
            f"- 风格: {selected['name']}\n"
            f"- 来源: {source_label}\n"
            f"- 是否用户明确指定: {'是' if result['explicit'] else '否'}\n\n"
            f"{selected['guidance']}\n\n"
            f"{result['common_rules']}"
        )


def build_script_style_context(user_request: str = "", requested_style_id: str = "") -> str:
    return ScriptStyleSkill().render_context(user_request, requested_style_id=requested_style_id)

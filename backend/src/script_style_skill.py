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


_COMMON_ANTI_BIAS = """### 通用反偏置规则
- 除非用户明确指定“竖屏短剧”“爽文”“甜宠圆满”“大团圆”等方向，否则不得自动把故事收束为善恶有报、所有人和解、主角圆满胜利。
- 用户指定抖音短视频时，优先快速情绪、反差、反转、讽刺或氛围，不补写完整人生弧光。
- 用户指定电影时，结局服务主题，可以圆满，也可以遗憾、开放、悲剧或克制留白。
- 用户指定脱口秀时，不要生成影视剧情闭环；重点是观点、段子、吐槽节奏和结尾金句。
- 即使是脱口秀，也必须遵守当前 Agent 要求的输出格式；不要因为“纯语言表达”而改成散文稿、台本段落或解释性文字。"""


_STYLES: List[ScriptStyle] = [
    ScriptStyle(
        style_id="douyin_short",
        name="抖音短视频",
        keywords=("抖音", "短视频", "快手", "小红书", "短片", "爆款", "网感"),
        guidance="""### 抖音短视频
抖音短视频节奏极速、开篇强钩子，弱化完整叙事与人物铺垫，以单一场景、单次冲突或反差反转制造瞬时情绪，追求快速共鸣与传播效果。结局灵活自由，可开放、可讽刺、可反转，不强制善恶闭环与大团圆，整体风格轻量化、碎片化、网感化。

常见爆款范式包括一秒反转情感短剧、职场尴尬瞬间、沉浸式治愈氛围感短片、街头真实吐槽、趣味反差日常等。主打单镜头快速出戏、看完即走，不需要完整故事闭环。""",
    ),
    ScriptStyle(
        style_id="vertical_drama",
        name="竖屏短剧",
        keywords=("竖屏短剧", "短剧", "爽文", "甜宠", "虐渣", "逆袭", "打脸", "赘婿", "重生"),
        guidance="""### 竖屏短剧
竖屏短剧主打“强冲突 + 集末留钩”，每集必有打脸、反转、情绪爆点，主线多为逆袭、甜宠、虐渣，结局以爽文式大团圆为主。题材适配大众爆款范式，涵盖重生复仇、豪门逆袭、赘婿翻盘、甜宠救赎、宅斗虐渣等主流类型。

典型剧情模式包括重生废柴千金逆袭复仇、神医赘婿绝地翻盘、穿越大佬虐渣宠妻、破镜重圆甜虐拉扯等。剧集全程高频穿插虐渣、撒糖、绝地反击、身份反转等高能名场面，剧情张力直白外放、情绪拉满，每集结尾预留悬念钩子，牢牢锁住观众追更欲。整体人设极致鲜明、正邪对立清晰，叙事通俗直白、无复杂留白，依托成熟商业化爽文逻辑，最终统一落地善恶有报、主角逆袭圆满的闭环结局，贴合大众即时情绪宣泄与连载追剧需求。""",
    ),
    ScriptStyle(
        style_id="film",
        name="电影",
        keywords=("电影", "影片", "影院", "长片", "cinema", "film"),
        guidance="""### 电影
电影叙事结构完整、节奏层层递进，注重人物弧光、氛围质感与主题深度，拥有细腻铺垫、转折与情绪留白。人物立体复杂，不刻意制造直白爽点，结局完全服务整体主题表达，不拘泥于圆满团圆，支持悲剧、开放式、遗憾式结局，风格克制、高级且具备现实反思性。

电影可以有《夏洛特烦恼》《西虹市首富》这类圆满治愈的喜剧收尾，也可以有《盗梦空间》式开放留白、《三傻大闹宝莱坞》式成长治愈升华，或现实题材的遗憾结局。不要为了观众观感强行缝合大团圆。""",
    ),
    ScriptStyle(
        style_id="standup",
        name="脱口秀",
        keywords=("脱口秀", "单口喜剧", "stand-up", "standup", "吐槽大会"),
        guidance="""### 脱口秀
脱口秀属于单人纯语言表达类内容，无剧情线、无人物表演冲突、无故事闭环需求，依靠个人视角、生活观察、犀利吐槽和密集段子输出观点。语言极具个人特色与口语张力，核心是情绪宣泄与价值共鸣，结尾以金句升华收束，不存在团圆、和解、结局闭环等影视叙事逻辑。

热门范式包括职场吐槽、生活扎心感悟、婚恋现实观点、普通人自嘲叙事，如柜员职场困境吐槽、中年生活感悟、普通人平凡日常解构等。全程靠观点密度和个人风格抓人，不靠剧情反转和结局圆满留观众。""",
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

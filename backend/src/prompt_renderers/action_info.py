"""Render a compact action catalogue for model prompts."""

import re
from collections import OrderedDict

from ..resource_loader import ACTION_POSTURE_CATEGORIES, ResourceLoader


_DURATION_NOTE_RE = re.compile(r"[（(]\s*时长[^）)]*[）)]")


def _compact_description(description: str) -> str:
    """Remove low-value metadata while retaining the action's semantic description."""
    text = _DURATION_NOTE_RE.sub("", str(description or ""))
    return " ".join(text.split()).strip(" ，,；;。") or "无描述"


def _display_description(description: str, max_chars: int = 72) -> str:
    text = description
    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip(" ，,；;。") + "…"
    return text


def render_action_info(resource_loader: ResourceLoader) -> str:
    """Render every action ID, grouping entries that share one description."""
    lines = [
        "## 可用动作库",
        "",
        "动作按执行前的角色姿态分类。只能从角色当前姿态对应的分类中选择动作ID；"
        "分类为空时不得编造动作。相同描述的动作会合并展示，但每个动作ID都可单独使用。",
        "",
    ]
    for state, category in ACTION_POSTURE_CATEGORIES:
        actions = resource_loader.get_actions_by_state(state)
        lines.append(f"### {category}（{state}，{len(actions)} 个动作）")
        if not actions:
            lines.extend(["- 暂无可用动作。", ""])
            continue

        grouped = OrderedDict()
        for action in actions:
            description = _compact_description(action.description)
            grouped.setdefault(description, []).append(action.action_id)

        for description, action_ids in grouped.items():
            displayed_description = _display_description(description)
            if len(action_ids) == 1:
                lines.append(f"- **{action_ids[0]}**: {displayed_description}")
            else:
                ids = "、".join(f"`{action_id}`" for action_id in action_ids)
                lines.append(f"- 动作ID：{ids}；共同描述：{displayed_description}")
        lines.append("")
    return "\n".join(lines)

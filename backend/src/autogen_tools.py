"""
AutoGen FunctionTool 包装层

将现有的验证函数提取为独立函数，并补充缺失的 camera_group 分组一致性检查。
这些函数既可以作为 AutoGen FunctionTool 使用，也可以直接调用。
"""

import json
import re
from typing import Any
from .resource_loader import ResourceLoader, Scene
from .json_generator import ScriptJSONGenerator


def _is_abstract_position(pos_id: str) -> bool:
    """检查是否是抽象位置占位符（如 'Position 1'），PositionAgent 处理后会替换为真实点位 ID"""
    return bool(re.match(r'^Position\s+\d+$', pos_id or '', re.IGNORECASE))


# ────────────────────────────────────────────────────────────────────────────
# 核心验证函数（独立，不依赖 DirectorAI 类）
# ────────────────────────────────────────────────────────────────────────────

def validate_script_constraints(
    script: list,
    scene: Scene,
    resource_loader: ResourceLoader
) -> dict:
    """
    验证剧本中的全部技术约束：
    1. current position 中的点位 ID 是否存在于场景
    2. 移动目标点位 ID 是否存在于场景
    3. 动作 ID 是否存在于动作库且与状态兼容
    4. 【补充】同一对白片段中所有角色是否属于同一 camera_group

    Returns:
        {"valid": bool, "errors": list[str], "warnings": list[str]}
    """
    errors = []
    warnings = []

    if not isinstance(script, list):
        return {"valid": False, "errors": ["剧本必须是 JSON 数组"], "warnings": []}

    for scene_idx, scene_obj in enumerate(script):
        scene_sequence = scene_obj.get("scene", [])

        for seg_idx, segment in enumerate(scene_sequence):
            is_movement = "move" in segment

            # ── 检查 current position 有效性 ──
            for pos_entry in segment.get("current position", []):
                pos_id = pos_entry.get("position")
                if pos_id and not scene.get_position(pos_id):
                    warnings.append(
                        f"场景{scene_idx} 片段{seg_idx}: "
                        f"current position '{pos_id}' 不在场景可用点位中"
                    )

            if is_movement:
                # ── 检查移动目标有效性（抽象占位符跳过，由 PositionAgent 处理）──
                for move in segment.get("move", []):
                    dest = move.get("destination")
                    if dest and not scene.get_position(dest) and not _is_abstract_position(dest):
                        errors.append(
                            f"场景{scene_idx} 片段{seg_idx}: "
                            f"移动目标 '{dest}' 不在场景可用点位中"
                        )
            else:
                # ── 检查动作有效性和状态兼容性 ──
                for action in segment.get("actions", []):
                    action_id = action.get("action")
                    if not action_id:
                        continue
                    action_obj = resource_loader.get_action_by_id(action_id)
                    if not action_obj:
                        warnings.append(
                            f"场景{scene_idx} 片段{seg_idx}: "
                            f"动作 '{action_id}' 不在动作资源库中"
                        )
                    else:
                        state = action.get("state", "standing")
                        if not action_obj.is_compatible_with_state(state):
                            warnings.append(
                                f"场景{scene_idx} 片段{seg_idx}: "
                                f"动作 '{action_id}' 不兼容状态 '{state}'"
                            )

                # ── 【新增】camera_group 分组一致性检查 ──
                # 仅在所有位置都是真实点位时才检查（抽象位置由 PositionAgent 处理后才验证）
                if scene.camera_groups:
                    all_positions = [
                        p.get("position", "")
                        for p in segment.get("current position", [])
                    ]
                    has_abstract = any(_is_abstract_position(p) for p in all_positions)
                    if not has_abstract:
                        _check_camera_group_consistency(
                            segment, scene_idx, seg_idx, scene, errors
                        )

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings
    }


def _check_camera_group_consistency(
    segment: dict,
    scene_idx: int,
    seg_idx: int,
    scene: Scene,
    errors: list
) -> None:
    """
    检查对白片段中，actions 列出的所有角色的 current position 是否属于同一 camera_group。
    如果场景有 camera_groups 定义，则执行此检查。
    """
    actions = segment.get("actions", [])
    if not actions:
        return

    # 提取 actions 中涉及的角色名
    action_chars = {a.get("character") for a in actions if a.get("character")}
    if not action_chars:
        return

    # 从 current position 中找到这些角色的位置
    current_pos_map = {
        p["character"]: p["position"]
        for p in segment.get("current position", [])
        if p.get("character") and p.get("position")
    }

    groups_seen = set()
    for char_name in action_chars:
        pos_id = current_pos_map.get(char_name)
        if not pos_id:
            continue
        group = scene.get_group_for_position(pos_id)
        if group:  # 空字符串表示该点位不在任何分组，跳过检查
            groups_seen.add(group)

    if len(groups_seen) > 1:
        errors.append(
            f"场景{scene_idx} 片段{seg_idx}: "
            f"对白中角色分属不同 camera_group（{groups_seen}），"
            f"同一镜头只能拍摄同组点位内的角色"
        )


def validate_json_spec(script: list) -> dict:
    """
    验证 JSON 结构是否符合 scene_json_spec 规范。
    直接调用现有的 ScriptJSONGenerator.validate_against_spec。

    Returns:
        {"valid": bool, "errors": list[str], "warnings": list[str]}
    """
    return ScriptJSONGenerator.validate_against_spec(script)


# ────────────────────────────────────────────────────────────────────────────
# AutoGen FunctionTool 工厂（绑定具体的 resource_loader 和 scene 实例）
# ────────────────────────────────────────────────────────────────────────────

def auto_fix_script(script: list, scene: Scene, resource_loader: ResourceLoader) -> list:
    """
    用 Python 代码自动修复剧本中的技术约束错误，不回退给任何创作 Agent。

    修复范围（只修复技术字段，不碰 content/what 等文学字段）：
    - 缺失/空 shot_type → 移动片段用"全景"，对白片段用"中近景"
    - 缺失/空 Follow → 0
    - 缺失/空 shot_blend → "cut"
    - 缺失 actions 字段 → []
    - 无效 action_id → 替换为同 state 下动作库第一个有效动作
    - 缺失 current position → 从上一片段继承（初始位置兜底）

    Returns:
        修复后的剧本（deepcopy，不修改原对象）
    """
    import copy
    result = copy.deepcopy(script)

    for scene_obj in result:
        # 用 initial position 初始化位置追踪表
        last_positions: dict = {}
        for entry in scene_obj.get("initial position", []):
            char = entry.get("character")
            pos = entry.get("position")
            if char and pos:
                last_positions[char] = pos

        for seg in scene_obj.get("scene", []):
            is_move = "move" in seg

            # ── shot_type ──
            if not seg.get("shot_type"):
                seg["shot_type"] = "全景" if is_move else "中近景"

            # ── Follow ──
            if "Follow" not in seg or seg["Follow"] is None:
                seg["Follow"] = 0

            # ── shot_blend ──
            if not seg.get("shot_blend"):
                seg["shot_blend"] = "cut"

            # ── current position：缺失时从上下文继承 ──
            if not seg.get("current position"):
                seg["current position"] = [
                    {"character": c, "position": p}
                    for c, p in last_positions.items() if p
                ]

            if not is_move:
                # ── actions 字段缺失 ──
                if "actions" not in seg:
                    seg["actions"] = []

                # ── 无效 action_id → 替换为同 state 的合法动作 ──
                for action in seg.get("actions", []):
                    action_id = action.get("action", "")
                    if action_id and not resource_loader.get_action_by_id(action_id):
                        state = action.get("state", "standing")
                        candidates = resource_loader.get_actions_by_state(state)
                        if candidates:
                            action["action"] = candidates[0].action_id

            # ── 更新位置追踪表 ──
            # 移动片段：记录目的地
            for move in seg.get("move", []):
                char = move.get("character")
                dest = move.get("destination")
                if char and dest:
                    last_positions[char] = dest
            # 所有片段：以 current position 为准更新
            for pos in seg.get("current position", []):
                char = pos.get("character")
                pos_id = pos.get("position")
                if char and pos_id:
                    last_positions[char] = pos_id

    return result


def make_validation_tools(resource_loader: ResourceLoader, scene: Scene):
    """
    工厂函数：创建绑定了具体资源实例的 AutoGen FunctionTool 列表。

    Returns:
        list of autogen_core.tools.FunctionTool
    """
    from autogen_core.tools import FunctionTool

    def _validate_constraints(script_json_str: str) -> str:
        """
        验证剧本 JSON 字符串中的技术约束（点位、动作、camera_group）。
        输入：剧本 JSON 字符串；输出：验证结果 JSON 字符串。
        """
        try:
            script = json.loads(script_json_str)
        except json.JSONDecodeError as e:
            return json.dumps({"valid": False, "errors": [f"JSON 解析失败: {e}"], "warnings": []}, ensure_ascii=False)
        result = validate_script_constraints(script, scene, resource_loader)
        return json.dumps(result, ensure_ascii=False)

    def _validate_spec(script_json_str: str) -> str:
        """
        验证剧本 JSON 字符串是否符合 scene_json_spec 规范。
        输入：剧本 JSON 字符串；输出：验证结果 JSON 字符串。
        """
        try:
            script = json.loads(script_json_str)
        except json.JSONDecodeError as e:
            return json.dumps({"valid": False, "errors": [f"JSON 解析失败: {e}"], "warnings": []}, ensure_ascii=False)
        result = validate_json_spec(script)
        return json.dumps(result, ensure_ascii=False)

    return [
        FunctionTool(
            _validate_constraints,
            description=(
                "验证剧本 JSON 的技术约束：点位 ID 有效性、动作 ID 有效性、"
                "动作状态兼容性、同一对白片段的 camera_group 一致性。"
                "输入为剧本 JSON 字符串，输出为验证结果 JSON 字符串。"
            )
        ),
        FunctionTool(
            _validate_spec,
            description=(
                "验证剧本 JSON 结构是否符合 scene_json_spec 规范（字段完整性检查）。"
                "输入为剧本 JSON 字符串，输出为验证结果 JSON 字符串。"
            )
        ),
    ]

"""
AutoGen Agent 定义模块

定义各个专业化 Agent 及其 system_message 构建函数。
DirectorAgent 的提示词逻辑从 director_ai.py 的 _build_context_prompt 迁移而来。
"""

import os
from typing import List, Optional
from autogen_agentchat.agents import AssistantAgent
from autogen_ext.models.openai import OpenAIChatCompletionClient
from .resource_loader import ResourceLoader, Character, Scene
from .autogen_tools import make_validation_tools


def make_model_client(model: Optional[str] = None) -> OpenAIChatCompletionClient:
    """创建 OpenAI 兼容的模型客户端（支持 DeepSeek / 火山引擎 ARK）"""
    api_key = os.getenv("API_KEY")
    base_url = os.getenv("BASE_URL", "https://api.deepseek.com")
    model_name = model or os.getenv("MODEL", "deepseek-v3-241226")

    if not api_key:
        raise ValueError("需要提供 API_KEY，请在 .env 文件中设置")

    # 是否支持 function calling，默认 False（火山引擎 code plan 不支持）
    # 如需开启，在 .env 中设置 MODEL_FUNCTION_CALLING=true
    function_calling = os.getenv("MODEL_FUNCTION_CALLING", "false").lower() == "true"

    return OpenAIChatCompletionClient(
        model=model_name,
        api_key=api_key,
        base_url=base_url,
        max_tokens=8000,
        temperature=0.7,
        model_info={
            "vision": False,
            "function_calling": function_calling,
            "json_output": True,
            "family": "unknown",
            "structured_output": False,
        },
    )


# ────────────────────────────────────────────────────────────────────────────
# system_message 构建函数
# ────────────────────────────────────────────────────────────────────────────

def build_director_system_message(
    characters: List[Character],
    scene: Scene,
    resource_loader: ResourceLoader,
    required_character_count: int = 0
) -> str:
    """
    构建 DirectorAgent 的 system_message。
    迁移自 director_ai.py 的 _build_context_prompt。
    """

    total_count = required_character_count if required_character_count > 0 else len(characters)
    if total_count == 0:
        total_count = 2
    extra_count = max(0, total_count - len(characters))

    # 1. 角色信息
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
            char_info += f"- 背景: {char.description}\n"
            char_info += f"- 性格: {char.personality}\n\n"
    else:
        char_info += f"本场景共需要 **{total_count}** 位角色，全部由 AI 自由创作。\n\n"

    # 2. 场景信息
    scene_info = f"## 场景信息\n\n### {scene.name} (ID: {scene.id})\n"
    scene_info += f"- 描述: {scene.description}\n\n#### 可用点位:\n"
    for pos in scene.valid_positions:
        sittable = " [可坐]" if pos.get('is_sittable', False) else ""
        group_tag = f" [组{pos['camera_group']}]" if pos.get('camera_group') else ""
        scene_info += f"- **{pos['id']}**{sittable}{group_tag}: {pos['description']}\n"

    if scene.camera_groups:
        scene_info += "\n#### 镜头分组（同一镜头只能拍摄同组点位内的角色）:\n"
        for group in scene.camera_groups:
            pos_list = ", ".join(group['position_ids'])
            scene_info += f"- **{group['id']}组 - {group['name']}**: {pos_list}\n"

    # 3. 动作库
    action_info = "## 可用动作库\n\n以下是所有可用的动作，请根据描述选择最合适的动作ID:\n\n"
    categories: dict = {}
    for action in resource_loader.actions:
        categories.setdefault(action.category, []).append(action)
    for category, actions in sorted(categories.items()):
        action_info += f"### {category} (状态: {actions[0].compatible_states})\n"
        for action in actions:
            action_info += f"- **{action.action_id}**: {action.description}\n"
        action_info += "\n"

    # 4. 角色数量规则
    if characters and extra_count == 0:
        char_count_rule = (
            f"1. **角色数量（最高优先级）**: 剧本中出现的角色总数必须恰好为 **{total_count}** 位，"
            f"即 {', '.join(c.name for c in characters)}，**绝对不得引入任何其他角色**。"
        )
    elif characters and extra_count > 0:
        char_count_rule = (
            f"1. **角色数量（最高优先级）**: 剧本中出现的角色总数必须恰好为 **{total_count}** 位："
            f"指定角色 {', '.join(c.name for c in characters)} 必须全部出现，"
            f"另外还需自由创作 {extra_count} 位新角色。"
        )
    else:
        char_count_rule = (
            f"1. **角色数量（最高优先级）**: 剧本中出现的角色总数必须恰好为 **{total_count}** 位，"
            f"全部由 AI 自由创作，但数量严格固定。"
        )

    task_info = (
        "\n## 你的任务\n\n"
        "你是一位专业的剧本导演AI。请根据上述信息生成完整的场景剧本JSON。\n\n"
        "**核心要求:**\n\n"
        + char_count_rule
        + "\n\n"
        + "2. **走位决策**:\n"
        + "   - 角色只能出现在\"可用点位\"列表中的位置\n"
        + "   - 同一镜头中出现的所有角色，必须位于同一 camera_group 的点位内\n"
        + "   - 如需同时展示不同组的角色，先用移动片段将角色集中到同组点位\n\n"
        + "3. **动作决策**:\n"
        + "   - 只能使用\"可用动作库\"中的动作名称\n"
        + "   - 注意动作的 compatible_states，确保角色状态匹配\n\n"
        + "4. **对白生成**:\n"
        + "   - 严格遵循角色的性格描述\n"
        + "   - 对白要符合人物性格和场景氛围\n\n"
        + "5. **镜头设计**:\n"
        + "   - 对白场景用 \"character\" 镜头（配合 shot_anchors）\n"
        + "   - 移动场景用 \"scene\" 镜头（配合 camera 编号）\n\n"
        + "**输出格式:** 严格按照以下 JSON 结构输出，直接输出 JSON，不要有其他说明文字。\n\n"
        + "```json\n"
        + "[\n"
        + "  {\n"
        + "    \"scene information\": {\n"
        + "      \"who\": [\"角色名1\", \"角色名2\"],\n"
        + "      \"where\": \"场景名称\",\n"
        + "      \"what\": \"场景核心事件一句话概述\"\n"
        + "    },\n"
        + "    \"initial position\": [\n"
        + "      {\"character\": \"角色名1\", \"position\": \"Position X\"}\n"
        + "    ],\n"
        + "    \"scene\": [\n"
        + "      {\n"
        + "        \"speaker\": \"角色名\",\n"
        + "        \"content\": \"台词\",\n"
        + "        \"shot\": \"character\",\n"
        + "        \"shot_anchors\": [\"Front\"],\n"
        + "        \"shot_blend\": \"cut\",\n"
        + "        \"actions\": [\n"
        + "          {\"character\": \"角色名\", \"state\": \"standing\", \"action\": \"Standing Speech 2\", \"motion_detail\": \"\"}\n"
        + "        ],\n"
        + "        \"current position\": [\n"
        + "          {\"character\": \"角色名1\", \"position\": \"Position X\"}\n"
        + "        ],\n"
        + "        \"motion_description\": \"氛围描述\"\n"
        + "      },\n"
        + "      {\n"
        + "        \"move\": [{\"character\": \"角色名\", \"destination\": \"Position Z\"}],\n"
        + "        \"shot\": \"scene\",\n"
        + "        \"camera\": 1,\n"
        + "        \"current position\": [\n"
        + "          {\"character\": \"角色名1\", \"position\": \"Position X\"}\n"
        + "        ]\n"
        + "      }\n"
        + "    ]\n"
        + "  }\n"
        + "]\n"
        + "```\n\n"
        + "**字段规则:**\n"
        + "- `shot` 为 \"character\" 时使用 `shot_anchors`，不使用 `camera`\n"
        + "- `shot` 为 \"scene\" 时使用 `camera`（整数），不使用 `shot_anchors`\n"
        + "- `current position` 须包含场景内所有在场角色\n"
        + "- 对白片段中所有 actions 角色的 current position 必须属于同一 camera_group\n"
        + "- 只使用可用点位列表中的 ID 和可用动作库中的动作名称\n"
    )

    return char_info + scene_info + action_info + task_info


def build_critic_system_message() -> str:
    return (
        "你是一位专业的剧本顾问，专注于叙事质量分析。\n\n"
        "你会收到一份剧本 JSON。你只需关注以下字段：\n"
        "- `scene information.what`：场景核心事件\n"
        "- `speaker` 和 `content`：对白内容\n"
        "- 角色的整体行为是否与其性格相符\n\n"
        "**请忽略** JSON 中的技术字段（position、action_id、camera_group 等），这不是你的职责。\n\n"
        "你的输出格式必须是以下 JSON（直接输出，无其他文字）：\n"
        "```json\n"
        "{\n"
        "  \"has_issues\": true,\n"
        "  \"issues\": [\n"
        "    {\"type\": \"character_consistency\", \"description\": \"问题描述\", \"location\": \"scene[2].speaker=角色名\"}\n"
        "  ],\n"
        "  \"revision_instruction\": \"请修改：...\"\n"
        "}\n"
        "```\n"
        "如果没有问题，输出 `{\"has_issues\": false, \"issues\": [], \"revision_instruction\": \"\"}`。\n"
        "保持简洁，每次最多指出 3 个最重要的问题。"
    )


def build_dialogue_system_message() -> str:
    return (
        "你是一位对白打磨专家，专注于台词的语言风格和人物一致性。\n\n"
        "你会收到一份剧本 JSON。你只需关注 `speaker` 和 `content` 字段。\n"
        "评估标准：台词是否符合角色性格、是否生动有力、是否有情感层次、是否避免了套话和过于书面化的表达。\n\n"
        "**请忽略** JSON 中的所有技术字段，这不是你的职责。\n\n"
        "你的输出格式必须是以下 JSON（直接输出，无其他文字）：\n"
        "```json\n"
        "{\n"
        "  \"has_issues\": true,\n"
        "  \"issues\": [\n"
        "    {\"type\": \"dialogue_quality\", \"description\": \"台词问题描述\", \"location\": \"scene[1].content\"}\n"
        "  ],\n"
        "  \"revision_instruction\": \"请修改：...\"\n"
        "}\n"
        "```\n"
        "如果没有问题，输出 `{\"has_issues\": false, \"issues\": [], \"revision_instruction\": \"\"}`。\n"
        "保持简洁，每次最多指出 3 个最重要的问题。"
    )


def build_validation_system_message() -> str:
    return (
        "你是一位技术验证员，负责验证剧本的技术约束。\n\n"
        "收到剧本 JSON 字符串后，你必须严格按照以下步骤执行：\n"
        "1. 调用 `_validate_constraints` 工具，传入剧本 JSON 字符串\n"
        "2. 调用 `_validate_spec` 工具，传入剧本 JSON 字符串\n"
        "3. 汇总两个工具的结果并输出\n\n"
        "**禁止**自行判断技术约束，必须通过工具验证。\n\n"
        "你的最终输出格式（JSON，无其他文字）：\n"
        "```json\n"
        "{\n"
        "  \"valid\": true,\n"
        "  \"errors\": [],\n"
        "  \"warnings\": [\"警告信息\"]\n"
        "}\n"
        "```\n"
        "如果 valid 为 false，列出所有 errors（严重问题）和 warnings（警告）。"
    )


# ────────────────────────────────────────────────────────────────────────────
# Agent 工厂函数
# ────────────────────────────────────────────────────────────────────────────

def create_director_agent(
    characters: List[Character],
    scene: Scene,
    resource_loader: ResourceLoader,
    required_character_count: int = 0,
    model: Optional[str] = None
) -> AssistantAgent:
    system_message = build_director_system_message(
        characters, scene, resource_loader, required_character_count
    )
    return AssistantAgent(
        name="DirectorAgent",
        model_client=make_model_client(model),
        system_message=system_message,
    )


def create_critic_agent(model: Optional[str] = None) -> AssistantAgent:
    return AssistantAgent(
        name="CriticAgent",
        model_client=make_model_client(model),
        system_message=build_critic_system_message(),
    )


def create_dialogue_agent(model: Optional[str] = None) -> AssistantAgent:
    return AssistantAgent(
        name="DialogueAgent",
        model_client=make_model_client(model),
        system_message=build_dialogue_system_message(),
    )


def create_validation_agent(
    resource_loader: ResourceLoader,
    scene: Scene,
    model: Optional[str] = None
) -> AssistantAgent:
    tools = make_validation_tools(resource_loader, scene)
    return AssistantAgent(
        name="ValidationAgent",
        model_client=make_model_client(model),
        system_message=build_validation_system_message(),
        tools=tools,
    )

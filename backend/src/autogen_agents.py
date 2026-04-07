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
    model_name = model or os.getenv("MODEL", "doubao-seed-2-0-lite-260215")

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

    # 2. 场景信息（不暴露具体点位，由 PositionAgent 处理映射）
    scene_info = f"## 场景信息\n\n### {scene.name} (ID: {scene.id})\n"
    scene_info += f"- 描述: {scene.description}\n\n"

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
        + "2. **走位设计（以演出效果为唯一标准）**:\n"
        + "   - 根据演出需要决定角色站位，依次命名为 Position 1、Position 2...\n"
        + "   - 在顶层 `position_descriptions` 字段中用自然语言描述每个位置的戏剧意图\n"
        + "   - 例：\"Position 1\": \"近窗俯瞰，背靠星空，适合独白或凝望\"\n"
        + "   - 位置映射将由专门的位置代理处理，你只需专注于演出效果\n\n"
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
        + "    \"position_descriptions\": {\n"
        + "      \"Position 1\": \"描述位置1的戏剧意图，如：近窗俯瞰，适合凝望或独白\",\n"
        + "      \"Position 2\": \"描述位置2的戏剧意图，如：正中央，适合面对面对峙\"\n"
        + "    },\n"
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
        + "- `position_descriptions` 必须包含剧本中所有使用到的 Position N 编号\n"
        + "- 只使用可用动作库中的动作名称\n"
    )

    return char_info + scene_info + action_info + task_info


def _build_stage_common_context(
    characters: List[Character],
    scene: Scene,
    required_character_count: int = 0
) -> str:
    """构建阶段化前置创作用的通用上下文。"""
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
        lines.append("已指定角色（必须保留）：")
        for char in characters:
            lines.append(f"- {char.name}｜背景：{char.description}｜性格：{char.personality}")
        if extra_count > 0:
            lines.append(f"还需新增角色数量：{extra_count}")
    else:
        lines.append("未指定角色，全部由 AI 自由创作。")

    return "\n".join(lines)


def build_concept_system_message(
    characters: List[Character],
    scene: Scene,
    required_character_count: int = 0
) -> str:
    common = _build_stage_common_context(characters, scene, required_character_count)
    return (
        "你是 ConceptAgent，负责产出 Logline（概念萌发）。\n"
        "你的目标是将创作想法压缩为高可执行的一句核心命题。\n\n"
        "## 已知上下文\n"
        f"{common}\n\n"
        "## 输出要求\n"
        "1. 只输出 JSON，不要附加解释。\n"
        "2. 字段固定为：\n"
        "{\n"
        "  \"logline\": \"一句话核心冲突与戏剧目标\",\n"
        "  \"core_conflict\": \"主要矛盾\",\n"
        "  \"tone\": \"风格基调\",\n"
        "  \"stakes\": \"失败代价或风险\"\n"
        "}\n"
        "3. 内容需可直接供后续 Synopsis 阶段使用。"
    )


def build_synopsis_system_message() -> str:
    return (
        "你是 SynopsisAgent，负责将 Logline 扩展为故事梗概。\n"
        "你会收到创作想法与上游 Concept 阶段结果。\n\n"
        "## 输出要求\n"
        "1. 只输出 JSON，不要附加解释。\n"
        "2. 字段固定为：\n"
        "{\n"
        "  \"synopsis\": \"200-400 字的完整梗概\",\n"
        "  \"opening\": \"开场状态\",\n"
        "  \"turning_point\": \"关键转折\",\n"
        "  \"ending_direction\": \"结局走向\"\n"
        "}\n"
        "3. 强调因果链路，避免只列设定。"
    )


def build_character_bios_system_message() -> str:
    return (
        "你是 CharacterBiosAgent，负责人物小传。\n"
        "你会收到 Logline、Synopsis 与角色约束。\n\n"
        "## 输出要求\n"
        "1. 只输出 JSON，不要附加解释。\n"
        "2. 字段固定为：\n"
        "{\n"
        "  \"character_bios\": [\n"
        "    {\n"
        "      \"name\": \"角色名\",\n"
        "      \"role\": \"叙事功能\",\n"
        "      \"goal\": \"当下目标\",\n"
        "      \"inner_conflict\": \"内在冲突\",\n"
        "      \"relationship_hint\": \"与其他角色的关系线索\"\n"
        "    }\n"
        "  ]\n"
        "}\n"
        "3. 若有已指定角色，必须保留姓名并对齐既有性格。"
    )


def build_treatment_system_message() -> str:
    return (
        "你是 TreatmentAgent，负责分场大纲（Beat Sheet）。\n"
        "你会收到前置阶段产物（Logline、Synopsis、Character Bios）。\n\n"
        "## 输出要求\n"
        "1. 只输出 JSON，不要附加解释。\n"
        "2. 字段固定为：\n"
        "{\n"
        "  \"treatment\": [\n"
        "    {\n"
        "      \"beat\": 1,\n"
        "      \"objective\": \"该节拍的戏剧目标\",\n"
        "      \"conflict\": \"冲突推进\",\n"
        "      \"outcome\": \"结果与状态变化\"\n"
        "    }\n"
        "  ],\n"
        "  \"draft_guidance\": \"供导演生成 JSON 剧本时遵循的短指令\"\n"
        "}\n"
        "3. 需形成清晰递进，可直接作为最终剧本初稿蓝图。"
    )


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


def create_concept_agent(
    characters: List[Character],
    scene: Scene,
    required_character_count: int = 0,
    model: Optional[str] = None
) -> AssistantAgent:
    return AssistantAgent(
        name="ConceptAgent",
        model_client=make_model_client(model),
        system_message=build_concept_system_message(characters, scene, required_character_count),
    )


def create_synopsis_agent(model: Optional[str] = None) -> AssistantAgent:
    return AssistantAgent(
        name="SynopsisAgent",
        model_client=make_model_client(model),
        system_message=build_synopsis_system_message(),
    )


def create_character_bios_agent(model: Optional[str] = None) -> AssistantAgent:
    return AssistantAgent(
        name="CharacterBiosAgent",
        model_client=make_model_client(model),
        system_message=build_character_bios_system_message(),
    )


def create_treatment_agent(model: Optional[str] = None) -> AssistantAgent:
    return AssistantAgent(
        name="TreatmentAgent",
        model_client=make_model_client(model),
        system_message=build_treatment_system_message(),
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


def build_position_agent_system_message(scene: Scene) -> str:
    """构建 PositionAgent 的 system_message，包含场景真实点位信息"""
    positions_info = ""
    for pos in scene.valid_positions:
        sittable = " [可坐]" if pos.get('is_sittable', False) else ""
        group_tag = f" [镜头组{pos['camera_group']}]" if pos.get('camera_group') else ""
        positions_info += f"- **{pos['id']}**{sittable}{group_tag}: {pos['description']}\n"

    camera_groups_info = ""
    if scene.camera_groups:
        camera_groups_info = "\n#### 镜头分组（同一对白片段内所有角色必须属于同一镜头组）:\n"
        for group in scene.camera_groups:
            pos_list = ", ".join(group['position_ids'])
            camera_groups_info += f"- **{group['id']}组 - {group['name']}**: {pos_list}\n"

    return (
        "你是位置映射专家。你的任务是把剧本中的抽象站位（Position 1/2/3...）"
        "映射到真实场景中已有的点位。\n\n"
        f"## 当前场景：{scene.name} (ID: {scene.id})\n\n"
        "### 可用真实点位:\n"
        + positions_info
        + camera_groups_info
        + "\n\n## 你的工作步骤:\n\n"
        "1. 读取剧本每个场景对象顶层的 `position_descriptions` 字段，了解每个抽象位置的戏剧意图\n"
        "2. 对照上方可用真实点位，为每个抽象位置选择最匹配戏剧意图的真实点位 ID\n"
        "3. **确保同一对白片段**中所有角色的映射点位属于同一镜头组\n"
        "4. 将剧本中所有 `\"Position N\"` 替换为真实点位 ID（包括 `initial position`、`current position`、`move.destination`）\n"
        "5. 删除每个场景对象中的 `position_descriptions` 字段\n"
        "6. 输出修改后的完整剧本 JSON\n\n"
        "## 无法映射时的处理:\n\n"
        "如果某个抽象位置在现有点位中找不到合理匹配，"
        "在输出 JSON **之前**用以下格式声明（每个无法映射的位置一行）：\n\n"
        "```\n"
        "POSITION_UNRESOLVED: Position X → 原因描述\n"
        "```\n\n"
        "然后再输出（尽力映射的）JSON。\n\n"
        "**直接输出，无需额外解释。若有 POSITION_UNRESOLVED 声明，写在 JSON 之前。**"
    )


def create_position_agent(scene: Scene, model: Optional[str] = None) -> AssistantAgent:
    return AssistantAgent(
        name="PositionAgent",
        model_client=make_model_client(model),
        system_message=build_position_agent_system_message(scene),
    )

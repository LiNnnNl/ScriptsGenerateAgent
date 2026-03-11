"""
导演AI核心模块
负责根据剧情大纲、角色性格、场景点位、动作库生成中间态剧本指令
"""

from typing import List, Dict, Optional
import os
from openai import OpenAI
from dotenv import load_dotenv
from .resource_loader import ResourceLoader, Character, Scene, Action

# 加载环境变量
load_dotenv()


class DirectorAI:
    """导演AI - 负责剧本生成的核心决策"""
    
    def __init__(self, resource_loader: ResourceLoader, api_key: Optional[str] = None, base_url: Optional[str] = None):
        self.resource_loader = resource_loader
        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY")
        self.base_url = base_url or os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        
        if not self.api_key:
            raise ValueError("需要提供 DEEPSEEK_API_KEY，可通过参数或 .env 文件设置")
        
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url
        )
    
    def _build_context_prompt(
        self, 
        characters: List[Character], 
        scene: Scene, 
        plot_outline: str
    ) -> str:
        """
        构建给AI的上下文提示词
        包含：角色性格、场景地图、可用动作、剧情大纲
        """
        
        # 1. 角色信息
        char_info = "## 角色信息\n\n"
        for char in characters:
            char_info += f"### {char.name} (ID: {char.id})\n"
            char_info += f"- 描述: {char.description}\n"
            char_info += f"- 性格: {char.personality}\n\n"
        
        # 2. 场景信息
        scene_info = f"## 场景信息\n\n"
        scene_info += f"### {scene.name} (ID: {scene.id})\n"
        scene_info += f"- 描述: {scene.description}\n\n"
        scene_info += f"#### 可用点位:\n"
        for pos in scene.valid_positions:
            sittable = " [可坐]" if pos.get('is_sittable', False) else ""
            scene_info += f"- **{pos['id']}**{sittable}: {pos['description']}\n"
        
        # 3. 动作库信息
        action_info = "## 可用动作库\n\n"
        action_info += "以下是所有可用的动作，请根据描述选择最合适的动作ID:\n\n"
        
        # 按类别组织动作
        categories = {}
        for action in self.resource_loader.actions:
            if action.category not in categories:
                categories[action.category] = []
            categories[action.category].append(action)
        
        for category, actions in sorted(categories.items()):
            action_info += f"### {category} (状态: {actions[0].compatible_states})\n"
            for action in actions:
                action_info += f"- **{action.action_id}**: {action.description}\n"
            action_info += "\n"
        
        # 4. 剧情要求
        if plot_outline and plot_outline.strip():
            plot_info = f"""## 创作要求

用户的创作想法：
{plot_outline}

请根据以上创作想法，结合角色性格、场景环境和可用动作，创作一段剧本。

**注意：**
- 充分利用角色的性格特点，让对白符合人物设定
- 利用场景的空间点位，设计合理的走位和互动
- 选择合适的动作，让表演生动有张力
- 围绕用户的创作想法展开，但可以适当发挥
- 每个角色都要有适当的戏份和表现机会

"""
        else:
            plot_info = f"""## 剧情要求

请根据以上角色性格、场景环境和可用动作，自由创作一段剧情。

**要求：**
- 充分利用角色的性格特点，让对白符合人物设定
- 利用场景的空间点位，设计合理的走位和互动
- 选择合适的动作，让表演生动有张力
- 剧情要有冲突、转折或情感变化
- 每个角色都要有适当的戏份和表现机会

"""
        
        # 5. 任务说明
        task_info = """
## 你的任务

你是一位专业的剧本导演AI。请根据上述信息，将剧情大纲转化为详细的场景指令。

**核心要求:**

1. **走位决策**: 
   - 角色只能出现在"可用点位"列表中的位置
   - 根据剧情需要（如"从门口走进来"）选择语义匹配的点位
   - 如果剧情未明确位置，根据场景描述和角色关系合理推断

2. **动作决策**:
   - 只能使用"可用动作库"中的动作ID
   - 根据动作的description描述选择最贴切的动作
   - 注意动作的compatible_states，确保角色状态匹配（如坐着的人不能执行standing动作）
   - 如果需要坐下，先移动到可坐位置，然后使用Interact_Sit_Down

3. **对白生成**:
   - 严格遵循角色的性格描述
   - 对白要符合人物性格和场景氛围
   - 理性的角色说话简洁明确，感性的角色可以更有情绪

4. **镜头设计**:
   - 对白场景用"character"镜头聚焦说话者
   - 移动场景用"scene"镜头展示全局
   - 氛围营造用"scene"镜头配合motion_description

**输出格式:**

请以JSON格式输出，包含以下结构：

```json
{
  "scene_sequence": [
    {
      "type": "dialogue",  // 或 "movement" 或 "description"
      "speaker": "角色名称",  // 或 "default" 表示旁白
      "content": "对白内容",
      "shot": "character",  // 或 "scene"
      "shot_anchors": ["Front"],  // 可选
      "camera": 1,  // 可选
      "actions": [
        {
          "character": "角色名称",
          "state": "standing",  // 或 "sitting"
          "action": "动作ID",
          "motion_detail": "动作细节描述"
        }
      ],
      "positions": [
        {
          "character": "角色名称",
          "position": "Position X"
        }
      ],
      "motion_description": "氛围描述"  // 可选
    },
    {
      "type": "movement",
      "move": [
        {
          "character": "角色名称",
          "destination": "Position X"
        }
      ],
      "shot": "scene",
      "camera": 1,
      "positions": [
        {
          "character": "角色名称",
          "position": "当前位置"
        }
      ]
    }
  ]
}
```

**重要提示:**
- 必须追踪每个角色的当前位置和状态（standing/sitting）
- 移动前后位置要保持连贯
- 只使用提供的点位ID和动作ID，不要编造
- 让剧情生动有张力，充分利用场景空间和动作表现力
"""
        
        return char_info + scene_info + action_info + plot_info + task_info
    
    def generate_script(
        self,
        characters: List[Character],
        scene: Scene,
        plot_outline: str,
        temperature: float = 0.7,
        model: str = None
    ) -> Dict:
        """
        生成剧本
        
        Args:
            characters: 参与角色列表
            scene: 场景对象
            plot_outline: 剧情大纲
            temperature: AI创作温度 (0-1)
            model: 使用的模型名称
        
        Returns:
            包含scene_sequence的字典
        """
        
        # 构建提示词
        system_prompt = self._build_context_prompt(characters, scene, plot_outline)

        # 从环境变量读取模型名，参数优先
        if model is None:
            model = os.getenv("DEEPSEEK_MODEL", "deepseek-v3-241226")

        # 调用 ARK API (兼容 OpenAI 格式)
        response = self.client.chat.completions.create(
            model=model,
            max_tokens=8000,
            temperature=temperature,
            messages=[
                {
                    "role": "system",
                    "content": system_prompt
                },
                {
                    "role": "user",
                    "content": "请开始生成剧本，直接输出JSON格式，不要有其他说明文字。"
                }
            ]
        )
        
        # 解析响应
        response_text = response.choices[0].message.content
        
        # 提取JSON（处理可能的markdown代码块包装）
        import json
        import re
        
        # 尝试提取JSON代码块
        json_match = re.search(r'```json\s*(\{.*?\})\s*```', response_text, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
        else:
            # 尝试直接解析
            json_str = response_text.strip()
        
        try:
            result = json.loads(json_str)
            return result
        except json.JSONDecodeError as e:
            # 如果解析失败，返回原始文本供调试
            return {
                "error": "JSON解析失败",
                "raw_response": response_text,
                "exception": str(e)
            }
    
    def validate_script_output(self, script: Dict, scene: Scene) -> Dict[str, any]:
        """
        验证AI生成的剧本是否有效
        检查：1) 位置是否存在  2) 动作是否存在  3) 状态是否匹配
        """
        errors = []
        warnings = []
        
        if "error" in script:
            errors.append(script["error"])
            return {"valid": False, "errors": errors, "warnings": warnings}
        
        scene_sequence = script.get("scene_sequence", [])
        
        for idx, segment in enumerate(scene_sequence):
            seg_type = segment.get("type", "unknown")
            
            # 检查位置有效性
            positions = segment.get("positions", [])
            for pos in positions:
                pos_id = pos.get("position")
                if not scene.get_position(pos_id):
                    errors.append(
                        f"段落{idx}: 位置 '{pos_id}' 不在场景 '{scene.name}' 的可用点位中"
                    )
            
            # 检查动作有效性
            if seg_type == "dialogue" or seg_type == "description":
                actions = segment.get("actions", [])
                for action in actions:
                    action_id = action.get("action")
                    if not self.resource_loader.get_action_by_id(action_id):
                        errors.append(
                            f"段落{idx}: 动作 '{action_id}' 不在动作资源库中"
                        )
                    else:
                        # 检查状态兼容性
                        state = action.get("state", "standing")
                        action_obj = self.resource_loader.get_action_by_id(action_id)
                        if not action_obj.is_compatible_with_state(state):
                            warnings.append(
                                f"段落{idx}: 动作 '{action_id}' 不兼容状态 '{state}'"
                            )
            
            # 检查移动目标有效性
            if seg_type == "movement":
                moves = segment.get("move", [])
                for move in moves:
                    dest = move.get("destination")
                    if not scene.get_position(dest):
                        errors.append(
                            f"段落{idx}: 移动目标 '{dest}' 不在场景 '{scene.name}' 的可用点位中"
                        )
        
        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings
        }


"""
导演AI核心模块
负责根据剧情大纲、角色性格、场景点位、动作库生成中间态剧本指令
"""

from typing import List, Dict, Optional
import os
from openai import OpenAI
from dotenv import load_dotenv
from .resource_loader import ResourceLoader, Character, Scene, Action
from .prompt_renderers.director_ai import director_ai_generate_user_prompt

# 加载环境变量
load_dotenv()


class DirectorAI:
    """导演AI - 负责剧本生成的核心决策"""
    
    def __init__(self, resource_loader: ResourceLoader, api_key: Optional[str] = None, base_url: Optional[str] = None):
        self.resource_loader = resource_loader
        self.api_key = api_key or os.getenv("API_KEY")
        self.base_url = base_url or os.getenv("BASE_URL", "https://api.deepseek.com")

        if not self.api_key:
            raise ValueError("需要提供 API_KEY，可通过参数或 .env 文件设置")
        
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url
        )
    
    def _build_context_prompt(
        self,
        characters: List[Character],
        scene: Scene,
        plot_outline: str,
        required_character_count: int = 0
    ) -> str:
        """构建给AI的上下文提示词。"""
        from .prompt_renderers.director_ai import build_director_ai_context_prompt

        return build_director_ai_context_prompt(
            self.resource_loader,
            characters,
            scene,
            plot_outline,
            required_character_count,
        )

    def generate_script(
        self,
        characters: List[Character],
        scene: Scene,
        plot_outline: str,
        required_character_count: int = 0,
        temperature: float = 0.7,
        model: str = None
    ) -> Dict:
        """
        生成剧本

        Args:
            characters: 参与角色列表
            scene: 场景对象
            plot_outline: 剧情大纲
            required_character_count: 剧本中角色总数（0表示用len(characters)或默认值）
            temperature: AI创作温度 (0-1)
            model: 使用的模型名称

        Returns:
            包含scene_sequence的字典
        """

        # 构建提示词
        system_prompt = self._build_context_prompt(characters, scene, plot_outline, required_character_count)

        # 从环境变量读取模型名，参数优先
        if model is None:
            model = os.getenv("MODEL", "deepseek-v3-241226")

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
                    "content": director_ai_generate_user_prompt
                }
            ]
        )
        
        # 解析响应
        response_text = response.choices[0].message.content

        # 提取JSON（处理可能的markdown代码块包装）
        import json
        import re

        # 尝试提取JSON代码块（数组或对象）
        json_match = re.search(r'```json\s*([\[\{].*?[\]\}])\s*```', response_text, re.DOTALL)
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
    
    def generate_script_stream(
        self,
        characters: List[Character],
        scene: Scene,
        plot_outline: str,
        required_character_count: int = 0,
        temperature: float = 0.7,
        model: str = None
    ):
        """
        流式生成剧本（生成器）。
        yields dict:
          {'type': 'thinking_chunk', 'text': str}  — 思考过程片段
          {'type': 'thinking_done'}                 — 思考阶段结束
          {'type': 'result', 'data': dict}          — 最终解析好的 JSON
          {'type': 'error', 'message': str, ...}    — 错误
        """
        import json
        import re

        system_prompt = self._build_context_prompt(
            characters, scene, plot_outline, required_character_count
        )

        if model is None:
            model = os.getenv("MODEL", "deepseek-v3-241226")

        full_content = ""
        thinking_active = False

        try:
            stream = self.client.chat.completions.create(
                model=model,
                max_tokens=8000,
                temperature=temperature,
                stream=True,
                messages=[
                    {"role": "system",  "content": system_prompt},
                    {"role": "user",    "content": director_ai_generate_user_prompt}
                ]
            )

            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta

                # 思考内容（reasoning models，如 deepseek-reasoner）
                reasoning = getattr(delta, 'reasoning_content', None)
                if reasoning:
                    thinking_active = True
                    yield {'type': 'thinking_chunk', 'text': reasoning}

                # 正式输出内容
                content = getattr(delta, 'content', None)
                if content:
                    if thinking_active:
                        thinking_active = False
                        yield {'type': 'thinking_done'}
                    full_content += content

        except Exception as e:
            yield {'type': 'error', 'message': str(e)}
            return

        if thinking_active:
            yield {'type': 'thinking_done'}

        # 解析最终 JSON
        json_match = re.search(r'```json\s*([\[\{].*?[\]\}])\s*```', full_content, re.DOTALL)
        json_str = json_match.group(1) if json_match else full_content.strip()

        try:
            result = json.loads(json_str)
            yield {'type': 'result', 'data': result}
        except json.JSONDecodeError as e:
            yield {
                'type': 'error',
                'message': 'JSON解析失败',
                'raw_response': full_content,
                'exception': str(e)
            }

    def validate_script_output(self, script, scene: Scene) -> Dict[str, any]:
        """
        验证AI生成的剧本是否有效
        检查：1) 位置是否存在  2) 动作是否存在  3) 状态是否匹配
        支持新格式（JSON数组）和旧格式（含scene_sequence的对象）
        """
        errors = []
        warnings = []

        if isinstance(script, dict) and "error" in script:
            errors.append(script["error"])
            return {"valid": False, "errors": errors, "warnings": warnings}

        # 兼容新格式（数组）和旧格式（对象）
        if isinstance(script, list):
            # 新格式：直接是场景数组
            scene_objects = script
        elif isinstance(script, dict):
            if "scene_sequence" in script:
                # 旧格式中间态，包装成新格式结构用于验证
                scene_objects = [{"scene": script["scene_sequence"]}]
            else:
                scene_objects = script if isinstance(script, list) else []
        else:
            errors.append("未知的剧本格式")
            return {"valid": False, "errors": errors, "warnings": warnings}

        for scene_idx, scene_obj in enumerate(scene_objects):
            scene_sequence = scene_obj.get("scene", [])

            for idx, segment in enumerate(scene_sequence):
                is_movement = "move" in segment

                # 检查 current position 有效性
                positions = segment.get("current position", [])
                for pos in positions:
                    pos_id = pos.get("position")
                    if pos_id and not scene.get_position(pos_id):
                        warnings.append(
                            f"场景{scene_idx}段落{idx}: 位置 '{pos_id}' 不在场景 '{scene.name}' 的可用点位中"
                        )

                if is_movement:
                    # 检查移动目标有效性
                    for move in segment.get("move", []):
                        dest = move.get("destination")
                        if dest and not scene.get_position(dest):
                            errors.append(
                                f"场景{scene_idx}段落{idx}: 移动目标 '{dest}' 不在场景 '{scene.name}' 的可用点位中"
                            )
                else:
                    # 检查动作有效性
                    for action in segment.get("actions", []):
                        action_id = action.get("action")
                        if not action_id:
                            continue
                        action_obj = self.resource_loader.get_action_by_id(action_id)
                        if not action_obj:
                            warnings.append(
                                f"场景{scene_idx}段落{idx}: 动作 '{action_id}' 不在动作资源库中"
                            )
                        else:
                            state = action.get("state", "standing")
                            if not action_obj.is_compatible_with_state(state):
                                warnings.append(
                                    f"场景{scene_idx}段落{idx}: 动作 '{action_id}' 不兼容状态 '{state}'"
                                )

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings
        }

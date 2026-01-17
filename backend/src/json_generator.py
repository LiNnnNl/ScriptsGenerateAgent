"""
JSON生成器模块
将导演AI的中间态指令转换为符合scene_json_spec.md规范的最终JSON文件
"""

from typing import List, Dict
from .resource_loader import Character, Scene


class ScriptJSONGenerator:
    """剧本JSON生成器"""
    
    def __init__(self, characters: List[Character], scene: Scene):
        self.characters = characters
        self.scene = scene
        self.character_states = {}  # 追踪每个角色的状态 (standing/sitting)
        self.character_positions = {}  # 追踪每个角色的位置
        
        # 初始化角色状态
        for char in characters:
            self.character_states[char.name] = "standing"
            self.character_positions[char.name] = None
    
    def generate_final_json(self, ai_script: Dict, plot_summary: str) -> List[Dict]:
        """
        将AI生成的中间态剧本转换为最终的JSON格式
        
        Args:
            ai_script: 导演AI生成的剧本数据
            plot_summary: 剧情概述（用于scene information的what字段）
        
        Returns:
            符合scene_json_spec.md规范的JSON数组
        """
        
        scene_sequence = ai_script.get("scene_sequence", [])
        
        # 构建scene information
        scene_info = {
            "who": [char.name for char in self.characters],
            "where": self.scene.name,
            "what": plot_summary
        }
        
        # 转换场景序列
        final_scene = []
        
        for segment in scene_sequence:
            seg_type = segment.get("type", "dialogue")
            
            if seg_type == "movement":
                # 移动场景
                movement_item = self._build_movement_item(segment)
                if movement_item:
                    final_scene.append(movement_item)
            
            elif seg_type in ["dialogue", "description"]:
                # 对白或描述场景
                dialogue_item = self._build_dialogue_item(segment)
                if dialogue_item:
                    final_scene.append(dialogue_item)
        
        # 返回完整结构
        return [
            {
                "scene information": scene_info,
                "scene": final_scene
            }
        ]
    
    def _build_movement_item(self, segment: Dict) -> Dict:
        """构建移动场景项"""
        
        moves = segment.get("move", [])
        
        # 更新位置追踪
        for move in moves:
            char_name = move.get("character")
            destination = move.get("destination")
            if char_name and destination:
                self.character_positions[char_name] = destination
        
        # 构建current position（移动前的位置）
        current_position = self._get_all_positions()
        
        return {
            "move": moves,
            "shot": segment.get("shot", "scene"),
            "camera": segment.get("camera", 1),
            "current position": current_position
        }
    
    def _build_dialogue_item(self, segment: Dict) -> Dict:
        """构建对白/描述场景项"""
        
        # 更新位置（如果segment提供了新位置）
        positions = segment.get("positions", [])
        for pos in positions:
            char_name = pos.get("character")
            position = pos.get("position")
            if char_name and position:
                self.character_positions[char_name] = position
        
        # 更新状态（根据动作）
        actions = segment.get("actions", [])
        for action in actions:
            char_name = action.get("character")
            action_id = action.get("action")
            
            # 如果是坐下动作，更新状态
            if action_id == "Interact_Sit_Down":
                self.character_states[char_name] = "sitting"
            # 如果是站起动作，更新状态
            elif action_id == "Interact_Stand_Up":
                self.character_states[char_name] = "standing"
        
        # 构建基础结构
        item = {
            "speaker": segment.get("speaker", "default"),
            "content": segment.get("content", ""),
            "shot": segment.get("shot", "character"),
            "actions": actions,
            "current position": self._get_all_positions()
        }
        
        # 添加可选字段
        if "shot_anchors" in segment:
            item["shot_anchors"] = segment["shot_anchors"]
        
        if "camera" in segment:
            item["camera"] = segment["camera"]
        
        if "motion_description" in segment:
            item["motion_description"] = segment["motion_description"]
        
        return item
    
    def _get_all_positions(self) -> List[Dict]:
        """获取所有角色的当前位置"""
        positions = []
        for char in self.characters:
            if self.character_positions.get(char.name):
                positions.append({
                    "character": char.name,
                    "position": self.character_positions[char.name]
                })
        return positions
    
    def export_to_file(self, output_data: List[Dict], filepath: str):
        """导出为JSON文件"""
        import json
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)
    
    @staticmethod
    def validate_against_spec(json_data: List[Dict]) -> Dict[str, any]:
        """
        验证生成的JSON是否符合scene_json_spec.md规范
        """
        errors = []
        warnings = []
        
        # 检查根结构
        if not isinstance(json_data, list):
            errors.append("根结构必须是数组")
            return {"valid": False, "errors": errors, "warnings": warnings}
        
        for idx, scene_obj in enumerate(json_data):
            # 检查scene information
            if "scene information" not in scene_obj:
                errors.append(f"场景{idx}: 缺少'scene information'字段")
            else:
                info = scene_obj["scene information"]
                if "who" not in info or not isinstance(info["who"], list):
                    errors.append(f"场景{idx}: 'who'字段必须是数组")
                if "where" not in info:
                    errors.append(f"场景{idx}: 缺少'where'字段")
                if "what" not in info:
                    errors.append(f"场景{idx}: 缺少'what'字段")
            
            # 检查scene数组
            if "scene" not in scene_obj:
                errors.append(f"场景{idx}: 缺少'scene'字段")
            elif not isinstance(scene_obj["scene"], list):
                errors.append(f"场景{idx}: 'scene'字段必须是数组")
            else:
                # 检查每个场景片段
                for seg_idx, segment in enumerate(scene_obj["scene"]):
                    # 检查必填字段
                    if "move" in segment:
                        # 移动场景
                        if "shot" not in segment:
                            warnings.append(f"场景{idx}片段{seg_idx}: 移动场景缺少'shot'字段")
                        if "current position" not in segment:
                            errors.append(f"场景{idx}片段{seg_idx}: 缺少'current position'字段")
                    else:
                        # 对白/描述场景
                        if "speaker" not in segment:
                            errors.append(f"场景{idx}片段{seg_idx}: 缺少'speaker'字段")
                        if "content" not in segment:
                            errors.append(f"场景{idx}片段{seg_idx}: 缺少'content'字段")
                        if "shot" not in segment:
                            errors.append(f"场景{idx}片段{seg_idx}: 缺少'shot'字段")
                        if "actions" not in segment:
                            errors.append(f"场景{idx}片段{seg_idx}: 缺少'actions'字段")
                        if "current position" not in segment:
                            errors.append(f"场景{idx}片段{seg_idx}: 缺少'current position'字段")
        
        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings
        }


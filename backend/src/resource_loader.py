"""
资源加载和验证模块
负责加载角色、场景、动作资源文件，并提供按画风筛选功能
"""

import json
from typing import List, Dict, Optional
from pathlib import Path


class Character:
    """角色资源"""
    def __init__(self, data: dict):
        self.id = data['id']
        self.name = data['name']
        self.style_tag = data['style_tag']
        self.description = data['description']
        self.personality = data['personality']
    
    def __repr__(self):
        return f"Character({self.name}, {self.style_tag})"


class Scene:
    """场景资源"""
    def __init__(self, data: dict):
        self.id = data['id']
        self.name = data['name']
        self.description = data['description']
        self.valid_positions = data['valid_positions']
        self.camera_groups = data.get('camera_groups', [])

    def get_position(self, position_id: str) -> Optional[dict]:
        """根据ID获取位置信息"""
        for pos in self.valid_positions:
            if pos['id'] == position_id:
                return pos
        return None

    def get_sittable_positions(self) -> List[str]:
        """获取所有可坐位置的ID列表"""
        return [pos['id'] for pos in self.valid_positions if pos.get('is_sittable', False)]

    def get_group_for_position(self, position_id: str) -> str:
        """返回该点位所属的camera_group id，若无分组则返回空字符串"""
        for pos in self.valid_positions:
            if pos['id'] == position_id:
                return pos.get('camera_group', '')
        return ''

    def __repr__(self):
        return f"Scene({self.name})"


class Action:
    """动作资源（通用，不区分画风）"""
    def __init__(self, data: dict):
        self.action_id = data.get('action_id') or data.get('trigger', '')
        self.category = data.get('category', '')
        self.description = data.get('description', '')
        self.file = data.get('file', '')
        self.compatible_states = data.get('compatible_states', ['standing'])
    
    def is_compatible_with_state(self, state: str) -> bool:
        """检查动作是否兼容指定状态"""
        return state in self.compatible_states
    
    def __repr__(self):
        return f"Action({self.action_id}, {self.category})"


class ResourceLoader:
    """资源管理器"""
    
    def __init__(self, resource_dir: str = "resources"):
        self.resource_dir = Path(resource_dir)
        self.characters: List[Character] = []
        self.scenes: List[Scene] = []
        self.actions: List[Action] = []
        
        self._load_all_resources()
    
    def _load_all_resources(self):
        """加载所有资源文件"""
        # 加载角色
        char_file = self.resource_dir / "characters_resource.json"
        with open(char_file, 'r', encoding='utf-8-sig') as f:
            char_data = json.load(f)
            self.characters = [Character(c) for c in char_data]
        
        # 加载场景
        scene_file = self.resource_dir / "scenes_resource.json"
        with open(scene_file, 'r', encoding='utf-8-sig') as f:
            scene_data = json.load(f)
            self.scenes = [Scene(s) for s in scene_data]
        
        # 加载动作
        action_file = self.resource_dir / "actions_resource.json"
        with open(action_file, 'r', encoding='utf-8-sig') as f:
            action_data = json.load(f)
            self.actions = [Action(a) for a in action_data]
    
    def get_available_styles(self) -> List[str]:
        """获取所有可用的画风标签"""
        styles = set()
        for char in self.characters:
            styles.add(char.style_tag)
        return sorted(list(styles))

    def get_characters_by_style(self, style_tag: str) -> List[Character]:
        """按画风筛选角色"""
        return [c for c in self.characters if c.style_tag == style_tag]

    def get_all_scenes(self) -> List[Scene]:
        """获取所有场景"""
        return list(self.scenes)
    
    def get_character_by_id(self, char_id: str) -> Optional[Character]:
        """根据ID获取角色"""
        for char in self.characters:
            if char.id == char_id:
                return char
        return None
    
    def get_character_by_name(self, name: str) -> Optional[Character]:
        """根据名称获取角色"""
        for char in self.characters:
            if char.name == name:
                return char
        return None
    
    def get_scene_by_id(self, scene_id: str) -> Optional[Scene]:
        """根据ID获取场景"""
        for scene in self.scenes:
            if scene.id == scene_id:
                return scene
        return None
    
    def get_action_by_id(self, action_id: str) -> Optional[Action]:
        """根据ID获取动作"""
        for action in self.actions:
            if action.action_id == action_id:
                return action
        return None
    
    def get_actions_by_category(self, category: str) -> List[Action]:
        """按分类获取动作"""
        return [a for a in self.actions if a.category == category]
    
    def get_actions_by_state(self, state: str) -> List[Action]:
        """获取与指定状态兼容的所有动作"""
        return [a for a in self.actions if a.is_compatible_with_state(state)]
    
    def validate_configuration(self, character_ids: List[str], scene_id: str) -> Dict[str, any]:
        """
        验证用户配置是否有效
        检查：1) 角色和场景是否存在  2) 画风是否一致
        """
        errors = []
        warnings = []
        
        # 检查场景是否存在
        scene = self.get_scene_by_id(scene_id)
        if not scene:
            errors.append(f"场景ID不存在: {scene_id}")
            return {"valid": False, "errors": errors, "warnings": warnings}
        
        # 检查角色是否存在
        characters = []
        for char_id in character_ids:
            char = self.get_character_by_id(char_id)
            if not char:
                errors.append(f"角色ID不存在: {char_id}")
            else:
                characters.append(char)
        
        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
            "scene": scene,
            "characters": characters
        }
    
    def build_custom_characters(self, custom_chars_input: List[Dict]) -> List[Character]:
        """根据用户输入构建自定义角色列表"""
        result = []
        for item in custom_chars_input:
            name = (item.get('name') or '').strip()
            if not name:
                continue
            desc = (item.get('description') or '').strip()
            char = Character({
                'id': name,
                'name': name,
                'style_tag': '',
                'description': desc if desc else f'用户自定义角色：{name}',
                'personality': desc if desc else '性格由AI自由发挥',
            })
            result.append(char)
        return result

    def get_resource_summary(self) -> str:
        """获取资源摘要信息"""
        summary = []
        summary.append(f"=== 资源库统计 ===")
        summary.append(f"角色总数: {len(self.characters)}")
        summary.append(f"场景总数: {len(self.scenes)}")
        summary.append(f"动作总数: {len(self.actions)}")
        summary.append(f"\n可用画风: {', '.join(self.get_available_styles())}")
        summary.append(f"场景列表: {', '.join([s.name for s in self.scenes])}")
        
        return "\n".join(summary)


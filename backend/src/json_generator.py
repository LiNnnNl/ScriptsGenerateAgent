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
    
    def generate_final_json(self, ai_script, plot_summary: str, title: str = "", preserve_shot_fields: bool = False) -> List[Dict]:
        """
        将AI生成的剧本转换为最终的JSON格式。

        支持两种输入：
        - 新格式（List）：AI直接输出符合规范的场景数组，仅补充 title 字段
        - 旧格式（Dict，含 scene_sequence）：兼容旧中间态格式，自动转换

        Args:
            ai_script: 导演AI生成的剧本数据（List 或 Dict）
            plot_summary: 剧情概述（用于 scene information.what，新格式中已由AI填写时可忽略）
            title: 剧集标题，用于生成 title 字段

        Returns:
            符合 scene_json_spec.md 规范并含 title、initial position 的完整 JSON 数组
        """

        # ── 新格式：AI 直接输出场景数组 ──
        if isinstance(ai_script, list):
            result = []
            for scene_obj in ai_script:
                scene_obj = dict(scene_obj)
                scene_info_default = scene_obj.get("scene information", {})
                scene_info_default["where"] = self.scene.id
                if "who" not in scene_info_default:
                    scene_info_default["who"] = [char.name for char in self.characters]
                if "what" not in scene_info_default:
                    scene_info_default["what"] = plot_summary
                normalized_beats = []
                prev_seg = None
                for i, seg in enumerate(scene_obj.get("scene", [])):
                    normalized = self._normalize_segment(seg, preserve_shot_fields, prev_seg)
                    normalized.setdefault("event_index", i)
                    normalized_beats.append(normalized)
                    prev_seg = normalized
                # 强制保证字段顺序：scene information → initial position → scene
                ordered = {
                    "scene information": scene_info_default,
                    "initial position": scene_obj.get(
                        "initial position",
                        [{"character": char.name, "position": ""} for char in self.characters]
                    ),
                    "scene": normalized_beats,
                }
                result.append(ordered)
            return result

        # ── 旧格式：含 scene_sequence 的中间态对象 ──
        scene_sequence = ai_script.get("scene_sequence", [])

        scene_info = {
            "who": [char.name for char in self.characters],
            "where": self.scene.id,
            "what": plot_summary
        }

        final_scene = []
        for segment in scene_sequence:
            seg_type = segment.get("type", "dialogue")
            if seg_type == "movement":
                item = self._build_movement_item(segment)
                if item:
                    final_scene.append(item)
            elif seg_type in ["dialogue", "description"]:
                item = self._build_dialogue_item(segment)
                if item:
                    final_scene.append(item)

        return [
            {
                "scene information": scene_info,
                "initial position": [
                    {"character": char.name, "position": self.character_positions.get(char.name, "")}
                    for char in self.characters
                ],
                "scene": final_scene
            }
        ]

    def _normalize_segment(self, seg: Dict, preserve_shot_fields: bool = False,
                            prev: Dict = None) -> Dict:
        seg = dict(seg)
        # Camera-only fields are written to camera_script; strip them from the main script.
        # Also strip current_position (underscore) — it's an internal processing artifact;
        # the canonical field is "current position" (with space).
        for field in ("shot", "shot_type", "shot_blend", "Follow", "camera", "current_position"):
            seg.pop(field, None)
        for action in seg.get("actions", []):
            action.setdefault("motion_detail", "")
        # 情绪分类（规则化，非LLM）
        emotion_info = self.classify_segment(seg, prev)
        seg["emotion"] = emotion_info["emotion"]
        seg["confidence"] = emotion_info["confidence"]
        seg["reason"] = emotion_info["reason"]
        return seg

    # ── 情绪分类 ──
    EMOTIONS = ["normal", "happy", "sad", "angry", "surprised", "disgusted", "fear"]

    def classify_segment(self, seg: Dict, prev: Dict = None) -> Dict:
        """
        规则化情绪分类，注入到每条 line 的 emotion/confidence/reason 字段。
        - 7个标签：normal/happy/sad/angry/surprised/disgusted/fear
        - 文本语义 + 语气符号 + 上下文判定主情绪
        - 一句一个主情绪；强度接近时沿用前句（连续性约束）
        - 低置信度默认 normal
        - 明显冲突保护：谢谢/好 → happy（不落到 angry/sad）
        """
        text = seg.get("content", "").strip()
        speaker = seg.get("speaker", "")

        if not text:
            return {"emotion": "normal", "confidence": 0.0, "reason": "空台词"}

        excl = text.count("！") + text.count("!")
        ques = text.count("？") + text.count("?")
        lower = text.lower()

        # ── 语义信号提取 ──
        neg = any(w in lower for w in ["不", "没", "别", "非", "无", "不会", "不是", "没在", "别来"])
        threat = any(w in lower for w in ["考考你", "考你", "你不得", "你还好意思", "配吗", "还敢", "谁给你的", "不得"])
        sarcasm = any(w in lower for w in ["不得", "还过", "你这", "咕咕", "你不得"])
        positive = any(w in lower for w in ["知道", "厉害", "棒", "聪明", "对", "好", "牛", "绝了", "都知道"])
        filler = len(text) <= 4 and excl == 0 and ques == 0

        # ── 强度等级 ──
        intensity = 1
        if ques or excl: intensity = 3
        if threat or sarcasm: intensity = 4
        if positive and not sarcasm: intensity = 2

        # ── 情绪判定 ──
        if ques > 0 and excl == 0:
            emotion, confidence = "surprised", 0.60
        elif excl > 0:
            emotion, confidence = ("surprised", 0.65) if (threat or neg) else ("happy", 0.70)
        elif threat and sarcasm:
            emotion, confidence = "angry", 0.70
        elif sarcasm:
            emotion, confidence = "sad", 0.65
        elif positive:
            # 明显冲突保护：知道/厉害 → happy，不会落到 angry/sad
            emotion, confidence = "happy", 0.80
        elif filler:
            emotion, confidence = "normal", 0.95
        else:
            emotion, confidence = "normal", 0.70

        # ── 低置信度 → normal ──
        if confidence < 0.60:
            emotion = "normal"
            confidence = 0.60

        # ── 同角色连续性约束 ──
        if (prev and prev.get("speaker") == speaker and
                prev.get("confidence", 0) > 0.75 and
                abs(intensity - prev.get("intensity", 3)) <= 1):
            emotion = prev["emotion"]
            confidence = max(confidence, prev["confidence"] - 0.05)

        if emotion not in self.EMOTIONS:
            raise ValueError(f"emotion 必须为 {self.EMOTIONS} 之一，实际值: {emotion}")
        reason = (f"text='{text[:15]}' excl={excl} ques={ques} "
                  f"threat={threat} sarcasm={sarcasm} pos={positive}")

        return {"emotion": emotion, "confidence": round(confidence, 2), "reason": reason,
                "intensity": intensity}

    def _build_title(self, title: str) -> Dict:
        """生成 title 字段模板"""
        return {
            "start": [title] if title else [],
            "EpisodeEndText": "本集结束",
            "end": [
                "导演与编剧智能体",
                "总导演 柯念昕",
                "编剧与故事设计 柯念昕 刘  艳 郑心怡",
                "________________________________________",
                "虚拟演员智能体",
                "语音表演设计 陈思涵",
                "角色表现与情感设计 柯念昕 刘  艳 张一蔚",
                "________________________________________",
                "摄影与视觉智能体",
                "摄影指导 马敬雯 王思颖",
                "镜头设计与运镜 柯念昕 刘  艳",
                "________________________________________",
                "动作与动画",
                "动作指导 林哲帆 吴翔宇 吕文涵",
                "动画与交互设计 陈  琦",
                "________________________________________",
                "美术与场景",
                "场景设计与解析 刘  艳 陈  可",
                "环境与灯光设计 林哲帆 陈思涵",
                "________________________________________",
                "制片部门",
                "监制 柯念昕",
                "制片人 柯念昕 刘  艳 陈  琦 林哲帆 陈思涵",
                "________________________________________",
                "后期制作",
                "视觉指导 王思颖 马敬雯",
                "成片优化 刘 艳 陈思涵",
                "________________________________________",
                "顾问团队",
                "技术顾问 吴清强 洪清启",
                "学术顾问 黄鸣奋",
                "艺术顾问 孙慧英",
                "项目顾问 佘莹莹",
                "产品顾问 肖  明",
                "________________________________________",
                "特别鸣谢",
                "开源社区贡献者",
                "测试团队与体验用户",
                "________________________________________",
                "出品与支持单位",
                "厦门大学电影学院",
                "厦门大学影视智能创作学社",
                "厦门大学影视制作智能技术研究中心",
                "厦门市人工智能创新中心",
                "________________________________________",
                "制作平台与技术支持",
                "Unity 实时三维引擎",
                "Filmaker创影者 V2.1",
                "________________________________________",
                "© 2026 All Rights Reserved"
            ]
        }
    
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
            "shot_blend": "",
            "shot": "",
            "shot_type": "",
            "shot_description": "",
            "Follow": segment.get("Follow", 0),
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
            "shot_blend": "",
            "shot": "",
            "shot_type": "",
            "shot_description": "",
            "Follow": segment.get("Follow", 0),
            "actions": actions,
            "current position": self._get_all_positions()
        }

        # 添加可选字段
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
        # 允许的 emotion 标签（规则化分类）
        EMOTION_WHITELIST = ["normal", "happy", "sad", "angry", "surprised", "disgusted", "fear"]
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
                    # ── emotion 字段验证 ──
                    # 有 speaker 且有 content 的才是真正台词行（移动行跳过）
                    is_dialogue = segment.get("speaker") and segment.get("content")
                    if is_dialogue:
                        emotion = segment.get("emotion")
                        if emotion is None:
                            errors.append(
                                f"场景{idx}片段{seg_idx}: 缺少'emotion'字段 "
                                f"（台词：'{segment.get('content', '')[:20]}'）"
                            )
                        elif emotion not in EMOTION_WHITELIST:
                            errors.append(
                                f"场景{idx}片段{seg_idx}: emotion='{emotion}' 不在白名单 "
                                f"（允许值：{EMOTION_WHITELIST}）"
                            )
                        conf = segment.get("confidence")
                        if conf is not None and not (0.0 <= conf <= 1.0):
                            errors.append(
                                f"场景{idx}片段{seg_idx}: confidence={conf} 超出 [0,1] 范围"
                            )
                        if "reason" not in segment:
                            warnings.append(
                                f"场景{idx}片段{seg_idx}: 缺少'reason'字段 "
                                f"（建议填写便于人工复核）"
                            )

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
                            warnings.append(f"场景{idx}片段{seg_idx}: 缺少'shot'字段")
                        if "actions" not in segment:
                            errors.append(f"场景{idx}片段{seg_idx}: 缺少'actions'字段")
                        if "current position" not in segment:
                            errors.append(f"场景{idx}片段{seg_idx}: 缺少'current position'字段")

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings
        }


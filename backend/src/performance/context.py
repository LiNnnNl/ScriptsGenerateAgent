"""Small, code-owned views of a screenplay beat for performance agents."""


def build_event_context(scene_obj, event_index):
    beats = scene_obj.get("scene", [])
    beat = beats[event_index]
    info = scene_obj.get("scene information", {})

    def line(item):
        if not isinstance(item, dict):
            return ""
        speaker = str(item.get("speaker") or "").strip()
        content = str(item.get("content") or "").strip()
        if speaker and content:
            return f"{speaker}：{content}"
        if content:
            return content
        moves = item.get("move") if isinstance(item.get("move"), list) else []
        return "；".join(
            f"{move.get('character', '角色')}移动到{move.get('destination', '目标位置')}"
            for move in moves if isinstance(move, dict)
        )

    nearby = []
    if event_index:
        nearby.append("前镜头：" + line(beats[event_index - 1]))
    nearby.append("本镜头：" + line(beat))
    if event_index + 1 < len(beats):
        nearby.append("后镜头：" + line(beats[event_index + 1]))
    script_description = "；".join(part for part in nearby if not part.endswith("："))
    what = str(info.get("what") or "").strip()
    if what:
        script_description = f"本幕剧情：{what}。{script_description}"

    visual_description = str(beat.get("shot_description") or "").strip()
    if not visual_description:
        visual_description = line(beat) or "未提供独立画面描述"
    return script_description, visual_description

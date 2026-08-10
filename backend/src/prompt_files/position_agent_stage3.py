position_agent_stage3_prompt = """## Stage 3 — 编译阶段 (stage3_compilation)

1. 编译最终的位置计划。
2. 移除所有 reason 字段。
3. 使用与 script_json.where 和 scene_info_json.where 完全相同的 where 值。
4. 恰好保留每个 position_id 一次。
5. 不要虚构任何额外字段。
6. 对于组的 lookat target 模式，恰好保留 target_character 或 target_object 之一。"""

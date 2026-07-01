character_generation_user_prompt = """请为以下场景创作 {character_count} 位角色的完整档案。

场景：{scene_desc}
{creative_idea_block}{char_instructions}{model_instruction}

请严格按照以下 JSON 数组格式输出。每位角色必须包含下列全部字段，不知道的字段留空字符串 ""，important_relationships 不知道的留空数组 []。直接输出 JSON 数组，不要有 ```json 包裹或任何说明文字：

{format_example}

要求：
- 输出恰好 {character_count} 位角色
- 每个角色对象必须且只能包含以上 10 个字段，字段名大小写完全一致
- gameobject_name 必须从「可用角色模型列表」中选取，填写列表中存在的值；无合适的则留空字符串
- important_relationships 中每条必须包含 object 和 relationship 两个字段
- 不知道的字段填空字符串，不要省略字段
- background 要有故事性，至少 30 字
- personality_traits 使用逗号分隔的词语
- 直接输出 JSON 数组，不加任何前缀后缀"""

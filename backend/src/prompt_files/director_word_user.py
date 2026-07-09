director_word_user_prompt = """请根据以下用户构想/文档生成具体剧本分镜。

## 用户构想/文档

{creative_idea}

## 生成要求

- 只输出符合系统要求的 JSON 数组
- 不要输出 Markdown、解释、前言或后记
- 分镜要具体到每一段画面和对白，`shot_description` 必须可直接放入 Word 给人阅读
"""

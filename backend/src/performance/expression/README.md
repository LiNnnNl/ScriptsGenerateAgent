# 表情选择包

表情负责人只需修改本目录：

- `rules.py`：ExpressionSelectionAgent 的选择规则。
- `stage.py`：剧本裁剪、表情库兼容判断、结果校验与安全回填。Agent 输入附带 `resources/pixar_emotions_context.md`；代码以 `resources/pixar_emotions_with_styles.json` 审查名称、样式、权重及每个复合表情最多 3 项。

输入只暴露当前镜头的表情字段、自然语言剧情/画面描述和合法角色；动作、站位及摄影参数不能被修改。最终合同和发布仍归上层 Pipeline。

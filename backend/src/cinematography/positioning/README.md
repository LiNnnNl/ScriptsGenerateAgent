# 点位生成包

这是当前生产链路的 Stage 2。在本仓库内，点位负责人只需修改这个目录；运行仍复用上层的 schema、场景节拍工具和 LLM 客户端。

- 改生成规则：`rules/grouping.py`、`rules/planning.py`
- 改分组、重试或校验流程：`stage.py`
- 改坐标计算：`coordinate_skill.py`
- 改 position_detail 转换：`detail_converter.py`

`backend/resources/cinematography/**` 是只读权威输入；三阶段顺序和最终输出合同仍由上层 `cinematography/__init__.py` 负责。

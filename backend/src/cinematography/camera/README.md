# 镜头生成包

这是当前生产链路的 Stage 1 与 Stage 3。在本仓库内，镜头负责人只需修改这个目录；运行仍复用上层的场景节拍工具和 LLM 客户端。

- 改所有镜头生成规则：`rules/`
- 改镜头描述生成：`shot_stage.py`
- 改景别、切换和跟随参数生成：`parameter_stage.py`

`CameraLib.json` 是只读候选库；阶段顺序、失败重试、`camera_script` 校验和发布仍由上层 `cinematography/__init__.py` 负责。

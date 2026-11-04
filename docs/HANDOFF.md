# 剧本生成维护交接（2026-11-04）

当前交接分支 `codex/handoff-20261104` 保存实际部署版本，包含后台任务、断线恢复和 native-v2.1 站位接入。
它基于 `25fd697`，不是已合并最新 main 的版本；main 的后续动作/表情模块开发需要另行评估合并。
不要强推 main 或把两套配置/源码混用。

动作生成、视频动作、统一登录、启动工具及新版站位引擎源码统一交接于私有仓库
[Motion-Script-Studio](https://github.com/LiNnnNl/Motion-Script-Studio/tree/codex/handoff-20261104)，
先读其中 `docs/HANDOFF.md`、`docs/DEPLOYMENT.md`、`docs/ASSETS.md`。
需要向仓库所有者申请访问；此公开仓库不包含私有引擎、真实场景包、人物素材、模型权重或密钥。

## 最小部署条件

1. 与本次实测一致的 Python 3.11 环境安装 `backend/requirements.txt`；前端是静态 JS，不用 npm 构建。
   历史 `.python-version` 标记 3.12，线上环境实际为 3.11.15。
2. 准备独立 Python 3.11 的站位环境、对应 release、ScenePackage、中文字体；具体见私有仓库。
3. 复制 `backend/position_agent.json` 到私有配置目录，修改 `home`，用环境变量
   `POSITION_AGENT_CONFIG=/absolute/path/to/position_agent.json` 指向它，勿直接改历史任务绑定的代码快照。
4. 在该 home 下私有 `model.env` 配 `GLM_API_KEY`（600 权限）。新版默认 `glm-5.3-flash`，
   思考开启且 `reasoning_effort=low`；模型配置由 `position_integration.configure_models()` 注入。
   `.env.example` 旧豆包配置仅适用于明确关闭 native 的兼容模式。
5. 设置 `POSITION_AGENT_FONT` 指向 Noto CJK 字体。仅 MinNan 已有完整新版场景包；缺少包不能继续正常新版生成。

```bash
# 仓库根目录，已配置好私有模型与站位路径后
python3.11 -m venv venv
venv/bin/python -m pip install -r backend/requirements.txt
venv/bin/python -m gunicorn --pythonpath backend --bind 127.0.0.1:5001 --workers 1 --worker-class gthread --threads 8 --timeout 900 --graceful-timeout 30 --access-logfile - --error-logfile - app:app
```

该命令保持前台运行；另一个终端检查 `curl --fail http://127.0.0.1:5001/api/health`。
Flask 同时提供 `/script/` 前端与 API，生产不需要另开 8080。
本服务不提供完整公网认证；必须放在受认证的 HTTPS 网关后，仅监听回环地址。
首次新机安装与真实模型/GPU 验收仍需接手者完成，不要在现有生产虚拟环境随意升级依赖。

## 使用与结果

- 正常生成输入创意；直接生成粘贴已有剧本，保留原对白；先以 MinNan 单幕短剧本验收。
- 页面使用 `POST/GET /api/generation_jobs`，稳定 job_id、防重复提交、独立工作进程。
  断线/刷新后查询原任务；网络错误不等于生成失败。不要对状态不明的模型请求重复计费提交。
- 当前一次一个活动任务。状态在 `backend/outputs/.jobs`，产物也在 outputs；备份必须包含隐藏目录。
- 原生四文件为 script、blocking_plan、spatial_solution、attention_plan；不能直接编辑正式交付破坏审批哈希。
  镜头引用 placement_id，身体朝向 body_facing_world。修改剧本应重新规划。
- 动作资源在 `backend/resources/actions_resource.json`，是 JSON 索引；不在剧本生成中调用 HY-Motion 推理。
  对应 FBX/Unity 动画资产另行交接，不能凭索引推断文件齐全。
- Unity pending / can_execute=false 仍需 Unity 实测，不能宣称可执行。

## 运维与排错

日志从 Gunicorn 标准输出以及任务目录查看。`ss -ltnp 'sport = :5001'` 确认端口与 PID，
再核对进程命令和工作目录。停止自己前台启动的实例可 Ctrl-C；原后台实例只对核实过的 master 发 TERM。
不要 pkill python。仅 Web 更新可 HUP master，但后台任务独立存活；更新站位 release 前先等任务结束。

502 先查服务是否存活和网关；401 查统一登录；network error 查同一 job_id 状态及任务错误记录；
429/额度不足查服务商账户；距离无解查真实 ScenePackage 和硬约束，不盲目加重试或放宽容差。
新版本按独立 release 目录发布，旧任务继续绑定旧快照；回滚整份配置，不删除新旧输出。

距离按 XZ 人物根坐标计算，1 单位=1 米，约数 ±0.3 米、精确数 ±0.05 米，仅增加 1e-6 米浮点余量。
“约 3 米”样例 3.300000429 米是容差边界通过，不是精确命中；旧版开场 1.56 米已不会静默通过。

## 验证

```bash
venv/bin/python -B -m unittest discover -s tests -v
node --check frontend/js/api.js
node --check frontend/js/main.js
node --check frontend/js/ui.js
python3 -m json.tool backend/position_agent.json > /dev/null
git diff --check
```

本次交接副本离线 124 项测试通过。详细新引擎/真实生成历史验收见
[v2 接入](position_agent_v2.md) 与 [v2.1 升级验收](position_agent_v2_1.md)。
本次上传整理没有重新调用付费模型或 GPU 推理，也未重启线上服务。
新机器、长篇多幕和真实 Unity 动画联调不能用这些离线结果代替验收。

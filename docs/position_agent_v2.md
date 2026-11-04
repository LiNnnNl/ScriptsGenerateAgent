# 新版走位接入与验证

部署与验收日期：2026-11-02 至 11-03。默认启用 native-v2，配置为 `backend/position_agent.json`。

**后续更新：2026-11-04 已升级数值距离版，当前配置与新增验收见 [v2.1 接入记录](position_agent_v2_1.md)。下文保留首版历史结果，尤其 3 米被规划为 1.56 米的问题不能当作当前版本仍未修复的结论；旧产物没有被覆盖。**

## 数据与运行位置

- 上传源码：`/mnt/data/lzf/character-assets/incoming/position_planning_agent_v2.zip`，223 个源码清单哈希匹配。
- 原始源码：`/mnt/data/lzf/position-planning/releases/20261102-facing`。
- Linux 部署源码：`/mnt/data/lzf/position-planning/releases/20261102-facing-linux`，只增加两个渲染模块的 Linux 中文字体查找。
- 独立 Python 环境：`/mnt/data/lzf/position-planning/venv`，按交付锁文件安装。未向旧剧本环境安装 LangGraph。
- 模型私密配置：`/mnt/data/lzf/position-planning/model.env`，权限 600；父目录权限 700。不输出密钥、不打包模型配置。
- 场景：`/mnt/data/lzf/position-planning/inputs/scenes/MinNan`，保留上传文件原始字节；同次导出 ID 为 `901d9e6ad7f84279af83fa1ce20d1de2`。
- 新站位任务：`/mnt/data/lzf/position-planning/jobs/script-<timestamp>-act-<n>`。
- 剧本后台任务：`backend/outputs/.jobs/<job_id>`。公开产物：`backend/outputs`。

## 接口与交付

`POST /api/generation_jobs` 接收 `{job_id, params}`，返回 202；`GET /api/generation_jobs/<id>?cursor=<byte-offset>` 返回状态及增量事件。相同编号、相同请求不会重复启动。当前最多一个完整剧本任务并行，运行中的任务由独立进程执行。浏览器刷新、网络断开或 Web worker 重启后可继续查询；机器重启后的中断会明确显示，暂不自动恢复完整剧本流程。

新版站位通过原生 `agent_runner run` 执行，HTTP 组件没有对公网开放。默认 `quality-v4`，每幕最多 64 次请求、500 万 token 准入预算、3600 秒阶段边界期限。额度含请求预留，不等同于实际计费 token；实际用量在对应 `run_result.json`。不自动增加预算或重试状态不明的请求。模型为 `glm-5.3-flash`，官方 BigModel 端点；剧本、标题和镜头也使用相同凭证与模型。

每幕正式四文件保留原始发布字节与哈希，放在走位 ZIP 的 `act_001/delivery/` 等目录。附 `final_result.json`、`run_result.json`、`RESULT.md`、任务列表、预览索引和图片。`camera_script.json` 是本项目额外生成的镜头伴随文件，目标点位引用同幕 `spatial_solution.placements[].placement_id`。镜头在 `before_event` 转换完成后取目标；`after_dialogue/after_actions` 的转换不能提前。多幕的汇总文件按 `scenes` 包装，但 Unity 交接应使用逐幕完整四文件目录。

ZIP 根目录另附 `integration_report.json`，包含本项目的原文距离偏差、移动连续性检查状态、镜头引用检查和镜头文件 SHA-256。不能仅看原生 `final_result.json` 判断所有原文要求均已满足；原生四文件不因追加检查而修改。

新版脚本删除旧 `initial position/current position/move` 槽位，纯移动事件将已有画面描述作为明确叙事输入；无描述时阻止提交。对白不变，事件顺序不变，增加稳定 script/event ID。站位服务不得修改输入字段。主剧本下载为新版结果，旧编辑稿仅用于上游叙事和资源校验。

发布前检查正式交付门槛、公开副本 SHA-256、原生 `verify-release`、场景与父文件引用、事件连续性、人物在场状态、点位归属、有限坐标、身体朝向空间、动作前姿态、注视人物和镜头目标。新方向采用 +Z=0°、+X=90°，禁止追加 180°。

## 验证中发现的问题

1. 原剧本豆包端点返回 `InvalidEndpoint.ClosedEndpoint`。已改为上传 GLM 配置。
2. GLM 5.3 拒绝 `thinking=disabled`，返回 1210。已按交付包相同约定启用思考并设 `reasoning_effort=low`；镜头阶段输出预算提高到 16384。
3. 试验设置的 30 万 token 准入额度不足。恢复到交付默认预算后通过；图像请求的保守预留可能超过一百万 token，必须区分预留和实际消耗。
4. Windows 字体路径在 Linux 上导致中文方框。Linux 快照加载 Noto Sans CJK SC；字体及 OFL 许可位于数据盘 `fonts/`。
5. 旧站位和新版 Blocking 结构不兼容。通过独立镜头视图读取新版状态，未让旧求解器改写坐标。
6. 原服务依赖启动目录寻找 outputs，而后台任务运行目录不同。下载、注册表与生成路径已固定到 backend/outputs。
7. 原评测脚本使用 Python 3.12 才支持的 f-string 写法，部署环境为 3.11。已做等价兼容修改，使项目测试可执行。
8. 移动镜头测试触发旧直接模式问题：无对白移动带有空 speaker/content，格式修复删除它们又被误判成改写对白。已在冻结对白前确定性规范化：叙事移入画面描述，真实说话人的对白不变。
9. 更重要的语义问题：原适配只传画面描述，丢失开场的距离和“原地不动”约束。换镜头的左右构图被新版模块理解成重新站位；模块自带门槛甚至全部通过。已传入原始本幕叙事、逐事件明确移动权限，并新增程序校验：未安排移动的人不得改变坐标，安排的移动必须落实。存在额外移动的验收产物已从成功列表隔离到 `backend/outputs/.rejected/1793634722277247478`，完整文件可恢复；对应原生运行保持原样供诊断。
10. 仅把开场约束放进场景摘要仍不足以通过新版 opening-facing 检查，出现 `opening evidence is missing or cites a later source`，随后停止重试。已把原始开场事实同时绑定到首事件的每个人物表演注释，后续实测通过。
11. **仍存在数值距离偏差**：移动样例开场要求约 3 米，最终坐标水平间距约 1.556 米；走近后要求约 1.5 米，实际约 1.100 米。原生规划保留了文本意图，却没有生成对应数值空间约束，沿用了双人布局间距。当前独立检查会将开场偏差标为 `AUTHORED_DISTANCE_MISMATCH`，在页面、校验报告及 ZIP 中展示，未擅自移动已审批坐标，也未宣称此问题解决。含“约”的距离允许 max(0.5 米, 20%) 偏差，因此本例移动后的 0.4 米偏差不触发警告。

## 实测验收记录（2026-11-03）

- 项目回归：121 项通过；含新版接入专项 14 项。原生 Linux 相关测试此前 35 项通过。Python 编译、前端 JS 语法及 JSON 配置校验通过。
- 静态端到端：`integration-facing-glm-low-20261102`，3 镜、2 句对白；交付、镜头引用、原生文件哈希与下载通过。
- 移动端到端：`integration-movement-opening-20261103`，4 镜、3 句对白；林阿公全程不平移，阿福仅在指定事件移动；原对白、事件连续性、镜头引用和四文件哈希通过，但存在上述距离偏差。对应产物时间戳 `1793635982951315646`。
- 两份成功测试的伴随报告已用最新检查重新生成；原生正式四文件与原始事件日志未改写。任务回放使用最新复核结果。
- 浏览器已验证首次提交断线后的同编号恢复、预览加载、只读剧本和真实 ZIP 下载；Web worker 重载期间独立生成任务继续运行。上述测试不等于外部公网永不掉线。
- 2026-11-03 00:31（北京时间）正式 5001 服务已平滑重载。正式服务及 9000 网关 `/script/` 均通过浏览器复测：首次请求断线、两次提交仅一个任务编号、距离警告展示、预览加载和 ZIP 下载，页面无脚本异常。网关健康与 ZIP 下载返回 200。公网隧道入口可达，未登录返回现有访问控制的 401，未绕过登录；公网登录后的完整操作未代替用户验收。

## 已知限制

- 只有 MinNan 有新版完整 ScenePackage。缺失场景在提交前拒绝，避免先消耗模型费用。
- 场景导出包含 38 个区域、1385 个网格、35 个姿态点；导出报告提示部分路径阻塞/未知，以及三个姿态点缺少接近点。规划通过不代表任意跨区动作均可行。
- `can_validate_in_unity=true` 仅表示可提交 Unity 验收；NavMesh、碰撞、移动、注视和动画适配仍需现场回执，未经验证 `can_execute=false`。
- 已批准的剧本不能在网页修改后沿用旧走位许可。页面只读展示原始剧本，剧情变化须重新生成。
- 长剧本、跨楼层、复杂进退场及所有动画资源并未因短样例通过而获得验收。出现无法闭合的规划问题时保留任务并报告，不能虚构通过。
- 当前距离检测只覆盖双人场景明确“相隔/相距…米”的开场或无对白事件，不是任意自然语言的完整空间约束验证器；多幕、复杂坐卧变化尚未实测。移动连续性按三维位置检查，未明确 move 的姿态根节点高度变化可能被严格拒绝。
- 旧 `move.then-interact` 的非空物体交互尚无新版运行时映射，明确拒绝而不丢弃；需要对应 Unity 执行适配。
- 服务沿用现有站点访问控制，不新增按用户隔离；任务和制品保留，未设置自动清理。

## 检查与恢复

查看站位任务 `worker.log`、`run/RESULT.md` 和 `run/reports/recovery.json`（若存在）。独立恢复应使用任务绑定的源码版本和原输入；源码变化不能直接复用旧检查点。恢复及提升预算须针对具体失败原因，不可通过改哈希或删检查结果绕过审批。

剧本服务启动时使用现有 venv，工作目录推荐为 backend：

```bash
cd /home/lzf/ScriptsGenerateAgent/backend
../venv/bin/python -m gunicorn --bind 127.0.0.1:5001 --workers 1 --worker-class gthread --threads 8 --timeout 900 app:app
```

操作前确认现有进程；不能重复占用端口。GLM 配置在应用和后台任务启动时读取，不需要把上传 .env 覆盖到旧 .env。

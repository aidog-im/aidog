# Feature spec — 守夜任务（mission / autonomous ops）

Status: **implemented**（2026-10-26，随 v0.5.44 发布车 1 最小闭环；同日
v0.5.45 车 2「编排与看板」增量落地，见 §7；同日 v0.5.46 车 3「守夜加固」
增量落地，见 §8）。
设计锚点：`docs/design/autonomous_ops_design.md` §3（操作流）与 §5 车 1/
车 2 验收闸。本页是第 32 份能力文档，覆盖 mission 的生命周期、三档汇报、
预算护栏、API/设置面与红线关系。

**边界**：车 1 = 最小闭环（立标 → 串行子任务逐分片执行 → 三档汇报 → 完成/
封存）；车 2 = 编排与看板（里程碑拆解、delegate 并行、`degraded` **入边**、
预算可配化与固化、`GET ?id=` 详情、控制台看板 + 只读编排视图，§7）；
**车 3 = 守夜加固**（`paused_approve` 入边与 web3 审批门联动、watchdog
心跳告警、`mission_tick` hook、静默期与最小间隔、A 面三约束，§8）。
没有通道也永不停摆：汇报
四级回退的最后一级是「仅写日志」。

Companion pages: [capabilities.md](capabilities.md)（能力目录契约）、
[natural_language_cron.md](natural_language_cron.md)（同源的定时腿与投递回退）、
[evolution_loop.md](evolution_loop.md)（同级的服务腿）、
[console_chat.md](console_chat.md)（分片执行复用的 run_turn）、
[event_hooks.md](event_hooks.md)（含车 3 新增的 `mission_tick` 事件）、
[frontend_coverage.md](frontend_coverage.md)（控制台设置面总矩阵）。

## 1. 生命周期（五态状态机）

Registry：`<state>/missions.json`（原子写 tmp + `os.replace`，历史 cap 50，
**同时至多一个 active**）；事件流：`<state>/mission_events.jsonl`
（append-only，kind ∈ `state` / `slice` / `report` / `sealed`）。

```
planned → running ⇄ paused_user          （控制台暂停/恢复）
             ├→ done                      （全部子任务完成或模型宣告完成）
             ├→ degraded ⇄ running        （GAP 入边；下一子项启动/UNBLOCK
             │                             回 running——车 2 开）
             └→ sealed                    （证据性封存：停止、预算触顶）
running → paused_approve ⇄ running        （web3 子项下单待批：该子项
                                            awaiting_approval、其余子项继续；
                                            审批清空自动回 running，车 3 开，§8）
```

- `done` 与 `sealed` 是**唯二终态**；`sealed` 必须携带 evidence（至少一条），
  永不空封。
- 合法边之外一律 409（如 `paused_user → done` 非法：只能 resume 后由执行器
  完成，或直接 stop 封存）。
- `planned` 是 create 落点，控制台 start 立即推进到 `running`；执行器 tick
  里还有一道防御性 promote。
- **pause 允许的起点是 `running | degraded`**（车 2 放宽），resume 只从
  `paused_user`；degraded 入边语义详见 §7；**`paused_approve` 态下
  pause/resume 恒 409**（detail 报 allowed states，车 3，E2E L6）。

## 2. 分片执行（守夜循环）

服务新腿 `_maybe_tick_missions`（`mycore/service.py`，与 cron 腿并列，
~5 s 节流）：

1. **先落 mark 再执行**：分片计数与 tick 戳先持久化，崩溃最多丢一次推进，
   永不重复执行（cron 腿同款哲学）。
2. **一分片 = 一次 `run_turn`**（`slice_iters` 默认 25）：分片 prompt 只带
   目标 + 当前子任务 + 已登记 gaps + 能力 manifest + 方法穷举清单——
   **不带全量历史**（上下文预算规则）。
3. **计划片 → 里程碑 + 串行队列**：模型按 `MILESTONE:`/`SUBTASK:` 行拆解
   （车 2 结构化 plan：无 MILESTONE = 单里程碑桶）；无标记则回退单任务 =
   目标本身；计划片忽略 `MISSION DONE`。
4. **断点续跑**：进度在 registry + checkpoint，不依赖内存；服务重启后按
   `last_tick` 判定 catch-up（>600 s 记 `MISSION_CATCHUP` 一行）。
5. 分片产出按标记协议折入状态机，事件流记录 `slice`（n/mode/stop/预览，
   预览经出口 scrub）。

**模型标记协议**（ASCII、行首、容忍 `- ` 项目符号，fail-soft —— 缺标记只是
子项继续在飞，由预算护栏兜底）：

| 标记 | 作用 |
|---|---|
| `MILESTONE: <title>` | 计划片分组标题：其后的 `SUBTASK:` 归入该里程碑；首个里程碑之前的散子任务进默认 **Plan** 桶（车 2） |
| `SUBTASK: <text>` | 计划片拆出一个串行子任务 |
| `TASK DONE` / `MISSION DONE` | 当前子项完成 / 整个目标达成 |
| `GAP: <text>` | 声明降级路径（触发 degraded 汇报） |
| `METHOD: <text>` / `TRIED: <text>` | 方法穷举清单加条目 / 打标 |
| `BLOCKER: <text>` | 卡点（all/milestones 档汇报，免节流保护之外） |
| `NEED DECISION: <text>` | 仅 PAID/AUTHORIZED/DECISION 项：该子项挂起，**其余子项立即继续** |
| `UNBLOCK: <tN>` | 解除挂起子项 |

### 意识层（宪法 + 提示层，双层）

- **宪法**：`overlay/constitution/SOUL.md` 新增
  `## Mission conduct (autonomous ops)` 六条款 C0-C5（穷举方法 / 中途不弃 /
  中途不问 / 诚实汇报含降级 / 能力诚实衔接硬律 §9 / 升级面窄不停车），位于
  Hard constitution 与 Stance 之间，9 条硬律一字未动。
- **分片 prompt** 显式写入「你在本任务中无法获得任何回答、批准或反馈——
  没有可问之人，不提问、不等待」（E2E 对**真实请求体**逐条断言）。
- **层次不变**：宪法恒在 system 栈最前；mission 目标文本只进 user 层
  （E2E 断言目标永不出现在 system 消息里）。

## 3. 三档汇报（settings `mission.reporting`）

| 档 | 触发（车 1 映射） | 节流（min_interval） |
|---|---|---|
| `all` | 每个子任务完成（`task_done`）+ 计划就绪 + 降级/卡点/异常 | 120 s，窗口内**合并**后一次发出 |
| `milestones`（**默认**） | 计划就绪 `plan_ready`（车 2 起 plan 为多里程碑结构；里程碑完成不单发档，子任务完成仅 `all` 档）+ 降级/卡点/异常 | 900 s |
| `final` | 仅终态（`done`/`sealed`）与需裁决 | 不限 |

- `decision` 与 `final` 事件**旁路节流**（永不被吞）；档外事件直接
  suppressed，不进合并队列。
- **消息格式**（复用谏言协议）：`[MISSION <id>] LEVEL 事实 → 影响 → 下一步`；
  终报固定四段：**完成 / 降级 gaps / 未竟 / 建议**（sealed 的「未竟」段列
  evidence）。
- **投递四级回退**（与 cron 完全同一条链，`_origin_deliver` 唯一实现）：
  `origin(route:target 或 chat id)` → env/槽 `MYCORE_WD_CHAT` → **仅写日志**
  （`delivered=none`）。mission 报告不发 hook（观察事件 `mission_tick` 车 3 已增，§8）；
  控制台 `stop` 的终报**不发 bot**（响应当场带回）。
- **凭据零回显**：所有出站报告经 `mycore.mission.scrub()` 单出口——
  token/bearer/`sk-` 形状与绝对机器路径替换为占位符（E2E 哨兵扫描零命中）。

## 4. 预算护栏（任一触顶 → 自动封存 + 汇报，永不静默）

| 护栏 | 默认 | 范围 |
|---|---|---|
| `max_slices` | 40 | 1..10000 |
| `max_wall_hours` | 24 | 0.1..8760（≤1 位小数） |
| `max_daily_model_calls` | 200（按北京日历滚动） | 1..100000 |

- **settings 六键（车 2 可配化 + 车 3 两键）**：settings `mission` 块 =
  `{reporting, max_slices, max_wall_hours, max_daily_model_calls,
  quiet_hours, min_interval}`，
  `mission.DEFAULT_SETTINGS` 常驻、`/api/settings` 正常读写，范围校验同源
  `mission.validate_mission_settings` / `mission.BUDGET_BOUNDS`。
- **start 可带 `budget{...}` 覆盖**：缺省取 settings 当时值，body 覆盖后做
  同源范围校验；**解析后的值固化进任务记录**——启动后再改 settings 不影响
  已启任务（E2E L5 对 30/2 两个冻结值分别断言）。

触顶时：封存先落盘（evidence 一行
`budget guard <guard> reached (<used>/<limit>)`）→ 事件流 `sealed` 记 guard
→ 免节流终报发出。sealed 之后**永不再跑分片**。

## 5. API 与设置面

| 端点 | 认证 | 契约 |
|---|---|---|
| `GET /api/mission` | loopback，无 auth | `{reporting, active: null\|row, recent[≤10], count}`；损坏/缺失 → 空结构，绝不 500；**无 id 行为即旧列表 4 键形状**（前端兼容，E2E 断言） |
| `GET /api/mission?id=<mid>` | loopback，无 auth | `{mission: {row, plan, events[≤20 升序], budget, budget_used}}`；row 带车 3 字段 `deadline/redlines/deliverable\|null` + `approvals:[{task, plans}]`，awaiting 子项行带 `plans`（审批深链）；未知 id → 404；车 2 前的旧 mission → `plan: null`（看板诚实显示“尚无计划”，车 2） |
| `POST /api/mission` | `Bearer WEB_TOKEN` | `{action: start\|stop\|pause\|resume, goal?, id?, reporting?, budget?, deadline?, redlines?, deliverable?}` → 200 行；400 校验 · 401 · 404 未知 id · 409 冲突（已有 active 再 start / 历史满 50 / 状态机不合法），409 带 `mission_id` 或原因；pause 接受 `running \| degraded`（车 2）；**`paused_approve` 下 pause/resume 恒 409 并在 detail 报 allowed states**（车 3）；三约束校验单一语法在 `mission.create`，非法 400 detail 以字段名开头 |
| `POST /api/settings {"mission": {...}}` | `Bearer WEB_TOKEN` | `mission.reporting ∈ all\|milestones\|final`（默认 `milestones`）+ 车 3 两键：`quiet_hours`（`""` 或 `HH:MM-HH:MM`，起≠止、可跨午夜）+ `min_interval`（0..86400，0=档默认），未知键/非法值 400 点名键；与写验证同源 `mission.validate_mission_settings` |
| `GET /api/capabilities` → `mission` | 无 auth | `{active, running, total, reporting}` 摘要——目标文本不上无认证目录 |

- start 语义：`goal` ≤4000 字符非空；`reporting` 参数**只覆盖该任务的档**，
  不写全局；F1 能力 manifest 在 start 时冻结（工具名 + 凭据键名 + **存在
  bool**、web3 `order_gate`/`withdrawals:false`/`paid_actions:false`、
  `mcp: disabled`）。
- 行字段走 `public_row` 固定白名单：id/goal/state/reporting/时间戳/
  progress/slices/daily_calls/gaps/blocked/budget/evidence——**无 origin、
  无 manifest、无内部路径、无凭据**；`goal` 是用户输入，原样回显。
- 任务看板卡（Lane F 设置面）读写以上同一对端点；detail 与看板同享
  `public_row` 白名单纪律（E2E 对 detail payload 做哨兵扫描）。

## 6. 红线关系（设计书 §6 精简）

- **提现/链上转账零代码**：mission 不新增任何资金出向工具——既有源码断言
  继续生效。
- **web3 审批门 fail-closed**：mission 仍只推进到 prepare/plan；下单类子项
  挂起进 `paused_approve`（0.5.46 车 3 已接入，E2E 红线断言 **execute 无
  审批仍被拒**），审批入口与 Bearer 闸形态不变。
- **社区 MCP 永久禁用**：manifest 生成期即写死 `mcp: disabled`，穷举方法
  不等于找野路子（C3：缺能力 = 降级登记）。
- **写盘范围**：分片复用 `execute_code` allowed-roots 与 checkpoint 写前
  快照；破坏性动作是声明，不是执行。
- **永久免费铁律**：能力面无任何付费工具（manifest `paid_actions:false`）。
- **永不静默**：三护栏触顶、封存、需裁决都必须有汇报（最低到日志级）。

## 7. 车 2 增量（0.5.45）：编排与看板

**（1）里程碑拆解（plan 结构化）**：计划片接受 `MILESTONE:` 分组行，
registry 落 `plan = {milestones: [{id, title, subtasks: [tN…]}],
generated_at}`，扁平 `tasks[]` 仍是状态唯一真值（detail 投影时把
`in_progress` 映射为看板词 `running`）；里程碑前的散子任务进 **Plan** 桶，
无 MILESTONE 的旧协议退化为单里程碑（车 1 兼容）。`METHOD:`/`TRIED:`
同片折入方法穷举清单，detail 里以 `methods[{text, marked}]` + `blockers[]`
回读。

**（2）delegate 并行**：mission 分片保留全工具集（含 `delegate` ≤3、隔离
上下文、递归护栏——`mycore/subagent.py` 不动）；`run_turn` 把本片 chat
绑定进 registry，一个分片可发起 ≥2 次 delegate 调用推进多个子任务；
slice 事件新增 `delegate` 计数字段（`mission.delegate_count(tool_log)`），
看板据此展示并行分发而无需解析预览。

**（3）degraded 入边（车 1 只留了出边）**：`running → degraded` 当前子项
未完成时出现新 `GAP:`；**单点降级不停摆（F4）**——队列与执行腿照走，下一
子项启动（`set_current`）或 `UNBLOCK:` 即 `degraded → running`，degraded
子项保留为“记录”不回炉；pause 自 degraded 合法（→ `paused_user`），
resume 后 gaps 原样保留；`degraded` 态分片照常执行。

**（4）预算固化**：settings 四键 + start `budget{...}` 覆盖（§4）；解析
值冻结进任务记录，之后改 settings 不再影响已启任务。

**（5）看板 + 编排视图（控制台，Lane I）**：MISSIONS 卡的 recent/active 行
可点开详情区（里程碑分组、子任务五态符号、方法穷举 details、事件尾窗、
预算占用与警示、状态徽章含 degraded）；missionCard 新增预算三输入
（start 时只提交填写项，留空 = 取 settings）；新增只读 `#orchCard` 编排卡
（工具/渠道/web3/槽位/skills 五源汇总 + 深链到现有控件，**不建第二套开
关**，数据源 = `/api/capabilities` 等既有端点）；locales 新增
`mission.*`/`orch.*` 32 键 → packs **594/594**（车 3 再 +20 → **614/614**，
见 §8（6））。

**（6）车 2 验收闸证据**：`tmp/e2e_mission_orch.py` 四态 E2E（§9）——
拆解 → 并行 → 降级 → 封存全链真跑。

## 8. 车 3 增量（0.5.46）：守夜加固

**（1）web3 `paused_approve` 联动（后端 Lane K）**：分片中成功的
`exchange_prepare_order` 预览出 `pl_<12hex>` 且计划仍 `pending` 时，该
子项挂起为 `awaiting_approval`（记录 plan ids），`running → paused_approve`
入边打开（同窗第二单只记录、不重复转移；degraded/paused_user/planned 入边
恒非法）；`paused_approve` 照常执行分片，`set_current` 跳过挂起子项——
**其余子项逐 tick 继续推进**（F4）。服务腿每 tick 先 `observe_approvals`
（approved/executed → 子项回 pending 重新排队；rejected/expired/cancelled →
子项 degraded + gap + 汇报，不停摆；pending/未知/读取异常 → 继续等，读失败
绝不编造裁决；全部落定 → `paused_approve → running`，事件 `via=approval`）。
**审批门形态零改动**（prepare→approve→execute、Bearer、当日 ledger 全不
动），`execute` 无审批仍被拒；`paused_approve` 态 pause/resume 恒 409
（detail 报 allowed states）。detail 新增 `deadline/redlines/deliverable|null`
+ `approvals:[{task, plans}]`，awaiting 子项行带 `plans`（深链数据）。

**（2）watchdog 心跳告警**：`MISSION_STALE_SEC=900`，active 态
（running/degraded/paused_approve）的 `last_progress_at`（旧行回退
updated_at/created_at）超阈 → `⚠️ [MISSION HEARTBEAT] 任务 {id} 已 {N} 分钟
无进展（{state}）· {goal}` 走既有 notify 路径（goal 经 scrub、零凭据；
dry_run 只闸重启、不抑告警）；内存节流按 `throttle_sec` 重报（never-
silent，重启即重报），恢复进度/进入终态即静默；registry 读失败 fail-soft。

**（3）`mission_tick` hook**：白名单 +1 事件；每个 active mission 每 tick
恰好一次（无 active 不发，paused_user 也算 active——观察腿不观察模型），
子进程 env 仅 `AIDOG_HOOK_MISSION`（净化 id≤32）/ `_MISSION_STATE`（枚举）/
`_MISSION_PROGRESS`（`d/t`）+ PATH/HOME——零回显、不阻塞、不超时即丢弃
非法值（E2E L6 对真实脚本捕获的 env 逐键断言）。

**（4）静默期与最小间隔（settings 两键）**：`mission.quiet_hours`（`""` =
关，或 `HH:MM-HH:MM`，起≠止、可跨午夜）——窗内只放行 decision/final
（旁路且**单独**发出、合并队列保留），其余进队列、窗后首个非静默点**合并**
发出；`mission.min_interval`（0..86400，0 = 档默认 120/900/∞）与窗口都
逐 tick 活读（save = effective）；非法值 400 detail 点名键，坏窗口绝不静默
（fail-soft 退回关）。

**（5）A 面三约束**：start 可选 `deadline`（ISO8601 且须在未来，naive 按
+08:00）/ `redlines`（≤500 纯文本）/ `deliverable`（≤200 纯文本），空/空白
= 不传；校验单一语法在 `mission.create`（所有 start 路径同源），非法 400
detail 以字段名开头；三约束注入**计划片与执行片** prompt（`Owner constraints`
段，F2 展示不等待），detail/row 回显、缺省 null。

**（6）控制台（Lane K2）**：MISSIONS 卡新增可折叠约束区（datetime-local →
ISO8601 传参、redlines textarea ≤500 实时计数、deliverable ≤200，只传填写
项，不挤爆主表单）；详情区显示三约束（deadline 相对时间 + 过期红字、
redlines/deliverable 文本块）与 **APPROVALS 区**（`paused_approve` 待批红
徽章 + 每项 `{task, plans[]}` 列表、plan id 点击深链到既有 Web3 面板并高亮
对应卡片 + 「在 Web3 面板完成审批后自动恢复，无需在此操作」文案，审批完成
控制台零操作、下 tick 自动回 running）；档位旁新增 quiet_hours / min_interval
控件（change 即写 `/api/settings` 同款机制，400 detail 原样显示）；五态徽章
补全（paused_approve 红 vs degraded 琥珀）+ awaiting_approval 子项符号；
locales 新增 20 个 `mission.*` 键 → packs **614/614**；既有
`mission_card_verify` / `mission_board_verify` 两探针 0 FAILED（
`#missionCard` 内 `<button>` 恒 4、三档 select、五符号集均不破）。

**（7）车 3 验收闸证据**：`tmp/e2e_mission_pause.py` — **E2E 73/73 PASS ·
7 legs (L1–L7)**：L1 审批挂起/恢复（prepare → paused_approve +
approvals 深链数据 + decision 上线 + 其余子项继续推进；console approve →
下 tick 回 running → 推进到 done + 终报；红线：execute 无审批被拒、模型
工具集无 approve 路径、本地 mock 收到 0 次 order POST）· L2 心跳（900s+
陈旧 → notify 路径告警一次 → 节流窗内不重复 → 越窗重报 → 恢复进度即
静默，dry_run 全程开、告警零机器路径）· L3 quiet hours（窗内
plan_ready/task_done 排队零上线、decision 单独放行且队列保留、窗后首个
非静默点合并发出；min_interval 3600 与档默认 120 两档实测：60s 间隔排队 /
4000s、200s 间隔发出）· L4 三约束（4 种非法 400 点名字段、缺省 null、
合法回显三字段、计划片请求体含 `Owner constraints` 三值）· L5 checkpoint
续跑压测（新进程 `MISSION_CATCHUP`、进度与计划结构不丢、分片号连续不重
置、事件流严格 append-only、重启后终报照发）· L6 暂停窗（pause/resume
409 报 allowed states + mission_tick env 逐键断言每 tick 恰一次、零回显）·
L7 生产 `secrets.env`/`web/settings.json` 字节恒等 + 全程哨兵/机器路径清扫
+ 目标文本永在 user 层。隔离姿势同车 1/2（throwaway profile + stub 模型
+ stub TG + 本地 OKX book mock、无订单端点）。另有 `tmp/lane_k_probe.py`
**19/19**（静默期合并/三约束注入/心跳三探）。Run:
`python3 tmp/e2e_mission_pause.py`（rc 0 = 0 fail）。

## 9. 证据（gates & tests）

- `python3 -m mycore.tests.run` → `test_mission`：**285 个运行时 check**
  （状态机合法/非法边含 degraded 入边与车 3 挂起/恢复、registry 原子与
  cap、标记协议折入含 MILESTONE 分组、reporter 三档 + 节流 + 合并 + 旁路
  + 静默窗 + min_interval、预算三护栏封存、scrub、manifest、settings 校验
  含六键、`public_row` 白名单含三约束与 approvals、detail 投影与事件尾 20
  行截断）；全套件 **1477/1477**。
- **`tmp/e2e_mission.py`（本车验收闸，Lane G）— `E2E 54/54 PASS` ·
  8 legs (L1–L8)**：L1 立标（三档 settings 回读 + 非法档 400 + start/
  409/capabilities 摘要）· L2 真实分片（事件流 state+slice + 看板进度）·
  L3 三档汇报各一例 + 120 s 窗口 queued→合并发出 · L4 done 四段报告 +
  stop 封存 evidence（且 stop 对线路静默）· L5 `max_slices=2` 触顶封存 +
  evidence + 终报 · L6 逐请求断言 system 栈含 Mission conduct、prompt 含
  「无法获得任何回答」、目标只在 user 层 · L7 出口哨兵扫描（报告/看板/
  事件流/模型请求体零凭据零机器路径）· L8 生产 `secrets.env` +
  `web/settings.json` 字节恒等。隔离姿势：独立 throwaway profile + 独立
  端口控制台子进程 + 本地 stub 模型端点（真 HTTP 分片）+ stub TG 传输捕获
  报告。Run: `python3 tmp/e2e_mission.py`（rc 0 = 0 fail）。
- **`tmp/e2e_mission_orch.py`（车 2 验收闸，Lane J）— `E2E 63/63 PASS` ·
  6 legs (L1–L6)**：L1 拆解（settings 四键写读 + start 冻结 settings 预算
  30 + `GET ?id=` 断言三里程碑分组/Plan 桶/METHOD·TRIED 打标/plan_ready
  已发）· L2 并行（同一 exec 片 2 次 `delegate` 分发真跑 3 个子代理回
  stub、分片工具集含 delegate、扇出覆盖 ≥2 个排队子任务且当前子项同片
  完成，slice 事件 `delegate=2`）· L3 降级（GAP → state/subtask 双
  degraded 且队列完好 → 下一片穿行 degraded 推进下一子项回 running（F4）
  → 二次 GAP 再入 → 自 degraded pause 合法 / 暂停态零执行 / resume 后
  gaps 保留，并把事件流推过 20 行）· L4 封存（`budget{max_slices:2}` 触顶
  → sealed + evidence 带计数 + 封存报发出永不静默；POST stop →
  `stopped via the console` 且对线路静默）· L5 detail（形状 row+plan/
  events/budget/budget_used、事件尾 = 流末 20 行升序、settings 改
  7 不动已启预算 30/覆盖 2、未知 id 404、无 id 4 键旧形状、detail 哨兵
  扫描）· L6 生产 `secrets.env` + `web/settings.json` 字节恒等 + 全进程
  哨兵/机器路径清扫 + 目标文本永在 user 层。隔离姿势与车 1 完全同款。
  Run: `python3 tmp/e2e_mission_orch.py`（rc 0 = 0 fail）。
- **`tmp/e2e_mission_pause.py`（车 3 验收闸，Lane K2）— `E2E 73/73 PASS` ·
  7 legs (L1–L7)**：详见 §8（7）（审批挂起/恢复主线 + 心跳告警 + 静默期/
  最小间隔两档实测 + 三约束 + checkpoint 续跑压测 + 409/hook env + 生产
  字节恒等与哨兵清扫）。Run: `python3 tmp/e2e_mission_pause.py`（rc 0 = 0
  fail）；辅以 `tmp/lane_k_probe.py` **19/19**。
- `python3 deploy/check_publish.py` → READY(0)（本页在 allow 域，无机器
  路径/凭据/品牌词）；`python3 tests/source_lang_check.py` → 源码零 CJK
  （本页 docs 豁免，中文按仓库文档惯例）。

## 10. 文件索引

| 文件 | 职责 |
|---|---|
| `mycore/mission.py` | 状态机、registry、串行队列、标记协议、预算护栏、reporter（节流/合并/旁路）、`scrub`、`public_row`、settings 校验 |
| `mycore/service.py` | `_maybe_tick_missions` / `_tick_one_mission` 推进腿 + `_mission_deliver`（复用 `_origin_deliver` 四级回退） |
| `web/server.py` | `GET/POST /api/mission`、settings `mission` 分区、`capabilities.mission` |
| `overlay/constitution/SOUL.md` | `## Mission conduct (autonomous ops)` C0-C5 |
| `mycore/tests/run.py` | `test_mission` 单测 |
| `tmp/e2e_mission.py` | 车 1 验收闸（本页 §9） |
| `tmp/e2e_mission_orch.py` | 车 2 四态验收闸（本页 §9） |
| `tmp/e2e_mission_pause.py` | 车 3 审批挂起/恢复 + 守夜加固验收闸（本页 §9） |
| `tmp/lane_k_probe.py` | 车 3 三探（静默期/三约束/心跳） |
| `mycore/watchdog.py` | 车 3 心跳告警（`MISSION_STALE_SEC` / 节流重报） |
| `mycore/hooks.py` | 事件白名单 + `mission_tick` env（零回显） |
| `docs/design/autonomous_ops_design.md` | 设计书（车 1–5 分期、红线全表） |

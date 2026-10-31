# Feature spec — 守夜任务（mission / autonomous ops）

Status: **implemented**（2026-10-26，随 v0.5.44 发布，车 1 最小闭环）。
设计锚点：`docs/design/autonomous_ops_design.md` §3（操作流）与 §5 车 1
验收闸。本页是第 32 份能力文档，覆盖 mission 的生命周期、三档汇报、预算
护栏、API/设置面与红线关系。

**诚实边界**：这是一条**最小闭环**（车 1）——立标 → 串行子任务逐分片执行 →
三档汇报 → 完成/封存。目标拆解编排、delegate 并行、`degraded`/`paused_approve`
两态的**入边**、看板视图属车 2/3（状态枚举已预留，本车不进入）；静默期
（quiet hours）与 `mission_tick` hook 同属车 3。没有通道也永不停摆：汇报
四级回退的最后一级是「仅写日志」。

Companion pages: [capabilities.md](capabilities.md)（能力目录契约）、
[natural_language_cron.md](natural_language_cron.md)（同源的定时腿与投递回退）、
[evolution_loop.md](evolution_loop.md)（同级的服务腿）、
[console_chat.md](console_chat.md)（分片执行复用的 run_turn）、
[event_hooks.md](event_hooks.md)（车 3 将增 `mission_tick` 事件）、
[frontend_coverage.md](frontend_coverage.md)（控制台设置面总矩阵）。

## 1. 生命周期（五态状态机）

Registry：`<state>/missions.json`（原子写 tmp + `os.replace`，历史 cap 50，
**同时至多一个 active**）；事件流：`<state>/mission_events.jsonl`
（append-only，kind ∈ `state` / `slice` / `report` / `sealed`）。

```
planned → running ⇄ paused_user          （控制台暂停/恢复）
             ├→ done                      （全部子任务完成或模型宣告完成）
             └→ sealed                    （证据性封存：停止、预算触顶）
degraded / paused_approve                 （仅枚举 + 出边，入边车 2/3 开）
```

- `done` 与 `sealed` 是**唯二终态**；`sealed` 必须携带 evidence（至少一条），
  永不空封。
- 合法边之外一律 409（如 `paused_user → done` 非法：只能 resume 后由执行器
  完成，或直接 stop 封存）。
- `planned` 是 create 落点，控制台 start 立即推进到 `running`；执行器 tick
  里还有一道防御性 promote。

## 2. 分片执行（守夜循环）

服务新腿 `_maybe_tick_missions`（`mycore/service.py`，与 cron 腿并列，
~5 s 节流）：

1. **先落 mark 再执行**：分片计数与 tick 戳先持久化，崩溃最多丢一次推进，
   永不重复执行（cron 腿同款哲学）。
2. **一分片 = 一次 `run_turn`**（`slice_iters` 默认 25）：分片 prompt 只带
   目标 + 当前子任务 + 已登记 gaps + 能力 manifest + 方法穷举清单——
   **不带全量历史**（上下文预算规则）。
3. **计划片 → 串行队列**：模型按 `SUBTASK:` 行拆解；无标记则回退单任务 =
   目标本身；计划片忽略 `MISSION DONE`。
4. **断点续跑**：进度在 registry + checkpoint，不依赖内存；服务重启后按
   `last_tick` 判定 catch-up（>600 s 记 `MISSION_CATCHUP` 一行）。
5. 分片产出按标记协议折入状态机，事件流记录 `slice`（n/mode/stop/预览，
   预览经出口 scrub）。

**模型标记协议**（ASCII、行首、容忍 `- ` 项目符号，fail-soft —— 缺标记只是
子项继续在飞，由预算护栏兜底）：

| 标记 | 作用 |
|---|---|
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
| `milestones`（**默认**） | 里程碑 = `plan_ready`（车 1 无更细里程碑结构）+ 降级/卡点/异常 | 900 s |
| `final` | 仅终态（`done`/`sealed`）与需裁决 | 不限 |

- `decision` 与 `final` 事件**旁路节流**（永不被吞）；档外事件直接
  suppressed，不进合并队列。
- **消息格式**（复用谏言协议）：`[MISSION <id>] LEVEL 事实 → 影响 → 下一步`；
  终报固定四段：**完成 / 降级 gaps / 未竟 / 建议**（sealed 的「未竟」段列
  evidence）。
- **投递四级回退**（与 cron 完全同一条链，`_origin_deliver` 唯一实现）：
  `origin(route:target 或 chat id)` → env/槽 `MYCORE_WD_CHAT` → **仅写日志**
  （`delivered=none`）。mission 报告不发 hook（车 3 增 `mission_tick`）；
  控制台 `stop` 的终报**不发 bot**（响应当场带回）。
- **凭据零回显**：所有出站报告经 `mycore.mission.scrub()` 单出口——
  token/bearer/`sk-` 形状与绝对机器路径替换为占位符（E2E 哨兵扫描零命中）。

## 4. 预算护栏（任一触顶 → 自动封存 + 汇报，永不静默）

| 护栏 | 默认 | 范围 |
|---|---|---|
| `max_slices` | 40 | 1..5000 |
| `max_wall_hours` | 24 | 0.01..8760 |
| `max_daily_model_calls` | 200（按北京日历滚动） | 1..100000 |

触顶时：封存先落盘（evidence 一行
`budget guard <guard> reached (<used>/<limit>)`）→ 事件流 `sealed` 记 guard
→ 免节流终报发出。sealed 之后**永不再跑分片**。

## 5. API 与设置面

| 端点 | 认证 | 契约 |
|---|---|---|
| `GET /api/mission` | loopback，无 auth | `{reporting, active: null\|row, recent[≤10], count}`；损坏/缺失 → 空结构，绝不 500 |
| `POST /api/mission` | `Bearer WEB_TOKEN` | `{action: start\|stop\|pause\|resume, goal?, id?, reporting?}` → 200 行；400 校验 · 401 · 404 未知 id · 409 冲突（已有 active 再 start / 历史满 50 / 状态机不合法），409 带 `mission_id` 或原因 |
| `POST /api/settings {"mission": {...}}` | `Bearer WEB_TOKEN` | `mission.reporting ∈ all\|milestones\|final`（默认 `milestones`），未知键/非法档 400；与写验证同源 `mission.validate_mission_settings` |
| `GET /api/capabilities` → `mission` | 无 auth | `{active, running, total, reporting}` 摘要——目标文本不上无认证目录 |

- start 语义：`goal` ≤4000 字符非空；`reporting` 参数**只覆盖该任务的档**，
  不写全局；F1 能力 manifest 在 start 时冻结（工具名 + 凭据键名 + **存在
  bool**、web3 `order_gate`/`withdrawals:false`/`paid_actions:false`、
  `mcp: disabled`）。
- 行字段走 `public_row` 固定白名单：id/goal/state/reporting/时间戳/
  progress/slices/daily_calls/gaps/blocked/budget/evidence——**无 origin、
  无 manifest、无内部路径、无凭据**；`goal` 是用户输入，原样回显。
- 任务看板卡（Lane F 设置面）读写以上同一对端点。

## 6. 红线关系（设计书 §6 精简）

- **提现/链上转账零代码**：mission 不新增任何资金出向工具——既有源码断言
  继续生效。
- **web3 审批门 fail-closed**：本车只推进到 prepare/plan；下单类子项的
  `paused_approve` 挂起态车 3 才接入，审批入口与 Bearer 闸形态不变。
- **社区 MCP 永久禁用**：manifest 生成期即写死 `mcp: disabled`，穷举方法
  不等于找野路子（C3：缺能力 = 降级登记）。
- **写盘范围**：分片复用 `execute_code` allowed-roots 与 checkpoint 写前
  快照；破坏性动作是声明，不是执行。
- **永久免费铁律**：能力面无任何付费工具（manifest `paid_actions:false`）。
- **永不静默**：三护栏触顶、封存、需裁决都必须有汇报（最低到日志级）。

## 7. 证据（gates & tests）

- `python3 -m mycore.tests.run` → `test_mission`：**138 个运行时 check**
  （状态机合法/非法边、registry 原子与 cap、标记协议折入、reporter 三档 +
  节流 + 合并 + 旁路、预算三护栏封存、scrub、manifest、settings 校验、
  `public_row` 白名单）。
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
- `python3 deploy/check_publish.py` → READY(0)（本页在 allow 域，无机器
  路径/凭据/品牌词）；`python3 tests/source_lang_check.py` → 源码零 CJK
  （本页 docs 豁免，中文按仓库文档惯例）。

## 8. 文件索引

| 文件 | 职责 |
|---|---|
| `mycore/mission.py` | 状态机、registry、串行队列、标记协议、预算护栏、reporter（节流/合并/旁路）、`scrub`、`public_row`、settings 校验 |
| `mycore/service.py` | `_maybe_tick_missions` / `_tick_one_mission` 推进腿 + `_mission_deliver`（复用 `_origin_deliver` 四级回退） |
| `web/server.py` | `GET/POST /api/mission`、settings `mission` 分区、`capabilities.mission` |
| `overlay/constitution/SOUL.md` | `## Mission conduct (autonomous ops)` C0-C5 |
| `mycore/tests/run.py` | `test_mission` 单测 |
| `tmp/e2e_mission.py` | 车 1 验收闸（本页 §7） |
| `docs/design/autonomous_ops_design.md` | 设计书（车 1–5 分期、红线全表） |

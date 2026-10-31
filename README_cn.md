<p align="center">
  <img width="140" src="site/assets/icons/icon-512.png" alt="I'm AiDog logo">
</p>

<h1 align="center">I'm AiDog（我是智能狗）</h1>

<p align="center">
  <em>您的忠实AI伙伴</em><br>
  <strong>为一个具体的人长出来的具身 AI · 但任何人都能跑起来</strong><br>
  宪法人格 + 策展记忆 + 自研核心，只效忠你一个人。<br>
  不是平台，不是助手农场——一个专属伙伴，长给你一个人，工程化到人人都能部署。
</p>

<p align="center">
  <a href="https://github.com/aidog-im/aidog/releases"><img src="https://img.shields.io/badge/release-0.5.53-blue?style=flat-square" alt="release 0.5.53"></a>
  <a href="https://aidog.im"><img src="https://img.shields.io/badge/website-aidog.im-2892ff?style=flat-square" alt="website"></a>
  <img src="https://img.shields.io/badge/core_tests-1649%2F1649-brightgreen?style=flat-square" alt="核心测试 1649/1649">
  <img src="https://img.shields.io/badge/console_smoke-283%2F283-brightgreen?style=flat-square" alt="控制台冒烟 283/283">
  <img src="https://img.shields.io/badge/permanently-free-ff6f00?style=flat-square" alt="永久免费">
</p>

<p align="center">
  <a href="#关于">关于</a> ·
  <a href="#核心能力">核心能力</a> ·
  <a href="#架构">架构</a> ·
  <a href="#快速开始">快速开始</a> ·
  <a href="#人工验收">人工验收</a> ·
  <a href="#质量闸">质量闸</a> ·
  <a href="#文档">文档</a> ·
  <a href="#路线图">路线图</a> ·
  <a href="#版本与升级">版本</a> ·
  <a href="#安全姿态如实">安全</a> ·
  <a href="#许可与定价所有者法令">许可</a> ·
  <a href="README.md">English</a>
</p>

> 🌍 **公开仓库**（2026-10-27 转公）——issues / discussions / PR 已开放；
> 内部设计与研究历史保存在所有者私有库（`aidog-im/aidog-internal`）。
> 🟢 **永久免费，绝无例外**：无付费档、无按量计费、绝无任何功能设付费墙。

## 关于

I'm AiDog 是一个**跑在你自己机器上的个人具身 AI 伙伴**。它构建在三个
被大多数助手忽略的信条之上：

- **宪法即人格**：`SOUL.md` 位于每一轮回复上下文的最顶端。红线、语气、
  操作原则不是事后补的提示词——它们在**每一轮**都被强制执行。
- **策展记忆**：记忆是带硬预算的纯 Markdown（`MEMORY.md`、`USER.md`、
  归档分层）+ 数字记忆体协议：唯一真值、跨内核可恢复、每次 reflect 回写。
  没有你读不了、审不动的向量库。
- **自研核心**：运行时内核 `mycore` 从零自研——诚实的函数调用循环（停止
  原因明示）、**30 模块 ≈16.5k 代码行、1649 项测试**，换芯后零上游依赖。
  换的是内核，永远不换「我」。

它通过 **Telegram**、**回环 Web 控制台**、**桌面 HUD 壳**和**语音**与你
交谈——背后是 9 路渠道网关、带确定性预路由的技能系统、自然语言定时任务、
子代理、MCP，以及制度化的自进化闭环（`/learn`、`/reflect`、lint 把关写入）
——成熟度随使用积累。

**Web3，人在闸上。** 自 v0.5.38 起，9 家场所一表打尽，挂在六个
`exchange_*` 工具之下——公开行情无需任何凭据，账户与下单工具槽位门控
（槽未配置 → 工具根本不注册）。每笔订单都走 `prepare`（干跑存计划）→
所有者在控制台批准 → `execute(plan_id)`；帽默认 `0`、市价单永不自动
批准，提现与链上转账在代码层面根本不存在。

**免费是宪法条款**：无订阅、绝无付费功能门、**产品运行时零遥测**——全站
唯一的计数是官网公开的访问计数（已如实注明）。你带上一个模型密钥
（或走零密钥试用路径），整个产品即刻激活。

## 核心能力

|  | 能力 | 提供什么 |
|---|---|---|
| 🧬 | **自研核心** | `mycore`：诚实停止原因的 FC 循环、fail-closed（默认拒绝）服务、看门狗、doctor 体检——30 模块、1649 测试、零上游 |
| 🎭 | **宪法人格** | `SOUL.md` 在系统栈最顶端；红线与语气每轮生效，而非抽样自人设提示词 |
| 🧠 | **策展记忆** | 有界 Markdown 预算 + 归档分层 + 数字记忆体协议；`memory` 套件 58/58 |
| 🧩 | **技能 + 确定性路由** | `SKILL.md` frontmatter（业界标准字段），路由预筛先选 TOP3 **再**加载正文——30/30 |
| ⏰ | **自然语言定时** | `/cron 明早九点发周报` → 解析、校验、调度——默认 fail-closed 待启用 |
| 🤝 | **子代理与 execute_code** | ≤3 个隔离上下文并行委派；≤8 步折叠代码执行，写前打检查点 |
| 🔌 | **MCP 客户端** | stdio + HTTP 服务器、工具 allow/deny 过滤、凭据槽、进度流式透传 |
| 🔗 | **Web3 交易所·人工审批闸** | 9 家场所一表、六个 `exchange_*` 工具——行情免凭据，账户/下单工具槽位门控（槽未配置 → 工具不注册）；`prepare`（干跑存计划）→ 所有者在控制台批准 → `execute(plan_id)`——帽默认 `0`、市价单**永不**自动批准，提现与链上转账**代码级不存在** |
| 📡 | **渠道网关** | **9 路适配器路由 + 实接 Telegram**——Discord / Slack / Email / Webhook / 钉钉 / 飞书 / 微信 / WhatsApp / Signal 适配器，逐路诚实状态 |
| 🔑 | **零密钥上手** | 试用模型起手或自带密钥；密钥只住 `secrets.env`（0600）且**绝不回显**——只报槽名 |
| 💬 | **全链路流式** | 上游 SSE 流式、Telegram typing 心跳、控制台流式聊天卡片 |
| ⚡ | **纯本地·可移植** | 核心仅用标准库（语音/图像为懒加载可选件），服务绑本地回环，SSE 流式贯穿全程，`execute_code` 把多步执行折叠成一轮往返 |
| 🖥️ | **回环 Web 控制台** | 七个视图——总览 · 聊天 · 技能 · 交易所 · 自进化 · 实时流 · 设置（用量与语音是卡片）：带修复深链的激活清单、凭据槽编辑器（32 槽——只显槽名与长度，绝不渲染值）、可删除的 cron 列表、能力总览网格、渠道表单带发信测试、钩子编辑器、自进化排程、Web3 帽——默认绑 `127.0.0.1`，写操作需令牌 |
| 🖼️ | **媒体内建** | 本地 STT 语音会话 + 模糊唤醒、TTS 播报、BYOK 图像生成、Telegram 发图 |
| 🖥️ | **桌面 HUD 壳** | Electron 无边框壳：透明度滑杆、置顶、托盘——Linux 打包已实证（Win/macOS 暂未构建，如实） |
| 👥 | **多 profile 全隔离** | `AIDOG_PROFILE` 选择完全隔离的档案（设置/密钥/状态/身体层）——隔离闸 63/63 + 真机 E2E 18 腿 |
| 🩺 | **自愈运维** | `aidog doctor` 体检 + 控制台一键 **Run doctor** 按钮、重启看门狗、带脏树守卫的一键升级，另有控制台内**重启 / 检查更新 / 应用更新**控件 |
| 🆓 | **永久免费** | 所有者法令：产品/文档/打包零付费触点——出现任何收费即视为缺陷 |

## 架构

```text
┌───────────────────────────────────────────────────────────────────┐
│  入口层                                                            │
│   Telegram · Web 控制台(127.0.0.1) · 桌面 HUD · 语音 · CLI          │
└──────────────────────────────┬────────────────────────────────────┘
                               │
┌──────────────────────────────▼────────────────────────────────────┐
│  内核层 — mycore（自研、可替换、零上游）                             │
│   loop · tools · memory · compact · scheduler · subagents · mcp    │
│   checkpoint · modeltier · channels(9) · exchanges                 │
│   stt/imagegen · browser                                           │
└──────────────────────────────┬────────────────────────────────────┘
                               │
┌──────────────────────────────▼────────────────────────────────────┐
│  身体层 — overlay/（本仓自持，与内核解耦）                           │
│   宪法(SOUL) · 记忆槽 · 技能 · 钩子 · 语言包                         │
└──────────────────────────────┬────────────────────────────────────┘
                               │
┌──────────────────────────────▼────────────────────────────────────┐
│  业务层 — adapters/ · web/ · desktop/ · deploy/                    │
│   模型供应商(BYOK) · 渠道凭据 · 控制台 · 桌面壳                      │
└───────────────────────────────────────────────────────────────────┘
```

三层分离就是重点：**换的是内核，永远不换「我」**。内核可替换，身体层与
业务资产归我们所有。

## 快速开始

> 🌍 **公开仓库：** `git clone` 无需任何凭据。机器级 `credential.helper`
> （如已登录的 `gh`）仍可能劫持其他远端的认证——详见
> [`docs/installation.md`](docs/installation.md)。

```bash
git clone https://github.com/aidog-im/aidog.git aidog && cd aidog  # 公开仓库
bash deploy/install.sh        # 一条命令：预检 + 建档 + 启动 + 自验
aidog setup                   # 交互向导：逐槽引导填写
```

`deploy/install.sh` 幂等（可安全重跑），并**只报缺失槽名、绝不回显值**：

| 退出码 | 含义 |
|---|---|
| `0` | 完全就绪——doctor 干净、服务已建档、控制台健康 |
| `2` | 已安装，但凭据/模型槽仍待补（fail-closed 设计内） |
| `1` | 硬失败（缺前置依赖、端口冲突、启动失败） |

`aidog setup` 是交互式续填：只提示仍为空的槽、隐藏输入、每次写入都读回
校验、绝不回显值。参数：`--check`（只读报告）、`--yes`（非交互）、
`--no-boot`（只建档不起服务）。

然后：

1. 打开控制台 **http://127.0.0.1:8790**
2. 设置面板 → 填模型端点 + 密钥、Telegram bot token + allowlist
   （或直接跑 `aidog setup`）
3. 聊天卡片发一句话——流式回复即模型链路通
4. Telegram → allowlist 内账号给 bot 发 `/help`

完整指南：[`docs/installation.md`](docs/installation.md)。

## 人工验收

自动化 E2E 覆盖本机栈；**人工抽查**是所有者的验收层。速查：

```bash
python3 deploy/aidog_doctor.py       # 体检：9 checks · 0 FAIL · 20 SKIP
bash deploy/boot_services.sh status  # 控制台 + 服务 + 看门狗全绿
bash deploy/run_service.sh --check   # 就绪 rc=0
python3 deploy/aidog_update.py check # 版本通道：up to date
```

逐个走控制台视图（总览 → 设置写读回环 → 聊天卡片 → 实时流 → 交易所 →
自进化），给 Telegram 发一条 `/help`，并确认任何地方都看不到密钥原文。
完整清单（含全新机器安装路径）见
**[`docs/manual_verification.md`](docs/manual_verification.md)**。

## 质量闸

每次发版都在干净树上全绿才切：

| 套件 | 结果 |
|---|---|
| `mycore` 核心套件 | **1649/1649** |
| 控制台冒烟（真实 HTTP） | **283/283** |
| 技能路由 | **30/30** |
| 记忆持久化 | **58/58** |
| 安装向导 | **99/99** |
| 多 profile 隔离（离线 + 子进程对拍） | **63/63** |
| 桌面壳（`node --test`） | **15/15** |
| `aidog doctor` | **0 FAIL · 0 WARN**（出站全断时如实报环境 WARN） |
| 发布闸（deny-by-default 清单） | **READY** |
| 真机 E2E | profiles **18/18** · channels **12/12** · 国内渠道 **46/46** · 管线 **39/39** · web3 只读 **33/33** · web3 审批 **61/61** · 前端覆盖 **62/62** · 前端 UI **33/33** · 激活 **81/81** · 激活 UI **50/50** · 设置矩阵 **63/63** + UI **20/20** · 守夜任务 **54/54** · 编排看板 **63/63** · 审批挂起 **73/73** · 技能目录 **176/176** · 远端目录 **58/58** |

目录规模：**26 个注册工具** · **9 家交易所场所** · **17 腿真机
E2E**——全部全绿。

语言包：`en`（默认）+ `zh` 各 **847 键**，parity lint 把关；官网另有
**257 键**双语站包。

## 文档

- [`docs/installation.md`](docs/installation.md) — 安装、槽位、向导
- [`docs/features/`](docs/features/) — **34 份能力文档**，一项能力一档
  （渠道、记忆、定时、子代理、MCP、语音、桌面、profile、工具开关……），
  每份都写明诚实边界
- [`docs/manual_verification.md`](docs/manual_verification.md) — 人工验收清单
- [`docs/security_checklist.md`](docs/security_checklist.md) — 安全审查清单
- **[aidog.im](https://aidog.im)** — 产品官网（双语、PWA、AI 可读 `llms.txt`）

**34 份一览**（分五组，每组一行）：

- **代理循环与工具** — [`acp`](docs/features/acp.md)（ACP v1 stdio 服务器）· [`openai_api_server`](docs/features/openai_api_server.md)（OpenAI 兼容 `/v1` 网关）· [`terminal_backends`](docs/features/terminal_backends.md)（7 后端 `run_bash` 矩阵）· [`execute_code`](docs/features/execute_code.md) · [`subagents`](docs/features/subagents.md) · [`mcp`](docs/features/mcp.md) · [`tool_toggles`](docs/features/tool_toggles.md) · [`browser_automation`](docs/features/browser_automation.md) · [`model_failover`](docs/features/model_failover.md) · [`missions`](docs/features/missions.md)（永不睡觉的任务守夜循环）· [`community_posting`](docs/features/community_posting.md)（站内讨论发帖 + 槽位检验）
- **知识与记忆** — [`memory_and_routing`](docs/features/memory_and_routing.md) · [`checkpoints`](docs/features/checkpoints.md) · [`context_files`](docs/features/context_files.md)（项目指令文件）· [`evolution_loop`](docs/features/evolution_loop.md) · [`connectors`](docs/features/connectors.md)（只读 Linear/Notion 搜索）
- **渠道与媒体** — [`channels`](docs/features/channels.md) · [`channels_domestic`](docs/features/channels_domestic.md) · [`console_chat`](docs/features/console_chat.md) · [`voice_session`](docs/features/voice_session.md) · [`voice_announcements`](docs/features/voice_announcements.md) · [`tts_engines`](docs/features/tts_engines.md) · [`image_generation`](docs/features/image_generation.md)
- **运维与控制台** — [`setup_wizard`](docs/features/setup_wizard.md) · [`frontend_coverage`](docs/features/frontend_coverage.md) · [`capabilities`](docs/features/capabilities.md) · [`desktop_client`](docs/features/desktop_client.md) · [`profiles`](docs/features/profiles.md) · [`site_visibility`](docs/features/site_visibility.md) · [`zero_key_onboarding`](docs/features/zero_key_onboarding.md) · [`natural_language_cron`](docs/features/natural_language_cron.md) · [`event_hooks`](docs/features/event_hooks.md)
- **Web3** — [`exchanges`](docs/features/exchanges.md)（9 家场所，下单过人工审批闸）
- **技能生态** — [`skills_ecosystem`](docs/features/skills_ecosystem.md)（官方目录页、公开技能仓、双闸审核、远端目录 API）

设计史与第三方调研笔记保存在所有者私有库（`aidog-im/aidog-internal`），不随本仓导出。

## 路线图

- **当前**——Web3 二期已上线：**W1 + W2 随 v0.5.38 交付**（9 家场所
  目录、`prepare` → 所有者审批 → `execute`）；同时在所有者验收下做一致性
  加固与打磨——每个能力都带测试与诚实边界。
- **下一步**——Web3 剩余阶段（身份与支付：ERC-8004 链上 agent 身份、
  x402 只读付费调用、官方 MCP opt-in 挂载），已调研预置——仍只在所有者
  明确启动令后执行。

## 版本与升级

- 版本真值 = `VERSION`（semver）+ `release.json`（日期 + 说明），两者同车
  bump；每个版本打 tag `vX.Y.Z` 并发布 **GitHub Release**（发现通道）。
- 升级：`python3 deploy/aidog_update.py apply` → 脏树守卫 → fetch →
  `--ff-only` → 重启运行时。有新版本时控制台自动显示升级横幅。

## 安全姿态（如实）

- 默认回环绑定；控制台写操作需 bearer 令牌；密钥只住 `secrets.env`
  （0600）且**绝不回显**——日志/状态/错误里只有槽名。
- 该 fail-closed 的地方全 fail-closed：渠道 allowlist、profile 名、设置
  校验、升级守卫。
- 单主设计 = 陌生人不在攻击面内。
- **如实的局限**：暂无第三方审计与 CVE 披露通道——已公开、未审计。
  我们只声称「攻击面被设计得小」，绝不声称「已验证更安全」。

## 语言 / 国际化

- 源码、注释、Markdown 文档：**英文**（国际路线）；本文件是完整中文镜像，英文原文见 [`README.md`](README.md)。
- UI 字符串：`overlay/locales/` 语言包——默认 **`en`**，可切 **`zh`**，
  加语言 = 加包、零代码改动。

## 许可与定价（所有者法令）

**永久免费，任何用途免费。** I'm AiDog 产品内**零付费触点**：无付费档、
无内购、无使用费、绝无功能设付费墙。向本产品加入任何收费都违反所有者
法令，一律视为缺陷。

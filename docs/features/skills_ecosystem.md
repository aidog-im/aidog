# Feature spec — Skills 生态（官方技能目录与双闸）

Status: **assets landed**（0.5.47 车 4 车道 M：独立仓建仓 + 首批入库 +
官网目录页 + 目录 schema 校验 E2E）。
设计锚点：`docs/design/autonomous_ops_design.md` §4（三层结构 / 独立仓 /
来源移植 / 官网目录接口）与 §5 车 4 行。本页是**第 33 份能力文档**，覆盖
三层结构、独立仓与双闸、首批清单、官网目录页与 catalog 同步机制、以及
`GET /api/skills/remote` 的接口预告。

**边界**：本车道只落**资产与文档**——独立仓、官网目录页、catalog 副本、
本页与 E2E。**产品侧读取端点 `GET /api/skills/remote` 归 0.5.47 车道 M2
实装**（`web/server.py` 与对应 E2E 在 car3 之后串行，见 §6）。

Companion pages: [capabilities.md](capabilities.md)（能力目录契约）、
[memory_and_routing.md](memory_and_routing.md)（`skill_route` 确定性预筛，
与本规范的"渐进披露"同构）、[tool_toggles.md](tool_toggles.md)（`requires`
所列工具的开关面）、[missions.md](missions.md)（守夜任务按能力 manifest
取用技能）、[site_visibility.md](site_visibility.md)（官网目录页的渲染与
断言面）、[frontend_coverage.md](frontend_coverage.md)（控制台技能视图
矩阵）。

## 1. 三层结构

```
第 1 层  本仓 overlay/skills/           随产品发行的核心技能（5 个，全过 lint + skill_route 预筛）
第 2 层  独立 skills 仓（私有 GitHub）   官方推荐技能的唯一真值源：catalog + 分类目录 + 双闸 + 版本
第 3 层  官网 aidog.im 目录页            静态 JSON 目录：用户可浏览，产品读同一份 JSON
```

- 第 1 层是**发行面**（装机即有）；第 2 层是**真值面**（审过才列）；第 3 层
  是**发现面**（人和模型都能读）。
- 三层用同一套格式：目录名 = frontmatter `name`，`description` 是唯一的
  检索入口（发现阶段只读 name + description，命中才载正文）。

## 2. 独立仓结构与 catalog 字段

```
aidog-skills/
├── catalog.json                 # 机器可读总目录（官网与产品读同一份）
├── skills/<category>/<name>/SKILL.md   [+ scripts/ references/ assets/ examples/]
├── tools/lint.py                # 机器闸
├── REVIEW.md                    # 人工闸（deny-by-default 清单）
├── THIRD_PARTY.md               # 上游复刻署名清单
└── README.md                    # 英文：私有仓声明 + 审核流程 + 同步机制
```

**catalog 条目字段恰为设计书 §4.4 的七个**（不自造）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `slug` | string | 小写连字符 id，= 目录名 = frontmatter `name` |
| `name` | string | 展示名（当前与 `slug` 同值） |
| `category` | 枚举 | `automation-ops` / `research` / `dev` / `content` / `trading-readonly` / `reporting` |
| `description` | string | ≤1024，一句话，与 frontmatter `description` 逐字一致 |
| `version` | semver | 技能修订版（与 frontmatter `metadata.version` 对齐） |
| `requires` | string[] | 所需**工具名或凭据槽位**（取自 `KNOWN_TOOL_NAMES` / `SECRET_GROUPS` 词表） |
| `install_status` | 枚举 | `bundled`（随产品内置）/ `catalog`（可从目录安装） |

顶层形状固定为 `{"skills": [...]}`。`requires` 是 §4.4 说的"按你已有什么
能力扩大应用面"的钩子：官网按它标注前置条件，产品按它与能力 manifest 对齐
（装了没工具的技能只会空转）。

## 3. 双闸（机器 + 人工，deny-by-default）

1. **机器闸 `tools/lint.py`**：`=== N/N PASS ===` 才算过。
   - 结构/规范：catalog ↔ frontmatter ↔ 目录三方一致、`name ≤64`、
     `description ≤1024`、semver、相对链接可解析、SKILL.md 字节预算。
   - **泄漏扫描（对仓内全部文本文件，含自身之外的所有文件）**：本机路径
     （挂载点 / root 家目录 / home / 盘符）、凭据形状（`KEY=value`、私钥块、
     常见 token 前缀）、个人消息 handle、邮箱（RFC 2606 保留域名豁免）、
     内部代号与跨仓私有名、上游品牌词——**0 命中**（品牌词以拼接常量写入
     并自我豁免，与产品 `deploy/check_publish.py` 同哲学）。
2. **人工闸 `REVIEW.md`**（四段全勾才放行）：
   - **A 脱敏闸**：路径/凭据/身份/内部代号/品牌词逐项确认；
   - **B 质量闸**：`description` 必须写"何时用"并带触发负例（描述质量 =
     路由质量）、渐进披露、命令真实可跑、经验带日期、通用第一人称；
   - **C 许可闸**：自有技能零第三方文本；上游复刻必须**逐技能带许可文件**
     并登记进 `THIRD_PARTY.md`（来源 URL + 访问日期 + 改动清单），拿不到
     干净许可就不收；
   - **D 安全闸**：只落文件不落执行、信任源只读、审批边界不动、`requires`
     诚实。
3. 版本纪律：内容变更 → `metadata.version` 递增 → 同一提交内更新
   `catalog.json` → 官网副本同步（§5）。

## 4. 首批入库清单（12 个：本仓 5 + 脱敏复刻 6 + 上游复刻 1）

| # | slug | 分类 | 来源 | 一句话 |
|---|---|---|---|---|
| 1 | `binance-read-only` | trading-readonly | 本仓原样 | 币安只读接入、最小权限 USER_DATA key 与鉴权诊断，下单只走审批门 |
| 2 | `kraken-read-only` | trading-readonly | 本仓原样 | Kraken 只读接入与 nonce/API-Sign 鉴权排障，从不用于交易 |
| 3 | `okx-read-only` | trading-readonly | 本仓原样 | OKX 只读接入与 passphrase/时间戳偏移排障，从不用于交易 |
| 4 | `systematic-debugging` | dev | 本仓原样 | 任何 bug 先根因后修复的排障铁律 |
| 5 | `writing-plans` | dev | 本仓原样 | 多步任务动代码前先写实现计划 |
| 6 | `growth-ops` | automation-ops | 脱敏复刻 | 免费分发作战手册：渠道矩阵、验证邮件闭环、目录提交 SOP、守夜提交、同类产品监测、度量元规则 |
| 7 | `site-ops` | automation-ops | 脱敏复刻 | 边缘平台站点运维：范围化 token 角色表、DNS 与 proxied 差异、带锁发布链与幽灵路径坑 |
| 8 | `mail-assistant` | automation-ops | 脱敏复刻 | 收信只读分拣四类、镜像语言且永不发送的回信草稿、激活链接核验、待办上报 |
| 9 | `research-brief` | research | 脱敏复刻 | 三档查证报告管线：多源核证、≥2 独立源、三色标注、出处必附、对比选型六步 |
| 10 | `analysis-pipeline` | content | 脱敏复刻 | 深度分析长文管线：值得性评分卡、取证归档、防投毒铁律、双语、lint、发布包 |
| 11 | `voice-reply` | reporting | 脱敏复刻 | 明确要求才发语音：免费 TTS、永远附文字、入站语音先转写、失败即降级纯文字 |
| 12 | `webapp-testing` | dev | 上游复刻 | Playwright 本地 Web 测试工具箱（**Apache-2.0**，逐技能许可文件 + `THIRD_PARTY.md` 署名） |

**复刻口径**（对姊妹项目运营工程技能）：语义搬运 + 改写为通用第一人称；
逐篇去本机绝对路径、个人身份与画像字段、真实 handle、凭据、内部代号、
跨仓私有链接；**身份/人设类技能不入**（个人资产，调研文档已裁定）。
上游复刻只取**许可文件随技能发布**的公开技能，整目录带 `LICENSE.txt` 复制，
改动（如有）在 `THIRD_PARTY.md` 逐条声明。

## 5. 官网目录页与 catalog 同步机制

- **页面**：`site/skills.html`（Draft E 语系，双语，站点独立页，不挂
  `assets/main.js`——本页导航指向 `index.html#…` 锚点，自备轻量脚本做
  语言包加载、汉堡导航、纹样与目录渲染）。
- **数据**：页面运行时读 `site/skills/catalog.json`（**本仓提交的副本**），
  按 §4.2 六分类分组渲染；条目卡显示 slug / 一句话描述 / 版本 / 安装状态 /
  `requires`。语言键只在 `site/i18n/{en,zh}.json` 的 `skills.*` 命名空间
  （本车新增 36 键，站包 **217 → 253**，en/zh 键集恒等）。
- **同步机制（建议口径，父代入文档）**：独立仓 `catalog.json` 是母本，
  `site/skills/catalog.json` 是**逐字节副本**；任何一次 catalog 变更都执行
  `cp aidog-skills/catalog.json site/skills/catalog.json`（或等价脚本化
  `sync` 步骤），随后跑 `tmp/e2e_skills_catalog.py`——它会断言副本 schema
  合法、页面渲染条目数与副本一致、泄漏 0 命中。**手工 cp + E2E 断言**是
  当前口径（脚本化 sync 可作为后续小改进，需父代点头再加文件）。

## 6. `GET /api/skills/remote`（**0.5.47 车道 M2 实装**，此处仅文档预告）

- **形状（预告，M2 定稿）**：`GET /api/skills/remote` →
  `200 {"source":"remote"|"cache","fetched_at":<iso>,"count":<n>,"skills":[...]}`
  ——`skills` 数组即 catalog 条目（§2 七字段原样）。
- **fail-soft 契约**：网络不可用 → 读内置缓存（随发行携带的 catalog 副本）
  并如实标 `source:"cache"`；**永不阻塞对话、永不 5xx 出口**；条目缺失或
  schema 不合 → 丢弃该条并继续（部分成功优于整体失败）。
- **归属**：`web/server.py` 归 car3 车道 K，故本端点串行到 Wave 3（M2）
  实装；本车道不落任何端点代码。

## 7. 红线

1. **只落文件、不落执行**：技能以文本落地，安装 = 复制文件进技能目录；
   永不自动执行、永不拉起远端代码、永不引入应用市场式自动安装。
2. **信任源只读**：技能与目录只从官方仓读取；社区 MCP 永久禁用的既有红线
   沿用到技能面。
3. **凭据零回显**：技能文案只写槽位名与 `len > 0`，catalog 里没有值、没有
   handle、没有本机路径（机器闸证明 0 命中）。
4. **审批边界不动**：涉及花钱、对外发布、外发消息、凭据变更的动作照旧走
   人工审批门；`requires` 里的凭据槽位只由用户自己填。
5. **私有仓 = 公发标准脱敏**：按 2026-10-25 的既定口径，私仓内容也按公发
   标准做脱敏，随时可裁决转公开。

## 8. 本车验收闸

```bash
cd aidog-skills && python3 tools/lint.py   # 独立仓：=== 300/300 PASS ===（含泄漏 0 命中）
python3 tmp/e2e_skills_catalog.py  # 目录 schema + 页面渲染 + 站闸回归（PASS n/n）
node tmp/shoot_site.js             # 62（既有站闸，不得因新页破坏）
node tmp/e2e_site_redesign.js      # 59（既有站闸）
python3 tests/source_lang_check.py # 0 违规（本车道未新增 .py 源）
python3 deploy/check_publish.py    # READY(0)
```

E2E 断言面：catalog 必填字段齐、分类合法、无空项、泄漏 0 命中；页面在
1440/390 双视口**横向溢出 0**、零 pageerror、双语切换生效、渲染条目数 =
catalog 条目数；既有站闸 62/59 仍绿。

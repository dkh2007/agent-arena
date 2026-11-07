# 外部评审包 v1 —— 请审查并回答第 6 节的问题

> **给外部 AI 的元指令**：你看到的是一份**自包含**的项目快照，看不到原始对话，也不需要。
> 项目处于"技术选型 spike"阶段，作者是大二学生 + 1 名同学（2 个开发人力），指导老师只做方向把关。
> **请重点做两件事**：① 检验第 5 节每条结论的证据是否真的支撑结论（不要客气，指出过度推断）；
> ② 回答第 6 节的开放问题，每条给出「怎么验证」而不只是「我认为」。
> 作者的工作纪律是「AI 自评不作证据」——所以**不要用"这样应该没问题"作为回答**，
> 每个判断请配一个十分钟内能做的验证动作。
>
> **匿名的要求**：本文档与仓库内不含真实姓名/校名，请不要在回复里要求提供。

---

## 1. 项目一句话

Agent 开发学习领域的**自适应诊断与训练闭环**：学习者提交一个 Agent，平台对其**执行轨迹**做错误诊断
（不只打分），把错误模式映射到技能缺口，按遗忘规律调度**可执行的任务变体**作为复习项，
并按「掌握度 + 遗忘风险 + 错误热度 + 项目截止」生成训练建议排期。

一句话区分：**现有平台（PinchBench / ClawBench / 千问 Arena）评的是模型，我们评的是学习者的技能缺口。**

参赛场景：某全国大学生软件创新大赛，AI+学习方向；初赛只交**项目计划书**（盲审匿名），
演示平台须基于 Android。MVP 规模锁死：3 个技能 × 每技能 2–3 变体，任务库上限 10 个。

---

## 2. 当前阶段与已完成

| 阶段 | 状态 | 产物 |
|---|---|---|
| 选题三轮演进 | 完成 | 计划书 v0.4（约 27KB，含采纳记录） |
| 数据模型 v0 | 完成（7 实体） | `docs/data-model.md` |
| 首个评测任务资产 | 完成（5 条断言 + 14 个 YAML 单测全绿） | `tasks/dbg-001-order-report.yaml` |
| 执行器语义约定 | 完成（故障注入归属、重试主体） | `docs/executor-semantics.md` |
| 评测底座 spike | **本轮（第 4 步）刚出结论** | `spike/README.md` 等 |
| MVP 代码 | 未开始 | — |

---

## 3. 技术框架

### 3.1 五层架构（模块边界，一次只动一层）

```
① 任务层   YAML 任务定义：scenario / tools(mock+fault_injection) / test_inputs / trace_requirements / rubric
              ↓
② 评测层   黑盒调用学习者 Agent → 收产物 + 收 trace → 逐条断言判定 → 诊断报告（错误模式 + 证据）
              ↓
③ 状态层   错误模式 → 技能缺口 → 每技能一张 FSRS 卡片（D/S/R 三值），掌握度**唯一口径**就是它
              ↓
④ 复习层   技能缺口 → 变体任务（同技能换场景/换数据/换故障点）→ 变成可执行复习项
              ↓
⑤ 排程层   四因子（截止 > 遗忘风险 > 错误热度 > 掌握度）→ 建议排期，不做全自动日历
```

### 3.2 关键设计决策（不再重议）

1. **黑盒调用式提交**：学习者 Agent 部署成公开服务，平台只调用 + 评分 + 收 trace，**不做代码沙箱**。
2. **复习项 = 可执行任务**，不是抽认卡。FSRS（py-fsrs）只回答"何时复习哪项技能"。
3. **MVP 推荐先规则后模型**：错误模式 → 技能缺口 → 变体任务用规则表，不上深度知识追踪。
4. **能调不造**：py-fsrs、评测底座、内部工作台全用现成；自研只押「诊断 → 推荐 → 可执行复习」链路。

### 3.3 最小 trace schema（提交格式的一部分）

```
agent_id: str
final_status: completed | crashed | timeout
tool_calls: [
  { call_index, name, params, status(success|error|timeout),
    http_status(仅 error 必填), error(仅 error 必填), result_summary(仅 success 必填) }
]
```
`attempt` 不要求上报——由平台从"同名同参调用序列"自行推导（避免要求学习者理解重试语义）。

### 3.4 任务资产长什么样（dbg-001 摘要）

场景：分页拉取 20 条订单（`limit ≤ 10`）→ 剔除无效订单（缺字段/null/负数）→ 统计 → 写 JSON 报告。
**故意注入故障**：`fetch_orders(offset=10)` 的**第 1 次**调用返回 500，之后正常。
**重试主体是 Agent 而不是 mock**——mock 绝不内部重试，否则 R3 无法区分"Agent 会不会重试"。

五条断言：

| ID | 类型 | 判定 | 权重 |
|---|---|---|---|
| R1 | 结果 | 文件存在且非空 | 1 |
| R2 | 结果 | JSON 的 `valid_count=17`、`total_amount=1837.99`（容差 0.01，允许额外字段） | 2 |
| R3 | 轨迹 | `fetch_orders(offset=10)` 出现 error(500) 之后，**同参数**再出现 success（允许中间穿插其他调用） | 1 |
| R4 | 轨迹 | 所有工具调用参数合法（required/type/range/不允许额外字段），**任一次违规即失败** | 1 |
| R5 | 轨迹 | `final_status == completed` | 1 |

R3 里 `params_match: {offset: 10}`（子集匹配，不锁 `limit`）是整个断言的**核心价值**：
它判的是"重试有没有落在同一页"，而不是"有没有再试一次"。**第 4 节的整个结论都围绕这一点。**

---

## 4. 本轮结论：为什么 A2E 不能作主判定层

被测：`github.com/datamllab/A2E` main 分支（2026-11-06 下载，`a2e.zip` sha256 `f401596f…6e2fba`）。

### 结论

A2E 的 26 个指标**全部是聚合画像或 LLM judge**，没有"任务级确定性布尔断言"的注册点。
它与我们的关系不是"二选一"，而是**不同层**：A2E 的评估单元是「指标」（一任务一聚合分），
我们的是「断言」（一任务 N 条独立布尔判定 + 逐条证据）。

### 三条证据

**证据 A（代码级）**：`eval/core/eval_common.py:264` `_tool_calls_from_spans()` 每条调用只产出
`{"name", "arguments", "result"}` 三个字段。我们的 schema 要 7 个——`status` / `http_status` /
`error` / `call_index` 在 A2E 侧全部丢失。R3 的判定条件 `status=error, http_status=500` 无处安放。

**证据 B（运行级）**：造一条 4 调用的假 trace 灌进 A2E 后跑 eval，输出：

```
span HTTP API unavailable (The 'trace_id' query parameter ... requires A2E >= 13.9.0,
but connected to server 0.0.1. Please upgrade your A2E server.);
sqlite span fallback unavailable; span-backed metrics will use task output only
```

上游自相矛盾：`server/.../a2e/version.py` 硬编码 `__version__ = "0.0.1"`，而 eval 客户端要求 `>= 13.9.0`
——同一份源码起的 server 被自己的 eval 拒绝。更关键的是**退化时给满分**：

```json
{"name":"self_correction_rate","label":"no_tool_errors","score":1.0,
 "explanation":"No tool error outputs were observed; self-correction not required (score=1.0)."}
```

这条轨迹里明明有一次 500。**漏报 trace 反而拿满分** → 学习者会学到"不报数据最安全"。

**证据 C（语义级）**：绕开 HTTP，直接喂函数。`tool_eval.py:936` 的判定是
「同名工具 + 后续有非错误结果 ⇒ 已修正」，**完全不看参数**：

| 轨迹 | 应判 | A2E 判 |
|---|---|---|
| `offset=10` 失败 → `offset=10` 成功 | 通过 | corrected / 1.0 |
| `offset=10` 失败 → **`offset=0`** 成功（游标退回） | 失败 | corrected / 1.0 |

两类轨迹在 R3 下应当相反，在 A2E 下同分。已固化为哨兵测试 `spike/test_a2e_semantics.py`（3 passed）。

### 由此产生的三个决定

1. 计划书措辞从"评测底座二选一"改为「**自研断言层（必做）+ 可选旁证画像层**」。
2. `trace_requirements` 语义补一条：不上报 trace → 轨迹类断言判 **unscored 且不计入掌握度**，
   而不是判 0。理由：判 0 把"数据缺失"和"能力不足"混为一谈，会污染 FSRS 的 D/S。
3. A2E **不进 MVP 依赖**，只把它的 26 指标清单当设计参考。

---

## 5. 可验证点清单（请挑你最怀疑的几条，指出证据是否够格）

每条都给命令；**"通过标准"写在箭头右边**。

| # | 断言 | 怎么验 | 通过标准 |
|---|---|---|---|
| V1 | dbg-001 的 YAML 结构合法、5 条断言齐全 | `python -m pytest tests/test_task_yaml.py -v`（仓库根） | 14 passed |
| V2 | A2E 抽工具调用只产 3 字段 | 打开 `eval/core/eval_common.py` 第 264–290 行 | 只有 name/arguments/result |
| V3 | A2E 的 server 版本号 = 0.0.1 而 eval 门槛 = 13.9.0 | `cat server/server/src/a2e/version.py`；`grep -rn "13.9.0" eval/` | 两处数字对不上 |
| V4 | 假重试与真重试同分 | `cd tools/A2E-main/server && ./.venv/Scripts/python.exe -m pytest ../../../agent-arena/spike/test_a2e_semantics.py -v -p no:postgresql` | 3 passed（**过=缺陷仍在**） |
| V5 | 无工具调用时给满分 1.0 而非 unscored | 同 V4 的第三个用例 | passed |
| V6 | A2E 26 个指标里没有"任务自定义 rubric" | `cat eval/metric_groups.py`（全文 100 行内） | 5 组共 26 个指标名，无 rubric 概念 |
| V7 | dbg-001 的期望值算得对（17 条有效、总额 1837.99） | 手算 `test_inputs.orders_fixture`：20 条剔 o-007(负)/o-012(缺字段)/o-018(null) | 17 与 1837.99 |
| V8 | R3 的子集匹配语义没写错 | 读 `tasks/dbg-001-order-report.yaml` 的 R3 块 + `docs/executor-semantics.md` | `params_match` 只锁 offset，`allow_interleaved: true` |

**已知的假设（不是结论，别当证据用）**：
- 「R1–R5 的判定逻辑几百行能写完」——未实测，只是估算。
- 「A2E 的 26 指标里有几条能作旁证」——只读了指标名与实现，没验证在真实学习者轨迹上的区分度。
- 「不上报 trace 的提交比例会很高」——对学习者行为的猜测，无数据。
- agentevals（OTel 那个，PyPI 包名 `agentevals-cli`）**还没做对称检查**，第 5 步才做；
  所以"自研断言层"这个决定目前只证伪了 A2E，没证明"没有现成的能直接用"。

---

## 6. 请回答的问题（每条请给"怎么验证"，不要只给判断）

**Q1** 第 4 节的推论跳步了吗？从"这三条证据"到"自研断言层"，中间还有没有更省力的第三条路
（比如用某个通用断言库 + 一层薄适配，而不是全自研）？如果有，给出库名 + license + 它的判定单元是不是布尔断言。

**Q2** R3 这种"序列 + 参数子集匹配"的断言，业界有成熟表达方式吗？
（我们在用自造的 YAML DSL；如果你知道 Pytest-style、JSONPath-on-trace、或者 OpenTelemetry 生态里的现成 DSL，请指名并给出它的表达能力边界——尤其"允许穿插其他调用"这一条它支不支持。）

**Q3** 「不上报 trace → 判 unscored 且不计入掌握度」会不会被学习者利用？
具体说：如果轨迹断言全部 unscored，只剩 R1/R2 两条结果断言，掌握度还能不能正常更新？
如果答案是不能，那我们的"黑盒调用 + 自愿上报"这个前提是不是本身就是错的？

**Q4** 五条断言的权重 `[1,2,1,1,1]` 有没有更合理的依据？
（现在这个权重是拍的。R2 权重 2 是因为"结果对但过程错"仍算部分掌握。）

**Q5** 如果只能保一个"防作弊"措施，应该保哪个？
威胁模型：学习者可以伪造 trace（声称重试过但其实没有）、可以硬编码报告内容、可以让 Agent 直接写死答案跳过工具调用。

**Q6** 从初赛只交**计划书**、盲审、AI+学习方向这个约束看，
第 3 节的框架里哪一层最像"评委一眼就信"，哪一层最像"评委一眼就疑"？
请按"评审可信度"而不是"技术先进度"排序五层。

---

## 7. 复现环境（若要真跑）

- Windows + Git Bash；Python 3.11+；A2E 源码在 `tools/A2E-main/`（不随仓库提交）。
- A2E 起服务：`cd tools/A2E-main/server && no_proxy=127.0.0.1,localhost ./.venv/Scripts/a2e.exe serve`（约 45 秒，端口 6006）
- 造 trace：`cd tools/A2E-main/server && ./.venv/Scripts/python.exe ../../../agent-arena/spike/a2e_probe.py`（幂等，可重跑）
- 跑 eval：`./.venv/Scripts/python.exe ../eval/scripts/run_eval.py --base-url http://127.0.0.1:6006 --experiment-id <上一步打印的 id> --metrics self_correction_rate,tool_execution_error_rate`
- 注：本机有代理 MITM，`uv`/`git` 需 `SSL_CERT_FILE` 指向自建 CA 包；实测与本结论无关，但会挡住依赖安装。

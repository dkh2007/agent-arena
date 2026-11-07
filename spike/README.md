# M-1 第 4 步 spike：A2E 承载能力判定（4c-3 / 4c-4）

> 目的不是"能不能跑起来"，而是**判定 A2E 能否承载 dbg-001 的 R1–R5 断言**。
> 日期：2026-11-07 ｜ 被测：`github.com/datamllab/A2E` main 分支（`a2e.zip` sha256 `f401596f…6e2fba`，2026-11-06 下载）
> 结论一句话：**A2E 不能作主判定层（R1–R5 无处安放），只能作"通用行为画像"的旁证层。**

---

## 1. 判据（跑之前登记，跑完对照）

| 编号 | 判据 | 结果 |
|---|---|---|
| P1 | A2E 能否存下 `http_status` / `call_index` / `attempt` —— 能存则 R3 有位置 | **部分失败**：`call_index` 等自定义属性可存，但 eval 侧不读；`http_status` 会被塞进 `result` 字符串，靠 `"error"` 字面匹配 |
| P2 | `self_correction_rate` 是否区分「真重试(offset=10 再试)」与「假重试(退回 offset=0)」 | **失败**：两者同分 `score=1.0 / label=corrected` |
| P3 | `tool_execution_error_rate` 是聚合数字还是布尔断言 | **聚合**：`{score: n, label: str, explanation: str}`，一次任务一个分数，不是"R3 过没过" |

---

## 2. 证据三级

### 2.1 代码级：A2E 从 span 抽工具调用只产 3 个字段

`eval/core/eval_common.py:264` `_tool_calls_from_spans()` 每条 call 只输出：

```python
calls.append({"name": str(name), "arguments": arguments or {}, "result": result})
```

对照 `tasks/dbg-001-order-report.yaml` 的 `trace_requirements.minimum.tool_calls.item_fields`：
我们要求 7 个字段 `[call_index, name, params, status, http_status, error, result_summary]`，
A2E 只认 3 个——**`status` / `http_status` / `error` / `call_index` 全部丢失**。

后果：R3 的判定条件 `status: error, http_status: 500` 在 A2E 的数据模型里没有位置；
R4 的 `check_schema: true`（required/type/range/additional_properties 四项）也没有对应实现——
`_parameter_schemas_from_tool_spans()` 只是把 schema 文本拼给 LLM judge 看，不做确定性校验。

### 2.2 运行级：轨迹类指标开箱即坏，且**失败时静默给满分**

按 `a2e_probe.py` 造了一条 4 调用的假 trace 灌进 server，跑 `run_eval.py` 实测：

```
span HTTP API unavailable (The 'trace_id' query parameter on GET /v1/projects/{id}/spans
requires A2E >= 13.9.0, but connected to server 0.0.1. Please upgrade your A2E server.);
falling back to sqlite span store if configured
sqlite span fallback unavailable; span-backed metrics will use task output only
```

- 上游自相矛盾：`server/server/src/a2e/version.py` 硬编码 `__version__ = "0.0.1"`，
  而 eval 客户端要求 `>= 13.9.0` → **同一份源码起的 server 被自己的 eval 拒绝**。
- 于是 spans 一条没读到，指标退化为"只看 task output"。
- **退化时的行为是给满分**，不是报无数据：

```json
{"name": "self_correction_rate", "annotator_kind": "CODE",
 "label": "no_tool_errors", "score": 1.0,
 "explanation": "No tool error outputs were observed; self-correction not required (score=1.0)."}
```

这条轨迹里明明有一次 500。**漏报 trace 反而拿满分**——对学习诊断是致命的：
学生会学到"不报数据最安全"。

### 2.3 语义级：即便 span 能读到，判定语义也不对（哨兵测试）

`spike/test_a2e_semantics.py` 直接喂 `tool_calls_full` 给 `make_self_correction_rate()`：

| 轨迹 | 应判 | A2E 判 |
|---|---|---|
| 真重试：`offset=10` 失败 → `offset=10` 成功 | 通过 | `corrected` / 1.0 |
| 假重试：`offset=10` 失败 → **`offset=0`** 成功（游标退回） | 失败 | `corrected` / 1.0 |

`tool_eval.py:936` 的实现是「同名工具 + 后续有非错误结果」→ 已修正，**完全不看参数**。
R3 的全部价值就在 `params_match: {offset: 10}`，A2E 恰好丢掉这个维度。

复现：

```bash
cd tools/A2E-main/server
no_proxy=127.0.0.1,localhost ./.venv/Scripts/python.exe -m pytest \
    ../../../agent-arena/spike/test_a2e_semantics.py -v -p no:postgresql
# 期望：3 passed（断言的是上游缺陷，过=缺陷仍在）
```

`-p no:postgresql` 是必须的：上游 venv 装了 `pytest-postgresql`，缺 libpq 会在 conftest 阶段直接 ImportError。

---

## 3. 26 个指标与 R1–R5 的映射表

| 我们的断言 | 需要的能力 | A2E 最近的东西 | 结论 |
|---|---|---|---|
| R1 文件存在非空 | 副作用检查（文件系统） | 无 | 不承载 |
| R2 JSON 字段等于 + 浮点容差 | 确定性比对 | `correctness` / `task_completion`（LLM judge，无数值容差） | 不承载 |
| R3 重试证据（同页重试） | 序列 + 参数匹配 | `self_correction_rate`（不看参数） | **语义不对**（2.3 已证） |
| R4 参数 schema 合法（任一次违规即失败） | 确定性 schema 校验 | `tool_invocation`（LLM judge）、`tool_recall`（需 ground truth 工具集） | 不承载 |
| R5 干净退出 | 终态字段比对 | `submitted` / `task_completion` | 部分可用 |
| — | 通用行为画像 | `repeated_tool_call_rate`、`tool_call_count`、`turn_count`、`cost`、`safety` 系列 | **可作旁证** |

由此定调：**A2E 是"画像层"，不是"判分层"。** 它的评估单元是「指标」（一个任务一个聚合分），
我们的评估单元是「断言」（一个任务 N 条独立布尔判定 + 逐条诊断依据）。
粒度不同，不是同一层的东西——所以"二选一"这个提法本身要改。

---

## 4. 对项目的三条直接影响

1. **计划书里"评测底座 A2E / agentevals 二选一"改为"自研断言层 + 可选旁证层"**：
   rubric 执行器必须自研（R1–R5 的判定逻辑就几百行），A2E/agentevals 只在有余力时接成附加画像。
2. **`trace_requirements` 语义要补一条**：现在写的是"不上报 trace → 轨迹类断言判 0"，
   应改为「**判 unscored，且不计入该技能掌握度更新**」。理由：判 0 会把"数据缺失"和"能力不足"
   混为一谈，污染 FSRS 的 D/S——这条是 A2E 的反面教材给的，反过来验证了我们自己的设计要更严。
3. **A2E 不进 MVP 依赖**。它的价值是 README 级的"26 指标清单"可作设计参考
   （错误模式 → 指标的映射思路），代码不引入，省掉 1024 + 725 包的依赖负担。

---

## 5. 文件清单

| 文件 | 作用 |
|---|---|
| `a2e_probe.py` | 造 dbg-001 假 trace 并灌进 A2E（建 dataset → experiment → spans → run），幂等可重跑 |
| `a2e_probe_result.json` | 探针输出（dataset/experiment/trace id、回读 span 数、属性键） |
| `test_a2e_semantics.py` | 哨兵测试：断言"假重试与真重试同分"这一缺陷 |

## 6. 未做 / 下一步

- **4c-4（写 R3 custom evaluator 并计时）取消**：A2E 没有"注册自定义 evaluator"的公共接口
  （`/v1/annotation_configs` 只支持内置模板），要接就得改上游源码，超出 spike 边界。
  自研断言层的计时改在 M0 第 5 步「rubric 执行器」里做。
- **4d（LLM judge）不做**：`repeated_tool_call_rate` 与全部 `plan` / `safety` 指标要 LLM，
  但既然结论已是"不进 MVP 依赖"，烧 key 无收益。
- 若后续要复核 A2E：改用 `a2e serve --with-trace-fixtures` 自带夹具（不依赖我们造的 trace），
  或在 `eval/core/deal_server.py` 里把版本门槛判断绕过——**但那是改上游，须先评估维护成本**。

# T4 线程指令：`retry-drill` 家族加 2 道重试题

> 这份文件是**一条线程的全部指令**。读完就能动手，不需要问人、不需要看别的线程。
> 编号段：`ret-005` / `ret-006`。只准用这两个编号。

---

## 0. 先确认你在哪（三条都对了再动手）

```bash
pwd                          # 路径末尾必须是 arena-t-retry
git branch --show-current    # 必须是 thread/retry
git log --oneline -1         # 应以 docs(threads): 开头
```

任何一条不符 → **停手**，把实际输出报给需求方。不要在错目录里写文件。

---

## 1. 先读这五份（按顺序，总共约 20 分钟）

| 文件 | 你从里面要拿什么 |
|---|---|
| `docs/authoring-tasks.md` | 加题七步清单、陷阱表。**§1 第 6 步与 §5 第 5 条是这家族的命门** |
| `tasks/dbg-001-order-report.yaml` | 母本：骨架 + 故障块 + R3 期望的完整形状 |
| `tasks/ret-001-refund-503.yaml` | 家族最简单的一份 |
| `tasks/ret-004-narrow-page.yaml` | 最新一份（2026-11-21，页上限 5、30 行 6 页、故障在第 20 条） |
| `src/arena/mock_tools.py` 的 `page_covers()` 与 `_take_fault()` | **故障到底怎么被触发**——不读这个你会写出"永远踩不到"的题 |

---

## 2. 要产出的两道题

| 编号 | 难度 | 页数要求 | 故障位置 | 建议行数 |
|---|---|---|---|---|
| `ret-005` | 2 | ≥ 3 页 | 第 3 页附近 | ≥ 21 行（建议 22–25） |
| `ret-006` | 3 | ≥ 4 页 | 第 4 页之后 | ≥ 31 行（建议 33–38） |

「难度 3 的故障更靠后」不是装饰：**这是这家族唯一真实的难度来源**——
学习者必须在翻了三四页、已经以为自己快成功的时候，才有能力处理失败。
（页数下限是测试硬查的：难度 2 → ≥3 页、3 → ≥4 页；故障位置靠后没有测试，靠你自觉。）

---

## 3. 在哪改（精确到文件）

**新增两个文件**：

```
tasks/ret-005-<英文短名>.yaml
tasks/ret-006-<英文短名>.yaml
```

短名 2–4 个英文单词、小写、连字符（例：`export-poll`、`recon-page6`）。

**一个文件都不许改**：母本、ret-001/002/003/004、除下面两行之外的任何 `tests/` 文件、
`docs/BACKLOG.md`、`docs/ROADMAP.md`。

**唯一允许改的两行测试**：

```python
# tests/test_task_variants.py
assert len(all_tasks) == 13, ...      →      assert len(all_tasks) == 15, ...

# tests/test_derivation.py
assert checked == 11, ...             →      assert checked == 13, ...
```

---

## 4. 必须写对的六个地方

**① 头部**
```yaml
base_task: dbg-001-order-report
task_id: ret-005-<短名>            # 必须与文件名一致
title: <场景>（重试向 · 变体）
primary_skill_id: failure_retry    # 家族主技能，写错测试会红
secondary_skill_ids: ['multi_step_planning', 'tool_param_validation']
variant_template_id: tpl-tool-debug
variant_meta:
  family_id: retry-drill
  difficulty: 2                    # ret-005 写 2，ret-006 写 3
  kind: variant
```

**② 变体只写差异**

**不许**出现 `rubric_gate:` / `scoring:` / `trace_requirements:` / `submission_requirements:` 块。
抄了会红。

**③ 题面：上限、行数必须真**

```
1. 使用工具 fetch_orders 分页拉取全部<业务对象>（limit 取值必须在 1 到 10 之间，
   超过 10 服务端会返回错误 invalid_limit；共 <N> 条记录）
```

**关键：题面不许提"可能会失败"或"失败就重试一次"**。这个家族考的就是"你没被提示，遇到失败
能不能自己重试"——题面一提，R3 就变成照抄。

**④ 数据：逐行注释，期望值先跑再写**

```yaml
  orders_fixture:
    - {order_id: ex-001, amount: 541.0}
    - {order_id: ex-002, amount: null}        # 金额为 null
```

`expected` 与 `rubric_credit[R2_report_correct].expected` 必须是同一对数。

**⑤ 故障位：这是这道题的全部技术难点**

```yaml
fault_injection:
  match:
    offset_covers: <你的故障点>
  failures:
    - attempt: 1
      http_status: 503            # 现有题用过 500 / 502 / 503
      error: service_unavailable
```

三条硬要求：

1. **必须用 `offset_covers: N`（页区间覆盖），不是 `offset: N`（精确相等）**。
   写后者的话，学习者用 `limit=3` 这类合法小页就**永远踩不到**故障——数据拉全了，
   却被判「没重试」。这是 B-31 修掉的坑，别退回去。
2. **故障点要在你的行数范围内**（第 N 条，`N` 从 0 起，且 `N < 行数`）。
3. **任意合法 limit（1–10）都要能翻到它一次**。

已用过：10 / 20 / 30（ret-004 也是 20）。选没被用过的（例如 12 / 25 / 34），
且 `ret-006` 要比 `ret-005` 更靠后。

**⑥ R3 期望：恰好两步，与上面逐字段相等**

```yaml
  - id: R3_retry_evidence
    expected:
      calls:
        - {name: fetch_orders, params_match: {offset_covers: <同一个数>}, status: error, http_status: 503}
        - {name: fetch_orders, params_match: {offset_covers: <同一个数>}, status: success}
```

`status` 只能是 `error`。**不要**设计"连续失败两次"、"超时"、"第二次调用才失败"
——现有骨架表达不出来（规范 §1 第 6 步末尾）。

**`error_pattern_detection` 不用写**：母本的 cases 已是相对谓词，直接继承。

---

## 5. 要不要测试：要。而且这家族多一条自检

### 动手前：先跑一次基线（不跑就是盲改）

```bash
PYTHONPATH=src pytest tests/test_task_yaml.py tests/test_task_variants.py \
  tests/test_task_executability.py tests/test_derivation.py -q
```

**这台机器的 `python` 是裸解释器，没有 pytest。必须写 `pytest`，写成 `python -m pytest` 会直接报错。**

基线应为 **171 passed / 约 36 秒**。如果不是 171，说明目录不对，停手。

### 特别自检：任意页大小都能踩到故障（这家族才会有的坑）

第二档测试**不会**遍历所有 limit 去验你的新题。自己花五分钟验一次，否则可能交付一道
"学习者换个页大小就拿不到分"的题。

把 `<你的短名>` 换掉，在仓库根执行（只读，不写文件，不留在 `tests/` 里）：

```bash
PYTHONPATH=src python - <<'EOF'
from pathlib import Path
from arena import diagnosis_report as dr
from arena import mock_tools as mt

task = dr.load_task(Path("tasks/ret-005-<你的短名>.yaml"))   # load_task 会合并母本
rows = task["test_inputs"]["orders_fixture"]
ceiling = task["tools"][0]["mock"]["pagination"]["page_size_max"]
for limit in range(1, ceiling + 1):       # 只跑合法范围！超限会返回 400，被误记成故障命中
    svc = mt.MockToolService(task)        # 每个 limit 必须新建，故障额度是 per_submission
    hits, off = [], 0
    while off < len(rows):
        if svc.call("fetch_orders", {"offset": off, "limit": limit})[0] != 200:
            hits.append(off)
        off += limit
    print(f"limit={limit:2d}  hits={hits}")
EOF
```

判据：**每一行的 `hits` 都恰好 1 个元素**。出现空列表（永远踩不到）或 2 个以上
（故障重复触发 / 或你跑到了超限的 limit）都是错的。

> 实测参考（`ret-004`，30 行、上限 5、故障第 20 条）：limit=1..5 全部 `hits=[20]`；
> limit 写 6 以上就变成 `[0, 6, 12, 18, 24]` 这种——那是 400 被当成了故障，不是故障点错。

`ret-006` 同样跑一遍，把文件名换掉。**两题的结果都要贴进报告。**

### 写完：第一档

```bash
PYTHONPATH=src pytest tests/test_task_yaml.py tests/test_task_variants.py \
  tests/test_task_executability.py tests/test_derivation.py -q
```

通过标准：**全绿**，条数约 **187**（每道题约 +8 条，允许浮动；红了一定有事）。

### 写完：第二档（最能发现题目写坏）

```bash
PYTHONPATH=src pytest tests/test_task_variants.py -q -k "full_marks or own_failure"
```

通过标准：**全绿**，条数约 **30**。

这一档跑两遍参考 Agent：一遍必须满分（`ratio == 1.0`），一遍故意放弃重试必须判
`R3_retry_evidence == fail` 且错误模式是 **`no_retry`**。
**分数对、模式错也会红**——那说明学习者会拿到"重试参数错"这种错诊断。

### 绝对不要做

- 不要跑 `pytest tests -q`（全量 12–15 分钟，四条线程同时跑会互相超时）
- 红了不要改测试（除 §3 那两行）。按这个顺序自查：
  1. 页数够不够（难度 2 → ≥3 页、3 → ≥4 页）
  2. `fault_injection.match` 与 R3 的 `params_match` 是否相等、是否都用了 `offset_covers`
  3. `expected` 与 `rubric_credit[R2].expected` 是否相等
  4. 题面写的行数是否等于 `orders_fixture` 长度
  5. 有没有在题面里泄漏"要重试"

---

## 6. 题材槽（需求方填；空着就用示例，并在报告里注明）

- **ret-005 场景**：__________
  - 示例：批量导出任务轮询，翻到第 3 页时网关返回 502
- **ret-006 场景**：__________
  - 示例：对账接口在第 6 页才 503（要翻过 5 页成功才会遇到）

---

## 7. 改完之后文件放哪

**全部留在你自己的工作区里，commit 到你自己的分支。不 push。**

```bash
git add tasks/ret-005-*.yaml tasks/ret-006-*.yaml tests/test_task_variants.py tests/test_derivation.py
git commit -m "feat(tasks): 加 ret-005 / ret-006（retry-drill）"
```

清单：

| 产出 | 位置 | 谁合并 |
|---|---|---|
| 两个新题 | `tasks/ret-005-*.yaml`、`tasks/ret-006-*.yaml` | 需求方最后统一合并 |
| 计数改动 | `tests/test_task_variants.py` + `tests/test_derivation.py` 各一行 | 同上（合并时按总数重算，别跟别人对齐） |
| **你的报告** | `docs/threads/report-retry.md`（也要 commit） | 同上，作为加题证据留档 |

**不要**：不 push、不 merge 到 main、不改别人编号段、不改 BACKLOG/ROADMAP。

---

## 8. 报告写什么（`docs/threads/report-retry.md`）

四块，缺一块算没完成：

```markdown
# retry-drill 加题报告（ret-005 / ret-006）

## A. 改了什么
（贴 `git diff --stat` 的输出）

## B. 验收结果
第一档：<x> passed in <y>s
第二档：<x> passed in <y>s
§5 自检：每个 limit 的 hits 列表（应全是 1）

## C. 三个问题（这是唯一能证明"不是只换了业务名"的东西）
1. 学习者要处理的东西数量变了吗？（行数 / 页数 / 要翻几页才遇到故障）
2. 故障或异常的种类变了吗？
3. 决策点变了吗？（在哪一步必须做出判断）
——三条都答"没变"说明这题只是改名，回去改题再做一次。

## D. 我不确定的地方
（宁可写"我不确定"，不要写"应该没问题"）
```

---

## 9. 一句话总结

换**场景、行数、故障在第几页、故障码**；
不换**断言结构、工具名 `fetch_orders`、剔除规则、R3 两步形状**。
故障必须 `offset_covers` 且任意合法 limit 都恰好踩到一次——这条错了整道题就是废的。
两档全绿 + §5 自检全 1 + 报告四块 = 结束。

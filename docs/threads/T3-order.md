# T3 线程指令：`order-report` 家族加 2 道汇报题

> 这条线程是**一条线程的全部指令**。读完就能动手，不需要问人、不需要看别的线程。
> 编号段：`dbg-005` / `dbg-006`。只准用这两个编号。

---

## 0. 先确认你在哪（三条都对了再动手）

```bash
pwd                          # 路径末尾必须是 arena-t-order
git branch --show-current    # 必须是 thread/order
git log --oneline -1         # 应以 docs(threads): 开头
```

任何一条不符 → **停手**，把实际输出报给需求方。不要在错目录里写文件。

---

## 1. 先读这四份（按顺序，总共约 15 分钟）

| 文件 | 你从里面要拿什么 |
|---|---|
| `docs/authoring-tasks.md` | 加题的七步清单、陷阱表、"什么算完成"。**这是判据来源** |
| `tasks/dbg-001-order-report.yaml` | 母本：骨架全貌 + **故障注入的完整写法**（重点看 `fault_injection` 与 R3） |
| `tasks/dbg-002-invoice-report.yaml` | **范本**：最干净的变体（66 行），你的两题照它的结构写 |
| `tasks/dbg-004-sensor-batch.yaml` | 同家族难度 3 的一份，看它怎么把"页数"做上去 |

### 一个必须提前知道的事，否则你会白找十分钟

`dbg-003` 和 `dbg-004` 这两个文件里**没有 `fault_injection` 块**——它们的故障是从母本
`dbg-001` **继承**下来的（`offset_covers: 10` / HTTP 500）。所以：

- **不要**去 `dbg-003/004` 里找故障码，那里没有
- **要**去 `dbg-001` 看故障块和 R3 期望的完整形状

---

## 2. 要产出的两道题

| 编号 | 难度 | 页数要求 | 建议行数 | 故障位 |
|---|---|---|---|---|
| `dbg-005` | 2 | ≥ 3 页 | ≥ 21 行（建议 22–25） | 与已有题错开 |
| `dbg-006` | 3 | ≥ 4 页 | ≥ 31 行（建议 33–36） | 比 dbg-005 更靠后 |

**为什么是 21 / 31 行**：母本的工具上限是 `page_size_max: 10`（继承，不用改），
`ceil(21/10)=3` 页、`ceil(31/10)=4` 页。难度与页数是测试硬查的
（`test_difficulty_is_not_decorative`：难度 2 → ≥3 页、难度 3 → ≥4 页），少一页就红。

---

## 3. 在哪改（精确到文件）

**新增两个文件**：

```
tasks/dbg-005-<英文短名>.yaml
tasks/dbg-006-<英文短名>.yaml
```

短名 2–4 个英文单词、小写、连字符（例：`return-refund`、`stock-audit`）。

**一个文件都不许改**：母本、dbg-002/003/004、除下面两行之外的任何 `tests/` 文件、
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
task_id: dbg-005-<短名>            # 必须与文件名一致
title: <业务对象>金额核对报告（调试向 · 变体）
primary_skill_id: multi_step_planning
secondary_skill_ids: ['tool_param_validation', 'failure_retry']
variant_template_id: tpl-tool-debug
variant_meta:
  family_id: order-report
  difficulty: 2                    # dbg-005 写 2，dbg-006 写 3
  kind: variant
```

**② 变体只写差异**

**不许**出现 `rubric_gate:` / `scoring:` / `trace_requirements:` / `submission_requirements:` 这几个块——
它们要从母本继承。抄了会红（`test_every_variant_declares_its_parent_and_does_not_restate_skeleton`）。

**③ 题面：三个数字必须与真实一致**

照 `dbg-002` 的句式：

```
1. 使用工具 fetch_orders 分页拉取全部<业务对象>（limit 取值必须在 1 到 10 之间，
   超过 10 服务端会返回错误 invalid_limit；共 <N> 条记录）
```

`1 到 10` / `invalid_limit` / `共 N 条` 三处必须与工具定义、`test_inputs` 真实值一致。
`N` 就是你 `orders_fixture` 的行数。

**不许写解法提示**（不写"先拉第一页再看有没有错"这类）。题面只描述工作，写了解法 R3/R4 就测不到真本事。

**④ 数据：每一行被剔除的都要逐行注释**

```yaml
  orders_fixture:
    - {order_id: rt-001, amount: 541.0}
    - {order_id: rt-002, amount: null}        # 金额为 null
    - {order_id: rt-003}                      # 缺 amount 字段
    - {order_id: rt-004, amount: -88.5}       # 负数
```

剔除规则与母本一致：缺 `amount`、`null`、负数。**别自己加新规则**——R2 的断言类型对不上。

建议让**两类以上**的无效行同时出现（比如既有 null 又有负数），这样"要处理的东西"确实变多了。

**⑤ 期望值：先跑再写**

```yaml
  expected:
    valid_count: <自己数>
    total_amount: <自己加>
```

`rubric_credit[R2_report_correct].expected` 必须是同一对数。写完复核一遍浮点求和。

**⑥ 故障位：必须显式写，且与已有题错开**

```yaml
fault_injection:
  match:
    offset_covers: <你的故障点>
  failures:
    - attempt: 1
      http_status: 500            # 500 / 502 / 503 是现有题用过的三个值
      error: internal_error
```

- 已用过：5 / 8 / 10 / 15 / 20 / 30。选没被用过的（例如 12 / 25 / 34）
- 故障点必须落在你行数范围内，且**任意合法 limit（1–10）都能翻到它**
  （`offset_covers` = 页区间覆盖，不是精确相等）
- `dbg-006` 的故障位要比 `dbg-005` 更靠后——这是"难度 3"的真实含义
- `rubric_credit[R3_retry_evidence].expected.calls` 必须恰好两步，且与上面逐字段相等：

```yaml
  - id: R3_retry_evidence
    expected:
      calls:
        - {name: fetch_orders, params_match: {offset_covers: <同一个数>}, status: error, http_status: 500}
        - {name: fetch_orders, params_match: {offset_covers: <同一个数>}, status: success}
```

**R3 形状是写死的**：恰好两步（失败 → 成功），失败那步 `status` 只能是 `error`。
不要设计"连续失败两次"或"第二次调用才失败"——现有骨架表达不出来。

**`error_pattern_detection` 不用写**：母本的 cases 已是相对谓词，直接继承。

---

## 5. 要不要测试：要，两档都必须全绿

### 动手前：先跑一次基线（不跑就是盲改）

```bash
PYTHONPATH=src pytest tests/test_task_yaml.py tests/test_task_variants.py \
  tests/test_task_executability.py tests/test_derivation.py -q
```

**这台机器的 `python` 是裸解释器，没有 pytest。必须写 `pytest`，写成 `python -m pytest` 会直接报错。**

基线应为 **171 passed / 约 36 秒**。如果不是 171，说明目录不对，停手。

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
`R3_retry_evidence == fail` 且错误模式是 `no_retry`。**分数对、模式错也会红。**

### 特别自检：任意页大小都能踩到故障

第二档测试**不会**遍历所有 limit 去验你的新题。自己花五分钟验一次，否则可能交付一道
"学习者换个页大小就拿不到分"的题。

把 `<你的短名>` 换掉，在仓库根执行（只读，不写文件，不留在 `tests/` 里）：

```bash
PYTHONPATH=src python - <<'EOF'
from pathlib import Path
from arena import diagnosis_report as dr
from arena import mock_tools as mt

task = dr.load_task(Path("tasks/dbg-005-<你的短名>.yaml"))   # load_task 会合并母本
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

判据：**每一行的 `hits` 都恰好 1 个元素**。空列表（永远踩不到）或 2 个以上（重复触发，
或你跑到了超限的 limit）都是错的。`dbg-006` 同样跑一遍，**两题结果都贴进报告**。

### 绝对不要做

- 不要跑 `pytest tests -q`（全量 12–15 分钟，四条线程同时跑会互相超时）
- 红了不要改测试（除 §3 那两行）。按这个顺序自查：
  1. 页数够不够（难度 2 → ≥3 页、3 → ≥4 页）
  2. `expected` 与 `rubric_credit[R2].expected` 是否相等
  3. `fault_injection.match` 与 R3 的 `params_match` 是否相等
  4. 题面写的行数是不是真的等于 `orders_fixture` 长度
  5. 有没有不小心抄了骨架块

---

## 6. 题材槽（需求方填；空着就用示例，并在报告里注明）

- **dbg-005 场景**：__________
  - 示例：退货单金额核对（`return_id`，无效行 = 缺金额 / 金额为负）
- **dbg-006 场景**：__________
  - 示例：库存盘点差异汇总（`sku_id`，无效行 = 缺金额 / null / 负数，共 3 类）

---

## 7. 改完之后文件放哪

**全部留在你自己的工作区里，commit 到你自己的分支。不 push。**

```bash
git add tasks/dbg-005-*.yaml tasks/dbg-006-*.yaml tests/test_task_variants.py tests/test_derivation.py
git commit -m "feat(tasks): 加 dbg-005 / dbg-006（order-report）"
```

清单：

| 产出 | 位置 | 谁合并 |
|---|---|---|
| 两个新题 | `tasks/dbg-005-*.yaml`、`tasks/dbg-006-*.yaml` | 需求方最后统一合并 |
| 计数改动 | `tests/test_task_variants.py` + `tests/test_derivation.py` 各一行 | 同上（合并时按总数重算，别跟别人对齐） |
| **你的报告** | `docs/threads/report-order.md`（也要 commit） | 同上，作为加题证据留档 |

**不要**：不 push、不 merge 到 main、不改别人编号段、不改 BACKLOG/ROADMAP。

---

## 8. 报告写什么（`docs/threads/report-order.md`）

四块，缺一块算没完成：

```markdown
# order-report 加题报告（dbg-005 / dbg-006）

## A. 改了什么
（贴 `git diff --stat` 的输出）

## B. 验收结果
第一档：<x> passed in <y>s
第二档：<x> passed in <y>s

## C. 三个问题（这是唯一能证明"不是只换了业务名"的东西）
1. 学习者要处理的东西数量变了吗？（行数 / 页数 / 剔除了几类无效行）
2. 故障或异常的种类变了吗？
3. 决策点变了吗？（在哪一步必须做出判断）
——三条都答"没变"说明这题只是改名，回去改题再做一次。

## D. 我不确定的地方
（宁可写"我不确定"，不要写"应该没问题"）
```

---

## 9. 一句话总结

换**业务对象、行数（要够页数）、故障位置（要更靠后）、无效行种类**；
不换**断言结构、工具名 `fetch_orders`、剔除规则、聚合口径、页上限 10**。
故障块必须显式写（dbg-003/004 是继承的，别去那里找）。两档全绿 + 报告四块 = 结束。

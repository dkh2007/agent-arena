# T2 线程指令：`param-surface` 家族加 1 道参数校验题

> 这份文件是**一条线程的全部指令**。读完就能动手，不需要问人、不需要看别的线程。
> 编号段：`prm-004`。只准用这一个编号。

---

## 0. 先确认你在哪（三条都对了再动手）

```bash
pwd                          # 路径末尾必须是 arena-t-param
git branch --show-current    # 必须是 thread/param
git log --oneline -1         # 应以 docs(threads): 开头
```

任何一条不符 → **停手**，把实际输出报给需求方。不要在错目录里写文件。

---

## 1. 先读这四份（按顺序，总共约 15 分钟）

| 文件 | 你从里面要拿什么 |
|---|---|
| `docs/authoring-tasks.md` | 加题的七步清单、陷阱表、"什么算完成"。**这是判据来源** |
| `tasks/dbg-001-order-report.yaml` | 母本：骨架全貌（5 条断言、工具契约、执行边界） |
| `tasks/prm-003-page-ceiling-4.yaml` | **范本**：同家族最难的一份，重点看它的题面怎么写上限 |
| `tests/test_task_yaml.py` 里的 `test_scenario_states_limit_error`、`test_scenario_mentions_the_real_ceiling_and_row_count` | 这两条会硬查你的题面，先读判据再写题面 |

最后两个测试函数是全家族的命门：**题面写的上限，必须与工具定义的真实上限一字不差**。
现有 4 道题里已经有 2 道栽过这里（两处各写一份，只改了一处）。

---

## 2. 要产出的题

| 编号 | 难度 | 页上限 | 硬指标 |
|---|---|---|---|
| `prm-004` | 2 | **6** | 总行数满足 `ceil(行数 / 6) ≥ 3`，即 **≥ 13 行** |

现有三道的上限是 5 / 5 / 4，你这道换成 **6**：更宽松，学习者更容易"顺手写 10"然后被拒。
难度必须真的是 2 —— 页数少于 3 页测试会红（`test_difficulty_is_not_decorative`）。

建议 18–20 行（`ceil(18/6)=3`、`ceil(20/6)=4`，都满足）。行数越多，剔除行越好藏。

---

## 3. 在哪改（精确到文件）

**新增一个文件**：

```
tasks/prm-004-<英文短名>.yaml
```

短名 2–4 个英文单词、小写、连字符（例：`tracking-batch`、`claim-ceiling-6`）。

**一个文件都不许改**：母本、prm-001/002/003、除下面两行之外的任何 `tests/` 文件、
`docs/BACKLOG.md`、`docs/ROADMAP.md`。

**唯一允许改的两行测试**：

```python
# tests/test_task_variants.py
assert len(all_tasks) == 13, ...      →      assert len(all_tasks) == 14, ...

# tests/test_derivation.py
assert checked == 11, ...             →      assert checked == 12, ...
```

为什么 derivation 也要 +1：汇报族（dbg/ret/prm）的答案来自推导规则，都声明了 `derivation`。
（定位族 `fix-*` 才不声明——这是 T1 线程的事，与你无关。）

---

## 4. 必须写对的五个地方

**① 头部**
```yaml
base_task: dbg-001-order-report
task_id: prm-004-<短名>            # 必须与文件名一致
primary_skill_id: tool_param_validation
secondary_skill_ids: ['multi_step_planning', 'failure_retry']
variant_template_id: tpl-tool-debug
variant_meta:
  family_id: param-surface
  difficulty: 2
  kind: variant
```

**② 题面：上限必须说清、说对**

照 `prm-003` 的句式，把数字换成你的：

```
1. 使用工具 fetch_orders 分页拉取全部<你的业务对象>（limit 取值必须在 1 到 6 之间，
   超过 6 服务端会返回错误 invalid_limit；共 <N> 条记录）
```

- 「1 到 6」「invalid_limit」「共 N 条」三个数字/词，必须与 §3 的工具定义和 `test_inputs` 完全一致
- **不许写解法提示**。不要写"先试 limit=6 再拉"——题面只描述工作，把 R4 变成照抄就没意义了

**③ 工具定义：两处上限必须同时改**

```yaml
tools:
  - name: fetch_orders
    parameters:
      - name: offset
        type: integer
        required: true
        minimum: 0
      - name: limit
        type: integer
        required: true
        minimum: 1
        maximum: 6                      # ← 这里
    mock:
      pagination:
        page_size_max: 6                # ← 和这里，必须是同一个数
```

这两处不一致，`test_scenario_mentions_the_real_ceiling_and_row_count` 会红。
**这是本家族最常见的错**。

**④ 数据与期望值：先跑再写，不许预填**

`test_inputs.orders_fixture` 每一行被剔除的，**逐行注释原因**（缺 `amount` / `null` / 负数）。
剔除规则与母本一致：`amount` 缺失、为 `null`、为负数，都不计入。

```yaml
  expected:
    valid_count: <自己数出来的>
    total_amount: <自己加出来的>
```

然后 `rubric_credit[R2_report_correct].expected` 必须是**同一对数**（测试会逐字段比）。

自己数的时候容易错在：浮点数求和精度、忘了跳过 null、把负数当有效。
写完用脚本或手算复核一遍——**这两处不一致是测试最常抓到的错**。

**⑤ 故障位：用没用过的位置**

```yaml
fault_injection:
  match:
    offset_covers: <你的故障点>
  failures:
    - attempt: 1
      http_status: 500            # 或 502 / 503，三者都是现有题用过的
      error: internal_error
```

- 已用过：5 / 8 / 10 / 15 / 20 / 30。尽量错开（无测试强制，是为了复习层能区分）
- 故障点必须落在你总行数**范围内**（第 N 条，N 从 0 起），且任意合法 `limit` 都能翻到它
  （`offset_covers` 是"页区间覆盖"，不是精确相等——B-31 已修，别退回旧写法）
- `rubric_credit[R3_retry_evidence].expected.calls` 的 `params_match` 必须是
  `{offset_covers: <同一个数>}`，两步：失败（`status: error`, 同 `http_status`）→ 成功

**`error_pattern_detection` 不用写**：母本的 cases 已经是相对谓词，与页大小、故障位置无关，直接继承。

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

通过标准：**全绿**，条数约 **179**（每道题约 +8 条，允许浮动；红了一定有事）。

### 写完：第二档（最能发现题目写坏）

```bash
PYTHONPATH=src pytest tests/test_task_variants.py -q -k "full_marks or own_failure"
```

通过标准：**全绿**，条数约 **28**。

这一档跑两遍参考 Agent：一遍必须满分（`ratio == 1.0`），一遍故意放弃重试必须判
`R3_retry_evidence == fail` 且错误模式是 `no_retry`。**分数对、模式错也会红。**

### 特别自检：任意页大小都能踩到故障

你的页上限是 6，第二档测试**不会**遍历所有 limit 去验这道题。自己验一次，
否则可能交付一道"学习者换个页大小就拿不到分"的题。

在仓库根执行（只读，不写文件，不留在 `tests/` 里）：

```bash
PYTHONPATH=src python - <<'EOF'
from pathlib import Path
from arena import diagnosis_report as dr
from arena import mock_tools as mt

task = dr.load_task(Path("tasks/prm-004-<你的短名>.yaml"))   # load_task 会合并母本
rows = task["test_inputs"]["orders_fixture"]
ceiling = task["tools"][0]["mock"]["pagination"]["page_size_max"]
for limit in range(1, ceiling + 1):       # 只跑到合法上限，超限会返回 400 混进 hits
    svc = mt.MockToolService(task)        # 每个 limit 必须新建，故障额度是 per_submission
    hits, off = [], 0
    while off < len(rows):
        if svc.call("fetch_orders", {"offset": off, "limit": limit})[0] != 200:
            hits.append(off)
        off += limit
    print(f"limit={limit:2d}  hits={hits}")
EOF
```

判据：**每一行 `hits` 都恰好 1 个元素**。空列表或 2 个以上都是错的。结果贴进报告。

### 绝对不要做

- 不要跑 `pytest tests -q`（全量 12–15 分钟，四条线程同时跑会互相超时）
- 红了不要改测试（除 §3 那两行）。按这个顺序自查：
  1. 题面的上限 / 行数 与 `tools`、`test_inputs` 是否一致
  2. `expected` 与 `rubric_credit[R2].expected` 是否相等
  3. `fault_injection.match` 与 R3 的 `params_match` 是否相等
  4. 页数是否 ≥ 3（难度 2 的要求）

---

## 6. 题材槽（需求方填；空着就用示例，并在报告里注明）

- **prm-004 场景**：__________
  - 示例：物流轨迹批量查询，字段名是 `tracking_id`，无效行是"缺签收时间 / 时间为 null / 时间为将来"

换场景时记得：`orders_fixture` 的键名可以改（`order_id` → `tracking_id`），但
**剔除规则与聚合口径不能改**（有效条数 + 金额总和），否则 R2 的断言类型对不上。

---

## 7. 改完之后文件放哪

**全部留在你自己的工作区里，commit 到你自己的分支。不 push。**

```bash
git add tasks/prm-004-*.yaml tests/test_task_variants.py tests/test_derivation.py
git commit -m "feat(tasks): 加 prm-004（param-surface 页上限 6）"
```

清单：

| 产出 | 位置 | 谁合并 |
|---|---|---|
| 新题 | `tasks/prm-004-*.yaml` | 需求方最后统一合并 |
| 计数改动 | `tests/test_task_variants.py` + `tests/test_derivation.py` 各一行 | 同上（合并时按总数重算，别跟别人对齐） |
| **你的报告** | `docs/threads/report-param.md`（也要 commit） | 同上，作为加题证据留档 |

**不要**：不 push、不 merge 到 main、不改别人编号段、不改 BACKLOG/ROADMAP。

---

## 8. 报告写什么（`docs/threads/report-param.md`）

四块，缺一块算没完成：

```markdown
# param-surface 加题报告（prm-004）

## A. 改了什么
（贴 `git diff --stat` 的输出）

## B. 验收结果
第一档：<x> passed in <y>s
第二档：<x> passed in <y>s

## C. 三个问题（这是唯一能证明"不是只换了业务名"的东西）
1. 学习者要处理的东西数量变了吗？（行数 / 剔除了几类 / 要翻几页）
2. 故障或异常的种类变了吗？
3. 决策点变了吗？（在哪一步必须做出判断）
——三条都答"没变"说明这题只是改名，回去改题再做一次。

## D. 我不确定的地方
（宁可写"我不确定"，不要写"应该没问题"）
```

---

## 9. 一句话总结

换**业务对象、行数、上限值（6）、故障位置**；
不换**断言结构、剔除规则、聚合口径、工具名 `fetch_orders`**。
两处上限必须同时改，两处期望值必须相等。测试两档全绿 + 报告四块齐全 = 这条线程结束。

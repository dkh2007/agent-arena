# T1 线程指令：`locate-drill` 家族加 2 道定位题

> 这份文件是**一条线程的全部指令**。读完就能动手，不需要问人、不需要看别的线程。
> 编号段：`fix-003` / `fix-004`。只准用这两个编号。

---

## 0. 先确认你在哪（三条都对了再动手）

```bash
pwd                          # 路径末尾必须是 arena-t-locate
git branch --show-current    # 必须是 thread/locate
git log --oneline -1         # 应以 docs(threads): 开头
```

任何一条不符 → **停手**，把实际输出报给需求方。不要在错目录里写文件。

---

## 1. 先读这四份（按顺序，总共约 15 分钟）

| 文件 | 你从里面要拿什么 |
|---|---|
| `docs/authoring-tasks.md` | 加题的七步清单、陷阱表、"什么算完成"。**这是判据来源** |
| `tasks/fix-001-token-expiry.yaml` | 母本：定位族骨架全貌（5 条断言、`locate` 块、`files` 结构） |
| `tasks/fix-002-cart-none-deref.yaml` | **范本**：最干净的一份变体，你的两题照它的结构写 |
| `tests/test_locate_skeleton.py` | 定位族特有的机器判据（行号、根因枚举、日志格式） |

第四份别跳过：定位族和汇报族**判分方式不同**，你会被 `locate` 块和根因枚举绊住。

---

## 2. 要产出的两道题

| 编号 | 难度 | 硬指标 | 题材 |
|---|---|---|---|
| `fix-003` | 2 | 日志里 **≥ 3 条**含 `" ERROR "` 的行 | 见 §6 题材槽 |
| `fix-004` | 3 | 日志里 **≥ 4 条**含 `" ERROR "` 的行 | 见 §6 题材槽 |

难度与 ERROR 条数是**测试硬查**的（`test_difficulty_is_not_decorative`：难度 1→≥2、2→≥3、3→≥4）。
少一条就红，别想着"差不多"。

两题都必须是「读日志 + 读源码 → 根因 + 出错行号」这个形状，不许改成别的题型。

---

## 3. 在哪改（精确到文件）

**新增两个文件**：

```
tasks/fix-003-<英文短名>.yaml
tasks/fix-004-<英文短名>.yaml
```

短名用 2–4 个英文单词、小写、连字符，表达场景即可（例：`tz-mismatch`、`race-overwrite`）。

**一个文件都不许改**（母本、fix-002、测试、文档）：

- `tasks/fix-001-token-expiry.yaml`、`tasks/fix-002-cart-none-deref.yaml`
- 除下面两行之外的**任何** `tests/` 文件
- `docs/BACKLOG.md`、`docs/ROADMAP.md`

**唯一允许改的测试行**（`tests/test_task_variants.py`）：

```python
assert len(all_tasks) == 13, ...      →      assert len(all_tasks) == 15, ...
```

`tests/test_derivation.py` 里的 `assert checked == 11` **不要动**——定位题的答案不是推导值，
不声明 `derivation` 块，所以那个计数不增加。

---

## 4. 每道题必须写对的六个地方

照 `fix-002` 抄结构，但这六处必须换成你自己的：

**① 头部四行**
```yaml
base_task: fix-001-token-expiry      # 不变
task_id: fix-003-<短名>              # 必须与文件名一致
title: <场景名>（定位向）
primary_skill_id: multi_step_planning
```

**② `variant_meta`**
```yaml
variant_meta:
  family_id: locate-drill            # 必须，否则家族映射测试红
  difficulty: 2                      # fix-003 写 2，fix-004 写 3
```

**③ `scenario`：日志格式与根因规则必须原样保留**

`fix-002` 的题面里那两段是**判据的一部分**，不是装饰，不许改写：

```
3. 日志把每次异常压成一行：`时间 级别 request_id=.. <异常类> in <函数名>: <消息>`。
   **最后一条 ERROR** 对应本次失败；更早的 ERROR 是别的已知问题，不是这次的根因
4. 根因取值必须是以下之一（按异常类判定，不要自创标签）：
   - 异常类是 TypeError                      → type_mismatch
   - 异常类是 IndexError                     → off_by_one
   - 异常类是 AttributeError 且消息含 NoneType → null_deref
   - 其余                                    → other
```

根因**只能**是这四个值之一。自创标签会让 `test_locate_skeleton.py` 红。
你换的是**业务场景、日志内容、源码内容**，不是这套规则。

**④ `locate` 块：三个路径自己起名，但三处必须互相一致**

```yaml
locate:
  log_path: /workspace/logs/<你的>.log
  source_path: /workspace/service/<你的>.py
  diagnosis_path: /workspace/diagnosis.json     # 这个别改
```

`log_path` 与 `source_path` 必须**真的**出现在 `test_inputs.files` 的键里。

**⑤ `test_inputs.files`：日志与源码都要你自己写**

- 日志：≥ 3 条（`fix-003`）/ ≥ 4 条（`fix-004`）含 ` ERROR ` 的行，**最后一条**才是本次根因
- 源码：必须让"按规则找行号"能唯一落在一行上。写完自己数一遍是第几行（1 基）
- `expected` 里的 `offending_line` 必须与你数的一致

**⑥ 故障位：换到"你希望它失败的那次读取"上**

`fix-002` 是让**源码**第一次读 503。你的两题应该一题让日志失败、一题让源码失败，
这样学习者在两种时机都要处理重试。

```yaml
fault_injection:
  match:
    path: <你选的那个文件的路径>
  failures:
    - attempt: 1
      http_status: 503
      error: service_unavailable
```

然后 `rubric_credit[R3_retry_evidence].expected.calls` 的 `params_match.path`
必须是**同一个路径**——两处不一致测试会红（`test_fault_declaration_matches_retry_expectation`）。

**`error_pattern_detection` 不用写**：B-31 之后母本的 cases 已经是相对谓词
（`no_tool_calls` / `fault_point_not_reached` / `fault_then_moved_on` / `last_status`），
与页大小、故障位置都无关，直接继承。`fix-002` 里那段是历史遗留，照抄也不出错，但没必要。

---

## 5. 要不要测试：要，两档都必须全绿

### 动手前：先跑一次基线（不跑就是盲改）

```bash
PYTHONPATH=src pytest tests/test_task_yaml.py tests/test_task_variants.py \
  tests/test_task_executability.py tests/test_derivation.py -q
```

**这台机器的 `python` 是裸解释器，没有 pytest。必须写 `pytest`，写成 `python -m pytest` 会直接报错。**

基线应为 **171 passed / 约 36 秒**。如果不是 171，说明目录不对，停手。

### 写完两道题：第一档

```bash
PYTHONPATH=src pytest tests/test_task_yaml.py tests/test_task_variants.py \
  tests/test_task_executability.py tests/test_derivation.py -q
```

通过标准：**全绿**，条数约 **187**（每题约 +8 条，允许浮动；红了一定有事）。

### 写完两道题：第二档（最能发现题目写坏）

```bash
PYTHONPATH=src pytest tests/test_task_variants.py -q -k "full_marks or own_failure"
```

通过标准：**全绿**，条数约 **30**。

这一档会跑两遍参考 Agent：
- 一遍走到满分（`ratio == 1.0`、`skill_gaps == []`）⇒ 说明你的数据和答案键真的自洽
- 一遍故意放弃重试 ⇒ 必须判 `R3_retry_evidence == fail` **且错误模式是 `no_retry`**

**只看第一条不够**：分数对、错误模式错，复习层就会推错题。

### 绝对不要做

- 不要跑 `pytest tests -q`（全量 12–15 分钟、吃满 CPU，四条线程同时跑会互相超时）
- 红了不要去改测试（除 §3 那一行）。测试红了基本是：日志 ERROR 条数不够、行号数错、
  `locate` 路径与 `files` 键不一致、R3 的 path 与 `fault_injection.match` 不一致。
  按这个顺序自己查。

---

## 6. 题材槽（需求方填；空着就用示例，并在报告里注明）

- **fix-003 场景**：__________
  - 示例：时间戳混用 UTC 与本地时区，导致对账差额——根因 `type_mismatch`
- **fix-004 场景**：__________
  - 示例：并发写入没加锁，后写覆盖前写——根因 `other`（或按你设计的异常类取对应值）

---

## 7. 改完之后文件放哪

**全部留在你自己的工作区里，commit 到你自己的分支。不 push。**

```bash
git add tasks/fix-003-*.yaml tasks/fix-004-*.yaml tests/test_task_variants.py
git commit -m "feat(tasks): 加 fix-003 / fix-004（locate-drill 补难度 3）"
```

清单：

| 产出 | 位置 | 谁合并 |
|---|---|---|
| 两个新题 | `tasks/fix-003-*.yaml`、`tasks/fix-004-*.yaml` | 需求方最后统一合并 |
| 计数改动 | `tests/test_task_variants.py` 一行 | 同上（合并时会按总数重算，别跟别人对齐） |
| **你的报告** | `docs/threads/report-locate.md`（也要 commit） | 同上，作为加题证据留档 |

**不要**：不 push、不 merge 到 main、不改别人编号段、不改 BACKLOG/ROADMAP。

---

## 8. 报告写什么（`docs/threads/report-locate.md`）

四块，缺一块算没完成：

```markdown
# locate-drill 加题报告（fix-003 / fix-004）

## A. 改了什么
（贴 `git diff --stat` 的输出）

## B. 验收结果
第一档：<x> passed in <y>s
第二档：<x> passed in <y>s
（贴两条命令的最后一行）

## C. 三个问题（这是唯一能证明"不是只换了业务名"的东西）
1. 学习者要处理的东西数量变了吗？（日志条数 / 文件数 / 错误种类数）
2. 故障或异常的种类变了吗？
3. 决策点变了吗？（在哪一步必须做出判断）
——三条都答"没变"说明这题只是改名，回去改题再做一次。

## D. 我不确定的地方
（宁可写"我不确定"，不要写"应该没问题"）
```

---

## 9. 一句话总结

换**情景、日志、源码、故障发生在读哪个文件**；
不换**断言结构、根因枚举、日志行格式、判分口径**。
测试两档全绿 + 报告四块齐全 = 这条线程结束。

# 任务编写规范（加一道题之前先读）

> **这份文档管什么**：在 `tasks/` 里加一道题、改一道题时，必须遵守的结构约束、必须同步的硬编码、
> 以及"这道题合格了吗"的机器判据。
> **它不管什么**：具体题目的教学内容、评分口径与权重数值——那些在 `docs/data-model.md`、
> `docs/review-packet-manual.md` §3、`docs/executor-semantics.md`。
> **本文件是索引**：每条约束都指向一条已有测试，**判据在测试里，不在这份文档里**。
> 文档与测试冲突时以测试为准，并把冲突报出来（2026-11-21 首次成文，来源：扩题前置）。
> **要并行开多条线程加题**（而不是一个人串行写）时，先读 `docs/parallel-authoring-brief.md`：
> 那里有 worktree 隔离、四条线程卡、合并时谁改计数行。本文档仍是单题写法的唯一判据。

**一句话**：加一道题＝**在已有骨架上换情景变量**；换判分结构是另一件事，要新建骨架，且要人拍。

**最快路径（2026-11-22 起）**——一条命令生成、三秒自检，不用先读完整份文档：

```bash
python scripts/new_task.py --list                       # 看各家族自己的难度梯度，挑缺的那一档
python scripts/new_task.py --new --family retry-drill \
    --id ret-008-xxx --title "……" --rows 28 --page 10 --write
python scripts/new_task.py --check ret-008-xxx          # 只查这一道，实测 0.6 秒
```

生成器会把 `expected` 按 `derivation` **现算好**（第 5 步那条纪律由代码代劳），
自动登记台账并提示还差什么。产出的题默认就能过 `--check` 与 pytest；
要人动手的只剩 `title` 与 `scenario` 的措辞——那是教学设计，脚本不该替你想。
下面七步仍是**判据来源**：工具报的每条错都对应这里的一条约束与 `tests/` 里的一条用例。

---

## 0. 三种"加题"方式（代价从低到高）

| 方式 | 改什么 | 学习者的体验变化 | 代价 | 谁决定 |
|---|---|---|---|---|
| **A. 新变体**（最常用） | 一个 YAML，`base_task` 继承母本，只写差异 | 情景、数据、故障位、参数上限、难度变 | 半小时级 | 我 |
| **B. 新家族** | 新变体 + 在 `tests/test_task_variants.py` 的 `FAMILY_PRIMARY` / `FAMILY_TEMPLATE` 登记 | 主技能变了，但"分数怎么算"没变 | 半天级 | 我（改了要说明） |
| **C. 新骨架** | 新母本 YAML + `task-families.yaml` 一行 + 一套新断言 id + 新参考 Agent 流程（可能要新 mock 形态，如第三套的 `source: static` 固定应答） | **"分数怎么算"变了** | 天级，且牵动参考 Agent / mock / 错误模式；门槛见 §6 | **人拍** |

选错方式的典型症状：想做 C 的效果，却用 A 硬凑——结果 28 道题共享 5 条断言，
学习者第一次和第四十次看到的判分维度一模一样（这个张力的处理见 §6）。

---

## 1. 加一道变体：七步清单

以 `tasks/dbg-002-invoice-report.yaml` 为范本（66 行，是最干净的一份变体：只写差异，不碰骨架）。

**第 0 步（可选但推荐）：跑生成器。** `python scripts/new_task.py --new ...` 会按下面七步里
的硬约束产出骨架，并把期望值算好。手写也行，但手写必须自己跑 `--check`——它会把下面
每一条都过一遍，比等全量测试报错快两个数量级。

**第 1 步：选家族与母本。** 家族决定主技能与骨架，母本决定继承谁：

| 家族 | 主技能 | 骨架 | 母本 |
|---|---|---|---|
| `order-report` | `multi_step_planning` | `tpl-tool-debug` | `dbg-001-order-report` |
| `retry-drill` | `failure_retry` | `tpl-tool-debug` | 同上 |
| `param-surface` | `tool_param_validation` | `tpl-tool-debug` | 同上 |
| `locate-drill` | `multi_step_planning` | `tpl-bug-locate` | `fix-001-token-expiry` |
| `handover-drill` | `multi_step_planning` | `tpl-ask-first` | `ask-001-report-handover` |

（这张表是 `docs/task-families.yaml` 的人读版，改那里即可；手工抄错会被测试比对出来。）

**不需要在任何清单里登记任务**：任务库靠 `tasks/*.yaml` 通配发现（`TASK_FILES = sorted(TASKS_DIR.glob("*.yaml"))`），
放进去就会被所有测试遍历到。唯一要手改的清单是 §3 那四张映射表/常量。

**第 2 步：写头部。** 必须写全 `base_task` / `task_id`（不能与母本撞）/ `task_version` /
`title` / `primary_skill_id` / `secondary_skill_ids` / `variant_template_id` / `variant_meta`（三键）。
`task_version` 的规则是硬的：**改断言、改期望值、改工具 schema 必须抬版本号**，
否则旧提交会被当成本版判分（TD-038）。

**第 3 步：改 `scenario`。** 题面是给学习者看的，两条硬要求（前者有测试，后者没有）：

- 若本题的参数有上限，**题面必须明说超限会报什么错**（不许诱导学习者"合理试探"后踩 R4）；
  —— 有测试：`test_scenario_states_limit_error`。
- 若本题要翻页，题面里的**行数与真实上限必须与 `test_inputs` / `tools` 一致**；
  —— 有测试：`test_scenario_mentions_the_real_ceiling_and_row_count`。
- **不许写"应该说……"式的提示**——题面只描述工作，不给解法。
  —— **没有测试**，靠人审；这条与「复述页不判分、提示只给步骤」是同一套口径，
  违反的症状是：学习者照着题面就能抄出流程，R3/R4 于是测不到真本事。

**第 4 步：改 `test_inputs`（数据）。** 每个被剔除的行都要逐行注释原因（见 dbg-002 的 20 行）。

**第 5 步：写 `expected`——先跑再写。** 这条是纪律不是建议：期望值**必须先跑出来**，
不许凭印象预填。仓库里已经因此写错过两次（`BACKLOG` §61.7 / §63.7）。
`test_inputs.expected` 与 `rubric_credit[R2].expected` 是同一件事的两处，必须相等。

**第 6 步：若动了故障位，改两处。** 两处都有测试拦：

1. `fault_injection.match` 与 `rubric_credit[R3].expected.calls[0].params_match`——必须相等，
   键名是 **`offset_covers`**（页区间覆盖）不是 `offset`（精确相等），见下面一段；
2. `fault_injection.failures[0].http_status` 与 `calls[0].http_status`——必须相等。

`error_pattern_detection.cases` **不再需要按页大小重算**。B-31（2026-11-22）把分类规则
统一换成相对谓词（`fault_point_not_reached` / `fault_then_moved_on` / `fault_point_hit_once`
/ `last_status`），变体直接继承母本的 cases 即可。上一版规范（2026-11-21）要求重算
`offsets_equals: [0,5,10,15,20]` 这类绝对序列——那是 `offset` 精确匹配时代的约束，
现在这些序列**全部删掉了**，因为它们只有一种页大小才成立。

**R3 的形状是写死的**（`test_fault_declaration_matches_retry_expectation`）：
`expected.calls` **恰好两步**（失败 → 成功），且失败那步 `status` 只能是 `error`，
`params_match` 用 `offset_covers`（见 §1 第 6 步）。
所以三种想法在现有骨架里**表达不出来**，别试：①连续失败两次；②用「超时」当故障
（`status` 枚举里虽有 `timeout`，但 R3 期望只接受 `error`）；③让第 2 次调用才失败
（`failures[].attempt: 2` 会让同一 offset 先成功一次再失败，两步期望对不上）。
要这些效果得新建骨架（方式 C，需人拍）。

**第 7 步：跑验收（§2）并同步 §3 的硬编码。**

---

## 2. 验收命令（快 → 慢，四档都要过）

```bash
# ⓪ 只查这一道题（2026-11-22 新增）：实测 0.6 秒/题、27 题全库 2.2 秒，逐条列出还差什么
python scripts/new_task.py --check tasks/新题.yaml

# ① 任务层的快门槛：258 条，实测 65 秒（2026-11-24；25 题时是 141 条 / 27 秒）
PYTHONPATH=src python -m pytest tests/test_task_yaml.py tests/test_task_variants.py \
  tests/test_task_executability.py -q

# ② 只看新题能不能真跑通、能不能判对错（最有用的一条，参数化到每个任务）
PYTHONPATH=src python -m pytest tests/test_task_variants.py -q -k "full_marks or own_failure"

# ③ 全量（收工必跑）：并行 4 分钟 / 串行 12.6 分钟（约 1912 用例）
PYTHONPATH=src python -m pytest tests -q -n 4 --dist loadfile   # 日常
PYTHONPATH=src python -m pytest tests -q                        # 交付证据（更保守）
```

第 ② 档解释：它跑两遍参考 Agent——一遍走到满分（`ratio == 1.0` 且 `skill_gaps == []`），
一遍故意放弃重试（必须判 `R3_retry_evidence == fail` **且错误模式是 `no_retry`**）。
**只看分数不够**：错偏移的变体照样 0 分，但模式可能落到兜底的 `retry_wrong_params`，
分数对、诊断错，复习层就会推错题。

---

## 3. 加题时要登记的两处（2026-11-22 起只剩两处，都是改 YAML）

| 位置 | 内容 | 什么时候改 |
|---|---|---|
| `docs/task-authoring-log.yaml` | 一行 `- id: <task_id>` + `why:`（说清这题变的是什么） | **每加一道题** |
| `docs/task-families.yaml` | 家族 → 主技能 / 骨架 / 母本 / **id 前缀** | 加**家族**时 |

`--new` 会自动往台账追加一行占位，`why` 由人来写。

**为什么闸门从"改数字"挪到"写 why"。** 原来这里有三处 Python 常量
（`len(all_tasks) == 20`、`checked == 16`、`test_lab.py` 的三处 `== 20`），注释写着
「故意写死，逼你停下来确认这题真的独立」。但这件事逼不住：把 20 改成 21 不需要任何判断，
而且这个数字本身没有信息量（机器能自己数）。现在改成：**数量由台账行数决定，判断由
`why` 那一句话负责**。写 why 必须先想清"它和同族其它题差在哪"，而这句话会被 review。

校验在 `tests/test_task_authoring.py`（台账 ↔ 磁盘双向一致、why 非空非占位、家族表自洽）。
数量那条守卫也接了过来：台账里同一 id 登记两次会红（并行加题时两行都写进合并结果）。

**加骨架（方式 C）仍要改 Python**：`MOTHER_OF` 由家族表推导，所以新骨架＝家族表里新增一个
`template` + `mother` 组合，同时要处理 `tests/test_task_variants.py` 里与骨架相关的断言比对
（那一层没法用 YAML 表达）。这仍是"要人拍"的动作。

---

## 4. 陷阱表（现象 → 原因 → 拦它的测试）

| 现象 | 原因 | 测试 |
|---|---|---|
| 分数全库不可比 | 某个变体偷偷改了 R2 的权重/类型/技能绑定 | `test_skeleton_is_identical_within_each_template` |
| 题面说"上限 5"，工具却是 10 | 两处各写一份，只改了一处 | `test_scenario_mentions_the_real_ceiling_and_row_count` |
| 标了难度 3 但只需翻 1 页 | `difficulty` 变成装饰字段 | `test_difficulty_is_not_decorative` |
| 0 分但诊断说"重试用错参数" | 故障位改了、R3 期望没跟上 | `test_fault_declaration_matches_retry_expectation` |
| 引用了词表里不存在的错误模式 | 词表先行没遵守 | `test_error_patterns_registered_in_vocabulary` |
| 两个家族都声明同一主技能顺序，`seen` 对不上 | 家族登记漏了 | `test_family_maps_to_declared_primary_skill_and_template` |
| 变体把骨架整段抄了一遍 | 继承没发生（改母本时这题不跟着变） | `test_every_variant_declares_its_parent_and_does_not_restate_skeleton` |
| 学习者用 `limit=3` 正确重试却被判「没重试」 | 写了 `offset: N`（精确相等）而不是 `offset_covers: N`（页区间覆盖） | `test_b31_fault_triggers_for_any_legal_page_size` / `test_b31_any_legal_page_size_can_pass_r3` |
| 以为新题「没有故障」，其实继承了母本第 10 条 | `fault_injection` 是**继承字段**：不写＝照抄母本，不等于关掉 | `new_task.py --check` 的 `[!]` 提示（2026-11-22 记：台账一度按"无故障"写了两道题的 why） |
| 把非任务文件放进 `tasks/` | 任务库靠 `tasks/*.yaml` 通配发现，任何 YAML 都会被当题解析 | 这里没有测试，`--check` 也不拦；约定是非任务文件放 `docs/` |

---

## 5. 机器锁得住 / 锁不住（诚实清单）

**锁得住**（都有测试，见 §4 与 §2）：
骨架一致、家族↔技能↔骨架映射、难度单调、期望值与数据一致、故障位两处相等、
参考 Agent 每题满分、每题的放弃重试都被判成 `no_retry`、词表引用、依赖无环、
计分与执行口径全库一致、三套骨架的 credit 断言 id 真的不同（R2/R4 共用，R3 与 R6 分属不同能力）。

2026-11-22 补的三条（B-30）：**台账 ↔ 磁盘双向一致**（加了题不登记 = 红，登记了但文件没了 = 红）、
**why 有实义**（非空、≥12 字、不是「同上/略/TODO」）、**家族表自洽**（母本存在且骨架相符、
一个骨架只挂一个母本、主技能在词表里）。

**锁不住**（不要假装它被锁住了）：

1. **"这道题是不是只换了业务名"**——没有任何机器判据。`dbg-002` 把"订单"换成"发票"、
   改了故障码 500→503、数据全换，机器全绿；它到底算不算"新题"，只能人看。
2. ~~**区分度**——同一道题五份不同质量的提交能不能分出分~~ **已锁住（2026-11-24）**：
   `tests/test_discrimination.py` 让 27 道题各跑 5–6 种形态，四条判据——
   上界可达、下界可达、**每种缺陷必掉分**、**任意两形态的 (分数, 失败断言, 错误模式) 三元组互不相同**。
   分数实测只分四档（1.00 / 0.75 / 0.50 / 0.00），0.00 内部靠状态与错误模式分开：
   `ask_late` 与 `extra_param` 同为 0.75，但一个掉 R6、一个掉 R4，诊断指向不同复习内容——
   **只看分数不够，这正是本条要验的东西**。
   另有一条骨架级判据：同骨架内「形态 → 签名」必须逐题相同（它抓的是 YAML 一样、跑起来不一样，
   比如某题故障位被别的分支遮住，`no_retry` 分数照掉而诊断落到别处）。
   **不再走「每题手写 5 份 JSON」**：`fixtures/raw-*.json` 是 2026-11-08 的产物，那时还没有
   `reference_agent` 与 `mock_tools`，只能手写一份"看起来像"的提交，而手写会漂——
   `raw-empty` 的 `artifacts` 其实是**空对象**（它是「0 次调用的地板哨兵」，不是「交了空文件」），
   于是 `empty_output_file` 这条模式在手写夹具里**零覆盖**。现在 trace 由参考 Agent 真跑，
   产物缺失由 `assemble_raw` 从任意 trace 组装、与题目内容无关，所以每题零成本。
3. **"学生要做的事"是否真的变了**——目前只能靠 §7 的清单人判。
4. **题面有没有把解法写进去**——没有测试。`scenario` 里写"先看 A 再看 B"照样全绿，
   但那会把 R3/R4 变成照抄。只能人审（判据：把题面独立发给一个没看过代码的人，
   他能不能只靠题面写出正确流程；能写出，就是题面泄漏了解法）。
5. **故障一定被踩到**——**已修（B-31，2026-11-22）**，但判据要记住：故障点写
   `offset_covers: N`（这次请求的页区间含第 N 条），不是 `offset: N`（精确相等）。
   写后者时学习者用 `limit=3` 就永远踩不到 N，数据拉全了却判 `fail + no_retry`。
   回归测试是 `test_b31_fault_triggers_for_any_legal_page_size`（防 6 种页大小都覆盖到）
   与 `test_b31_any_legal_page_size_can_pass_r3`（判分侧）。
   写 `offset` 的残留处只剩 `fix-*` 两题的 `path` 精确匹配，那是另一回事（同一文件只读一次）。

---

## 6. 骨架张力的现状（2026-11-23 更新：第三套已投产）

历史：骨架曾只有 2 套，而"变体不许改断言结构"是硬约束（`SKELETON_FIELDS` 逐字段相等），
所以全走 A（新变体）的话，学习者第 1 次和第 40 次看到的判分维度完全一样——只有情景与数据在变。
当初登记了三个选项（多家族同骨架 / 新建第 3-4 套 / 混合），并把决定推到"到 20 题再说"。

**现在（27 题）：走了 B，建了第三套 `tpl-ask-first`，并已扩到 3 道。** 它带来的是**新维度**而不是新场景：

| 骨架 | 考的是 | credit 断言 |
|---|---|---|
| `tpl-tool-debug` | 拉全 + 剔除 + 汇总 | R2 结果 / R3 重试 / R4 参数 |
| `tpl-bug-locate` | 读日志 + 读源码 → 根因与行号 | R2 结论 / R3 重试 / R4 参数 |
| `tpl-ask-first` | **信息不全时先确认再动手** | R2 结果 / **R6 确认顺序** / R4 参数 |

第四套骨架的门槛（写在这里，免得下次又从零讨论）：

1. **必须是新维度**，不是"同样的三条断言换个场景"——否则它只是又一个变体，白付天级成本；
2. **要能说清"哪条形态只打掉它"**（现有每套都有 correct / extra_param / 骨架专属形态，
   每条形态必须只落在对应断言上）。说不清就是维度不独立，会污染归因；
3. **优先复用现有 5 种断言类型**。第三套唯一的新能力是 `trace_sequence` 的**跨工具序列**
   （原实现只支持同参重试），那是在执行器里加一个匹配器，不是加一种断言类型。
   若新题型必须新增断言**类型**，成本再上一档，要单独评估（它会让"分数怎么算"多一套语义）。

---

## 7. 未决（登记，别擅自定）

- **"一道新题"能不能写成机器判据？** 两个候选都没过关：
  ① *scenario 的动词序列不同*——反例：`dbg-002` 与母本动词序列完全相同（拉取/剔除/统计/写入），
  但数据与故障都换了；② *`test_inputs` 与母本无交集*——反例：同家族共用同一批行语义（只是值不同）。
  目前只有人判清单：**要处理的东西数量变了吗？故障/异常的种类变了吗？决策点变了吗？**
  三个都"没变" ⇒ 只是改名。
- **一份题算"验过区分度"，需要几份什么质量的提交？** 本轮给了口径（**待人认**）：
  **每个计分断言维度至少要有一个「只打掉它」的形态**，外加两种缺席（没交产物 / 交了空文件）必须分开。
  实测三套骨架所需形态数不同：汇报族 5 种（满分 / 答错 / 参数瞎填 / 无产物 / 空文件）、
  定位族 6 种（多一条"定位到错行"）、交接族 6 种（多一条"问晚了"）。
  「无过程」与「放弃重试」两种不由本文件覆盖——前者在矩阵里（`no_trace`），
  后者仍在 `test_each_task_classifies_its_own_failure`，因为它验的是**归因**而不只是分数。
  **"部分正确"已补上（2026-11-24，B-41）**：不需要给参考 Agent 加缺陷——
  R2 是 `json_field_equals`，产物是一份 JSON，改其中一个键即可，纯计算、不起服务。
  它**不进**形态矩阵，单独一条判据：只错一个字段时失败断言只能有 R2、错误模式只能有一个、
  且不同字段必须报不同模式（`test_partial_answers_are_diagnosed_field_by_field`）。
  不进矩阵的理由值得记：定位族的 `wrong_line` 本身只错一个字段（行号），
  它的签名必然与「改行号」那个 partial 相同——同一个缺陷的两种造法，不是区分度缺口；
  一起比会要求它们必须不同，等于测错东西。
- **哪些内容可以交给 AI 批量写？** 我的判断（需人认）：
  `test_inputs` 与 `expected` 可由脚本生成（因为有 `derivation` 可执行 + `expected` 必须现算），
  `scenario` 与"要做什么"必须人审——那是教学设计，不是数据填充。
- **计划书里的任务数上限**：`ROADMAP` 记录「13 个 > 计划书的『≤10』（待人确认措辞）」。
  扩到 40 之前，这句措辞必须先定，否则对外材料与仓库不一致（本仓库 §3.4「数字不许说过头」）。

---

## 8. 附录：`dbg-001` 字段注解（改之前先知道谁在读）

| 字段 | 谁在读 | 改了会怎样 |
|---|---|---|
| `task_version` | `diagnosis_report._task_version_mismatch` | 与提交里的版本不符 ⇒ 整份 `voided`（不计分、不计掌握度） |
| `submission_requirements.requires_process` | 过程门槛 | 设 true 时缺过程 ⇒ 不计能力分、不记缺点、记中性事实 |
| `variant_template_id` | `reference_agent` 分派、`test_task_variants` 分组 | 跨骨架继承会报错 |
| `variant_meta.family_id / difficulty / kind` | 复习层选变体 | 家族未登记 → 红；难度退化成装饰 → 红 |
| `fault_injection.*` | `mock_tools` 的故障注入 | 与 R3 期望不一致 → 红（静默漂移最贵的一种） |
| `execution.*` | 执行边界 | 平台重试改成非 0 ⇒ 重试能力测不到（R3 失效） |
| `tools[].mock.pagination.page_size_max` | mock 分页切片 | 与题面写的上限不一致 → 红 |
| `derivation:` | `derivation.py`（哪些行有效、怎么聚合） | 声明了就必须与 `expected` 相等；`fix-*` 不声明（答案不是推导值） |
| `rubric_gate` / `rubric_credit` | 判分 | **变体不许改**；改母本 ⇒ 同骨架所有题一起变 |

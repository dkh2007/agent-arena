# Agent Arena

Agent 开发学习者的自适应诊断与训练闭环（第二十届全国大学生软件创新大赛参赛项目，AI+学习方向）。

> 匿名纪律：本仓库及所有提交记录不得出现真实姓名、学校名称。协作者一律使用代号（学生A / 学生B / 评审）。

## 结构

```
docs/       数据模型、spike 结论、任务规范
tasks/      任务 YAML 定义（技能节点 + 原子 rubric + 测试输入）
src/arena/  薄服务层（评测 / 状态 / 复习 / 排程，按步逐个加入）
tests/      pytest 用例（每步验证沉淀于此）
workbench/  Streamlit 内部工作台（不进交付物）
```

## 环境

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows
pip install -r requirements.txt
copy .env.example .env        # 填入 API key，勿提交 .env
```

## 开发流程

**给 AI Agent 的开工上下文先读 `docs/agent-guide.md`**（硬口径、已否决的外部建议、已知坑、当前该干什么）。

见 `docs/COLLABORATION.md`（协作规范 v2）：小步迭代，一步一验，main 受保护，feat/* 短命分支 + PR。
两人协作的分支/提交/合并/验收规则、文件所有权表、AI 代码质量清单都在那里；开 PR 时模板会自动带出验收清单。
交接说明见 `HANDOFF.md`，润色范围见 `docs/peer-brief-mvp-polish.md`。

> **与现实不符的一条（已登记，待人拍）**：上面"main 受保护、只经 PR"是两人协作期的约定；
> 单人维护期实际是直推 main（截至 2026-11-21：148 提交、零合并提交、直推成功 ⇒ 无分支保护）。
> 待拍期间按现状直推，但闸门必须绿（`PUSH_GATE=CLEAN` + 全量测试绿）。详见 `docs/BACKLOG.md` B-29。

## 测试证据（评委/评审可直接复核）

「测试全绿」这句话本身不是证据。每次全量运行都会留一份档案：

```bash
python scripts/test_evidence.py      # 跑一遍 pytest，把结果写成档案
```

产物在 `docs/test-evidence/`：

- `INDEX.md`——一行一次运行：时间、commit、通过/总数、失败数、耗时，点进去是当次全文；
- `<日期>-<sha7>.md`——结论 + 运行身份（commit / 分支 / 工作区是否干净 / Python / pytest / 依赖 pin）
  + 自校验类与变异类测试计数 + **逐文件用例数** + 失败用例（若有）+ 复现命令；
- `<日期>-<sha7>.json`——同一份数据的机器可读版。

档案由脚本生成、不手改；工作区有未提交改动时会显式标注「对应工作区状态而非干净提交树」。
判定「干净」时排除 `docs/test-evidence/` 自身——否则每份档案都会因为「要写这份档案」而自称不干净。
最近一次干净树运行：`1607 passed / 1607 total`，被测提交 `772b09a`（见 `INDEX.md`）。

## 文案总表

页面上每一句用户能看到的字都收在 `docs/copy-inventory.md`（153 条，带 `文件:行` 定位）。
表由脚本从源码生成、**不许手改**——手改会让表和代码脱钩，变成错的第二真相源。

```bash
python scripts/copy_inventory.py          # 改过文案后重生成
python scripts/copy_inventory.py --check  # 只校验；不同步时退出 1
```

`pytest tests/test_copy_inventory.py -q` 里还有两条额外的锁：一是**提取器不许静默空转**
（扫到的条数少于阈值就红，否则空表对空文件也会「一致」），二是**页面不许出现评价性用词**
（「不错」「加油」「满分」这类，那是「复述不判分」这条对外承诺的机器判据）。

两点实话，免得复核时误会：

- 全量跑一次约 10 分钟（605 秒），不是秒级命令；
- 跑完可能看到一段 `atexit cleanup_numbered_dir` 的 traceback——那是 pytest 收尾时删自己的临时目录，
  被本机沙箱的批量删除保护拦下了。它发生在**测试结束之后**，不影响用例结果；档案里的数字取自 junit。
- 只改了档案模板、不想再等十分钟时：`python scripts/test_evidence.py --rerender docs/test-evidence/<档案>.json`，
  按同一份运行数据重排 `.md`（不重算、不追加索引行）。


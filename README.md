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

见 `docs/COLLABORATION.md`（协作规范 v2）：小步迭代，一步一验，main 受保护，feat/* 短命分支 + PR。
两人协作的分支/提交/合并/验收规则、文件所有权表、AI 代码质量清单都在那里；开 PR 时模板会自动带出验收清单。
交接说明见 `HANDOFF.md`，润色范围见 `docs/peer-brief-mvp-polish.md`。

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


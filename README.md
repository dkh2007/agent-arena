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

见《协作规范 v1》：小步迭代，一步一验，main 受保护，feat/* 短命分支 + PR。

# 变异测试脚本（作者侧证据，可复核）

这里的每个脚本干同一件事：**故意把源码改坏一处**，跑指定的测试节点，看测试有没有变红。
红的叫 `DETECTED`（测试真的在管这事），不红叫 `MISSED`（测试没管），
脚本自己写错锚点叫 `INVALID`（看起来抓住了，其实没测）。

## 为什么放在仓库里而不是 `.git/`

这些脚本原本躺在 `.git/` 下（那里 git 不跟踪，等于**只有作者本机能跑**）。
既然 `docs/review-response-v1.md` 把它们当作「十分钟可复核路径」引用，就得能被第三方拿到，
所以 2026-11-15 挪到这里。

`assemble_variants.py` / `gen_fixtures.py` 是纯一次性的装配/生成工具（依赖 `.git/gen_out.txt`
这种中间文件，本身不入库），仍留在 `.git/`。

## 跑法

脚本自己会往上找到仓根（按 `src/arena` 是否存在判断），所以**在哪个目录调用都一样**：

```bash
python tools/mutations/mutate_locate.py      # 相对路径也行
python /abs/path/to/agent-arena/tools/mutations/mutate_locate.py   # 绝对路径也行
```

> 本文件刻意不写作者的机器绝对路径：仓库里一旦出现「盘符 + 用户名」那种路径，
> `scripts/push_gate.py` 的 `ABS_PATH` 规则就会拦下（那是匿名要求的一部分）。
> 你本机要完整路径时，把仓根换成你自己的路径即可。

判定：末尾一行是「全 DETECTED（N 条）」且退出码 0；出现 `MISSED` / `INVALID` 就是有洞。

## 每个脚本对应哪一轮的哪条结论

| 脚本 | 覆盖 | 出处 |
|---|---|---|
| `mutate_b04.py` | 工具客户端（超时/重试/参数校验） | round 早 |
| `mutate_client.py` | 同上，补充分支 | round 早 |
| `mutate_judge.py` | 裁判口径（四态、门禁） | round 早 |
| `mutate_mastery.py` | 掌握度 EMA 与证据收敛 | round-80 |
| `mutate_scheduler.py` | FSRS 调度与 ReviewItem 生成 | round-84 / B-08 |
| `mutate_advisor.py` | 四因子排程（缺数据均值顶、平手格、改序审计） | round-85 / B-09 |
| `mutate_locate.py` | 第二套骨架（定位题）与按路径回放 | round-86 / B-05b |
| `mutate_decay.py` | P 的衰减（缺卡不衰减、锚点取更晚、时间不倒流、R 只算一处） | round-87 / B-08b |

## 两条踩过的坑（写进脚本注释了，这里再记一次）

1. **每条变异必须换一个新的 `PYTEST_DEBUG_TEMPROOT`。** 复用同一个目录时，pytest 开场会清理
   旧编号目录，沙箱的 bulk-delete 守卫拦掉删除 ⇒ 整场 INTERNALERROR，输出里既没有 `failed`
   也没有断言信息，看起来像「变异无效」。round-84 和 round-85 各栽过一次。
2. **锚点打错地方会得到 `MISSED`，别急着加测试。** round-86 的 M1 打在只进 evidence、不参与
   判定的代码上，端到端用例当然抓不住——该换成打单元测试。

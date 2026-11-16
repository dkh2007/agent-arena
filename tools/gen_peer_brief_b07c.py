"""生成 B-07c 外部审核委托包（自包含、逐字内联、可自检）。

    python tools/gen_peer_brief_b07c.py            # 重新生成（历史快照）
    python tools/gen_peer_brief_b07c.py --check    # 自检：包内块 vs 锚点提交 + 匿名
    python tools/gen_peer_brief_b07c.py --self-test

**round-88 回件已收到并据此改了源码**，所以本包冻结为 `ANCHOR` 那个提交的快照：
内联块从 `git show ANCHOR:<file>` 读，不再从当前工作区读。
否则源码一改 `--check` 就永久假红，而回件引用的行号/源码又只对得上旧版本。

自检仍非恒真：`--self-test` 证明改一个块内字节、插一条本机路径都会报红。
匿名规则复用 scripts/push_gate.py，不另立词表。
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import push_gate  # noqa: E402

OUT = ROOT / "docs" / "peer-brief-b07c.md"
# 委托包外发时的仓库 HEAD；回件 review-round88-b07c.md 核的就是这份内容。
ANCHOR = "7e614fe756d526028cb38a95b35a0def6df817e7"

FILES = (
    ("src/arena/mastery_snapshot.py", "本轮新增：快照的算、写、校验、排序"),
    ("src/arena/store.py", "v2 表结构与迁移、提交写路径"),
    ("src/arena/mastery.py", "P 的权威公式与重放（本轮未改算法）"),
    ("tests/test_mastery_snapshot.py", "本轮新增的 27 条测试"),
    ("scripts/independent_audit.py", "独立对拍器（裁判本身也要被审）"),
)

HEAD = """<!-- status=SUPERSEDED anchor=7e614fe756d526028cb38a95b35a0def6df817e7 generated-by=tools/gen_peer_brief_b07c.py -->
# 外部审核委托包 —— B-07c 掌握度快照（round-88）

> **本包已冻结为历史快照**（对应提交 `7e614fe`）。回件 `review-round88-b07c.md`
> 已收到并逐条核证，见 `docs/review-response-v1.md` §88；部分源码已按回件修改，
> 所以 §3 的内联块**不再代表当前磁盘**。`--check` 改为与本包生成时那个提交逐字比对
> （`git show ANCHOR:<file>`），既能防包被悄悄改，又不会因后续修复永久假红。
> 若要复审当前源码，另起一个新版本委托包，不要改这一份。

## 0 任务书（请按这里交付）

1. 你只拿到这一份文件，**看不到仓库，也不需要仓库**。所有需要的源码在 §3 逐字内联。
2. 目标不是给总体评价，而是找出**可能存在的 BUG 与契约漏洞**。请按 §4 的 T1–T9 逐条判定。
3. 每条结论必须标 **【事实】**（能指到 §3 的文件名、函数名或具体表达式）或
   **【推测】**（需要更多信息才能定），两者不许混写。
4. 每条判定都要给一个**十分钟内能做的验证**：输入 → 期望 → 实际 → 什么算通过。
   给不出的写入【假设】，不要写成结论。
5. **不要求读代码库、不要求联网、不要引用本包里没出现的版本号 / 条款号 / API 名。**
   本包不提供任何真实姓名、学校、机器路径或凭据，也请不要索要。
6. 已拍板的产品口径不翻案；但「按此口径落地会留下什么洞」属于审核范围。
7. 交付格式（硬性）：① 核证总表（编号 / 判定 / 依据 / 事实或推测）；② 每条的最小复现；
   ③ 待拍板项写成两个选项各自代价，**不替人拍板**；④ 不写「这样应该没问题」。

## 1 现状（中性陈述，全部可在 §3 复核）

- 项目是「Agent 开发学习领域的自适应诊断与训练闭环」：对学习者的 Agent 执行轨迹做诊断，
  把错误模式映射到技能缺口，按遗忘规律调度任务变体，并生成训练建议排期。
- 本轮（B-07c）新增第八张表 `mastery_snapshots`，每个 (用户, 技能) 一行，
  存 `mastery_p` / `lam` / `p0` / `evidence_count` / `last_submission_id` / `updated_at`；
  `PRAGMA user_version` 升到 2，配一个增量迁移函数。
- 代码自述的契约：`build_series()` 从提交历史重放出的 P 是**唯一权威**，
  表里只是缓存；校核时现场重放、不一致以重放为准；只存未衰减的 `P_raw`；
  没有证据的技能不写行（不写 0）；写路径在提交落库后自动刷新，读路径只读缓存。
- 边界（代码自述）：排名功能未开、平滑系数与初值未经数据标定、衰减曲线借自 FSRS 且未标定。

## 2 请优先审这四个「我自己判过」的地方

上一轮全部代码、测试、变异、文档由**同一个作者（一个 AI）**写出，本地绿灯只能证明
「实现符合它自己写的测试」。下面四处是作者对自己的判断，**本地裁判覆盖不到**：

| # | 作者自己的判断 | 请你核什么 |
|---|---|---|
| A | 「新建第八张表虽与数据模型文档里『不另设掌握度表』的原则字面冲突，但没违背精神」 | 这个自我豁免成立吗？有没有更便宜的写法？有没有被绕开的约束？ |
| B | 「开工条件被提前解除，但排名功能仍未开、开榜前必须先改口径」 | 本轮代码有没有悄悄产出名次、榜单或跨用户比较口径？ |
| C | 「27 条测试 + 18 组变异全绿，所以实现可信」 | 有没有测试覆盖不到的真实故障形态？哪条测试可能是恒真断言？ |
| D | 「快照只存 P_raw，衰减是读法不是状态」 | 有没有任何路径把某个时间点钉进库里？ |

## 3 逐字内联源码

> 每块在锚点提交里的 sha256 见 §6；`python tools/gen_peer_brief_b07c.py --check`
> 会把包内块抽出来与 `git show ANCHOR:<file>` 重新比对。**块内不改动一个字节**，包括注释。
"""

TAIL_HEAD = """
## 4 可判定命题（逐条给判定 + 最小复现）

| # | 命题 | 什么算通过 |
|---|---|---|
| T1 | `verify_all()` 能发现「库里有行、重放却无证据」的孤儿行 | 说出它扫的用户×技能集合从哪来；举一个能绕过该集合的构造，或证明绕不过 |
| T2 | `refresh_one()` 在重放无证据时删旧行 | 找出一个「该删」与「不该删」的边界输入；判断是否误删合法数据 |
| T3 | `migrate()` 从真 v1 库升级是增量、幂等、不降级、旧报告不变 | 指出任一失败步骤后库是否仍可用；指出 `init_db` 与 `migrate` 的调用顺序有无半迁移窗口 |
| T4 | `evidence_count` / `last_submission_id` / `lam` 与重放序列末步一致 | 构造「P 值碰巧相同但参数漂移」的输入；看校核是否报不一致 |
| T5 | `list_ranked()` 只给有序行、不含名次字段、并列时顺序稳定 | 从代码指出稳定的依据；举一个会让它不稳定的输入 |
| T6 | 提交落库与快照刷新之间没有「报告落了、缓存没刷」的半状态 | 指出两者是否在同一个事务里；举一个中途失败的具体位置 |
| T7 | `verify_*` / `compute_row` 纯读，不写库 | 找出任何写副作用；找不到就给出你据以判定的代码位置 |
| T8 | 27 条测试里没有恒真断言 | 至少指出 1 条最可疑的测试名，给出「删掉哪个行为它仍会绿」的变异方式；或证明全部有效 |
| T9 | 文档自述与代码一致 | 在 §3 里找出一处与 §1 现状描述不符的代码，或确认无不符 |

**T8 是本次审核的重点**：作者自己写的测试可能只测了它实现过的分支。

## 5 交付格式（硬性）

```
核证总表：T1 … T9 各一行（判定 / 依据（文件:函数名或表达式）/ 事实 or 推测）
最小复现：每条一段，写清输入与期望；不能实跑的写「需维护者提供什么」
待拍板项：若有，写两个选项各自的代价，不替人拍板
```

不要写「整体质量不错」这类总体评价；不要用本包未提供的信息补全结论。

## 6 指纹表与自检

| 文件 | 行数 | sha256（前 16） |
|---|---|---|
"""

TAIL_FOOT = """
自检命令（相对仓库根，**本包不写任何机器绝对路径**）：

```
python tools/gen_peer_brief_b07c.py --check
```

输出应含 `BLOCKS_MATCH=True` 与 `ANON=CLEAN`（比对对象是锚点提交，不是当前工作区）。

## 7 边界

- 本包**已由维护者转发给外部 AI**，回件 `review-round88-b07c.md` 已收到并登记在
  `docs/review-response-v1.md` §88。本文件从此不再更新，保留为当时那份证据；
  要复审当前源码另起新版本包，不回改这一份。
- 若维护者愿意另外开放仓库只读权限，路径清单为 §6 表中五个文件加
  `docs/data-model.md` / `docs/BACKLOG.md` / `docs/review-response-v1.md` §87；
  均为仓库相对路径。不给整个仓库目录、不给内部处置记录、不给已过期评审包。
- 回件按「核证 → 登记 → 处置 → 落仓 → 人拍板」处理：**外部 AI 的建议不当证据**，
  每条仍由本地实测复核。
"""

BLOCK_RE = re.compile(
    r"<!-- BEGIN_FILE (\S+) -->\n```python\n(.*?)\n```\n<!-- END_FILE \1 -->",
    re.S,
)


def _blocks() -> list[tuple[str, str, int, str]]:
    """从**锚点提交**读五个文件，不碰当前工作区。

    `git show` 返回 LF（与 `read_text` 的 universal-newline 结果一致），
    所以包内块、指纹表、`--check` 三处用的是同一份文本。
    """
    out = []
    for rel, _ in FILES:
        p = subprocess.run(["git", "show", f"{ANCHOR}:{rel}"], cwd=ROOT,
                           capture_output=True, check=True)
        text = p.stdout.decode("utf-8").replace("\r\n", "\n")
        out.append((rel, text, len(text.splitlines()),
                    hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]))
    return out


def _body() -> str:
    blocks = _blocks()
    by_rel = {rel: text for rel, text, _, _ in blocks}
    parts = [HEAD]
    for i, (rel, note) in enumerate(FILES, start=1):
        text = by_rel[rel]
        parts.append(f"\n### 3.{i} `{rel}` — {note}\n\n"
                     f"<!-- BEGIN_FILE {rel} -->\n```python\n{text.rstrip()}\n```\n"
                     f"<!-- END_FILE {rel} -->\n")
    parts.append(TAIL_HEAD)
    for rel, _, lines, digest in blocks:
        parts.append(f"| `{rel}` | {lines} | `{digest}` |\n")
    parts.append(TAIL_FOOT)
    return "".join(parts)


def _inspect(text: str) -> tuple[bool, list[str]]:
    found = {m.group(1): m.group(2) for m in BLOCK_RE.finditer(text)}
    ok = True
    for rel, disk, _, digest in _blocks():
        in_packet = found.get(rel)
        same = in_packet is not None and in_packet == disk.rstrip()
        print(f"  {rel}: {'MATCH' if same else 'MISMATCH'} ({digest})")
        ok &= same
    words = push_gate.forbidden_words()
    paths, username = push_gate.needles()
    return ok, push_gate.tag_text(text, words, paths, username)


def _check() -> int:
    if not OUT.is_file():
        print("FAIL: 委托包尚未生成")
        return 1
    ok, tags = _inspect(OUT.read_text(encoding="utf-8"))
    print(f"COMPARE_AGAINST=git show {ANCHOR[:7]}:<file>（历史快照，非当前工作区）")
    print(f"BLOCKS_MATCH={ok}")
    print(f"ANON={'CLEAN' if not tags else tags}")
    return 0 if ok and not tags else 1


def _self_test() -> int:
    """证明自检不是恒真：改一个块内字节必须报红，插入本机路径必须报 ABS_PATH。"""
    if not OUT.is_file():
        print("FAIL: 先生成委托包")
        return 1
    original = OUT.read_text(encoding="utf-8")
    print("① 正常包")
    base_ok, base_tags = _inspect(original)

    needle = "def compute_row("
    assert needle in original, "自检锚点不在包内，先换一个"
    print("② 把块内一个函数名改坏")
    mutated = original.replace(needle, "def compute_rowX(", 1)
    mut_ok, _ = _inspect(mutated)

    paths, username = push_gate.needles()
    print("③ 往包里塞一条本机路径")
    _, dirty_tags = _inspect(original + "\n" + paths[0] + "leak.txt\n")

    OUT.write_text(original, encoding="utf-8", newline="\n")
    restored_ok, restored_tags = _inspect(OUT.read_text(encoding="utf-8"))
    print("④ 恢复原包")

    ok = (base_ok and not base_tags and not mut_ok
          and "ABS_PATH" in dirty_tags and restored_ok and not restored_tags)
    print(f"SELF_TEST: BASE_CLEAN={base_ok and not base_tags} "
          f"MUTATION_DETECTED={not mut_ok} PATH_DETECTED={'ABS_PATH' in dirty_tags} "
          f"RESTORED={restored_ok and not restored_tags} => {ok}")
    return 0 if ok else 1


def main() -> int:
    if "--self-test" in sys.argv:
        return _self_test()
    if "--check" in sys.argv:
        return _check()
    OUT.write_text(_body(), encoding="utf-8", newline="\n")
    print(f"OK: {OUT.relative_to(ROOT)}")
    return _check()


if __name__ == "__main__":
    raise SystemExit(main())

# Android 前端交接说明

> 状态：2026-11-20 风格定稿，仅完成「诊断报告」一页。
> 目的：后续由他人接手前端时，能在不动判据逻辑的前提下改界面。
> 判据：读完本文 + 看 `Theme.kt`，能独立加一页且不破坏任何已有数值。

## 1. 技术栈与文件职责

Kotlin + Jetpack Compose（Material 3），`compileSdk 35 / minSdk 26`，
Compose BOM `2024.09.00`，AGP 8.7.3。**没有任何第三方 UI 库和网络库**。

| 文件 | 只负责 | 允许改 |
|---|---|---|
| `Theme.kt` | 颜色、字号、形状、间距（唯一视觉真相源） | ✅ 换肤、调字号圆角，随意改 |
| `ReportScreen.kt` | 诊断报告页布局 | ✅ 增删区块、调整信息顺序 |
| `ReportModel.kt` | 报告 JSON 解析、字段中文映射 | ⚠️ 只能加字段，不许改已有含义 |
| `MainActivity.kt` | 入口、Scaffold、联调探活 | ✅ 加导航、加页面入口 |

## 2. 设计 token（改这里就够，别在页面里写魔法数字）

颜色：

| 变量 | 值 | 用途 |
|---|---|---|
| `Bg` | `#F4F6F9` | 页面底色 |
| `CardBg` | `#FFFFFF` | 卡片底 |
| `Line` | `#E6EAF1` | 卡片描边 |
| `LineSoft` | `#F0F3F8` | 分隔线、胶囊底、弱化块 |
| `Ink` | `#141821` | 主文字 |
| `Ink2` | `#5B6475` | 次要说明 |
| `Ink3` | `#8D95A6` | 弱化标记、计数为 0 的档位 |
| `Brand` | `#2B4BD8` | 强调（仅按钮文字与主题色） |
| `Pass` | `#12775A` | 通过、完整 |
| `Fail` | `#C0392B` | 失败、缺口、未获得 |
| `Warn` | `#9A6A0B` | 得分率 0.5–0.8 区间 |
| `Track` | `#EDF1F6` | 环形/条形底槽 |

字号（`Theme.kt` 内的 `T*` 常量）：`TH1 22 SemiBold` 页标题 ·
`TH2 15 SemiBold` 区块标题 · `TEyebrow 11 Medium / tracking 1.2` 小标签 ·
`TBody 14 / 行高 21` · `TSmall 12.5 / 18` · `TMicro 11 / 15` · `TNum 17 SemiBold` 数字。

形状与间距：`CardShape 18dp` · `BannerShape 14dp` · `InnerShape 12dp` ·
`PillShape 50%`（按高度取半的百分比，不是 50dp）· `PagePadH 18` ·
`PagePadTop 24` · `PagePadBottom 30` · `ListGap 14` · `CardPad 16`。

## 3. 组件规格

- **SurfaceCard** — 白底 + `1dp Line` 描边 + 18dp 圆角 + 16dp 内边距，无阴影。
- **ScoreRing** — 直径 116dp、描边 12dp、Round 端点、从 -90°（12 点）顺时针；
  底槽 `Track`，前景按 `ratioTone()`。中心 27sp Bold 分数 + 10sp「得分率」。
- **StateChip** — 胶囊，内边距 10×5，11sp；计数为 0 时前景 `Ink3`、底 `LineSoft`。
- **LossBar** — 高 6dp 全圆角，`Track` 打底，`Fail` 填充，最小可见比例 5%
  （避免 1/20 这种小值在屏幕上消失）。
- **DiffLine** — 字段名 62dp 宽 `12sp Ink3`；`期望`标签 12sp + 值 12.5sp Medium `Ink2`；
  `实际`标签 12sp + 值 12.5sp SemiBold `Fail`。
- **StatusDot** — 8dp 圆，颜色取 `stateTone()`。
- **WeightChip** — 胶囊 10.5sp，底 `LineSoft`，字 `Ink2`；`weight == 0` 时显示「门槛项」。

## 4. 页面信息顺序（不要随意调换）

身份 → 总分（环 + 三项数字 + 状态胶囊）→ 技能缺口（按权重损失降序）→
断言明细（gate 在前，credit 在后，与 JSON 同序）→ 过程材料 → 判定说明（有则显示）→
数据来源 → 联调。

顺序本身就是论述：先给结论，再给理由，最后给证据来源。判定说明排在过程材料之后，
因为它是「这个结论对你意味着什么」，不是新证据。

## 5. 红线（违反即为 bug，不是审美分歧）

1. **界面里不许出现硬编码的业务数值。** 所有数字来自 `assets/sample-report.json`
   （或后端返回），页面只做格式化，不做计算。
2. **不许改判据口径。** 得分率 = `weighted_sum / total_weight`，分母固定为权重合计，
   不因失败项减少而变小。这是产品主张，不是显示细节。
3. **门槛项（`weight == 0`）不进分母**，显示文案是「门槛项」，不写「权重 0」。
4. **中文展示名只许来自报告的 `display_labels`。** 它是评测管线从
   `docs/skill-taxonomy-v0.yaml` 的 `name` 与 `docs/error-patterns-v0.yaml` 的 `label`
   读出来、随报告下发的。页面里**不许**再写 `when (id) { "multi_step_planning" -> "多步规划" }`
   这类映射表——2026-11-20 就存在过这样一张表，与词表三条全不一致（页面写「合计金额算错」，
   词表里是「总额算错」）。旧报告缺这个字段时回退原始 id，那只是兼容路径，不是常态；
   未知字段名同理回退，不许猜一个中文名。
5. **`disqualified` / `voided` 必须显式显示**，不许为了版面干净藏起来。
6. **不许加入场动画让首帧与真实值不一致。** 2026-11-20 踩过：环形进度做 720ms 补间，
   模拟器不在前台时帧不推进，截图里环是空的，看起来像 0 分。现在直接按真实值绘制
   （`ReportScreen.kt` 的 `ScoreRing` 注释里写了原因）。
7. **换肤只改 `Theme.kt`。** 页面里的形状/颜色必须引用常量，不许新写字面量。
8. **`notes` 必须原样显示，不许改写、不许挑着显示。** 这些句子是判定的一部分：
   「本次不计能力分、不计入掌握度，**不视为能力不足**」——少这一句，学生只看到 0 分，
   会当成「我被判死刑」。空数组时整块不出现，不许留空卡、不许写「暂无」。

## 6. 加一页的最小步骤

1. 在 `ReportScreen.kt` 同目录新建 `XxxScreen.kt`，复用 `SurfaceCard` 风格的容器
   （若需要，把 `SurfaceCard` 提到公共文件，别复制一份改颜色）。
2. 布局只用 `CardShape / PillShape / ListGap / CardPad / T*` 这些常量。
3. 数据从 `ReportModel.kt` 的解析结果取，不新建"演示用假数据"。
4. `MainActivity.kt` 里加导航入口（目前没有底部导航，是刻意留白）。
5. 编译 + 截图 + 按第 7 节对账。

## 7. 验收（每次改完必做）

```bash
cd android
./gradlew assembleDebug          # 必须 BUILD SUCCESSFUL
adb connect 127.0.0.1:5555       # 模拟器 AVD arena35
adb -s 127.0.0.1:5555 install -r app/build/outputs/apk/debug/app-debug.apk
adb -s 127.0.0.1:5555 shell am start -n com.arena.companion/.MainActivity
adb -s 127.0.0.1:5555 exec-out screencap -p > ../docs/images/shot.png
```

对账清单（打开 `assets/sample-report.json` 逐项核）：

| 界面位置 | 应为 |
|---|---|
| 环形中心 | `0.25` |
| 有效得分 / 权重合计 / 未获得 | `1` / `4` / `3` |
| 状态胶囊 | 通过 3、失败 2、受阻 0、未计分 0 |
| 缺口 1 | 多步任务分解，权重损失 2，错法标签「总额算错」「有效数算错」 |
| 缺口 2 | 失败重试策略，权重损失 1，错法标签「重试参数错误」 |
| 断言条数 | 共 5 条 |
| R2 对比块 | 有效条数 期望 17 实际 9；合计金额 期望 1837.99 实际 861.24 |
| 通过项的归因 | R4 显示「归因 工具参数校验」——**页面上不许出现任何英文 id**（`multi_step_planning`、`wrong_total_amount` 之类）。出现即为 bug |
| 过程材料 | 完整（绿色），「本份计入掌握度」 |
| 判定说明 | 本样例的 `notes` 为空 ⇒ **该区块不出现**（不许留空卡）。要验它真的会显示：把 `assets/sample-report.json` 换成 `--fixture raw-no-trace` 生成的报告，应出现 3 条，其中「流程不完整」「不视为能力不足」为加粗，且不出现 `**` 星号；看完换回 |

任何一项对不上，说明改到了数据层，不是改界面。

## 8. 换技术栈时怎么搬

所有 token 都是纯数值，与 Compose 无关：颜色是十六进制，字号 ≈ dp/sp，
圆角与间距是 dp。搬到 Flutter / React Native / Web 时：

- 建一份 `tokens.{dart,ts,css}`，把第 2 节原样抄过去；
- 环形进度换成任意 canvas 的 `arc`，参数照抄（直径 116、线宽 12、起点 -90°、Round 端点）；
- 「期望 / 实际」对比块是纯排版，不需要图表库。

## 9. 已知未完成

- 训练页、提醒页未做（报告 → 缺口 → 训练 是下一步产品主线）。
- `submission_api` 无鉴权且只监听 `127.0.0.1`，手机演示需 `adb reverse`，
  正式演示前要补最小令牌。
- 真机（荣耀 400 Pro）安装未跑通，原因在通用 WinUSB 驱动不注册 adb 接口 GUID，
  与本文无关；模拟器可完成全部界面验收。

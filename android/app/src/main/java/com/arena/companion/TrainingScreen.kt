package com.arena.companion

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/**
 * 训练单页：报告说"缺什么"，这一页说"接下来练什么、怎么算练会、回来核对哪条"。
 *
 * 三条硬约束（与报告页同一套纪律，见 docs/android-ui-handoff.md）：
 * 1. 页面上的每个字都必须能追到 `training-plan.json`，不许自己加"系统建议你…"。
 * 2. 训练单与报告来自两个文件，`task_id` 对不上就报错——错位的训练单比没有更糟。
 * 3. 族尾 / 没标族时明说，不编造"再加难一档"。
 */
@Composable
fun TrainingScreen(
    modifier: Modifier = Modifier,
    plan: TrainingPlan?,
    reportTaskId: String?,
    onBack: () -> Unit,
) {
    LazyColumn(
        modifier = modifier.fillMaxSize().background(Bg),
        contentPadding = PaddingValues(
            start = PagePadH,
            end = PagePadH,
            top = PagePadTop,
            bottom = PagePadBottom,
        ),
        verticalArrangement = Arrangement.spacedBy(ListGap),
    ) {
        item { BackRow("返回报告", onBack) }
        item {
            Column {
                Text("TRAINING", style = TEyebrow)
                Spacer(Modifier.height(4.dp))
                Text("训练单", style = TH1)
                Spacer(Modifier.height(6.dp))
                Text("按「同一个场景、逐步加难」推进：修好这一档，再测下一档。", style = TSmall)
            }
        }

        if (plan == null) {
            item { NoticeCard("训练单读取失败", "assets/training-plan.json 缺失或格式不符。本页不猜测该练什么。", Fail) }
            return@LazyColumn
        }

        val mismatch = plan.taskId != null && reportTaskId != null && plan.taskId != reportTaskId
        if (mismatch) {
            item {
                NoticeCard(
                    "训练单与报告对不上",
                    "报告判的是 ${reportTaskId}，训练单讲的是 ${plan.taskId}。两份数据不是同一次评测的产物，" +
                        "按纪律不显示建议——错位的训练单会让人练错题。请重新生成 assets/training-plan.json。",
                    Fail,
                )
            }
            return@LazyColumn
        }

        item { PositionCard(plan) }
        item { NextCard(plan) }
        item { CriterionCard(plan.criterion) }
        item { FocusCard(plan.focusAssertions) }
        item { SourceCard(plan) }
    }
}

/** 位置：第几档、共几档。档位是本页唯一的"判断"，必须显眼。 */
@Composable
private fun PositionCard(p: TrainingPlan) {
    SurfaceCard {
        Text("你在这一族的哪一档", style = TEyebrow)
        Spacer(Modifier.height(8.dp))
        if (p.level == null || p.levelsTotal == 0) {
            Text("这道题没有标注族与难度", style = TH2)
            Spacer(Modifier.height(4.dp))
            Text("没有族就排不出前后档，本页不编一个。任务 YAML 补上 variant_meta.family_id / difficulty 后会自动有。", style = TSmall)
            return@SurfaceCard
        }
        Row(verticalAlignment = Alignment.Bottom) {
            Text("第 ${p.level} 档", fontSize = 26.sp, fontWeight = FontWeight.Bold, color = Ink)
            Spacer(Modifier.width(6.dp))
            Text("共 ${p.levelsTotal} 档", style = TSmall, modifier = Modifier.padding(bottom = 6.dp))
        }
        Spacer(Modifier.height(10.dp))
        LevelBars(p.level, p.levelsTotal)
    }
}

/** 档位条：已过的档实心、当前档强调、未来档留灰。只有 2–3 档，不需要图表库。 */
@Composable
private fun LevelBars(level: Int, total: Int) {
    Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        (1..total).forEach { i ->
            val tone = when {
                i < level -> Pass
                i == level -> Brand
                else -> Track
            }
            Box(
                Modifier
                    .weight(1f)
                    .height(6.dp)
                    .clip(PillShape)
                    .background(tone),
            )
        }
    }
}

/** 下一档练什么：任务标题 + 任务自述的编号要求（原样，不总结）。 */
@Composable
private fun NextCard(p: TrainingPlan) {
    SurfaceCard {
        Text("下一档练什么", style = TEyebrow)
        Spacer(Modifier.height(8.dp))

        val nxt = p.next
        if (!p.hasNext || nxt == null) {
            Text(
                if (p.status == "top_of_family") "这已是本族最高档" else "没有可推的下一档",
                style = TH2,
            )
            Spacer(Modifier.height(4.dp))
            Text(
                if (p.status == "top_of_family")
                    "本族到顶了。再往上找不到「同场景更难」的题，本页不做「换个更花哨的说法」这种事——换族还是收工由你定。"
                else
                    "这道题缺少族/难度标注，推不出下一档。",
                style = TSmall,
            )
            return@SurfaceCard
        }

        Text(nxt.title, style = TH2)
        Spacer(Modifier.height(6.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            Tag("第 ${nxt.difficulty} 档", Brand)
            Tag(nxt.taskId, Ink2)
        }
        if (nxt.asks.isNotEmpty()) {
            Spacer(Modifier.height(10.dp))
            nxt.asks.forEachIndexed { index, ask ->
                Row(Modifier.padding(bottom = 6.dp)) {
                    Text("${index + 1}", style = TMicro, modifier = Modifier.width(16.dp))
                    Text(ask, style = TSmall)
                }
            }
            Spacer(Modifier.height(2.dp))
            Text("以上要求逐字取自该任务的 scenario，页面没有改写。", style = TMicro)
        }
    }
}

@Composable
private fun CriterionCard(criterion: String) {
    SurfaceCard(padding = PaddingValues(CardPad)) {
        Text("怎么算练会", style = TEyebrow)
        Spacer(Modifier.height(8.dp))
        Text(criterion, style = TBody)
        Spacer(Modifier.height(8.dp))
        Text("这是一条项目规则，不是实测阈值：它可判定，但没有学习科学依据背书。", style = TMicro)
    }
}

@Composable
private fun FocusCard(assertions: List<String>) {
    SurfaceCard {
        Text("练完回来看哪一条", style = TEyebrow)
        Spacer(Modifier.height(8.dp))
        if (assertions.isEmpty()) {
            Text("这份报告没有失败的断言，所以没有需要回看的目标。", style = TSmall)
            return@SurfaceCard
        }
        assertions.forEach { id ->
            Row(Modifier.padding(bottom = 6.dp), verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.size(6.dp).clip(PillShape).background(Fail))
                Spacer(Modifier.width(8.dp))
                Text(id, style = TSmall, color = Ink)
            }
        }
        Spacer(Modifier.height(2.dp))
        Text("这些是当前报告里没通过的断言 id，重测后回来对照是否消掉。", style = TMicro)
    }
}

@Composable
private fun SourceCard(p: TrainingPlan) {
    SurfaceCard(padding = PaddingValues(14.dp)) {
        Text("数据来源", style = TEyebrow)
        Spacer(Modifier.height(6.dp))
        Text(
            "本页由 tasks/ 下的任务定义推出：族与档位来自 variant_meta，要求清单来自 scenario。" +
                "没有一条建议是手写的；改任务定义后重新生成 training-plan.json 即可。",
            style = TMicro,
        )
    }
}

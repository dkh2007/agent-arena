package com.arena.companion

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/**
 * 诊断报告页（结构版）。
 *
 * 布局顺序固定：身份 → 总分 → 缺口 → 逐条断言 → 过程材料 → 判定说明 → 下一步 → 今日复习 → 数据来源。
 * 所有数值直接取自 Report 对象，页面不做任何计算或换算。
 *
 * 「下一步」卡是页尾的行动号召：它读的是 training-plan.json（族 + 下一档），
 * 不是页面自己想的建议。没有训练单时整块不出现，不写"敬请期待"。
 * 「今日复习」卡只显示后端算好的标题与条数，点进去看清单；没有提醒数据时整块不出现。
 */
@Composable
fun ReportScreen(
    modifier: Modifier = Modifier,
    plan: TrainingPlan? = null,
    reminder: Reminder? = null,
    onOpenTraining: () -> Unit = {},
    onOpenFeynman: () -> Unit = {},
    onOpenReminder: () -> Unit = {},
) {
    val ctx = LocalContext.current
    val report = remember { loadSampleReport(ctx) }

    if (report == null) {
        Column(
            modifier.fillMaxSize().background(Bg).padding(24.dp),
            verticalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            Text("内置样例读取失败", style = TH2)
            Text("assets/sample-report.json 缺失或格式不符", style = TSmall, color = Fail)
        }
        return
    }

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
        item { PageHead(report) }
        item { ScorePanel(report) }
        // 放在分数下面，不放断言明细之后：门禁失败时最有用的就是「为什么 0 分」，
        // 排到五条断言后面等于让学生滑到底才看见（2026-11-25 挪上来）。
        item { NotesCard(report.notes) }

        item { SectionHead("技能缺口", "按权重损失排序") }
        if (report.gaps.isEmpty()) {
            item { EmptyNote("本次没有归因到任何技能缺口") }
        } else {
            items(report.gaps) { GapCard(it, report.totalWeight) }
        }

        item { SectionHead("断言明细", "共 ${report.assertions.size} 条") }
        item { AssertionListCard(report.assertions) }

        item { ProcessBanner(report.processMissing) }
        plan?.let { item { NextStepCard(it, onOpenTraining, onOpenFeynman) } }
        reminder?.let { item { ReminderEntryCard(it, onOpenReminder) } }
        item { ProvenanceCard(report) }
    }
}

// ---------------- 顶部身份 ----------------

@Composable
private fun PageHead(r: Report) {
    Column(verticalArrangement = Arrangement.spacedBy(5.dp)) {
        Text("ARENA COMPANION", style = TEyebrow)
        Text("诊断报告", style = TH1)
        Text("${r.taskId} · ${r.submissionId}", style = TMicro)
        Text("评测时间 ${r.evaluatedAt}", style = TMicro)
    }
}

// ---------------- 总分 ----------------

@Composable
private fun ScorePanel(r: Report) {
    val tone = ratioTone(r.ratio, r.disqualified, r.voided)
    SurfaceCard {
        Row(verticalAlignment = Alignment.CenterVertically) {
            ScoreRing(progress = r.ratio.toFloat(), tint = tone) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text(
                        "%.2f".format(r.ratio),
                        fontSize = 27.sp,
                        fontWeight = FontWeight.Bold,
                        color = tone,
                    )
                    Text("得分率", fontSize = 10.sp, color = Ink3)
                }
            }
            Spacer(Modifier.width(20.dp))
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(9.dp)) {
                MetricLine("有效得分", fmt(r.awarded))
                MetricLine("权重合计", fmt(r.totalWeight))
                MetricLine("未获得", fmt(r.lost), tone = Fail)
            }
        }

        Spacer(Modifier.height(14.dp))
        HorizontalDivider(color = LineSoft)
        Spacer(Modifier.height(12.dp))

        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            r.byState.forEach { (state, n) ->
                val c = stateTone(state)
                StateChip(stateLabel(state), n, c)
            }
        }

        if (r.disqualified) {
            Spacer(Modifier.height(10.dp))
            NoteLine("本份已判不合格：命中了 gate 层否定断言", Fail)
        }
        if (r.voided) {
            Spacer(Modifier.height(10.dp))
            NoteLine("本份作废：任务版本不一致，需按当前版本重交", Fail)
        }
    }
}

@Composable
private fun MetricLine(label: String, value: String, tone: Color = Ink) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
        Text(label, style = TSmall)
        Text(value, style = TNum, color = tone)
    }
}

/**
 * 环形进度。
 *
 * 刻意不做"从 0 补间到真实值"的入场动画：动画依赖帧回调，模拟器不在前台、
 * 截图、录屏首帧这些场景下帧不推进，环会停在空槽 —— 屏幕上就变成"0 分"，
 * 与真实数据矛盾。宁可不动，也不能让画面在任一时刻说谎。
 */
@Composable
private fun ScoreRing(
    progress: Float,
    tint: Color,
    diameter: Dp = 116.dp,
    stroke: Dp = 12.dp,
    content: @Composable () -> Unit,
) {
    val sweep = progress.coerceIn(0f, 1f)
    Box(Modifier.size(diameter), contentAlignment = Alignment.Center) {
        Canvas(Modifier.matchParentSize()) {
            val w = stroke.toPx()
            val inset = w / 2f
            val arcSize = Size(size.width - w, size.height - w)
            drawArc(
                color = Track,
                startAngle = -90f,
                sweepAngle = 360f,
                useCenter = false,
                topLeft = Offset(inset, inset),
                size = arcSize,
                style = Stroke(width = w, cap = StrokeCap.Round),
            )
            if (sweep > 0.001f) {
                drawArc(
                    color = tint,
                    startAngle = -90f,
                    sweepAngle = 360f * sweep,
                    useCenter = false,
                    topLeft = Offset(inset, inset),
                    size = arcSize,
                    style = Stroke(width = w, cap = StrokeCap.Round),
                )
            }
        }
        content()
    }
}

@Composable
private fun StateChip(label: String, n: Int, tone: Color) {
    // 计数为 0 的档位弱化，避免四个彩色块同时抢注意力
    val active = n > 0
    val fg = if (active) tone else Ink3
    val bg = if (active) tone.copy(alpha = 0.10f) else LineSoft
    Row(
        modifier = Modifier
            .clip(PillShape)
            .background(bg)
            .padding(horizontal = 10.dp, vertical = 5.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(label, fontSize = 11.sp, color = fg)
        Spacer(Modifier.width(4.dp))
        Text(n.toString(), fontSize = 11.sp, fontWeight = FontWeight.SemiBold, color = fg)
    }
}

// ---------------- 缺口 ----------------

@Composable
private fun GapCard(g: Gap, totalWeight: Double) {
    SurfaceCard {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            Text(g.skillLabel, style = TH2)
            Text(
                "权重损失 ${fmt(g.weightLost)}",
                fontSize = 12.sp,
                color = Fail,
                fontWeight = FontWeight.Medium,
            )
        }

        Spacer(Modifier.height(10.dp))
        LossBar(fraction = if (totalWeight > 0) (g.weightLost / totalWeight).toFloat() else 0f)
        Spacer(Modifier.height(12.dp))

        if (g.patterns.isNotEmpty()) {
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                g.patternLabels.forEach { TagChip(it) }
            }
            Spacer(Modifier.height(10.dp))
        }
        Text("依据 ${g.failedAssertions.size} 条判据，逐条见下方", style = TMicro)
    }
}

@Composable
private fun LossBar(fraction: Float) {
    Box(
        Modifier.fillMaxWidth().height(6.dp).clip(PillShape).background(Track),
    ) {
        Box(
            Modifier
                .fillMaxWidth(fraction.coerceIn(0.05f, 1f))
                .height(6.dp)
                .clip(PillShape)
                .background(Fail),
        )
    }
}

@Composable
private fun TagChip(text: String) {
    Text(
        text,
        fontSize = 11.sp,
        color = Fail,
        modifier = Modifier
            .clip(PillShape)
            .background(Fail.copy(alpha = 0.08f))
            .padding(horizontal = 9.dp, vertical = 4.dp),
    )
}

// ---------------- 断言明细 ----------------

@Composable
private fun AssertionListCard(rows: List<AssertionRow>) {
    SurfaceCard(padding = PaddingValues(0.dp)) {
        rows.forEachIndexed { i, a ->
            AssertionItem(a)
            if (i != rows.lastIndex) {
                HorizontalDivider(
                    color = LineSoft,
                    modifier = Modifier.padding(start = 34.dp, end = 14.dp),
                )
            }
        }
    }
}

@Composable
private fun AssertionItem(a: AssertionRow) {
    Column(Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 12.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            StatusDot(a.state)
            Spacer(Modifier.width(10.dp))
            Text(
                a.title,
                modifier = Modifier.weight(1f),
                fontSize = 13.5.sp,
                fontWeight = FontWeight.Medium,
                color = Ink,
            )
            WeightChip(a.weight)
        }

        // 失败项：每条错误模式一句人话。句子由报告下发（数字来自本次证据），
        // 页面不改写、不补句——改写一句就可能把「这一步没做」说成「你不行」。
        a.notes.forEach { note ->
            Spacer(Modifier.height(8.dp))
            Column(Modifier.padding(start = 18.dp)) {
                Text(
                    "${note.label}：${note.text}",
                    style = TSmall,
                    color = Ink,
                )
                note.hint?.let {
                    Spacer(Modifier.height(3.dp))
                    Text("→ $it", style = TMicro, color = Ink3)
                }
            }
        }

        if (a.diffs.isNotEmpty()) {
            Spacer(Modifier.height(8.dp))
            Column(
                Modifier
                    .fillMaxWidth()
                    .padding(start = 18.dp)
                    .clip(InnerShape)
                    .background(Fail.copy(alpha = 0.05f))
                    .padding(horizontal = 10.dp, vertical = 8.dp),
                verticalArrangement = Arrangement.spacedBy(5.dp),
            ) {
                a.diffs.forEach { d -> DiffLine(d) }
            }
        }

        a.why?.let {
            Spacer(Modifier.height(6.dp))
            Text("原因：$it", style = TSmall, modifier = Modifier.padding(start = 18.dp))
        }

        a.note?.let {
            Spacer(Modifier.height(6.dp))
            Text(it, style = TMicro, modifier = Modifier.padding(start = 18.dp))
        }

        // 页脚只留「状态 · 归因」：断言 id（R2_report_correct）是给审计追踪用的，
        // 学生读不懂，也会让页面看起来像技术日志。要追 ID 请看报告 JSON。
        Spacer(Modifier.height(6.dp))
        Text(
            listOfNotNull(stateLabel(a.state), a.skillLabel?.let { "归因 $it" }).joinToString(" · "),
            style = TMicro,
            modifier = Modifier.padding(start = 18.dp),
        )
    }
}

@Composable
private fun DiffLine(d: DiffRow) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Text(d.field, style = TSmall, color = Ink3, modifier = Modifier.width(62.dp))
        Text("期望 ", fontSize = 12.sp, color = Ink3)
        Text(d.expected, fontSize = 12.5.sp, color = Ink2, fontWeight = FontWeight.Medium)
        Spacer(Modifier.width(10.dp))
        Text("实际 ", fontSize = 12.sp, color = Ink3)
        Text(d.actual, fontSize = 12.5.sp, color = Fail, fontWeight = FontWeight.SemiBold)
    }
}

@Composable
private fun StatusDot(state: String) {
    Box(
        Modifier.size(8.dp).clip(PillShape).background(stateTone(state)),
    )
}

@Composable
private fun WeightChip(weight: Double) {
    val text = if (weight > 0) "权重 ${fmt(weight)}" else "门槛项"
    Text(
        text,
        fontSize = 10.5.sp,
        color = Ink2,
        modifier = Modifier
            .clip(PillShape)
            .background(LineSoft)
            .padding(horizontal = 8.dp, vertical = 3.dp),
    )
}

// ---------------- 过程材料 / 来源 / 联调 ----------------

@Composable
private fun ProcessBanner(missing: Boolean) {
    val tone = if (missing) Fail else Pass
    Row(
        Modifier
            .fillMaxWidth()
            .clip(BannerShape)
            .background(tone.copy(alpha = 0.07f))
            .padding(horizontal = 14.dp, vertical = 13.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(Modifier.width(3.dp).height(30.dp).clip(PillShape).background(tone))
        Spacer(Modifier.width(11.dp))
        Column {
            Text(
                if (missing) "过程材料缺失" else "过程材料完整",
                fontSize = 12.5.sp,
                fontWeight = FontWeight.SemiBold,
                color = tone,
            )
            Text(
                if (missing) "本份不计入掌握度（分数照给）" else "本份计入掌握度",
                style = TMicro,
                color = tone.copy(alpha = 0.85f),
            )
        }
    }
}

/**
 * 判定说明。有才显示，没有就整块不出现——不留空卡、不写「暂无」。
 *
 * 这些句子由评测管线产出（`notes`），属于判定的一部分：作废、缺过程、门禁不过时，
 * 学生看到的 0 分配一句「这意味着什么」，才不至于读成「你不行」。此前页面把它们整段丢了，
 * 只剩数字。措辞不许改写——「不视为能力不足」这种话改了性质就变了。
 */
@Composable
private fun NotesCard(notes: List<String>) {
    if (notes.isEmpty()) return
    Column(
        Modifier
            .fillMaxWidth()
            .clip(CardShape)
            .background(LineSoft)
            .padding(15.dp),
        verticalArrangement = Arrangement.spacedBy(9.dp),
    ) {
        Text("判定说明", fontSize = 12.5.sp, fontWeight = FontWeight.SemiBold, color = Ink2)
        notes.forEach { note ->
            Row(Modifier.fillMaxWidth()) {
                Box(
                    Modifier
                        .padding(top = 6.dp)
                        .size(4.dp)
                        .clip(PillShape)
                        .background(Ink2.copy(alpha = 0.45f)),
                )
                Spacer(Modifier.width(9.dp))
                Text(boldEmphasis(note), style = TMicro, color = Ink2)
            }
        }
    }
}

/** 把 `**xxx**` 渲染成加粗。管线用这两个星号标出判定关键词，直接显示会露出星号本身。 */
private fun boldEmphasis(text: String): AnnotatedString = buildAnnotatedString {
    var bold = false
    text.split("**").forEachIndexed { index, part ->
        if (index > 0) bold = !bold
        if (part.isEmpty()) return@forEachIndexed
        withStyle(if (bold) SpanStyle(fontWeight = FontWeight.SemiBold) else SpanStyle()) {
            append(part)
        }
    }
}

/**
 * 下一步卡：把「练什么」放在评委翻到的位置，替掉原来那张调试卡。
 *
 * 三种状态都要显式：有下一档 / 已到本族最高档 / 没有训练单数据。
 * 只有第一种才给按钮——另外两种点进去也没有内容，给了按钮等于骗一次点击。
 *
 * 「复述这题」是第二个动作，只在有训练单时一起给：讲的对象是**这次判的题**，
 * 不是下一档。没训练单时这一块整块不出现，避免页尾变成一排没人点的按钮。
 */
@Composable
private fun NextStepCard(p: TrainingPlan, onOpen: () -> Unit, onSpeak: () -> Unit) {
    SurfaceCard {
        Text("下一步", style = TEyebrow)
        Spacer(Modifier.height(8.dp))
        when {
            p.hasNext && p.next != null -> {
                Text("练「${p.next.title}」", style = TH2)
                Spacer(Modifier.height(6.dp))
                Text(
                    "同一场景的第 ${p.next.difficulty} 档，共 ${p.levelsTotal} 档。练完按同一判据重测这份报告。",
                    style = TSmall,
                )
                Spacer(Modifier.height(12.dp))
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Row(
                        modifier = Modifier
                            .clip(PillShape)
                            .background(Brand.copy(alpha = 0.08f))
                            .clickable(onClick = onOpen)
                            .padding(horizontal = 14.dp, vertical = 8.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text("看训练单 →", fontSize = 12.5.sp, color = Brand, fontWeight = FontWeight.Medium)
                    }
                    Spacer(Modifier.width(8.dp))
                    Row(
                        modifier = Modifier
                            .clip(PillShape)
                            .border(1.dp, Line, PillShape)
                            .clickable(onClick = onSpeak)
                            .padding(horizontal = 14.dp, vertical = 8.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text("复述这题", fontSize = 12.5.sp, color = Ink2, fontWeight = FontWeight.Medium)
                    }
                }
            }
            p.status == "top_of_family" -> {
                Text("这已是本族最高档", style = TH2)
                Spacer(Modifier.height(6.dp))
                Text("没有「同场景更难」的下一档，本页不编一档出来。换族或收工由你决定。", style = TSmall)
            }
            else -> {
                Text("这道题没有标注族与难度", style = TH2)
                Spacer(Modifier.height(6.dp))
                Text("排不出前后档，所以这里不给「练哪道」的建议。", style = TSmall)
            }
        }
    }
}

/**
 * 今日复习入口。只搬后端算好的句子与条数——
 * "今天 2 项要复习" / "今天你设成了不复习" / "今天没有要复习的" 是三种不同的话，
 * 页面不在这里重新拼一句，也不按 0 条就自动改口。
 */
@Composable
private fun ReminderEntryCard(r: Reminder, onOpen: () -> Unit) {
    SurfaceCard {
        Text("今日复习", style = TEyebrow)
        Spacer(Modifier.height(8.dp))
        Text(r.headline, style = TH2)
        r.notice?.let {
            Spacer(Modifier.height(6.dp))
            Text(it, style = TSmall)
        }
        Spacer(Modifier.height(12.dp))
        Row(
            modifier = Modifier
                .clip(PillShape)
                .border(1.dp, Line, PillShape)
                .clickable(onClick = onOpen)
                .padding(horizontal = 14.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text("看清单 →", fontSize = 12.5.sp, color = Ink2, fontWeight = FontWeight.Medium)
        }
    }
}

@Composable
private fun ProvenanceCard(r: Report) {
    Column(
        Modifier
            .fillMaxWidth()
            .clip(CardShape)
            .background(LineSoft)
            .padding(15.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Text("样例来源", style = TEyebrow)
        Text(
            "本页数据由本地评测管线对夹具 raw-fake-retry 真实跑出，未经手工修改；" +
                "评分分母固定为权重合计 ${fmt(r.totalWeight)}，缺口按失败断言归因，均为代码判定。",
            style = TMicro,
            color = Ink2,
        )
    }
}

// ---------------- 通用件 ----------------

/** 卡片外壳。两个页面共用一份，避免训练页另起一套圆角/描边。 */
@Composable
internal fun SurfaceCard(
    padding: PaddingValues = PaddingValues(CardPad),
    content: @Composable ColumnScope.() -> Unit,
) {
    Column(
        Modifier
            .fillMaxWidth()
            .clip(CardShape)
            .background(CardBg)
            .border(1.dp, Line, CardShape)
            .padding(padding),
        content = content,
    )
}

@Composable
private fun SectionHead(title: String, hint: String) {
    Row(
        Modifier.fillMaxWidth().padding(top = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.SpaceBetween,
    ) {
        Text(title, style = TH2)
        Text(hint, style = TMicro)
    }
}

@Composable
private fun NoteLine(text: String, tone: Color) {
    Text(text, style = TMicro, color = tone)
}

@Composable
private fun EmptyNote(text: String) {
    Text(text, style = TSmall)
}

private fun stateTone(state: String): Color = when (state) {
    "pass" -> Pass
    "fail" -> Fail
    "blocked" -> Warn
    else -> Ink3
}

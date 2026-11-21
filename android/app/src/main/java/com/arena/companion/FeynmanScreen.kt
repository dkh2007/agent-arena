package com.arena.companion

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import androidx.compose.foundation.background
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
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.delay
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * 费曼复述页：先不看材料，自己讲一遍；**交上去之后**才解开对照材料。
 *
 * 四条纪律（与报告页、训练单一脉）：
 * 1. 页面上的每句话都要能追到 `feynman-prompt.json`；不打分、不评价、不写"不错，继续加油"。
 * 2. `task_id` 对不上就报错——让你复述一道不是这次判的题，比不给材料更糟。
 * 3. **对照材料默认锁着**。清单、期望 vs 实际、错法解释在提交前一律不显示，
 *    所以「提示」弹层在提交前只给"题目要几步"（题干本来就写着的那部分）。
 *    锁不是奖励机制，是费曼技巧的前提：先自己生成，再拿材料修。
 * 4. 提交之后正文只读。这是自己给自己定的规则——锁住的是"看完答案再改"，不是防作弊。
 *
 * 输入只保留文字。语音与手写两版做过又删了（2026-11-21 人拍板）：
 * 手写丢笔未修净、语音只在模拟器验过，而打字对"逼自己组织语言"这个目标已经够用。
 */
@Composable
fun FeynmanScreen(
    modifier: Modifier = Modifier,
    prompt: FeynmanPrompt?,
    reportTaskId: String?,
    onBack: () -> Unit,
) {
    val ctx = LocalContext.current
    val taskId = prompt?.taskId
    val commitKey = prompt?.let { "${it.taskId}_${it.submissionId ?: "none"}" }

    var text by rememberSaveable(taskId) {
        mutableStateOf(taskId?.let { loadDraft(ctx, it) } ?: "")
    }
    var savedAt by remember(taskId) { mutableLongStateOf(taskId?.let { draftSavedAt(ctx, it) } ?: 0L) }
    // 提交时刻 > 0 即已提交。按「题 + 提交号」记：换了评测结果就是另一轮复述，旧锁不该延续。
    var committedAt by remember(commitKey) {
        mutableLongStateOf(commitKey?.let { prefs(ctx).getLong("commit_$it", 0L) } ?: 0L)
    }
    var showHints by remember { mutableStateOf(false) }
    var askCommit by remember { mutableStateOf(false) }
    val committed = committedAt > 0L

    LaunchedEffect(text) {
        if (prompt == null || committed || text.isBlank()) return@LaunchedEffect
        delay(600)
        saveDraft(ctx, prompt.taskId, text)
        savedAt = System.currentTimeMillis()
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
        item { BackRow("返回报告", onBack) }
        item {
            Column {
                Text("FEYNMAN", style = TEyebrow)
                Spacer(Modifier.height(4.dp))
                Text("费曼复述", style = TH1)
                Spacer(Modifier.height(6.dp))
                Text(
                    if (committed) {
                        "复述已交，下面是对照材料——逐条看自己讲到了没有。"
                    } else {
                        "先不看对照，用大白话讲一遍。交上去之后才会解开对照。"
                    },
                    style = TSmall,
                )
            }
        }

        if (prompt == null) {
            item {
                NoticeCard(
                    "复述材料读取失败",
                    "assets/feynman-prompt.json 缺失或格式不符。本页不拿占位数据凑一份材料。",
                    Fail,
                )
            }
            return@LazyColumn
        }

        if (reportTaskId != null && prompt.taskId != reportTaskId) {
            item {
                NoticeCard(
                    "复述材料与报告对不上",
                    "报告判的是 $reportTaskId，这页让你复述的是 ${prompt.taskId}。" +
                        "两份数据不是同一次评测的产物，按纪律不显示——讲错题比不练更伤。请重新生成 assets/feynman-prompt.json。",
                    Fail,
                )
            }
            return@LazyColumn
        }

        item {
            RecallCard(
                prompt = prompt,
                text = text,
                onTextChange = { text = it },
                savedAt = savedAt,
                committedAt = committedAt,
                onOpenHints = { showHints = true },
                onAskCommit = { askCommit = true },
                ctx = ctx,
            )
        }

        if (!committed) {
            item { LockedCard() }
        } else {
            if (prompt.gaps.isNotEmpty()) item { GapCard(prompt) }
            item {
                SelfCheckCard(
                    prompt = prompt,
                    ctx = ctx,
                    storeKey = commitKey.orEmpty(),
                    committedText = text,
                )
            }
        }

        item { SourceCard(prompt) }
    }

    if (askCommit && prompt != null && !committed) {
        CommitDialog(
            text = text,
            onDismiss = { askCommit = false },
            onConfirm = {
                val key = commitKey
                if (key != null) {
                    val now = System.currentTimeMillis()
                    prefs(ctx).edit()
                        .putLong("commit_$key", now)
                        .putString("commit_text_$key", text.trim())
                        .apply()
                    committedAt = now
                }
                askCommit = false
            },
        )
    }

    if (showHints && prompt != null) {
        HintSheet(prompt, unlocked = committed) { showHints = false }
    }
}

/**
 * 复述卡：三问提纲 + 输入区 + 草稿状态 + 提交。
 *
 * 「提交复述」放在输入框**上方**，与草稿状态同一行。原因很实在：2026-11-20 实测，
 * 软键盘弹起后输入框下方的可操作项会被压住（见 docs/android-ui-handoff.md §9），
 * 而"我写的东西有没有存住""现在能不能交"正是键盘打开时最想知道的两件事。
 */
@Composable
private fun RecallCard(
    prompt: FeynmanPrompt,
    text: String,
    onTextChange: (String) -> Unit,
    savedAt: Long,
    committedAt: Long,
    onOpenHints: () -> Unit,
    onAskCommit: () -> Unit,
    ctx: Context,
) {
    val committed = committedAt > 0L
    SurfaceCard {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Text("复述", style = TEyebrow)
            Spacer(Modifier.weight(1f))
            PillButton("提示", onOpenHints, tone = Warn)
        }

        Spacer(Modifier.height(10.dp))
        prompt.questions.forEachIndexed { index, q ->
            Row(Modifier.padding(bottom = 6.dp)) {
                Text(
                    "${index + 1}",
                    style = TMicro,
                    modifier = Modifier.width(16.dp).padding(top = 2.dp),
                )
                Text(q.text, style = TSmall)
            }
        }

        Spacer(Modifier.height(12.dp))
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Text(
                when {
                    committed -> "已提交 · ${clock(committedAt)} · ${text.trim().length} 字"
                    savedAt > 0 -> "草稿已存 · ${clock(savedAt)}"
                    else -> "还没有草稿"
                },
                style = TMicro,
            )
            Spacer(Modifier.weight(1f))
            if (committed) {
                PillButton("已锁定", {}, tone = Ink3)
            } else {
                PillButton("提交复述", onAskCommit, tone = Brand)
            }
        }

        Spacer(Modifier.height(8.dp))
        BasicTextField(
            value = text,
            onValueChange = onTextChange,
            readOnly = committed,
            textStyle = TBody.copy(color = if (committed) Ink2 else Ink),
            cursorBrush = SolidColor(Brand),
            modifier = Modifier.fillMaxWidth().heightIn(min = 132.dp),
            decorationBox = { inner ->
                Box(
                    Modifier
                        .fillMaxWidth()
                        .clip(InnerShape)
                        .background(if (committed) CardBg else LineSoft)
                        .padding(12.dp),
                ) {
                    if (text.isEmpty()) {
                        Text(
                            "用大白话讲：这题要交什么 → 你卡在哪一步 → 下次具体改哪一处。",
                            style = TSmall,
                            color = Ink3,
                        )
                    }
                    inner()
                }
            },
        )

        Spacer(Modifier.height(10.dp))
        Text(
            if (committed) {
                "提交后这段不再改动。草稿存在本机，不联网；这一页不打分——对不对由你对照下面的清单自己判。"
            } else {
                "草稿存在本机，不联网。提交之前看不到对照材料，这是这一页的规矩，不是故障。"
            },
            style = TMicro,
        )
    }
}

/** 提交前的对照占位：说明为什么锁着，别让人以为是加载失败。 */
@Composable
private fun LockedCard() {
    SurfaceCard {
        Text("对照区已锁", style = TEyebrow)
        Spacer(Modifier.height(6.dp))
        Text(
            "交上去之后才会出现：这次没过的断言、期望和实际的差、错法解释。",
            style = TSmall,
        )
        Spacer(Modifier.height(6.dp))
        Text(
            "先看答案再讲，这一页就只剩抄写。真想提前看，点右上角「提示」——那里只给题目要几步，答案仍锁着。",
            style = TMicro,
        )
    }
}

@Composable
private fun CommitDialog(text: String, onDismiss: () -> Unit, onConfirm: () -> Unit) {
    AlertDialog(
        onDismissRequest = onDismiss,
        containerColor = CardBg,
        title = { Text("提交这段复述？", style = TH2) },
        text = {
            Column {
                Text("交上去之后正文不能再改，对照材料会立刻解锁。", style = TSmall)
                Spacer(Modifier.height(6.dp))
                if (text.isBlank()) {
                    Text("现在还是空的——空着交也行，但这次复述就没内容可对照了。", style = TSmall, color = Warn)
                } else {
                    Text("当前 ${text.trim().length} 字。", style = TMicro)
                }
            }
        },
        confirmButton = {
            TextButton(onClick = onConfirm) {
                Text(if (text.isBlank()) "空着交" else "提交并解锁", color = Brand, fontWeight = FontWeight.Medium)
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) { Text("再想想", color = Ink2) }
        },
    )
}

// ---------------------------------------------------------------- 对照

/** 缺口卡：仍用报告的中文技能名与错法标签，不在这里给新解释。 */
@Composable
private fun GapCard(prompt: FeynmanPrompt) {
    SurfaceCard {
        Text("这次没过的", style = TEyebrow)
        Spacer(Modifier.height(8.dp))
        prompt.gaps.forEachIndexed { index, gap ->
            if (index > 0) Spacer(Modifier.height(10.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(gap.skillLabel, style = TH2)
                Spacer(Modifier.weight(1f))
                Text(gap.failedAssertions.joinToString("、"), style = TMicro)
            }
            if (gap.patterns.isNotEmpty()) {
                Spacer(Modifier.height(6.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    gap.patterns.forEach { Tag(it.label, Fail) }
                }
            }
        }
        Spacer(Modifier.height(8.dp))
        Text("讲的时候要能对上这些名字：说「总额算错」比说「算得不太对」有用。", style = TMicro)
    }
}

/**
 * 自查清单：提交后才有。
 *
 * 两组：`step` 来自任务自述的编号步骤（逐字），`missed` 来自报告里没过的那几条。
 * 勾选是**给用户自己看的记号**，写在本机 prefs，不进掌握度、不上传——
 * 自评校准度没有验证过，拿它抬分数就是给判分层塞未仲裁的噪声。
 */
@Composable
private fun SelfCheckCard(
    prompt: FeynmanPrompt,
    ctx: Context,
    storeKey: String,
    committedText: String,
) {
    val ticks = remember(storeKey) {
        mutableStateMapOf<String, Boolean>().apply {
            if (storeKey.isNotEmpty()) putAll(loadSelfCheck(ctx, storeKey))
        }
    }
    val steps = prompt.checklist.filter { it.kind == "step" }
    val misses = prompt.checklist.filter { it.kind != "step" }
    val ticked = ticks.count { it.value }

    SurfaceCard {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Text("对照清单", style = TEyebrow)
            Spacer(Modifier.weight(1f))
            Text("已勾 $ticked / ${prompt.checklist.size}", style = TMicro)
        }

        if (steps.isNotEmpty()) {
            Spacer(Modifier.height(10.dp))
            Text("这题该说到这几步", style = TH2)
            Spacer(Modifier.height(6.dp))
            steps.forEach { item -> ChecklistRow(item, ticks[item.key] == true) {
                ticks[item.key] = !(ticks[item.key] ?: false)
                if (storeKey.isNotEmpty()) saveSelfCheck(ctx, storeKey, ticks)
            } }
        }

        if (misses.isNotEmpty()) {
            Spacer(Modifier.height(12.dp))
            Text("这次没说对的", style = TH2)
            Spacer(Modifier.height(6.dp))
            misses.forEach { item -> ChecklistRow(item, ticks[item.key] == true) {
                ticks[item.key] = !(ticks[item.key] ?: false)
                if (storeKey.isNotEmpty()) saveSelfCheck(ctx, storeKey, ticks)
            } }
        }

        Spacer(Modifier.height(12.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            PillButton("复制复述与自评", {
                val cm = ctx.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
                cm.setPrimaryClip(
                    ClipData.newPlainText(
                        "费曼复述",
                        copyPayload(prompt, committedText, ticks.toMap()),
                    ),
                )
            })
            Spacer(Modifier.weight(1f))
            Text("只存本机", style = TMicro)
        }
        Spacer(Modifier.height(8.dp))
        Text(
            "勾选不给分、不进掌握度：它是你留给自己的记号。" +
                "自评准不准没有验证过，拿它抬成绩等于往判分里掺噪声。",
            style = TMicro,
        )
    }
}

@Composable
private fun ChecklistRow(item: FeynmanChecklistItem, checked: Boolean, onToggle: () -> Unit) {
    Row(Modifier.padding(bottom = 8.dp), verticalAlignment = Alignment.Top) {
        TickBox(checked, onToggle)
        Spacer(Modifier.width(10.dp))
        Column(Modifier.weight(1f)) {
            Text(
                item.text,
                style = if (item.kind == "step") TSmall else TSmall.copy(color = Ink),
            )
            item.diffs.forEach { d ->
                Spacer(Modifier.height(3.dp))
                Text("${d.field}：期望 ${d.expected}，实际 ${d.actual}", style = TMicro, color = Fail)
            }
            if (item.kind != "step" && item.diffs.isEmpty()) {
                Spacer(Modifier.height(3.dp))
                Text("报告没给期望/实际，只有这一条断言没过。", style = TMicro)
            }
        }
    }
}

@Composable
private fun SourceCard(prompt: FeynmanPrompt) {
    SurfaceCard(padding = PaddingValues(14.dp)) {
        Text("数据来源", style = TEyebrow)
        Spacer(Modifier.height(6.dp))
        Text(prompt.sourceNote, style = TMicro)
        Spacer(Modifier.height(4.dp))
        Text(
            "${prompt.taskId} · ${prompt.submissionId ?: "无提交号"} · ${prompt.taskTitle}",
            style = TMicro,
            color = Ink3,
        )
    }
}

// ---------------------------------------------------------------- 提示弹层

/**
 * 提示：用户说"卡住了"才展开，展开的每一行都来自数据包，页面不补解释。
 *
 * 提交前只放「题目到底要几步」——题干本来就写着这几点，看它不算看答案。
 * 期望/实际、错法解释、现场描述都直接给出结论，必须等到提交之后。
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun HintSheet(prompt: FeynmanPrompt, unlocked: Boolean, onDismiss: () -> Unit) {
    val visible = if (unlocked) prompt.hints else prompt.hints.filter { it.key == "asks" }
    ModalBottomSheet(onDismissRequest = onDismiss, containerColor = CardBg) {
        Column(
            Modifier
                .fillMaxWidth()
                .padding(horizontal = PagePadH)
                .padding(bottom = 28.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Text("卡住了就点一条", style = TH2)
            Text(
                if (unlocked) "下面全部是这次评测已经产出的内容，不是我临时想的提示。"
                else "提交前只给「题目要几步」——其余几条直接给答案，得等你交上来。",
                style = TMicro,
            )
            visible.forEach { hint -> HintRow(hint) }
            if (!unlocked) {
                Text("还有 ${prompt.hints.size - visible.size} 条锁着：期望和实际、错法解释、当时现场。", style = TMicro)
            }
        }
    }
}

@Composable
private fun HintRow(hint: FeynmanHint) {
    var open by remember { mutableStateOf(false) }
    Column(
        Modifier
            .fillMaxWidth()
            .clip(InnerShape)
            .background(LineSoft)
            .clickable { open = !open }
            .padding(horizontal = 12.dp, vertical = 11.dp),
    ) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Text(hint.label, fontSize = 13.sp, color = Ink, fontWeight = FontWeight.Medium)
            Spacer(Modifier.weight(1f))
            Text(if (open) "收起" else "展开", style = TMicro)
        }
        if (open) {
            Spacer(Modifier.height(8.dp))
            hint.lines.forEach { line ->
                Text(line, style = TSmall, modifier = Modifier.padding(bottom = 4.dp))
            }
        }
    }
}

// ---------------------------------------------------------------- 小件

@Composable
private fun PillButton(label: String, onClick: () -> Unit, tone: androidx.compose.ui.graphics.Color = Ink2) {
    Box(
        Modifier
            .clip(PillShape)
            .background(tone.copy(alpha = 0.08f))
            .clickable(onClick = onClick)
            .padding(horizontal = 11.dp, vertical = 5.dp),
    ) {
        Text(label, fontSize = 11.5.sp, color = tone, fontWeight = FontWeight.Medium)
    }
}

/**
 * 复制给外部 AI 的整段文本：问题 + 我的复述 + 对照材料 + 自评勾选。
 * 自评一起带出去，是因为"勾了哪几条"本身就是判断复述质量的材料。
 */
private fun copyPayload(
    prompt: FeynmanPrompt,
    text: String,
    ticks: Map<String, Boolean>,
): String {
    val sb = StringBuilder()
    sb.append("费曼复述 · ").append(prompt.taskId).append('\n')
    prompt.questions.forEachIndexed { index, q ->
        sb.append(index + 1).append(") ").append(q.text).append('\n')
    }
    sb.append("\n我的复述：\n").append(text.trim().ifEmpty { "（还没写）" }).append('\n')
    if (prompt.checks.isNotEmpty()) {
        sb.append("\n本次没过的断言：\n")
        prompt.checks.forEach { check ->
            sb.append("- ").append(check.assertionId)
            check.diffs.forEach { d ->
                sb.append("；").append(d.field).append(" 期望 ").append(d.expected)
                    .append(" 实际 ").append(d.actual)
            }
            sb.append('\n')
        }
    }
    val checked = prompt.checklist.filter { ticks[it.key] == true }
    if (checked.isNotEmpty()) {
        sb.append("\n我自查时勾了这些：\n")
        checked.forEach { sb.append("- ").append(it.text).append('\n') }
    }
    return sb.toString()
}

private fun clock(millis: Long): String =
    SimpleDateFormat("HH:mm", Locale.getDefault()).format(Date(millis))

// 草稿：键里带 task_id，换一道题就是另一份草稿——不然复述会被上一题的内容污染
private const val PREFS = "feynman-draft"

private fun prefs(ctx: Context) = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

private fun loadDraft(ctx: Context, taskId: String): String =
    prefs(ctx).getString("text_$taskId", "") ?: ""

private fun draftSavedAt(ctx: Context, taskId: String): Long =
    prefs(ctx).getLong("at_$taskId", 0L)

private fun saveDraft(ctx: Context, taskId: String, text: String) {
    prefs(ctx).edit()
        .putString("text_$taskId", text)
        .putLong("at_$taskId", System.currentTimeMillis())
        .apply()
}

// 自评：`id:1;id:0` 这种最笨的格式。条目 id 只有字母数字下划线和短横，分隔符不会撞。
private fun loadSelfCheck(ctx: Context, key: String): Map<String, Boolean> =
    prefs(ctx).getString("selfcheck_$key", "").orEmpty()
        .split(';')
        .mapNotNull { seg ->
            val cut = seg.lastIndexOf(':')
            if (cut <= 0) null else seg.substring(0, cut) to (seg.substring(cut + 1) == "1")
        }
        .toMap()

private fun saveSelfCheck(ctx: Context, key: String, ticks: Map<String, Boolean>) {
    prefs(ctx).edit()
        .putString(
            "selfcheck_$key",
            ticks.entries.joinToString(";") { "${it.key}:${if (it.value) 1 else 0}" },
        )
        .apply()
}

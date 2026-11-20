package com.arena.companion

import android.app.Activity
import android.content.Context
import android.content.ClipData
import android.content.ClipboardManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
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
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateListOf
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
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.delay
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * 费曼复述页：先不看材料，自己讲一遍；讲完再对照这次评测留下的证据。
 *
 * 三条纪律（与报告页、训练单一脉）：
 * 1. 页面上的每句话都要能追到 `feynman-prompt.json`；不打分、不评价、不写"不错，继续加油"。
 * 2. `task_id` 对不上就报错——让你复述一道不是这次判的题，比不给材料更糟。
 * 3. 三种输入方式都是**录入手段**，不是新判据：语音走系统识别、手写只存笔迹。
 *    手写不做文字识别（端上离线模型体积与许可都不合适），页面必须写明这一点。
 *
 * 输入方式按豆包桌面端的做法放在输入框旁边：一个胶囊显示当前方式，点开切换。
 * 没有做成底部 Tab —— 切换的是"怎么打字"，不是页面，Tab 会让人以为换了功能。
 */
@Composable
fun FeynmanScreen(
    modifier: Modifier = Modifier,
    prompt: FeynmanPrompt?,
    reportTaskId: String?,
    onBack: () -> Unit,
) {
    val ctx = LocalContext.current

    var text by rememberSaveable(prompt?.taskId) {
        mutableStateOf(prompt?.let { loadDraft(ctx, it.taskId) } ?: "")
    }
    var savedAt by remember(prompt?.taskId) { mutableLongStateOf(prompt?.let { draftSavedAt(ctx, it.taskId) } ?: 0L) }
    var mode by rememberSaveable { mutableStateOf(InputMode.Text.name) }
    var showHints by remember { mutableStateOf(false) }
    val strokes = remember { mutableStateListOf<Stroke>() }
    var padSize by remember { mutableStateOf(IntSize.Zero) }
    var inkSaved by remember { mutableStateOf<String?>(null) }
    var voiceNote by remember { mutableStateOf<String?>(null) }

    val voiceAvailable = remember { VoiceInput.isAvailable(ctx) }
    val voiceLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.StartActivityForResult(),
    ) { result ->
        if (result.resultCode == Activity.RESULT_OK) {
            val heard = VoiceInput.firstResult(result.data)
            if (heard == null) {
                voiceNote = "没听清。再说一遍，或者切到文字补两笔。"
            } else {
                text = if (text.isBlank()) heard else text.trimEnd() + "\n" + heard
                voiceNote = "已接在复述末尾。"
            }
        } else {
            voiceNote = null
        }
    }

    LaunchedEffect(text) {
        if (prompt == null || text.isBlank()) return@LaunchedEffect
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
                Text("先不看下面的对照，用大白话讲一遍；讲完再回来对。", style = TSmall)
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
                mode = InputMode.valueOf(mode),
                onModeChange = { mode = it.name },
                savedAt = savedAt,
                onOpenHints = { showHints = true },
                ctx = ctx,
                strokes = strokes,
                onStrokeStart = { Stroke().also { strokes.add(it) } },
                padSize = padSize,
                onPadSize = { padSize = it },
                inkSaved = inkSaved,
                onClearInk = { strokes.clear(); inkSaved = null },
                onSaveInk = {
                    inkSaved = saveInkToPng(ctx, strokes, padSize.width, padSize.height)
                },
                voiceAvailable = voiceAvailable,
                voiceNote = voiceNote,
                onStartVoice = {
                    voiceNote = null
                    voiceLauncher.launch(VoiceInput.intent())
                },
            )
        }

        if (prompt.gaps.isNotEmpty()) item { GapCard(prompt) }
        if (prompt.checks.isNotEmpty()) item { CheckCard(prompt) }
        item { SourceCard(prompt) }
    }

    if (showHints && prompt != null) {
        HintSheet(prompt) { showHints = false }
    }
}

// ---------------------------------------------------------------- 输入方式

private enum class InputMode(val label: String, val hint: String) {
    Text("文字", "键盘输入，随时改"),
    Voice("语音", "说一遍，系统转成文字"),
    Ink("手写", "写或画，存成本地图片"),
}

/**
 * 复述卡：问题提纲 + 输入区 + 三种录入方式 + 草稿状态。
 *
 * 「提示」放在卡右上而不是页尾：卡住发生在写的过程中，让人先滚到底再滚回来，
 * 等于逼他把刚想到的半句丢掉。
 */
@Composable
private fun RecallCard(
    prompt: FeynmanPrompt,
    text: String,
    onTextChange: (String) -> Unit,
    mode: InputMode,
    onModeChange: (InputMode) -> Unit,
    savedAt: Long,
    onOpenHints: () -> Unit,
    ctx: Context,
    strokes: List<Stroke>,
    onStrokeStart: () -> Stroke,
    padSize: IntSize,
    onPadSize: (IntSize) -> Unit,
    inkSaved: String?,
    onClearInk: () -> Unit,
    onSaveInk: () -> Unit,
    voiceAvailable: Boolean,
    voiceNote: String?,
    onStartVoice: () -> Unit,
) {
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
            Text("输入方式", style = TMicro)
            Spacer(Modifier.width(8.dp))
            ModePicker(mode, onModeChange)
            Spacer(Modifier.weight(1f))
            Text("${text.length} 字", style = TMicro)
        }

        Spacer(Modifier.height(8.dp))
        // 草稿状态与「复制全文」放在输入框**上方**，不是下方。
        // 原因很实在：2026-11-20 实测，软键盘弹起后这两项被压在键盘底下（见 docs/android-ui-handoff.md §9），
        // 而它们一个是「我写的东西有没有存住」、一个是「我要把这段话拿出去」——恰恰是键盘打开时最想看到的。
        // 把可操作项移到输入框上方，不依赖 insets 是否分派正确；下面的说明文字被遮住无所谓。
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Text(
                if (savedAt > 0) "草稿已存 · ${clock(savedAt)}" else "还没有草稿",
                style = TMicro,
            )
            Spacer(Modifier.weight(1f))
            PillButton("复制全文", {
                val cm = ctx.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
                cm.setPrimaryClip(ClipData.newPlainText("费曼复述", copyPayload(prompt, text)))
            })
        }

        Spacer(Modifier.height(8.dp))
        BasicTextField(
            value = text,
            onValueChange = onTextChange,
            textStyle = TBody.copy(color = Ink),
            cursorBrush = SolidColor(Brand),
            modifier = Modifier.fillMaxWidth().heightIn(min = 132.dp),
            decorationBox = { inner ->
                Box(
                    Modifier
                        .fillMaxWidth()
                        .clip(InnerShape)
                        .background(LineSoft)
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

        when (mode) {
            InputMode.Voice -> VoicePanel(voiceAvailable, voiceNote, onStartVoice)
            InputMode.Ink -> InkPanel(
                strokes = strokes,
                onStrokeStart = onStrokeStart,
                onSizeChanged = onPadSize,
                savedName = inkSaved,
                onClear = onClearInk,
                onSave = onSaveInk,
            )
            InputMode.Text -> Unit
        }

        Spacer(Modifier.height(10.dp))
        Text(
            "草稿存在本机，不联网；这一页不打分——讲得对不对，由你自己对照上面的证据。",
            style = TMicro,
        )
    }
}

@Composable
private fun ModePicker(mode: InputMode, onChange: (InputMode) -> Unit) {
    var expanded by remember { mutableStateOf(false) }
    Box {
        Row(
            Modifier
                .clip(PillShape)
                .background(Brand.copy(alpha = 0.08f))
                .clickable { expanded = true }
                .padding(horizontal = 10.dp, vertical = 5.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(mode.label, fontSize = 12.sp, color = Brand, fontWeight = FontWeight.Medium)
            Spacer(Modifier.width(4.dp))
            Text("▾", fontSize = 11.sp, color = Brand)
        }
        DropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }) {
            InputMode.values().forEach { item ->
                DropdownMenuItem(
                    text = {
                        Column {
                            Text(
                                item.label,
                                fontSize = 13.sp,
                                color = if (item == mode) Brand else Ink,
                                fontWeight = if (item == mode) FontWeight.SemiBold else FontWeight.Normal,
                            )
                            Text(item.hint, style = TMicro)
                        }
                    },
                    onClick = {
                        onChange(item)
                        expanded = false
                    },
                )
            }
        }
    }
}

/** 语音面板：先判断本机有没有识别服务，没有就直说，不给死按钮。 */
@Composable
private fun VoicePanel(available: Boolean, note: String?, onStart: () -> Unit) {
    Spacer(Modifier.height(10.dp))
    if (!available) {
        Column(
            Modifier
                .fillMaxWidth()
                .clip(InnerShape)
                .background(Warn.copy(alpha = 0.07f))
                .padding(12.dp),
        ) {
            Text("这台设备没有语音识别服务", fontSize = 12.5.sp, color = Warn, fontWeight = FontWeight.Medium)
            Spacer(Modifier.height(4.dp))
            Text(
                "系统没装识别器（模拟器常见）。切到文字或手写，一样能完成复述；" +
                    "手机上装了语音服务时这里会出现「开始说」。",
                style = TMicro,
            )
        }
        return
    }
    Row(
        Modifier
            .fillMaxWidth()
            .clip(InnerShape)
            .background(Brand.copy(alpha = 0.07f))
            .clickable(onClick = onStart)
            .padding(horizontal = 14.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text("开始说", fontSize = 13.sp, color = Brand, fontWeight = FontWeight.SemiBold)
        Spacer(Modifier.width(10.dp))
        Text("说完回来，文字接在复述末尾", style = TMicro)
    }
    note?.let {
        Spacer(Modifier.height(6.dp))
        Text(it, style = TMicro, color = Ink2)
    }
}

/** 手写面板：画板 + 清空 + 存图。文案必须写明「不自动转文字」。 */
@Composable
private fun InkPanel(
    strokes: List<Stroke>,
    onStrokeStart: () -> Stroke,
    onSizeChanged: (IntSize) -> Unit,
    savedName: String?,
    onClear: () -> Unit,
    onSave: () -> Unit,
) {
    Spacer(Modifier.height(10.dp))
    InkPad(strokes = strokes, onStrokeStart = onStrokeStart, onSizeChanged = onSizeChanged)
    Spacer(Modifier.height(8.dp))
    Row(verticalAlignment = Alignment.CenterVertically) {
        PillButton("存为图片", onSave, tone = Brand)
        Spacer(Modifier.width(8.dp))
        PillButton("清空", onClear, tone = Ink2)
        Spacer(Modifier.weight(1f))
        Text(if (savedName == null) "${strokes.size} 笔" else "已存 $savedName", style = TMicro)
    }
    Spacer(Modifier.height(6.dp))
    Text("手写只存笔迹，不会自动转成文字：识别要么联网，要么塞一个离线模型，两条都不划算。", style = TMicro)
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

/** 对照卡：期望 vs 实际 + 现场。勾选只存本地。 */
@Composable
private fun CheckCard(prompt: FeynmanPrompt) {
    val ticks = remember { mutableStateMapOf<String, Boolean>() }
    SurfaceCard {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Text("讲完对照", style = TEyebrow)
            Spacer(Modifier.weight(1f))
            Text("共 ${prompt.checks.size} 条", style = TMicro)
        }
        Spacer(Modifier.height(8.dp))
        prompt.checks.forEachIndexed { index, check ->
            if (index > 0) Spacer(Modifier.height(12.dp))
            Row(verticalAlignment = Alignment.Top) {
                TickBox(ticks[check.assertionId] == true) {
                    ticks[check.assertionId] = !(ticks[check.assertionId] ?: false)
                }
                Spacer(Modifier.width(10.dp))
                Column(Modifier.weight(1f)) {
                    Text(check.assertionId, fontSize = 13.sp, fontWeight = FontWeight.Medium, color = Ink)
                    check.diffs.forEach { d ->
                        Spacer(Modifier.height(3.dp))
                        Text(
                            "${d.field}：期望 ${d.expected}，实际 ${d.actual}",
                            style = TSmall,
                            color = Fail,
                        )
                    }
                    check.scene.forEach { line ->
                        Spacer(Modifier.height(3.dp))
                        Text(line, style = TMicro)
                    }
                }
            }
        }
        Spacer(Modifier.height(10.dp))
        Text("勾选只记在本页，不做判定：这些句子是让你对照自己刚才讲的话，不是给你打分。", style = TMicro)
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

/** 提示：用户说"卡住了"才展开，展开的每一行都来自数据包，页面不补解释。 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun HintSheet(prompt: FeynmanPrompt, onDismiss: () -> Unit) {
    ModalBottomSheet(onDismissRequest = onDismiss, containerColor = CardBg) {
        Column(
            Modifier
                .fillMaxWidth()
                .padding(horizontal = PagePadH)
                .padding(bottom = 28.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Text("卡住了就点一条", style = TH2)
            Text("下面全部是这次评测已经产出的内容，不是我临时想的提示。", style = TMicro)
            prompt.hints.forEach { hint -> HintRow(hint) }
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

/** 复制给外部 AI 的整段文本：问题 + 我的复述 + 对照材料，粘过去就能让人评。 */
private fun copyPayload(prompt: FeynmanPrompt, text: String): String {
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

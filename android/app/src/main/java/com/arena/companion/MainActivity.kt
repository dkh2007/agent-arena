package com.arena.companion

import android.content.Context
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import kotlin.concurrent.thread

/**
 * 第 2 步（2026-11-20）：诊断报告页。
 *
 * 报告不是编的：`assets/sample-report.json` 由本机评测管线对夹具
 * `raw-fake-retry` 真实跑出（ratio=0.25，两个技能缺口，两条断言失败）。
 * 离线也能渲染，评委演示不依赖电脑。
 */
class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme {
                Scaffold(modifier = Modifier.fillMaxSize()) { inner ->
                    ReportScreen(Modifier.padding(inner))
                }
            }
        }
    }
}

/** 演示期用 adb reverse tcp:8080 tcp:8080，手机会把 127.0.0.1:8080 转到电脑。 */
private const val DEFAULT_BASE = "http://127.0.0.1:8080"

private val PassGreen = Color(0xFF1B7F4B)
private val FailRed = Color(0xFFB3261E)
private val MutedGray = Color(0xFF5F6368)

private data class Gap(
    val skillId: String,
    val failedAssertions: List<String>,
    val patterns: List<String>,
    val weightLost: Double,
)

private data class AssertionRow(
    val id: String,
    val state: String,
    val layer: String,
    val weight: Double,
    val skillId: String?,
    val note: String?,
)

private data class Report(
    val taskId: String,
    val submissionId: String,
    val evaluatedAt: String,
    val ratio: Double,
    val weightedSum: Double,
    val totalWeight: Double,
    val disqualified: Boolean,
    val voided: Boolean,
    val byState: List<Pair<String, Int>>,
    val gaps: List<Gap>,
    val assertions: List<AssertionRow>,
    val processMissing: Boolean,
) {
    val awarded: Double get() = weightedSum
    val lost: Double get() = (totalWeight - weightedSum).coerceAtLeast(0.0)
}

@Composable
fun ReportScreen(modifier: Modifier = Modifier) {
    val ctx = LocalContext.current
    val report = remember { loadSampleReport(ctx) }
    var probe by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }

    if (report == null) {
        Column(modifier.fillMaxSize().padding(20.dp)) {
            Text("内置样例读取失败", style = MaterialTheme.typography.titleMedium)
            Text("assets/sample-report.json 缺失或格式不符", color = FailRed)
        }
        return
    }

    LazyColumn(
        modifier = modifier.fillMaxSize(),
        contentPadding = androidx.compose.foundation.layout.PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item { Header(report) }
        item { ScoreCard(report) }
        item { SectionTitle("技能缺口（按权重损失排序）") }
        if (report.gaps.isEmpty()) {
            item { EmptyHint("本次没有归因到任何技能缺口") }
        } else {
            items(report.gaps) { GapCard(it) }
        }
        item { SectionTitle("断言明细（${report.assertions.size} 条）") }
        items(report.assertions) { AssertionLine(it) }
        item { ProcessRow(report.processMissing) }
        item { ProvenanceNote(report) }
        item {
            Card(modifier = Modifier.fillMaxWidth()) {
                Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("后端联调（可选）", style = MaterialTheme.typography.labelLarge)
                    Text(
                        "演示时先执行 adb reverse tcp:8080 tcp:8080，手机即可访问电脑上的评测服务。",
                        style = MaterialTheme.typography.bodySmall,
                        color = MutedGray,
                    )
                    Button(
                        enabled = !busy,
                        onClick = {
                            busy = true
                            probe = "正在请求 /healthz …"
                            thread {
                                probe = probeHealth(DEFAULT_BASE)
                                busy = false
                            }
                        },
                    ) { Text(if (busy) "请求中…" else "探活后端") }
                    if (probe.isNotEmpty()) {
                        Text(probe, style = MaterialTheme.typography.bodySmall)
                    }
                }
            }
        }
    }
}

@Composable
private fun Header(r: Report) {
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Text("诊断报告", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.SemiBold)
        Text(
            "${r.taskId} · ${r.submissionId}",
            style = MaterialTheme.typography.bodySmall,
            color = MutedGray,
        )
        Text("评测时间 ${r.evaluatedAt}", style = MaterialTheme.typography.bodySmall, color = MutedGray)
    }
}

@Composable
private fun ScoreCard(r: Report) {
    val tone = when {
        r.disqualified || r.voided -> FailRed
        r.ratio >= 0.8 -> PassGreen
        r.ratio >= 0.5 -> Color(0xFF8A6100)
        else -> FailRed
    }
    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(14.dp),
        colors = CardDefaults.cardColors(containerColor = tone.copy(alpha = 0.08f)),
    ) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Row(verticalAlignment = Alignment.Bottom) {
                Text(
                    "%.2f".format(r.ratio),
                    fontSize = 46.sp,
                    fontWeight = FontWeight.Bold,
                    color = tone,
                )
                Spacer(Modifier.width(8.dp))
                Text(
                    "得分率",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MutedGray,
                    modifier = Modifier.padding(bottom = 10.dp),
                )
            }
            Text(
                "有效得分 ${fmt(r.awarded)} / 权重合计 ${fmt(r.totalWeight)}　未获得 ${fmt(r.lost)}",
                style = MaterialTheme.typography.bodyMedium,
            )
            HorizontalDivider()
            Row(horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                r.byState.forEach { (state, n) ->
                    Text(
                        "${stateLabel(state)} $n",
                        style = MaterialTheme.typography.bodySmall,
                        color = stateTone(state),
                    )
                }
            }
            if (r.disqualified) {
                Text("本份已判不合格：命中了 gate 层否定断言", color = FailRed, style = MaterialTheme.typography.bodySmall)
            }
            if (r.voided) {
                Text("本份作废：任务版本不一致，需按当前版本重交", color = FailRed, style = MaterialTheme.typography.bodySmall)
            }
        }
    }
}

@Composable
private fun GapCard(g: Gap) {
    Card(modifier = Modifier.fillMaxWidth(), shape = RoundedCornerShape(12.dp)) {
        Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(skillLabel(g.skillId), style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.SemiBold)
                Text("损失 ${fmt(g.weightLost)} 分", color = FailRed, style = MaterialTheme.typography.bodySmall)
            }
            if (g.patterns.isNotEmpty()) {
                Text(
                    "错法：${g.patterns.joinToString("、") { patternLabel(it) }}",
                    style = MaterialTheme.typography.bodySmall,
                )
            }
            Text(
                "依据断言：${g.failedAssertions.joinToString("、")}",
                style = MaterialTheme.typography.bodySmall,
                color = MutedGray,
            )
        }
    }
}

@Composable
private fun AssertionLine(a: AssertionRow) {
    val tone = stateTone(a.state)
    Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                if (a.state == "pass") "✓" else if (a.state == "fail") "✗" else "—",
                color = tone,
                fontWeight = FontWeight.Bold,
                modifier = Modifier.width(18.dp),
            )
            Text(a.id, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f))
            if (a.weight > 0) {
                Text("权重 ${fmt(a.weight)}", style = MaterialTheme.typography.bodySmall, color = MutedGray)
            } else {
                Text("gate", style = MaterialTheme.typography.bodySmall, color = MutedGray)
            }
        }
        val detail = buildString {
            a.skillId?.let { append("归因 ${skillLabel(it)}") }
            a.note?.let { if (isNotEmpty()) append(" · "); append(it) }
        }
        if (detail.isNotEmpty()) {
            Text(
                detail,
                style = MaterialTheme.typography.bodySmall,
                color = MutedGray,
                modifier = Modifier.padding(start = 18.dp),
            )
        }
    }
}

@Composable
private fun ProcessRow(missing: Boolean) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(
            containerColor = if (missing) FailRed.copy(alpha = 0.08f) else PassGreen.copy(alpha = 0.08f),
        ),
    ) {
        Text(
            if (missing) "过程材料缺失 ⇒ 本份不计入掌握度（分数照给）"
            else "过程材料完整 ⇒ 本份计入掌握度",
            modifier = Modifier.padding(14.dp),
            style = MaterialTheme.typography.bodySmall,
            color = if (missing) FailRed else PassGreen,
        )
    }
}

@Composable
private fun ProvenanceNote(r: Report) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .background(Color(0xFFF4F4F5), RoundedCornerShape(10.dp))
            .padding(12.dp),
        verticalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        Text("样例来源", style = MaterialTheme.typography.labelMedium)
        Text(
            "本页数据由本地评测管线对夹具 raw-fake-retry 真实跑出，未经手工修改；" +
                "评分分母固定、缺口按断言归因，均为代码判定。",
            style = MaterialTheme.typography.bodySmall,
            color = MutedGray,
        )
    }
}

@Composable
private fun SectionTitle(text: String) {
    Text(text, style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.SemiBold)
}

@Composable
private fun EmptyHint(text: String) {
    Text(text, style = MaterialTheme.typography.bodySmall, color = MutedGray)
}

private fun fmt(d: Double): String =
    if (d == d.toLong().toDouble()) d.toLong().toString() else "%.2f".format(d)

private fun stateTone(state: String): Color = when (state) {
    "pass" -> PassGreen
    "fail" -> FailRed
    else -> MutedGray
}

private fun stateLabel(state: String): String = when (state) {
    "pass" -> "通过"
    "fail" -> "失败"
    "blocked" -> "受阻"
    "unscored" -> "未计分"
    else -> state
}

/** 词汇表的中文口径只做展示翻译；未知项回退原始 id，绝不猜测。 */
private fun skillLabel(id: String): String = when (id) {
    "multi_step_planning" -> "多步规划"
    "failure_retry" -> "失败重试"
    "tool_param_validation" -> "工具参数校验"
    else -> id
}

private fun patternLabel(id: String): String = when (id) {
    "wrong_total_amount" -> "合计金额算错"
    "wrong_valid_count" -> "有效条数算错"
    "retry_wrong_params" -> "重试时参数用错"
    else -> id
}

private fun loadSampleReport(ctx: Context): Report? = try {
    val text = ctx.assets.open("sample-report.json").bufferedReader().use { it.readText() }
    val root = JSONObject(text)
    val score = root.getJSONObject("score")
    val byStateObj = score.optJSONObject("by_state")
    val byState = mutableListOf<Pair<String, Int>>()
    byStateObj?.let { obj -> obj.keys().forEach { k -> byState.add(k to obj.optInt(k)) } }

    val gapsJson = root.optJSONArray("skill_gaps") ?: org.json.JSONArray()
    val gaps = (0 until gapsJson.length()).map { i ->
        val o = gapsJson.getJSONObject(i)
        Gap(
            skillId = o.optString("skill_id"),
            failedAssertions = o.optJSONArray("failed_assertions").toStringList(),
            patterns = o.optJSONArray("patterns").toStringList(),
            weightLost = o.optDouble("weight_lost", 0.0),
        )
    }

    val asJson = root.optJSONArray("assertions") ?: org.json.JSONArray()
    val assertions = (0 until asJson.length()).map { i ->
        val o = asJson.getJSONObject(i)
        AssertionRow(
            id = o.optString("id"),
            state = o.optString("state"),
            layer = o.optString("layer"),
            weight = o.optDouble("weight", 0.0),
            skillId = if (o.isNull("skill_id")) null else o.optString("skill_id"),
            note = if (o.isNull("note")) null else o.optString("note"),
        )
    }

    val sub = root.optJSONObject("submission")
    val pf = root.optJSONObject("process_fact")
    Report(
        taskId = sub?.optString("task_id").orEmpty(),
        submissionId = sub?.optString("submission_id").orEmpty(),
        evaluatedAt = sub?.optString("evaluated_at").orEmpty(),
        ratio = score.optDouble("ratio", 0.0),
        weightedSum = score.optDouble("weighted_sum", 0.0),
        totalWeight = score.optDouble("total_weight", 0.0),
        disqualified = score.optBoolean("disqualified", false),
        voided = score.optBoolean("voided", false),
        byState = byState,
        gaps = gaps,
        assertions = assertions,
        processMissing = pf?.optBoolean("missing", false) ?: false,
    )
} catch (e: Exception) {
    null
}

private fun org.json.JSONArray?.toStringList(): List<String> {
    if (this == null) return emptyList()
    return (0 until length()).map { optString(it) }
}

/** 返回人类可读结论；任何异常都原样写出来，不吞掉。 */
private fun probeHealth(base: String): String = try {
    val conn = (URL("$base/healthz").openConnection() as HttpURLConnection).apply {
        requestMethod = "GET"
        connectTimeout = 4000
        readTimeout = 4000
    }
    try {
        val code = conn.responseCode
        val body = (if (code in 200..299) conn.inputStream else conn.errorStream)
            ?.bufferedReader()?.use { it.readText() } ?: ""
        "HTTP $code\n${body.take(300)}"
    } finally {
        conn.disconnect()
    }
} catch (e: Exception) {
    "连接失败：${e.javaClass.simpleName}: ${e.message}"
}

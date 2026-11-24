package com.arena.companion

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject

/**
 * 报告数据模型 + 解析。
 *
 * 全部字段都来自 `assets/sample-report.json`（本机评测管线对夹具 raw-fake-retry
 * 真跑出来的产物）。这里不做任何"美化补数"：JSON 没有的字段就是 null，
 * 页面负责不显示，而不是编一个好看的默认值。
 */

data class DiffRow(
    val field: String,
    val expected: String,
    val actual: String,
)

/**
 * 一条错误模式的人话。
 *
 * `text` 里每个数字都来自本次评测证据（由 `arena/error_notes.py` 按词表模板拼），
 * 页面只负责显示——它既不可以改写措辞，也不可以自己补一句「你可能还漏了……」。
 * `hint` 是一句动作（这类错下次怎么改），没有就不显示那一行。
 */
data class PatternNote(
    val patternId: String,
    val label: String,
    val text: String,
    val hint: String?,
)

data class Gap(
    val skillId: String,
    val skillLabel: String,
    val failedAssertions: List<String>,
    val patterns: List<String>,
    val patternLabels: List<String>,
    val weightLost: Double,
)

data class AssertionRow(
    val id: String,
    val state: String,
    val layer: String,
    val weight: Double,
    val skillId: String?,
    val skillLabel: String?,
    val title: String,
    val notes: List<PatternNote>,
    val note: String?,
    val diffs: List<DiffRow>,
    val why: String?,
)

data class Report(
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
    /**
     * 评测管线写下的判定说明（`notes`）。这些句子是**判定的一部分**，不是装饰：
     * 例如「本次不计能力分、不计入掌握度，**不视为能力不足**」——缺了它，
     * 学生只会看到 0 分，以为自己被判了死刑。页面必须原样显示，不许改写措辞。
     */
    val notes: List<String>,
) {
    val awarded: Double get() = weightedSum
    val lost: Double get() = (totalWeight - weightedSum).coerceAtLeast(0.0)
}

fun loadSampleReport(ctx: Context): Report? = try {
    val text = ctx.assets.open("sample-report.json").bufferedReader().use { it.readText() }
    parseReport(text)
} catch (e: Exception) {
    null
}

fun parseReport(text: String): Report {
    val root = JSONObject(text)
    val score = root.getJSONObject("score")
    // 中文展示名由报告下发（词表是唯一真相源）。这里只做「查表 + 查不到回退 id」，
    // 绝不自己写一份 id → 中文的映射：那会变成第二处真相源，改词表时不同步。
    val labels = root.optJSONObject("display_labels")
    val skillLabels = labels?.optJSONObject("skills").toMap()
    val patternLabels = labels?.optJSONObject("patterns").toMap()
    // 差异行字段的中文名（valid_count → 有效条数）。旧报告没有这张表，
    // 回退到字段原名即可——宁可露出一处英文，也不在页面里另存一份映射。
    val fieldLabels = labels?.optJSONObject("fields").toMap()

    val byState = mutableListOf<Pair<String, Int>>()
    score.optJSONObject("by_state")?.let { obj ->
        // 固定顺序：判定口径的顺序不能随 JSON 键序漂移
        listOf("pass", "fail", "blocked", "unscored").forEach { k ->
            if (obj.has(k)) byState.add(k to obj.optInt(k))
        }
    }

    val gapsJson = root.optJSONArray("skill_gaps") ?: JSONArray()
    val gaps = (0 until gapsJson.length()).map { i ->
        val o = gapsJson.getJSONObject(i)
        val skillId = o.optString("skill_id")
        val patterns = o.optJSONArray("patterns").toStringList()
        Gap(
            skillId = skillId,
            skillLabel = skillLabels[skillId] ?: skillId,
            failedAssertions = o.optJSONArray("failed_assertions").toStringList(),
            patterns = patterns,
            patternLabels = patterns.map { patternLabels[it] ?: it },
            weightLost = o.optDouble("weight_lost", 0.0),
        )
    }

    val asJson = root.optJSONArray("assertions") ?: JSONArray()
    val assertions = (0 until asJson.length()).map { i ->
        val o = asJson.getJSONObject(i)
        val ev = o.optJSONObject("evidence")
        val extra = ev?.optJSONObject("extra")
        val skillId = if (o.isNull("skill_id")) null else o.optString("skill_id")
        val id = o.optString("id")
        AssertionRow(
            id = id,
            state = o.optString("state"),
            layer = o.optString("layer"),
            weight = o.optDouble("weight", 0.0),
            skillId = skillId,
            skillLabel = skillId?.let { skillLabels[it] ?: it },
            // 标题由报告下发（按断言 type 翻的中文）。旧报告没有这个键时退回 id，
            // 那只是兼容路径：新报告一律带 title。
            title = o.optString("title").takeIf { it.isNotBlank() } ?: id,
            notes = parsePatternNotes(o.optJSONArray("pattern_notes"), patternLabels),
            note = if (o.isNull("note")) null else o.optString("note"),
            diffs = parseDiffs(extra, fieldLabels),
            why = extra?.optString("why")?.takeIf { it.isNotBlank() },
        )
    }

    val sub = root.optJSONObject("submission")
    val pf = root.optJSONObject("process_fact")
    return Report(
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
        notes = root.optJSONArray("notes").toStringList(),
    )
}

private fun parsePatternNotes(arr: JSONArray?, labels: Map<String, String>): List<PatternNote> {
    if (arr == null) return emptyList()
    return (0 until arr.length()).mapNotNull { i ->
        val o = arr.optJSONObject(i) ?: return@mapNotNull null
        val pid = o.optString("pattern_id")
        val text = o.optString("text")
        if (text.isBlank()) return@mapNotNull null
        PatternNote(
            patternId = pid,
            label = labels[pid] ?: pid,
            text = text,
            hint = o.optString("hint").takeIf { it.isNotBlank() },
        )
    }
}

private fun parseDiffs(extra: JSONObject?, fieldLabels: Map<String, String>): List<DiffRow> {
    val arr = extra?.optJSONArray("diffs") ?: return emptyList()
    return (0 until arr.length()).mapNotNull { i ->
        val d = arr.optJSONObject(i) ?: return@mapNotNull null
        val field = d.optString("field")
        DiffRow(
            field = fieldLabels[field] ?: field,
            expected = cell(d.opt("expected")),
            actual = cell(d.opt("actual")),
        )
    }
}

/** JSON 数字可能是 Integer 也可能是 Double，统一成"整数不带小数点"。 */
private fun cell(v: Any?): String = when {
    v == null || v == JSONObject.NULL -> "—"
    v is Number -> {
        val d = v.toDouble()
        if (d == Math.floor(d) && !d.isInfinite()) d.toLong().toString() else "%.2f".format(d)
    }
    else -> v.toString()
}

fun JSONArray?.toStringList(): List<String> {
    if (this == null) return emptyList()
    return (0 until length()).map { optString(it) }
}

fun fmt(d: Double): String =
    if (d == d.toLong().toDouble()) d.toLong().toString() else "%.2f".format(d)

/** 键值表；空值跳过，避免页面出现空白标签。 */
private fun JSONObject?.toMap(): Map<String, String> {
    if (this == null) return emptyMap()
    val out = mutableMapOf<String, String>()
    val keys = keys()
    while (keys.hasNext()) {
        val key = keys.next()
        val value = optString(key)
        if (value.isNotBlank()) out[key] = value
    }
    return out
}

// skillLabel / patternLabel 已删除。中文只能来自报告里的 display_labels（源自 docs 下两份词表），
// 页面不许再维护第二份映射表——2026-11-20 就是因为这里手写了一张表，与词表三条全不一致。
// 同一理由，2026-11-24 删掉了本文件里的 fieldLabel（valid_count → 有效条数）：
// 那份表与 arena/error_notes.py 的 FIELD_LABELS 是两处真相源，现在字段中文名由报告下发。

fun stateLabel(state: String): String = when (state) {
    "pass" -> "通过"
    "fail" -> "失败"
    "blocked" -> "受阻"
    "unscored" -> "未计分"
    else -> state
}

/** 层级的中文叫法。gate 本来是「门槛」，页面上一直写的就是「门槛项」。 */
fun layerLabel(layer: String): String = when (layer) {
    "gate" -> "门槛项"
    "credit" -> "计分项"
    else -> layer
}

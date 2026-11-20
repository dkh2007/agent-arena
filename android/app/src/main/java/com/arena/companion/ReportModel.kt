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
        AssertionRow(
            id = o.optString("id"),
            state = o.optString("state"),
            layer = o.optString("layer"),
            weight = o.optDouble("weight", 0.0),
            skillId = skillId,
            skillLabel = skillId?.let { skillLabels[it] ?: it },
            note = if (o.isNull("note")) null else o.optString("note"),
            diffs = parseDiffs(extra),
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

private fun parseDiffs(extra: JSONObject?): List<DiffRow> {
    val arr = extra?.optJSONArray("diffs") ?: return emptyList()
    return (0 until arr.length()).mapNotNull { i ->
        val d = arr.optJSONObject(i) ?: return@mapNotNull null
        DiffRow(
            field = fieldLabel(d.optString("field")),
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

/** 报告字段的中文叫法（valid_count → 有效条数）。这是 JSON 字段的展示名，不是词表内容。 */
fun fieldLabel(id: String): String = when (id) {
    "valid_count" -> "有效条数"
    "total_amount" -> "合计金额"
    "offset" -> "翻页游标"
    else -> id
}

fun stateLabel(state: String): String = when (state) {
    "pass" -> "通过"
    "fail" -> "失败"
    "blocked" -> "受阻"
    "unscored" -> "未计分"
    else -> state
}

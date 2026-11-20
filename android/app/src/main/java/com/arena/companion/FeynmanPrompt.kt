package com.arena.companion

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject

/**
 * 费曼复述页的数据包。
 *
 * 来源是 `assets/feynman-prompt.json`，由 `python -m arena.feynman --from-report …` 生成：
 * 要求清单逐字取自任务定义，对照项取自诊断报告，错法解释取自错误模式词表。
 * 页面不加判断、不打分、不生成"系统觉得你讲得不错"这类话。
 *
 * `task_id` 同样是**对账键**：报告判的、训练单讲的、这一页让你复述的，必须是同一道题。
 */
data class FeynmanQuestion(val key: String, val text: String)

data class FeynmanPattern(val label: String, val description: String)

data class FeynmanGap(
    val skillLabel: String,
    val failedAssertions: List<String>,
    val patterns: List<FeynmanPattern>,
)

data class FeynmanDiff(val field: String, val expected: String, val actual: String)

data class FeynmanCheck(
    val assertionId: String,
    val diffs: List<FeynmanDiff>,
    val scene: List<String>,
)

data class FeynmanHint(val key: String, val label: String, val lines: List<String>)

data class FeynmanPrompt(
    val taskId: String,
    val submissionId: String?,
    val taskTitle: String,
    val asks: List<String>,
    val questions: List<FeynmanQuestion>,
    val gaps: List<FeynmanGap>,
    val checks: List<FeynmanCheck>,
    val hints: List<FeynmanHint>,
    val sourceNote: String,
)

/** 读不到或格式不对一律返回 null：页面明说"没有材料"，不拿假数据顶上。 */
fun loadFeynmanPrompt(ctx: Context): FeynmanPrompt? = try {
    parseFeynmanPrompt(ctx.assets.open("feynman-prompt.json").bufferedReader().use { it.readText() })
} catch (e: Exception) {
    null
}

fun parseFeynmanPrompt(text: String): FeynmanPrompt {
    val root = JSONObject(text)
    return FeynmanPrompt(
        taskId = root.optString("task_id"),
        submissionId = if (root.isNull("submission_id")) null else root.optString("submission_id"),
        taskTitle = root.optString("task_title"),
        asks = root.optJSONArray("asks").toStringList(),
        questions = root.optJSONArray("questions").mapObjects { o ->
            FeynmanQuestion(key = o.optString("key"), text = o.optString("text"))
        },
        gaps = root.optJSONArray("gaps").mapObjects { o ->
            FeynmanGap(
                skillLabel = o.optString("skill_label"),
                failedAssertions = o.optJSONArray("failed_assertions").toStringList(),
                patterns = o.optJSONArray("patterns").mapObjects { p ->
                    FeynmanPattern(
                        label = p.optString("label"),
                        description = p.optString("description"),
                    )
                },
            )
        },
        checks = root.optJSONArray("checks").mapObjects { o ->
            FeynmanCheck(
                assertionId = o.optString("assertion_id"),
                diffs = o.optJSONArray("diffs").mapObjects { d ->
                    // 字段名复用 ReportModel.kt 的 fieldLabel：同一份映射，两处各写一份迟早不一致
                    FeynmanDiff(
                        field = fieldLabel(d.optString("field")),
                        expected = d.optString("expected"),
                        actual = d.optString("actual"),
                    )
                },
                scene = o.optJSONArray("scene").toStringList(),
            )
        },
        hints = root.optJSONArray("hints").mapObjects { o ->
            FeynmanHint(
                key = o.optString("key"),
                label = o.optString("label"),
                lines = o.optJSONArray("lines").toStringList(),
            )
        },
        sourceNote = root.optString("source_note"),
    )
}

/** 只认对象元素；缺字段的对象照样解析（页面会看出空值），但 null 元素直接跳过。 */
private fun <T> JSONArray?.mapObjects(transform: (JSONObject) -> T): List<T> {
    if (this == null) return emptyList()
    return (0 until length()).mapNotNull { i -> optJSONObject(i)?.let(transform) }
}

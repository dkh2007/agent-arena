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

// plain 不是 description：description 是 judge 判定时读的清单（带 valid_count / fixture 这类词），
// 页面上出现的那句是 plain。两个读者两种口径，字段名跟着词表走，避免下一次又把 judge 文本搬上来。
data class FeynmanPattern(val label: String, val plain: String)

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

/**
 * 提交后才解锁的自查项。
 *
 * `kind` 只有两种：`step`（任务自述的编号步骤，逐字）与 `missed`（报告里没过的那几条）。
 * 页面靠它决定分行样式，不靠文案猜——文案改一个字不该改动分组。
 */
data class FeynmanChecklistItem(
    val key: String,
    val kind: String,
    val text: String,
    val diffs: List<FeynmanDiff>,
)

data class FeynmanPrompt(
    val taskId: String,
    val submissionId: String?,
    val taskTitle: String,
    val asks: List<String>,
    val questions: List<FeynmanQuestion>,
    val gaps: List<FeynmanGap>,
    val checks: List<FeynmanCheck>,
    val checklist: List<FeynmanChecklistItem>,
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
                        plain = p.optString("plain"),
                    )
                },
            )
        },
        checks = root.optJSONArray("checks").mapObjects { o ->
            FeynmanCheck(
                assertionId = o.optString("assertion_id"),
                diffs = o.optJSONArray("diffs").mapObjects { d ->
                    // 字段名由 feynman.py 按 error_notes.FIELD_LABELS 翻好再下发：
                    // 页面侧一份映射表都不留（2026-11-20 的四张表就是这么坏掉的）
                    FeynmanDiff(
                        field = d.optString("field"),
                        expected = d.optString("expected"),
                        actual = d.optString("actual"),
                    )
                },
                scene = o.optJSONArray("scene").toStringList(),
            )
        },
        checklist = root.optJSONArray("checklist").mapObjects { o ->
            FeynmanChecklistItem(
                key = o.optString("key"),
                kind = o.optString("kind"),
                text = o.optString("text"),
                diffs = o.optJSONArray("diffs").mapObjects { d ->
                    FeynmanDiff(
                        field = d.optString("field"),
                        expected = d.optString("expected"),
                        actual = d.optString("actual"),
                    )
                },
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

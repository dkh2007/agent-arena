package com.arena.companion

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject

/**
 * 复习提醒页的数据包。
 *
 * 来源是 `assets/reminder.json`，由
 * `python scripts/review_page.py --at … --out android/app/src/main/assets/reminder.json`
 * 直接写出 `arena.reminder.build_reminder()` 的返回值——本文件**只做解析**。
 *
 * ## 为什么这里没有排序、没有截断
 *
 * 今天给几项（`cap`）、先看哪一项（`scheduler` 的排序）、被挤到明天的几项怎么点名，
 * 全部在 Python 侧算完。页面若自己再排一遍，就成了第二真相源：两处各写一份
 * "今天该给几项"，迟早有一处先改（`due` 跨时区比较、同 `due` 的兜底键），
 * 然后页面上出现"昨天该看的今天还在"。`tests/test_reminder_asset.py` 钉住这条。
 *
 * ## 为什么只解析显示字段
 *
 * `skill_id` / `kind` / `review_id` 这些在 JSON 里，但**不进这个模型**。
 * 不解析就渲染不出来——"页面上不出现内部词"这条纪律靠类型保证，而不是靠人记得。
 * 技能一栏显示 `skill`（中文名，来自词表），复习形态显示 `kind_label`。
 */

/** 一条今日复习项。字段与 JSON 一一对应，没有派生值。 */
data class ReminderRow(
    val skill: String,
    val kindLabel: String,
    val taskTitle: String,
    val overdueDays: Int,
    val overdueText: String,
)

data class Reminder(
    val generatedAt: String,
    val cap: Int,
    val dueTotal: Int,
    val rows: List<ReminderRow>,
    val deferred: Int,
    val empty: Boolean,
    /** 后端算好的标题。`cap=0` 时是"今天你设成了不复习"，与"今天没有要复习的"是两句话。 */
    val headline: String,
    /** 补充说明，可能为 null。被推迟的项在这里点名，否则学生以为题被吞了。 */
    val notice: String?,
)

/** 读不到或格式不对返回 null：页面明说"没有数据"，不拿假数据顶上。 */
fun loadReminder(ctx: Context): Reminder? = try {
    parseReminder(ctx.assets.open("reminder.json").bufferedReader().use { it.readText() })
} catch (e: Exception) {
    null
}

fun parseReminder(text: String): Reminder {
    val root = JSONObject(text)
    val arr: JSONArray = root.optJSONArray("today") ?: JSONArray()
    return Reminder(
        generatedAt = root.optString("generated_at"),
        cap = root.optInt("cap", -1),
        dueTotal = root.optInt("due_total", 0),
        rows = (0 until arr.length()).map { i ->
            val o = arr.getJSONObject(i)
            ReminderRow(
                skill = o.optString("skill"),
                kindLabel = o.optString("kind_label"),
                taskTitle = o.optString("task_title"),
                overdueDays = o.optInt("overdue_days", 0),
                overdueText = o.optString("overdue_text"),
            )
        },
        deferred = root.optInt("deferred", 0),
        empty = root.optBoolean("empty", false),
        headline = root.optString("headline"),
        notice = if (root.isNull("notice")) null else root.optString("notice").takeIf { it.isNotBlank() },
    )
}

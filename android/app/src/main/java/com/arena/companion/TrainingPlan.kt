package com.arena.companion

import android.content.Context
import org.json.JSONObject

/**
 * 训练单数据模型。
 *
 * 来源是 `assets/training-plan.json`，由 `python -m arena.training --from-report …` 生成：
 * 全部字段来自 `tasks` 目录下任务定义的 `variant_meta`（族 + 难度）与 `scenario`（要求清单），
 * 页面不做任何推断，也不许补一句"系统认为你应该…"。
 *
 * `task_id` 是**对账键**：训练单与报告来自两个文件，对不上时页面必须报错，
 * 不许把错位的训练单显示成"给你的建议"。
 */

data class TrainingNext(
    val taskId: String,
    val title: String,
    val difficulty: Int,
    val asks: List<String>,
)

data class TrainingPlan(
    val taskId: String?,
    val familyId: String?,
    val level: Int?,
    val levelsTotal: Int,
    val status: String,          // has_next / top_of_family / no_family
    val next: TrainingNext?,
    val criterion: String,
    val focusAssertions: List<String>,
) {
    val hasNext: Boolean get() = status == "has_next" && next != null
}

/** 读不到或格式不对一律返回 null：页面显示"没有训练单"，不编一张。 */
fun loadTrainingPlan(ctx: Context): TrainingPlan? = try {
    parseTrainingPlan(ctx.assets.open("training-plan.json").bufferedReader().use { it.readText() })
} catch (e: Exception) {
    null
}

fun parseTrainingPlan(text: String): TrainingPlan {
    val root = JSONObject(text)
    val nextJson = root.optJSONObject("next")
    val asks = nextJson?.optJSONArray("asks").toStringList()
    return TrainingPlan(
        taskId = if (root.isNull("task_id")) null else root.optString("task_id"),
        familyId = if (root.isNull("family_id")) null else root.optString("family_id"),
        level = if (root.isNull("level")) null else root.optInt("level"),
        levelsTotal = root.optInt("levels_total", 0),
        status = root.optString("status", "no_family"),
        next = nextJson?.let {
            TrainingNext(
                taskId = it.optString("task_id"),
                title = it.optString("title"),
                difficulty = it.optInt("difficulty", 0),
                asks = asks,
            )
        },
        criterion = root.optString("criterion"),
        focusAssertions = root.optJSONArray("focus_assertions").toStringList(),
    )
}

// 列表解析复用 `ReportModel.kt` 里已有的 `JSONArray?.toStringList()`——
// 两份实现迟早会漂移（null / 非字符串元素的处理各不一样），不新写一份。

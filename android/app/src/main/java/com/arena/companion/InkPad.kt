package com.arena.companion

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Paint
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.runtime.Composable
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.nestedscroll.NestedScrollConnection
import androidx.compose.ui.input.nestedscroll.NestedScrollSource
import androidx.compose.ui.input.nestedscroll.nestedScroll
import androidx.compose.ui.input.pointer.PointerEventPass
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import java.io.File
import java.io.FileOutputStream

/**
 * 手写板：手指写、画都行，笔迹存成本地 PNG 附在复述里。
 *
 * **不做手写识别。** 端上离线识别要引第三方模型（体积、许可都可能踩坑），
 * 在线识别又要联网——这一页至今不发任何网络请求，不为了"看起来智能"破这条线。
 * 所以这里的定位是「草稿纸」：把思路画出来、和文字一起存档；
 * 页面文案必须写明"不自动转成文字"，否则用户写完发现没变字，会以为是 bug。
 *
 * 笔迹用点列存（`Stroke.points`）而不是 `Path`：`Path` 是可变对象，
 * `lineTo` 不会触发重组，画面会卡在落笔那一帧。点列进 `SnapshotStateList` 才会逐点重绘。
 */
class Stroke {
    val points = mutableStateListOf<Offset>()
}

/** 画板本体。尺寸固定高度 208dp，宽度撑满卡片内宽。 */
@Composable
fun InkPad(
    modifier: Modifier = Modifier,
    strokes: List<Stroke>,
    onStrokeStart: () -> Stroke,
    onSizeChanged: (IntSize) -> Unit = {},
) {
    // 画板要"吃掉"纵向滚动请求，否则外层 LazyColumn 会把竖直笔画抢成滚动：
    // 2026-11-20 实测，横线能画满，竖线只画出开头的几十像素（列表先滚了，手势被取消）。
    // 只用 consume 拦不住——指针事件从根到叶分派，父列表在 Initial 阶段就已经看到未消费的移动。
    // nestedScroll 的 preScroll 是"子先说话"的唯一入口，把纵向位移全部认领，列表就不动。
    val lockScroll = remember {
        object : NestedScrollConnection {
            override fun onPreScroll(available: Offset, source: NestedScrollSource): Offset =
                Offset(0f, available.y)
        }
    }
    Canvas(
        modifier = modifier
            .fillMaxWidth()
            .height(208.dp)
            .clip(InnerShape)
            .background(CardBg)
            .border(1.dp, Line, InnerShape)
            .onSizeChanged(onSizeChanged)
            .nestedScroll(lockScroll)
            // 不用 detectDragGestures：它要等 touch slop 判方向，而外层 LazyColumn 也在等同一个
            // slop。2026-11-20 实测：连着画三笔，第一笔被列表吞掉（变成滚动），计数只有 2。
            // 这里在 Initial 阶段（父节点看不到之前）就落指起笔、全程 consume，列表抢不走。
            // 第二次实测补了这一条：只在 Main 阶段消费时，**竖直**笔画仍会只画出很短的几像素——
            // 列表的 scrollable 在 Initial 阶段已经先一步起了滚动。改到 Initial 后竖线完整。
            .pointerInput(Unit) {
                awaitEachGesture {
                    val down = awaitFirstDown(
                        requireUnconsumed = false,
                        pass = PointerEventPass.Initial,
                    )
                    val stroke = onStrokeStart()
                    stroke.points.add(down.position)
                    down.consume()
                    while (true) {
                        val event = awaitPointerEvent(PointerEventPass.Initial)
                        val change = event.changes.firstOrNull { it.id == down.id } ?: break
                        if (!change.pressed) {
                            change.consume()
                            break
                        }
                        stroke.points.add(change.position)
                        change.consume()
                    }
                }
            },
    ) {
        // 画一条淡淡的基线，暗示"这里可以写"，不画格子——格子会让人以为是练字本
        drawLine(
            color = LineSoft,
            start = Offset(16f, size.height * 0.72f),
            end = Offset(size.width - 16f, size.height * 0.72f),
            strokeWidth = 2f,
        )
        strokes.forEach { stroke ->
            val points = stroke.points
            when {
                points.size == 1 -> drawCircle(Ink, radius = 2.5f, center = points[0])
                points.size > 1 -> for (i in 1 until points.size) {
                    drawLine(
                        color = Ink,
                        start = points[i - 1],
                        end = points[i],
                        strokeWidth = 3f,
                        cap = androidx.compose.ui.graphics.StrokeCap.Round,
                    )
                }
            }
        }
    }
}

/**
 * 把当前笔迹渲染成 PNG 存进 app 私有目录，返回文件名。
 *
 * 空笔迹返回 null——不产出 0 字节的文件冒充"手写稿"。
 * 白底而不是透明底：贴进文档、发给别人时透明底在深色背景上会变成一团墨。
 */
fun saveInkToPng(ctx: Context, strokes: List<Stroke>, widthPx: Int, heightPx: Int): String? {
    val real = strokes.filter { it.points.isNotEmpty() }
    if (real.isEmpty() || widthPx <= 0 || heightPx <= 0) return null

    val bitmap = Bitmap.createBitmap(widthPx, heightPx, Bitmap.Config.ARGB_8888)
    val canvas = android.graphics.Canvas(bitmap)
    canvas.drawColor(android.graphics.Color.WHITE)
    val paint = Paint().apply {
        color = android.graphics.Color.parseColor("#141821")
        strokeWidth = 3f * ctx.resources.displayMetrics.density
        isAntiAlias = true
        style = Paint.Style.STROKE
        strokeCap = Paint.Cap.ROUND
        strokeJoin = Paint.Join.ROUND
    }
    real.forEach { stroke ->
        val points = stroke.points
        if (points.size == 1) {
            canvas.drawCircle(points[0].x, points[0].y, paint.strokeWidth / 2f, paint)
        } else {
            val path = android.graphics.Path()
            path.moveTo(points[0].x, points[0].y)
            for (i in 1 until points.size) path.lineTo(points[i].x, points[i].y)
            canvas.drawPath(path, paint)
        }
    }

    val dir = File(ctx.filesDir, "ink").apply { mkdirs() }
    val file = File(dir, "ink-${System.currentTimeMillis()}.png")
    return try {
        FileOutputStream(file).use { bitmap.compress(Bitmap.CompressFormat.PNG, 100, it) }
        file.name
    } catch (e: Exception) {
        null
    } finally {
        bitmap.recycle()
    }
}

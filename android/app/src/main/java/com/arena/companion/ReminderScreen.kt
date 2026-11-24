package com.arena.companion

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/**
 * 复习提醒页：今天该看哪几项、每项为什么在、看不完的怎么办。
 *
 * 三条约束（页面这一侧不许违反，判据在后端）：
 *
 * 1. **只摆位置**：顺序就是 `reminder.json` 里 `today` 的顺序，页面不重排、不截断。
 *    排序与上限只在 `scheduler.take_daily()` 一处实现。
 * 2. **标题原样显示**：`headline` / `notice` 是后端算好的句子。`cap=0` 时说的是
 *    "今天你设成了不复习"，与"今天没有要复习的"是两件不同的事——页面不许合成一句，
 *    否则学生会把"我自己关了"读成"平台没给我"。
 * 3. **不出现内部词**：模型里根本没有 `skill_id` / `kind` / `review_id`（见 ReminderModel），
 *    页面想漏也漏不出来。
 */
@Composable
fun ReminderScreen(
    modifier: Modifier = Modifier,
    reminder: Reminder?,
    onBack: () -> Unit,
) {
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
                Text("REVIEW", style = TEyebrow)
                Spacer(Modifier.height(4.dp))
                Text("今日复习", style = TH1)
                Spacer(Modifier.height(6.dp))
                Text("每次练完会排一次复习。一天看几项由你自己设的上限决定，没轮到的排到明天。", style = TSmall)
            }
        }

        if (reminder == null) {
            item {
                NoticeCard(
                    "提醒读取失败",
                    "assets/reminder.json 缺失或格式不符。本页不猜测今天该复习什么——" +
                        "猜出来的清单会让学生练错题。重新生成该文件即可。",
                    Fail,
                )
            }
            return@LazyColumn
        }

        // 手改 asset 或后端改了上限口径时会撞上：说明白，不静默丢行。
        val overCap = reminder.cap >= 0 && reminder.rows.size > reminder.cap
        if (overCap) {
            item {
                NoticeCard(
                    "这份数据的条数超过了上限",
                    "页面上有 ${reminder.rows.size} 项，上限是 ${reminder.cap} 项。" +
                        "本页不会自己删掉多出来的行（那会让学生看不见该做的事），请重新生成数据。",
                    Fail,
                )
            }
        }

        item { HeadlineCard(reminder) }

        if (reminder.rows.isEmpty()) {
            item { NothingDueCard(reminder) }
        } else {
            items(reminder.rows.size) { i -> ReminderItemCard(i + 1, reminder.rows[i]) }
        }

        item { CapCard(reminder) }
        item { ProvenanceCard(reminder) }
    }
}

/** 标题：今天做什么。后端给哪句就显示哪句，页面不改写。 */
@Composable
private fun HeadlineCard(r: Reminder) {
    SurfaceCard {
        Text("今天", style = TEyebrow)
        Spacer(Modifier.height(8.dp))
        Text(r.headline, style = TH1)
        // 被推迟的项在这里被点名。少了这一行，学生会以为漏掉的题被平台吞了。
        r.notice?.let {
            Spacer(Modifier.height(8.dp))
            Text(it, style = TSmall)
        }
        Spacer(Modifier.height(10.dp))
        Text("排队中 ${r.dueTotal} 项 · 今天安排 ${r.rows.size} 项", style = TMicro)
    }
}

/** 一项复习：序号 + 技能（中文名）+ 形态 + 到期说法。 */
@Composable
private fun ReminderItemCard(index: Int, row: ReminderRow) {
    SurfaceCard {
        Row(verticalAlignment = Alignment.Top) {
            Box(
                Modifier
                    .size(20.dp)
                    .clip(RoundedCornerShape(6.dp))
                    .background(Brand.copy(alpha = 0.08f)),
                contentAlignment = Alignment.Center,
            ) {
                Text("$index", fontSize = 11.sp, color = Brand, fontWeight = FontWeight.Bold)
            }
            Spacer(Modifier.width(10.dp))
            Column(Modifier.weight(1f)) {
                Text(row.skill, style = TH2)
                Spacer(Modifier.height(6.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    Tag(row.kindLabel, Brand)
                    Tag(row.overdueText, if (row.overdueDays >= 1) Warn else Ink3)
                }
                if (row.taskTitle.isNotBlank()) {
                    Spacer(Modifier.height(8.dp))
                    Text(row.taskTitle, style = TSmall)
                }
            }
        }
    }
}

/** 空页：不催、不凑一项出来。后端已经说了是哪一种空，这里只补一句「明天照旧」。 */
@Composable
private fun NothingDueCard(r: Reminder) {
    SurfaceCard {
        Text("这一页是空的", style = TEyebrow)
        Spacer(Modifier.height(8.dp))
        Text(
            if (r.cap == 0 && r.dueTotal > 0)
                "上限设成了 0，所以今天不排任何一项。排队的 ${r.dueTotal} 项都还在，" +
                    "把上限调回来它们就会出现——没有被删掉。"
            else
                "目前没有到期要复习的项。练完一道题会自动排下一次。",
            style = TSmall,
        )
    }
}

@Composable
private fun CapCard(r: Reminder) {
    SurfaceCard(padding = PaddingValues(14.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("每天最多 ${r.cap} 项", style = TBody, fontWeight = FontWeight.Medium)
            Spacer(Modifier.width(8.dp))
            if (r.deferred > 0) Tag("${r.deferred} 项排到明天", Warn)
        }
        Spacer(Modifier.height(6.dp))
        Text(
            "这个上限来自你自己的设置，不是本页定的。看不完的项不会消失，明天照旧到期。",
            style = TMicro,
        )
    }
}

@Composable
private fun ProvenanceCard(r: Reminder) {
    Column(
        Modifier
            .fillMaxWidth()
            .clip(CardShape)
            .background(LineSoft)
            .padding(15.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Text("数据来源", style = TEyebrow)
        Text(
            "本页由本机 reminder.build_reminder() 生成（数据生成时间 ${r.generatedAt}）：" +
                "排序与上限只在后端一处实现，页面不重排、不截断；" +
                "技能一栏取词表中文名，查不到就直接报错，不显示英文 id。",
            style = TMicro,
            color = Ink2,
        )
    }
}

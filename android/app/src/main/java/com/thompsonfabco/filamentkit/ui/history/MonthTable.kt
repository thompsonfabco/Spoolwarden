package com.thompsonfabco.filamentkit.ui.history

import android.content.Context
import com.thompsonfabco.filamentkit.R
import com.thompsonfabco.filamentkit.net.UsageMonth
import com.thompsonfabco.filamentkit.ui.spool.formatGrams
import java.time.YearMonth
import java.time.format.DateTimeFormatter
import java.util.Locale

/** One row of the By month table: month, grams, prints, prints that did not finish, most used material. */
data class MonthLine(
    val month: String,
    val grams: String,
    val prints: Int,
    val unfinished: Int,
    val material: String?,
)

/** "15 prints", "2 did not finish", "Most used: PA6" as the row says them. */
internal fun MonthLine.printsText(context: Context): String = context.resources.getQuantityString(R.plurals.history_prints, prints, prints)

internal fun MonthLine.unfinishedText(context: Context): String = context.getString(R.string.history_unfinished, unfinished)

internal fun MonthLine.materialText(context: Context): String? = material?.let { context.getString(R.string.history_most_used, it) }

/** The row as one sentence, for a screen reader. */
internal fun MonthLine.spoken(context: Context): String =
    listOfNotNull(month, grams, printsText(context), unfinishedText(context), materialText(context)).joinToString(". ")

/** The table's rows, newest month first; pure, so the numbers and words are tested without a screen. */
object MonthTable {
    /** "2026-11" as "November 2026" in the phone's language; the text as given when it is not a month. */
    fun monthName(month: String, locale: Locale = Locale.getDefault()): String = try {
        YearMonth.parse(month).format(DateTimeFormatter.ofPattern("LLLL yyyy", locale))
    } catch (_: Exception) {
        month
    }

    fun lines(context: Context, months: List<UsageMonth>, locale: Locale = Locale.getDefault()): List<MonthLine> =
        months.sortedByDescending { it.month }.map { m ->
            MonthLine(
                month = monthName(m.month, locale),
                grams = context.getString(R.string.common_grams, formatGrams(m.grams)),
                prints = m.prints,
                unfinished = m.unfinishedPrints,
                material = m.topMaterial,
            )
        }
}

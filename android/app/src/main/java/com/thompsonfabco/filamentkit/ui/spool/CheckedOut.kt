package com.thompsonfabco.filamentkit.ui.spool

import com.thompsonfabco.filamentkit.R
import com.thompsonfabco.filamentkit.net.ActivityEvent
import com.thompsonfabco.filamentkit.net.SpoolsAnswer
import com.thompsonfabco.filamentkit.ui.history.RecentValues
import com.thompsonfabco.filamentkit.ui.printers.Say
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull

/** Catalogue texts for "Checked out" (rows, Return, Home). */
object CheckedOutSay {
    fun returned(location: String?, printerNotifiedFalse: Boolean = false): Say {
        val base = if (location.isNullOrBlank()) Say.Words(R.string.spools_returned) else Say.Words(R.string.spools_returned_to, listOf(location))
        return if (printerNotifiedFalse) Say.Joined(listOf(base, Say.Words(R.string.spools_return_printer_not_told))) else base
    }

    fun homeNow(location: String): Say = Say.Words(R.string.spools_home_done, listOf(location))
}

fun JsonObject.printerNotifiedFalse(): Boolean = (this["printer_notified"] as? JsonPrimitive)?.booleanOrNull == false

/** What the Hub says of a spool's remembered place (`checked_out`, `home`, `checked_out_since`). */
data class CheckedOutInfo(val checkedOut: Boolean, val home: String?, val since: String?)

private fun JsonElement?.text(): String? = (this as? JsonPrimitive)?.contentOrNull?.trim()?.ifBlank { null }

/**
 * The checked-out fields of a spool: on the spool itself or on its `view`. Null when the Hub sent none (an older Hub),
 * which is not the same as "not checked out".
 */
fun JsonObject.checkedOutInfo(): CheckedOutInfo? {
    val o = listOf(this, this["view"] as? JsonObject).firstOrNull { it != null && it.containsKey("checked_out") } ?: return null
    val flag = (o["checked_out"] as? JsonPrimitive)?.booleanOrNull ?: return null
    return CheckedOutInfo(flag, o["home"].text(), o["checked_out_since"].text())
}

/** The toast of a Return: [text], with Undo when the Hub offers one ([event], found after the return). */
data class ReturnToast(val text: Say, val event: ActivityEvent?)

/** The pure rules of the Checked out filter, the rows and Return. */
object CheckedOutRules {
    /** The kind of event a Return writes; undone as an unload is (the Hub's record only). */
    const val RETURNED_KIND = "spool.returned"

    /** The filter name in a list answer's `filters`. */
    const val FILTER = "checked_out"

    /** Whether the Hub applies the filter: its list answer names `checked_out` in `filters`; no `filters` is an older Hub. */
    fun supported(filters: List<String>?): Boolean = filters?.contains(FILTER) == true

    /**
     * The count of "Checked out (<n>)": the `total` of the list answer for `filter=checked_out`, with the
     * rows of the answer when it carries no `total`; null for a Hub that does not apply the filter.
     */
    fun count(answer: SpoolsAnswer): Int? = if (supported(answer.filters)) answer.total ?: rows(answer.spools).size else null

    /** The rows that are checked out, as the list shows them (a Hub that ignored the filter is not trusted to have cut it). */
    fun rows(rows: List<JsonObject>): List<SpoolSummary> =
        rows.map { it.toSpoolSummary() }.filter { it.checkedOut }

    /** "In <printer> – <slot>" when the Hub gave the slot, else "In <location>"; null when it gave neither. */
    fun place(s: SpoolSummary): Say? = (s.inSlot ?: s.location)?.let { Say.Words(R.string.spools_in_place, listOf(it)) }

    /** The lines under a row's name, in order: where it is, its home, since when. */
    fun lines(s: SpoolSummary, zone: java.time.ZoneId = java.time.ZoneId.systemDefault()): List<Say> = listOfNotNull(
        place(s),
        s.home?.let { Say.Words(R.string.spools_home, listOf(it)) } ?: Say.Words(R.string.spools_home_none),
        s.checkedOutSince?.let { Say.Words(R.string.spools_since, listOf(RecentValues.time(it, zone))) },
    )

    /**
     * Whether the spool screen offers the Home line, Return and Change home: the spool's own answer carried `checked_out`
     * (every list row and `GET /api/spools/{id}` do on a Hub that applies the filter). Nothing outlives the answer,
     * so another Hub cannot inherit it. An answer without the fields (an older Hub, or a route that omits them) offers none.
     */
    fun offered(s: SpoolSummary): Boolean = s.checkedOutKnown

    /** Return is offered on a checked-out spool only. */
    fun canReturn(s: SpoolSummary): Boolean = s.checkedOut

    /** The line of the spool's detail while it is checked out ("Home: not recorded" without a home); null when it is not. */
    fun homeLine(s: SpoolSummary): Say? =
        if (!s.checkedOut) null else s.home?.let { Say.Words(R.string.spools_home, listOf(it)) } ?: Say.Words(R.string.spools_home_none)

    /** Home can be changed while the spool is checked out. */
    fun canChangeHome(s: SpoolSummary): Boolean = s.checkedOut
}

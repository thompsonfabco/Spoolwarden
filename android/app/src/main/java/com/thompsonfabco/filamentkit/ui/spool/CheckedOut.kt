package com.thompsonfabco.filamentkit.ui.spool

import com.thompsonfabco.filamentkit.net.ActivityEvent
import com.thompsonfabco.filamentkit.net.SpoolsAnswer
import com.thompsonfabco.filamentkit.ui.history.RecentValues
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull

/**
 * The texts of "Checked out" (R88 section 1) that are not in the string catalogue yet. Only the catalogue step opens
 * `strings/en.json`; each text has a proposed key in the CO6 report and moves there with it.
 */
object CheckedOutTexts {
    const val CHIP = "Checked out"
    const val EMPTY = "No spool is checked out."
    const val RETURN = "Return"
    const val HOME = "Home"
    const val CHANGE_HOME = "Change home"

    fun chip(count: Int) = "$CHIP ($count)"
    fun inPlace(place: String) = "In $place"
    fun home(home: String) = "Home: $home"
    fun since(time: String) = "since $time"
    fun returnTo(home: String?) = if (home.isNullOrBlank()) RETURN else "Return to $home"
    fun homeNow(location: String) = "Home is now $location."
    fun returned(home: String?) = if (home.isNullOrBlank()) "Returned." else "Returned to $home."
}

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
data class ReturnToast(val text: String, val event: ActivityEvent?)

/** The pure rules of the Checked out filter, the rows and Return (R88 section 1.6). */
object CheckedOutRules {
    /** The kind of event a Return writes; undone as an unload is (the Hub's record only). */
    const val RETURNED_KIND = "spool.returned"

    /** The filter name in a list answer's `filters`. */
    const val FILTER = "checked_out"

    /** Whether the Hub applies the filter: its list answer names `checked_out` in `filters`; no `filters` is an older Hub (R88 01:55). */
    fun supported(filters: List<String>?): Boolean = filters?.contains(FILTER) == true

    /** The checked-out count from a list answer for `filter=checked_out`; null for a Hub that does not apply the filter. */
    fun count(answer: SpoolsAnswer): Int? = if (supported(answer.filters)) rows(answer.spools).size else null

    /** The rows that are checked out, as the list shows them (a Hub that ignored the filter is not trusted to have cut it). */
    fun rows(rows: List<JsonObject>): List<SpoolSummary> =
        rows.map { it.toSpoolSummary() }.filter { it.checkedOut }

    /** "In <printer> – <slot>" when the Hub gave the slot, else "In <location>"; null when it gave neither. */
    fun place(s: SpoolSummary): String? = (s.inSlot ?: s.location)?.let(CheckedOutTexts::inPlace)

    /** The lines under a row's name, in order: where it is, its home, since when. */
    fun lines(s: SpoolSummary, zone: java.time.ZoneId = java.time.ZoneId.systemDefault()): List<String> = listOfNotNull(
        place(s),
        s.home?.let(CheckedOutTexts::home),
        s.checkedOutSince?.let { CheckedOutTexts.since(RecentValues.time(it, zone)) },
    )

    /** Return is offered on a checked-out spool only. */
    fun canReturn(s: SpoolSummary): Boolean = s.checkedOut

    /** The line of the spool's detail while it is checked out; null when it is not. */
    fun homeLine(s: SpoolSummary): String? = s.home?.takeIf { s.checkedOut }?.let(CheckedOutTexts::home)

    /** Home can be changed while the spool is checked out. */
    fun canChangeHome(s: SpoolSummary): Boolean = s.checkedOut
}

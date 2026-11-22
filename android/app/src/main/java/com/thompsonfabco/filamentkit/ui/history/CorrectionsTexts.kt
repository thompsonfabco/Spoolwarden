package com.thompsonfabco.filamentkit.ui.history

/**
 * Every text of Share corrections and Corrections waiting (05-android-app 10.6) that is not in the string catalogue yet.
 * Only the catalogue step (HD2) opens `strings/en.json`; each text here has a proposed key in the report and in the
 * contract requests, and moves there with it. The Hub's own sentences (a refusal's text, the reason a correction was not
 * taken) are shown as the Hub sent them and never built here.
 */
object CorrectionsTexts {
    const val TITLE = "Corrections waiting"

    /** Recently added, under the header facts: also the way to Corrections waiting. */
    const val LINE_ON = "Share corrections is on"
    fun lineOnCount(n: Int) = "Share corrections is on · $n waiting"

    /** Edit by hand and a proposal, before the save; Check the spools, when the spools were read from photos. */
    const val LINE_WILL_WAIT = "Share corrections is on: this correction will wait on the Hub to be shared."
    const val LINE_CHECK = "Share corrections is on: fields you change here will wait on the Hub to be shared once the spools are added."

    /** After "Save changes" or "Accept" succeeded. */
    const val LINE_WAITING = "This correction is waiting to be shared."
    const val LINE_NO_PHOTO = "Not shared: no photo was kept for this record."
    fun lineNotWaiting(reason: String) = "Saved. The correction is not waiting to be shared: $reason."

    /** The head of the list: "12 waiting · 3.4 MB", then what to do. */
    fun head(count: Int, megabytes: String) = "$count waiting · $megabytes MB"
    const val HEAD_TEXT = "Nothing is sent until Send now is pressed on the Hub page. Remove any you do not want to share."
    const val HEAD_OFF = "Share corrections is off, so no new corrections are added. These are still waiting."
    const val FOOT = "Sending is done on the Hub page, under Settings > Label Reader."

    const val EMPTY_TITLE = "No corrections waiting"
    const val EMPTY_TEXT = "Corrections you make in Recently added, or while adding spools, appear here."
    const val READER_DOWN = "The reader does not answer, so the corrections waiting cannot be shown. They are kept."

    /** A row: who, when, what changed. */
    const val UNNAMED = "A correction"
    const val ENGINE_CLOUD = "Cloud (Claude)"
    fun rowMeta(time: String, engine: String) = "$time · $engine"
    fun change(field: String, read: String, now: String) = "$field: $read → $now"
    fun note(note: String) = "Note: $note"
    fun notWaiting(reason: String) = "Not waiting: the reader did not take this correction. $reason"
    const val NO_RECORD = "This record is no longer kept."
    const val NO_PHOTOS = "The photos of this correction are no longer on the Hub."

    /** Remove: the button while it runs, and the result. */
    const val REMOVING = "Removing…"
    const val REMOVED = "Removed. The record in Recently added is unchanged."

    /** Spoken forms. */
    fun a11yChange(field: String, read: String, now: String) = "$field, $read, becomes $now"
    fun a11yRemove(name: String) = "Remove the correction for $name"
    fun a11yRow(name: String, time: String, engine: String, changes: Int) =
        "$name, corrected $time, read by $engine, $changes ${if (changes == 1) "change" else "changes"}"
    fun a11yPhotos(name: String, count: Int) = "Photos of the correction for $name: $count"
}

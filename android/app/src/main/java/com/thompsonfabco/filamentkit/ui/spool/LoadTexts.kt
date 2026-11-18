package com.thompsonfabco.filamentkit.ui.spool

/**
 * The texts of the load result that are not in the string catalogue yet (EV7b). Only the catalogue step (EV2) opens
 * `strings/en.json`; each text here has a proposed key in the EV7b report and moves there with it. "Waiting for the
 * spool to load" (the slot rows) is a catalogue text already (`slot_waiting`). The Auto texts (R83 addition, AS4) have
 * their proposed keys in the contract requests.
 */
object LoadTexts {
    /** The short result of a load the printer has not carried out yet (the toast's text); [slot] is the slot's label. */
    fun waitingIn(slot: String): String =
        if (isAuto(slot)) "Waiting for the spool to be loaded." else "Waiting for the spool to load in $slot."

    /** The slot step's first choice and its hint (A5); `spool_load_auto`, `spool_load_auto_hint`. */
    const val AUTOMATIC = "Automatic"
    const val AUTOMATIC_HINT = "The slot where the spool appears."

    /** The Auto load's refusal when the Hub says nothing of its own (A6); `spool_load_auto_refused`. */
    const val AUTO_REFUSED = "This printer has no slots the Hub can watch. Choose a slot."

    /** The preview's question for an Auto load; `spool_load_auto_title`. */
    fun autoTitle(printer: String): String = "Load into $printer: Automatic"

    /** The dry run's line when another spool was waiting (A4); `spool_load_auto_replaces`. */
    fun replaces(spool: String): String = "Replaces $spool, which was waiting."

    /** The notice and the load result of an Auto that waits (A4); `printer_auto_waiting`. */
    fun waitingAuto(spool: String, printer: String): String = "Waiting for $spool to be loaded into any slot of $printer."

    /** The result of a load that waits, whichever slot choice made it. */
    fun waiting(result: SpoolResult.Waiting): String =
        if (isAuto(result.slot)) waitingAuto(result.spool.ifBlank { "the spool" }, result.printer) else waitingIn(result.slot)
}

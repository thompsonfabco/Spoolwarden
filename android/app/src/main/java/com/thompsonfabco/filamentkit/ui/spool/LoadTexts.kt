package com.thompsonfabco.filamentkit.ui.spool

import com.thompsonfabco.filamentkit.R
import com.thompsonfabco.filamentkit.ui.printers.Say

/**
 * The words of the load result and the Auto load (A4 to A6), as catalogue texts with their arguments: the screen turns a
 * [Say] into text, so the view models and their tests need no resources.
 */
object LoadTexts {
    /** The short result of a load the printer has not carried out yet (the toast's text); [slot] is the slot's label. */
    fun waitingIn(slot: String): Say =
        if (isAuto(slot)) Say.Words(R.string.spool_load_auto_waiting) else Say.Words(R.string.live_load_waiting, listOf(slot))

    /** The preview's question for an Auto load. */
    fun autoTitle(printer: String): Say = Say.Words(R.string.spool_load_auto_title, listOf(printer))

    /** The dry run's line when another spool was waiting (A4); [spool] is [replacedName]. */
    fun replaces(spool: Say): Say = Say.Words(R.string.auto_replaces, listOf(spool))

    /** The waiting spool the dry run names: its name, else its number, else "the spool". */
    fun replacedName(name: String, id: Int?): Say = when {
        name.isNotBlank() -> Say.FromHub(name)
        id != null -> Say.Words(R.string.spool_title, listOf(id))
        else -> Say.Words(R.string.spool_load_the_spool)
    }

    /** The notice and the load result of an Auto that waits (A4); [spool] is the spool as the sentence names it. */
    fun waitingAuto(spool: Say, printer: String): Say = Say.Words(R.string.auto_waiting, listOf(spool, printer))

    /** The result of a load that waits, whichever slot choice made it. */
    fun waiting(result: SpoolResult.Waiting): Say =
        if (isAuto(result.slot)) {
            waitingAuto(result.spool.ifBlank { null }?.let { Say.FromHub(it) } ?: Say.Words(R.string.spool_load_the_spool), result.printer)
        } else {
            waitingIn(result.slot)
        }
}

package com.thompsonfabco.filamentkit.ui.printers

import com.thompsonfabco.filamentkit.R
import com.thompsonfabco.filamentkit.net.AutoWait
import com.thompsonfabco.filamentkit.ui.spool.LoadTexts
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull

/**
 * The words of Clear slot, Unload on a Klipper printer and the Auto notice, as catalogue texts with their arguments
 * ([Say]): the view model holds them and its tests need no resources; the screen turns one into text. The texts of the
 * guided add are used on its screen straight from the catalogue.
 */
object PrinterTexts {
    /** Where a spool goes back to when the Hub names no place. */
    private val PREVIOUS_PLACE = Say.Words(R.string.printer_previous_place)

    /** The Hub's [place] as given, else "its previous place". */
    private fun placeSay(place: String?): Say = place?.ifBlank { null }?.let { Say.FromHub(it) } ?: PREVIOUS_PLACE

    /** on a Bambu Lab printer Clear slot changes only the Hub's record. */
    val CLEAR_BAMBU_NOTE: Say = Say.Words(R.string.printer_clear_bambu_note)

    /** An older Hub has no Clear slot call (404): said, not hidden, because the button is on every slot. */
    val CLEAR_UPDATE_HUB: Say = Say.Words(R.string.printer_clear_update_hub)

    /** What Unload adds when a spool that was waiting for the slot is waiting no more. */
    val UNLOAD_WAITING_ENDED: Say = Say.Words(R.string.printer_unload_waiting_ended)

    /**
     * Unload's question by printer family. A Bambu Lab printer's slot setting is not touched (the catalogue
     * `printer_unload_body*`); on a Klipper printer it is cleared too.
     */
    fun unloadKlipperBody(spool: String, slot: String, location: String): Say =
        Say.Words(R.string.printer_unload_body_klipper, listOf(spool, slot, placeSay(location)))

    fun clearTitle(label: String): Say = Say.Words(R.string.printer_clear_title, listOf(label))

    /** The Auto in the state `choose` (A4). */
    fun autoChoose(spool: Say): Say = Say.Words(R.string.printer_auto_choose, listOf(spool))

    /** A spool as the Auto notice names it: its name, else its number, else "the spool". */
    fun autoSpool(auto: AutoWait): Say =
        auto.spool?.name?.ifBlank { null }?.let { Say.FromHub(it) }
            ?: (auto.spoolId ?: auto.spool?.id)?.let { Say.Words(R.string.spool_title, listOf(it)) }
            ?: Say.Words(R.string.spool_load_the_spool)

    /**
     * The one notice an Auto assignment makes on the printer's screen (A4): [text] (waiting, or the question when two slots
     * were loaded) and, in that second state, the [choices] to press (the slots' labels).
     */
    data class AutoNotice(val text: Say, val choices: List<String>)

    fun autoNotice(auto: AutoWait, printer: String): AutoNotice {
        val spool = autoSpool(auto)
        return if (auto.choosing) AutoNotice(autoChoose(spool), auto.slots)
        else AutoNotice(LoadTexts.waitingAuto(spool, printer), emptyList())
    }

    /**
     * The sentence for the question and the result: the Hub's own [hubText] when it gives one, else the app's own on a
     * Bambu Lab printer, else none (Klipper).
     */
    fun clearNote(hubText: String?, bambu: Boolean): Say? =
        hubText?.ifBlank { null }?.let { Say.FromHub(it) } ?: CLEAR_BAMBU_NOTE.takeIf { bambu }

    /**
     * The question after its title: what the Hub forgets and, only with a recorded [spool], where it goes back to
     * ([place] blank: its previous place), then the printer [note] when there is one.
     */
    fun clearBody(spool: Say?, place: String?, note: Say? = null): Say {
        val first = if (spool == null) Say.Words(R.string.printer_clear_forgets)
        else Say.Words(R.string.printer_clear_forgets_spool, listOf(Say.Cap(spool), placeSay(place)))
        return Say.Joined(listOfNotNull(first, note))
    }

    /** The whole question as one text: "Clear slot A1? The Hub forgets ... goes back to Shelf B." */
    fun clearQuestion(label: String, spool: Say?, place: String?, note: Say? = null): Say =
        Say.Joined(listOf(clearTitle(label), clearBody(spool, place, note)))

    /** The result of the real call. [spool] and [place] are what the question said; [note] ends it. */
    fun clearDone(
        label: String, spool: Say?, place: String?, waitingEnded: Boolean, printerCleared: Boolean, note: Say? = null,
    ): Say =
        Say.Joined(
            listOfNotNull(
                Say.Words(R.string.printer_clear_done, listOf(label)),
                spool?.let { Say.Words(R.string.printer_clear_spool_back, listOf(Say.Cap(it), placeSay(place))) },
                Say.Words(R.string.printer_clear_waiting_ended).takeIf { waitingEnded },
                Say.Words(R.string.live_clear_printer).takeIf { printerCleared },
                note,
            ),
        )

    /** A spool view (`inventory.spool_view`) as a person names it: brand and name, else "Spool id", else "the spool" (a sentence that starts with it capitalises it). */
    fun spoolName(view: JsonObject): Say {
        fun text(key: String) = (view[key] as? JsonPrimitive)?.takeIf { it.isString }?.contentOrNull?.trim()?.ifBlank { null }
        val named = listOfNotNull(text("vendor"), text("name")).joinToString(" ")
        if (named.isNotBlank()) return Say.FromHub(named)
        val id = (view["id"] as? JsonPrimitive)?.let { it.intOrNull?.toString() ?: it.contentOrNull }
        return if (id.isNullOrBlank()) Say.Words(R.string.spool_load_the_spool) else Say.Words(R.string.spool_title, listOf(id))
    }
}

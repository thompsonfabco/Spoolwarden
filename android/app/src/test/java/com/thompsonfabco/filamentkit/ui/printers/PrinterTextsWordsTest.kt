package com.thompsonfabco.filamentkit.ui.printers

import android.os.Build
import org.junit.Assert.assertEquals
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config

/** The words of Clear slot, Unload on a Klipper printer and the Auto notice, from the catalogue. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [Build.VERSION_CODES.S])
class PrinterTextsWordsTest {
    private val context = RuntimeEnvironment.getApplication()
    private fun Say.text() = resolve(context)
    private val stillShows = "The printer still shows the old filament for this slot until a spool is loaded."

    @Test fun unloadOnAKlipperPrinterSaysTheSlotSettingIsClearedToo() {
        assertEquals(
            "Unload Acme PLA from A1? It goes back to Shelf 2. The slot's setting on the printer is cleared too, when the printer is online and can do it.",
            PrinterTexts.unloadKlipperBody("Acme PLA", "A1", "Shelf 2").text(),
        )
        assertEquals(true, PrinterTexts.unloadKlipperBody("Acme PLA", "A1", "").text().contains("goes back to its previous place."))
    }

    @Test fun theClearTextsCarryTheNoteLast() {
        assertEquals(
            "Clear slot A1? The Hub forgets the spool recorded here. $stillShows",
            PrinterTexts.clearQuestion("A1", null, null, PrinterTexts.clearNote(null, bambu = true)).text(),
        )
        assertEquals("Cleared slot A1. $stillShows", PrinterTexts.clearDone("A1", null, null, false, false, PrinterTexts.CLEAR_BAMBU_NOTE).text())
    }

    @Test fun theClearQuestionReadsAsTheDesignSays() {
        assertEquals(
            "Clear slot A1? The Hub forgets the spool recorded here. Acme Basic Black goes back to Shelf 2.",
            PrinterTexts.clearQuestion("A1", Say.FromHub("Acme Basic Black"), "Shelf 2").text(),
        )
        assertEquals("Clear slot A1? The Hub forgets the spool recorded here.", PrinterTexts.clearQuestion("A1", null, null).text())
        assertEquals(
            "Clear slot A1? The Hub forgets the spool recorded here. Spool 7 goes back to its previous place.",
            PrinterTexts.clearQuestion("A1", Say.Words(com.thompsonfabco.filamentkit.R.string.spool_title, listOf("7")), " ").text(),
        )
        assertEquals(
            "Clear slot A1? The Hub forgets the spool recorded here. The spool goes back to Shelf 1.",
            PrinterTexts.clearQuestion("A1", Say.Words(com.thompsonfabco.filamentkit.R.string.spool_load_the_spool), "Shelf 1").text(),
        )
    }

    @Test fun theClearResultListsWhatHappened() {
        assertEquals("Cleared slot A1.", PrinterTexts.clearDone("A1", null, null, waitingEnded = false, printerCleared = false).text())
        assertEquals(
            "Cleared slot A1. The spool is back on Shelf 1.",
            PrinterTexts.clearDone("A1", Say.Words(com.thompsonfabco.filamentkit.R.string.spool_load_the_spool), "Shelf 1", waitingEnded = false, printerCleared = false).text(),
        )
        assertEquals(
            "Cleared slot A1. The spool that was waiting for it is not waiting any more. The slot's setting on the printer is cleared too.",
            PrinterTexts.clearDone("A1", null, null, waitingEnded = true, printerCleared = true).text(),
        )
    }

    @Test fun theAutoNoticeNamesTheSpoolOrAsksWhichSlot() {
        assertEquals("Two slots were loaded. Which one got Acme PLA Red?", PrinterTexts.autoChoose(Say.FromHub("Acme PLA Red")).text())
        assertEquals("Update the Hub to clear a slot.", PrinterTexts.CLEAR_UPDATE_HUB.text())
        assertEquals("The spool waiting for this slot is not waiting any more.", PrinterTexts.UNLOAD_WAITING_ENDED.text())
    }
}

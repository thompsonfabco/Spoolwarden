package com.thompsonfabco.filamentkit.ui.printers

import com.thompsonfabco.filamentkit.nav.PrinterRoute
import com.thompsonfabco.filamentkit.nav.mapOfferRoute
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** Where each button of the add-printer map offer leads (05-android-app 9.12 step 4, and 9.7 for the question). */
class MapOfferTargetTest {
    @Test
    fun mapSlotsOpensThePrinterWithTheMapQuestionOpen() {
        assertEquals(PrinterRoute("K1", askMap = true), mapOfferRoute("K1", mapNow = true))
    }

    @Test
    fun laterOpensThePrinterWithoutTheQuestion() {
        assertEquals(PrinterRoute("K1"), mapOfferRoute("K1", mapNow = false))
        assertFalse(mapOfferRoute("K1", mapNow = false).askMap)
    }

    @Test
    fun theQuestionOpensOnceWhenThePrinterCanMap() {
        assertTrue(mapQuestionDue(askMap = true, asked = false, klipper = false, mapEnabled = true))
        assertFalse("not asked for", mapQuestionDue(askMap = false, asked = false, klipper = false, mapEnabled = true))
        assertFalse("only once", mapQuestionDue(askMap = true, asked = true, klipper = false, mapEnabled = true))
    }

    @Test
    fun aPrinterThatCannotMapKeepsItsDisabledReasonAndNoQuestion() {
        assertFalse(mapQuestionDue(askMap = true, asked = false, klipper = true, mapEnabled = true))
        assertFalse(mapQuestionDue(askMap = true, asked = false, klipper = false, mapEnabled = false))
    }
}

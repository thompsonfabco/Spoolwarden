package com.thompsonfabco.filamentkit.ui.printers

import org.junit.Assert.assertEquals
import org.junit.Test

/** Where each button of the map offer goes (05-android-app step 4). */
class MapOfferTargetTest {
    @Test
    fun mapSlotsOpensTheMapFlowOfThatPrinter() {
        val mapped = mutableListOf<String>()
        val opened = mutableListOf<String>()
        openFromMapOffer("K1", mapNow = true, onMap = { mapped += it }, onOpen = { opened += it })
        assertEquals(listOf("K1"), mapped)
        assertEquals(emptyList<String>(), opened)
    }

    @Test
    fun laterOpensThePrinter() {
        val mapped = mutableListOf<String>()
        val opened = mutableListOf<String>()
        openFromMapOffer("K1", mapNow = false, onMap = { mapped += it }, onOpen = { opened += it })
        assertEquals(emptyList<String>(), mapped)
        assertEquals(listOf("K1"), opened)
    }
}

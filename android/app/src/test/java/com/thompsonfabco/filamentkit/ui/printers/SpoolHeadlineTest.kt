package com.thompsonfabco.filamentkit.ui.printers

import com.thompsonfabco.filamentkit.net.SlotSpool
import org.junit.Test
import org.junit.Assert.assertEquals

class SpoolHeadlineTest {
    @Test
    fun `a headline starts with a capital and a named spool is unchanged`() {
        assertEquals("Spool #9", spoolHeadline(PrinterHelp.spoolName(SlotSpool(9))))
        assertEquals("Acme Basic Black", spoolHeadline("Acme Basic Black"))
        assertEquals("The spool", spoolHeadline("the spool"))
    }

    @Test
    fun `mid-sentence the name stays lowercase`() {
        assertEquals("spool #9", PrinterHelp.spoolName(SlotSpool(9)))
    }
}

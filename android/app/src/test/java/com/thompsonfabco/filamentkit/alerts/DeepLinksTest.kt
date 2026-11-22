package com.thompsonfabco.filamentkit.alerts

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class DeepLinksTest {
    @Test
    fun `the three places open their screens`() {
        assertEquals(AlertTarget.Printer("p-3"), deepLinkTarget("spoolwarden://printers/p-3"))
        assertEquals(AlertTarget.Spool(42), deepLinkTarget("spoolwarden://spools/42"))
        assertEquals(AlertTarget.Record("h1"), deepLinkTarget("spoolwarden://recent/h1"))
    }

    @Test
    fun `a trailing slash, a query, a fragment and an escaped id are read`() {
        assertEquals(AlertTarget.Spool(7), deepLinkTarget("spoolwarden://spools/7/"))
        assertEquals(AlertTarget.Spool(7), deepLinkTarget("SPOOLWARDEN://spools/7?x=1#top"))
        assertEquals(AlertTarget.Printer("a b+c"), deepLinkTarget("spoolwarden://printers/a%20b+c"))
    }

    @Test
    fun `anything else opens nothing`() {
        for (bad in listOf(
            null, "", "https://printers/p-3", "spoolwarden://", "spoolwarden://printers", "spoolwarden://printers/",
            "spoolwarden://spools/abc", "spoolwarden://spools/0", "spoolwarden://spools/-1", "spoolwarden://spools/+5",
            "spoolwarden://spools/1/2", "spoolwarden://settings/1", "spoolwarden://printers/%zz",
        )) {
            assertNull(bad, deepLinkTarget(bad))
        }
    }
}

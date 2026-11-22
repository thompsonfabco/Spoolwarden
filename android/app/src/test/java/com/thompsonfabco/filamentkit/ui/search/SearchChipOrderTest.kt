package com.thompsonfabco.filamentkit.ui.search

import org.junit.Assert.assertEquals
import org.junit.Test

class SearchChipOrderTest {
    @Test fun neitherFilter() = assertEquals(listOf(SearchChip.SealedOnly), searchChipOrder(false, false))

    @Test fun checkedOnly() = assertEquals(listOf(SearchChip.SealedOnly, SearchChip.CheckedOut), searchChipOrder(true, false))

    @Test fun lowOnly() = assertEquals(listOf(SearchChip.SealedOnly, SearchChip.RunningLow), searchChipOrder(false, true))

    @Test fun checkedOutComesBeforeRunningLow() =
        assertEquals(listOf(SearchChip.SealedOnly, SearchChip.CheckedOut, SearchChip.RunningLow), searchChipOrder(true, true))
}

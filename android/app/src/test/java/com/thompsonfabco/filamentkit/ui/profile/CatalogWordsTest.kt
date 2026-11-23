package com.thompsonfabco.filamentkit.ui.profile

import android.os.Build
import com.thompsonfabco.filamentkit.R
import org.junit.Assert.assertEquals
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config

/** The words of the removal question and the entry's labels, from the catalogue. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [Build.VERSION_CODES.S])
class CatalogWordsTest {
    private val context = RuntimeEnvironment.getApplication()

    @Test fun theRemovalTextsNameTheEntryLine() {
        val line = CatalogTexts.entryLine("SUNLU", "PLA+")
        assertEquals("Remove SUNLU PLA+ from your own entries? Slicer presets are rebuilt.", context.getString(R.string.catalog_remove_question, line))
        assertEquals("Remove SUNLU PLA+?", context.getString(R.string.catalog_remove_title, line))
        assertEquals("Remove SUNLU PLA+", context.getString(R.string.catalog_remove_entry, line))
        assertEquals("Added Nov 14, 2026", context.getString(R.string.catalog_entry_added, "Nov 14, 2026"))
    }
}

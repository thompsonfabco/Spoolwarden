package com.thompsonfabco.filamentkit.ui

import android.os.Build
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [Build.VERSION_CODES.S])
class WeightsTest {
    private val context = RuntimeEnvironment.getApplication()

    @Test fun acceptsOnlyFinitePositiveGrams() {
        assertEquals(812.0, parseWeight("812")!!, 0.0)
        assertEquals(12.5, parseWeight(" 12.5 ")!!, 0.0)
        for (bad in listOf("", " ", "abc", "0", "-1", "NaN", "Infinity", "-Infinity", "1e400")) {
            assertNull(bad, parseWeight(bad))
        }
    }

    @Test fun theWeightHintSaysWhyUpdateWeightIsGreyed() {
        assertNull(weightHint("812"))
        assertEquals("Type the weight with the spool to update it.", context.getString(weightHint(" ")!!))
        for (bad in listOf("abc", "0", "-1")) assertEquals("Type a weight in grams, more than 0.", context.getString(weightHint(bad)!!))
    }

    @Test fun tareMustBeMoreThanZero() {
        assertEquals("Empty spool weight must be more than 0 g", context.getString(tareProblem("0")!!))
        assertEquals("Empty spool weight must be more than 0 g", context.getString(tareProblem("-12")!!))
        assertEquals("Empty spool weight must be more than 0 g", context.getString(tareProblem("abc")!!))
        assertNull("blank = unknown", tareProblem(""))
        assertNull(tareProblem("  "))
        assertNull(tareProblem("213"))
        assertNull(tareProblem("0.5"))
    }

    @Test fun validWeightRejectsNonFiniteAndNonPositive() {
        assertTrue(isValidWeight(0.1))
        assertFalse(isValidWeight(0.0))
        assertFalse(isValidWeight(-2.0))
        assertFalse(isValidWeight(Double.NaN))
        assertFalse(isValidWeight(Double.POSITIVE_INFINITY))
    }
}

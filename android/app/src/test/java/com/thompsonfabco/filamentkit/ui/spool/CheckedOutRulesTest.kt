package com.thompsonfabco.filamentkit.ui.spool

import com.thompsonfabco.filamentkit.net.SpoolsAnswer
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.ZoneOffset

/** The Checked out rules: the fields are read leniently, the row says where, the home and since when. */
class CheckedOutRulesTest {
    private fun row(id: Int, out: Boolean?, home: String? = null, since: String? = null, slot: Boolean = false, inView: Boolean = true): JsonObject {
        val fields = buildJsonObject {
            if (out != null) put("checked_out", out)
            home?.let { put("home", it) }
            since?.let { put("checked_out_since", it) }
            if (slot) put("loaded", buildJsonObject { put("printer_id", "p1"); put("printer", "Workshop X1"); put("slot", "A1") })
        }
        return buildJsonObject {
            put("id", id)
            put("location", "Workshop X1 A1")
            if (inView) put("view", fields) else fields.forEach { (k, v) -> put(k, v) }
        }
    }

    @Test fun theFieldsAreReadFromTheViewOrTheSpoolItself() {
        assertEquals(CheckedOutInfo(true, "Shelf B", "2026-11-22T08:00:00+00:00"), row(1, true, "Shelf B", "2026-11-22T08:00:00+00:00").checkedOutInfo())
        assertEquals(CheckedOutInfo(false, null, null), row(1, false, inView = false).checkedOutInfo())
    }

    @Test fun anOlderHubSendsNoFieldsAndThatIsNotTheSameAsNotCheckedOut() {
        assertNull(row(1, null).checkedOutInfo())
        assertFalse(row(1, null).toSpoolSummary().checkedOut)
    }

    private fun answer(filters: List<String>?, vararg rows: JsonObject) = SpoolsAnswer(rows.toList(), null, filters)

    @Test fun theHubKnowsTheFilterOnlyWhenItsAnswerNamesIt() {
        assertTrue(CheckedOutRules.supported(listOf("sealed", "checked_out")))
        assertFalse("no filters field: an older Hub", CheckedOutRules.supported(null))
        assertFalse(CheckedOutRules.supported(listOf("sealed", "low")))
        assertFalse("an empty list is no support", CheckedOutRules.supported(emptyList()))
    }

    @Test fun theCountIsTheTotalOfTheAnswerAndFallsBackToTheRowsWithoutOne() {
        assertEquals(7, CheckedOutRules.count(SpoolsAnswer(listOf(row(1, true)), null, listOf("checked_out"), total = 7)))
        assertEquals(1, CheckedOutRules.count(answer(listOf("checked_out"), row(1, true), row(2, false))))
        assertEquals("zero spools is still support", 0, CheckedOutRules.count(answer(listOf("checked_out"))))
        assertNull(CheckedOutRules.count(answer(null, row(1, true))))
        assertNull(CheckedOutRules.count(SpoolsAnswer(emptyList(), null, null, total = 3)))
        assertNull(CheckedOutRules.count(answer(emptyList())))
    }

    @Test fun onlyCheckedOutRowsAreListed() {
        val rows = CheckedOutRules.rows(listOf(row(1, true, "Shelf B"), row(2, false), row(3, true)))
        assertEquals(listOf(1, 3), rows.map { it.id })
    }

    @Test fun aRowSaysWhereTheSpoolIsItsHomeAndSinceWhen() {
        val s = row(7, true, "Shelf B", "2026-11-22T08:05:00+00:00", slot = true).toSpoolSummary()
        assertEquals("Workshop X1 – A1", s.inSlot)
        assertEquals(
            listOf("In Workshop X1 – A1", "Home: Shelf B", "since 2026-11-22 08:05"),
            CheckedOutRules.lines(s, ZoneOffset.UTC),
        )
    }

    @Test fun anEntryWithoutSinceShowsNoTimeAndOneWithoutAHomeNoHome() {
        val s = row(7, true, slot = true).toSpoolSummary()
        assertEquals(listOf("In Workshop X1 – A1"), CheckedOutRules.lines(s, ZoneOffset.UTC))
    }

    @Test fun withoutASlotTheLocationIsWhereItIs() {
        assertEquals("In Workshop X1 A1", CheckedOutRules.place(row(7, true, "Shelf B").toSpoolSummary()))
    }

    @Test fun theActionNamesTheHome() {
        assertEquals("Return to Shelf B", CheckedOutTexts.returnTo("Shelf B"))
        assertEquals("Return", CheckedOutTexts.returnTo(null))
        assertEquals("Returned to Shelf B.", CheckedOutTexts.returned("Shelf B"))
        assertEquals("Returned.", CheckedOutTexts.returned(null))
        assertEquals("Checked out (3)", CheckedOutTexts.chip(3))
        assertEquals("Home is now Dry box 1.", CheckedOutTexts.homeNow("Dry box 1"))
    }

    @Test fun theDetailShowsHomeOnlyWhileCheckedOut() {
        val out = row(7, true, "Shelf B").toSpoolSummary()
        val back = row(7, false, "Shelf B").toSpoolSummary()
        assertEquals("Home: Shelf B", CheckedOutRules.homeLine(out))
        assertTrue(CheckedOutRules.canChangeHome(out))
        assertEquals("Home: not recorded", CheckedOutRules.homeLine(row(7, true).toSpoolSummary()))
        assertNull(CheckedOutRules.homeLine(back))
        assertFalse(CheckedOutRules.canChangeHome(back))
        assertFalse(CheckedOutRules.canReturn(back))
        assertTrue(CheckedOutRules.canReturn(out))
    }
}

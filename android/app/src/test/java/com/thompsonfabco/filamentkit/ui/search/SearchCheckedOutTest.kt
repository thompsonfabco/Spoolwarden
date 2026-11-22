package com.thompsonfabco.filamentkit.ui.search

import com.thompsonfabco.filamentkit.net.ActivityEvent
import com.thompsonfabco.filamentkit.net.ApiError
import com.thompsonfabco.filamentkit.net.SpoolsAnswer
import com.thompsonfabco.filamentkit.net.UndoAnswer
import com.thompsonfabco.filamentkit.net.UndoState
import com.thompsonfabco.filamentkit.ui.history.ActivityBackend
import com.thompsonfabco.filamentkit.ui.history.UndoFlow
import com.thompsonfabco.filamentkit.ui.history.UndoOutcome
import com.thompsonfabco.filamentkit.ui.spool.HubFailure
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/** The Checked out filter and the Return action of Spools (R88 section 1.6). */
@OptIn(ExperimentalCoroutinesApi::class)
class SearchCheckedOutTest {
    @Before fun setUp() = Dispatchers.setMain(UnconfinedTestDispatcher())
    @After fun tearDown() = Dispatchers.resetMain()

    private fun spool(id: Int, out: Boolean?, home: String? = "Shelf B"): JsonObject = buildJsonObject {
        put("id", id)
        put("location", "Workshop X1 A1")
        put("view", buildJsonObject {
            if (out != null) put("checked_out", out)
            home?.let { put("home", it) }
            put("loaded", buildJsonObject { put("printer_id", "p1"); put("printer", "Workshop X1"); put("slot", "A$id") })
        })
    }

    private var rows: List<JsonObject> = listOf(spool(1, true), spool(2, true, "Dry box 1"))
    private var filters: List<String>? = listOf("sealed", "checked_out")
    private var loadError: Exception? = null
    private var returnError: Exception? = null
    private val returned = mutableListOf<Int>()
    private var events = emptyList<ActivityEvent>()
    private val undone = mutableListOf<Long>()

    private val backend = object : ActivityBackend {
        override suspend fun activity(subject: String, limit: Int) = events
        override suspend fun undoEvent(id: Long): UndoAnswer { undone += id; return UndoAnswer(true, 99, null) }
    }

    private fun vm() = SearchViewModel(
        loadChecked = { loadError?.let { throw it }; SpoolsAnswer(rows, null, filters) },
        returnSpool = { id -> returnError?.let { throw it }; returned += id; spool(id, false) },
        undoFlow = UndoFlow(backend, now = { 1_000L }),
    ) { _, _ -> emptyList() }

    private fun event(id: Long, kind: String = "spool.returned") =
        ActivityEvent(id, "", 900L, kind, "Pixel", "", "spool:1", UndoState(true, null, null), false, null)

    @Test fun theFilterListsTheCheckedOutSpools() {
        val vm = vm()
        vm.setCheckedOnly(true)
        assertEquals(listOf(1, 2), vm.state.value.checked.map { it.id })
    }

    @Test fun aHubWithoutTheFiltersFieldTurnsTheFilterOffAndShowsNothing() {
        filters = null
        val vm = vm()
        vm.setCheckedOnly(true)
        assertFalse(vm.state.value.checkedOnly)
        assertTrue(vm.state.value.checked.isEmpty())
        assertFalse(vm.state.value.checkedLoaded)
    }

    @Test fun aHubWithoutTheRouteHidesTheChipAndATurnedOnFilterGoesOff() {
        val vm = vm()
        vm.setCheckedOnly(true)
        loadError = ApiError(404, "not_found", "Not found")
        vm.loadCheckedOut()
        assertFalse(vm.state.value.checkedOnly)
        assertNull(vm.state.value.failure)
    }

    @Test fun aHubThatNamesTheFilterWithNothingCheckedOutShowsTheEmptyList() {
        rows = emptyList()
        val vm = vm()
        vm.setCheckedOnly(true)
        assertTrue(vm.state.value.checkedLoaded)
        assertTrue(vm.state.value.checked.isEmpty())
    }

    @Test fun anOfflineHubKeepsTheListAndSaysSo() {
        val vm = vm()
        vm.setCheckedOnly(true)
        loadError = ApiError(0, "offline", "no")
        vm.loadCheckedOut()
        assertEquals(2, vm.state.value.checked.size)
        assertEquals(HubFailure.Unreachable, vm.state.value.failure)
        assertTrue(vm.state.value.checkedOnly)
    }

    @Test fun returnAsksNothingTakesTheRowOutAndOffersUndo() {
        events = listOf(event(8))
        val vm = vm()
        vm.setCheckedOnly(true)
        vm.returnToHome(vm.state.value.checked.first { it.id == 1 })
        assertEquals(listOf(1), returned)
        assertEquals(listOf(2), vm.state.value.checked.map { it.id })
        assertEquals("Returned to Shelf B.", vm.state.value.returnToast!!.text)
        assertEquals(8L, vm.state.value.returnToast!!.event!!.id)
        assertNull(vm.state.value.returningId)
    }

    @Test fun anOlderEventKindOffersNoUndo() {
        events = listOf(event(8, kind = "spool.moved"))
        val vm = vm()
        vm.setCheckedOnly(true)
        vm.returnToHome(vm.state.value.checked.first())
        assertNotNull(vm.state.value.returnToast)
        assertNull(vm.state.value.returnToast!!.event)
    }

    @Test fun undoReadsTheListAgain() = runBlocking {
        events = listOf(event(8))
        val vm = vm()
        vm.setCheckedOnly(true)
        vm.returnToHome(vm.state.value.checked.first())
        val out = vm.undoReturn(vm.state.value.returnToast!!.event!!)
        assertTrue(out is UndoOutcome.Done)
        assertEquals(listOf(8L), undone)
        assertEquals("the list was read again", 2, vm.state.value.checked.size)
    }

    @Test fun aRefusalReadsTheHubsMessageAndTheListIsReadAgain() {
        val vm = vm()
        vm.setCheckedOnly(true)
        returnError = ApiError(409, "not_checked_out", "This spool is not checked out.")
        rows = listOf(spool(2, true))
        vm.returnToHome(vm.state.value.checked.first { it.id == 1 })
        assertEquals(HubFailure.Message("This spool is not checked out."), vm.state.value.returnFailure)
        assertEquals(listOf(2), vm.state.value.checked.map { it.id })
        assertNull(vm.state.value.returnToast)
        assertNull(vm.state.value.returningId)
    }

    @Test fun withoutAReturnCallNothingHappens() {
        val vm = SearchViewModel(loadChecked = { SpoolsAnswer(rows, null, filters) }) { _, _ -> emptyList() }
        vm.setCheckedOnly(true)
        vm.returnToHome(vm.state.value.checked.first())
        assertTrue(returned.isEmpty())
        assertNull(vm.state.value.returnToast)
    }

    @Test fun theRunningLowFilterAndThisOneExcludeEachOther() {
        val vm = vm()
        vm.setCheckedOnly(true)
        vm.setLowOnly(true)
        assertFalse(vm.state.value.checkedOnly)
        vm.setCheckedOnly(true)
        assertFalse(vm.state.value.lowOnly)
    }
}

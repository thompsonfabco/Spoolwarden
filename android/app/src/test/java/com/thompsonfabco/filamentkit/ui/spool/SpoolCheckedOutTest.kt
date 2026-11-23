package com.thompsonfabco.filamentkit.ui.spool

import com.thompsonfabco.filamentkit.R
import com.thompsonfabco.filamentkit.net.ActivityEvent
import com.thompsonfabco.filamentkit.net.ApiError
import com.thompsonfabco.filamentkit.net.UndoAnswer
import com.thompsonfabco.filamentkit.net.UndoState
import com.thompsonfabco.filamentkit.ui.printers.Say
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runTest
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

/** The spool screen's Return and Home. */
@OptIn(ExperimentalCoroutinesApi::class)
class SpoolCheckedOutTest {
    private val dispatcher = StandardTestDispatcher()
    private val base = FakeSpoolBackend()
    private val calls = mutableListOf<String>()
    private var events = emptyList<ActivityEvent>()
    private var refusal: ApiError? = null
    private var returnedAt: String? = "Shelf B"

    private fun withFields(spool: JsonObject, out: Boolean, home: String?): JsonObject = buildJsonObject {
        spool.forEach { (k, v) -> put(k, v) }
        put("view", buildJsonObject { put("checked_out", out); home?.let { put("home", it) } })
    }

    private val backend = object : SpoolBackend by base {
        override suspend fun returnSpool(spoolId: Int): JsonObject {
            calls += "return:$spoolId"
            refusal?.let { throw it }
            return withFields(spoolJson(spoolId, location = returnedAt), out = false, home = null)
        }

        override suspend fun setHome(spoolId: Int, home: String): JsonObject {
            calls += "home:$spoolId:$home"
            refusal?.let { throw it }
            return withFields(spoolJson(spoolId), out = true, home = home)
        }

        override suspend fun activity(subject: String, limit: Int) = events
        override suspend fun undoEvent(id: Long): UndoAnswer { calls += "undo:$id"; return UndoAnswer(true, 99, null) }
    }

    private val out = withFields(spoolJson(214), out = true, home = "Shelf B").toSpoolSummary()

    @Before fun setUp() {
        Dispatchers.setMain(dispatcher)
        SpoolCache.clear()
    }

    @After fun tearDown() = Dispatchers.resetMain()

    private fun vm() = SpoolViewModel(214, backend, out, now = { 1_000L })

    private fun event(id: Long, kind: String = "spool.returned") =
        ActivityEvent(id, "", 900L, kind, "Pixel", "", "spool:214", UndoState(true, null, null), false, null)

    @Test fun theControlsAreOfferedOnlyWhenTheSpoolsOwnAnswerCarriesTheFields() = runTest(dispatcher) {
        // a spool opened first (a tag, a link, an alert) on a Hub that has the feature: the read of the spool says so
        val first = spoolJson(214).toSpoolSummary()
        assertFalse(CheckedOutRules.offered(first))
        base.spools = listOf(withFields(spoolJson(214), out = true, home = "Shelf B"))
        val vm = SpoolViewModel(214, base, first, now = { 1_000L })
        vm.refresh()
        advanceUntilIdle()
        assertTrue(CheckedOutRules.offered(vm.state.value.spool!!))
        assertEquals(Say.Words(R.string.spools_home, listOf("Shelf B")), CheckedOutRules.homeLine(vm.state.value.spool!!))
    }

    @Test fun aHubWithoutTheFeatureOffersNothingEvenForAnOpenSpool() = runTest(dispatcher) {
        base.spools = listOf(spoolJson(214))
        val vm = SpoolViewModel(214, base, null, now = { 1_000L })
        advanceUntilIdle()
        assertFalse(CheckedOutRules.offered(vm.state.value.spool!!))
        assertNull(CheckedOutRules.homeLine(vm.state.value.spool!!))
    }

    @Test fun aNotCheckedOutAnswerStillMeansTheHubHasTheFeature() {
        assertTrue(CheckedOutRules.offered(withFields(spoolJson(214), out = false, home = null).toSpoolSummary()))
    }

    @Test fun anotherHubsAnswerReplacesWhatTheScreenKnew() = runTest(dispatcher) {
        // the screen of one spool follows the answer it last read, so a Hub change (a new read) cannot leave the old controls
        base.spools = listOf(withFields(spoolJson(214), out = true, home = "Shelf B"))
        val vm = SpoolViewModel(214, base, null, now = { 1_000L })
        advanceUntilIdle()
        assertTrue(CheckedOutRules.offered(vm.state.value.spool!!))
        base.spools = listOf(spoolJson(214))
        vm.refresh()
        advanceUntilIdle()
        assertFalse(CheckedOutRules.offered(vm.state.value.spool!!))
    }

    @Test fun returnSendsTheSpoolHomeAndOffersUndo() = runTest(dispatcher) {
        events = listOf(event(8))
        val vm = vm()
        advanceUntilIdle()
        vm.returnToHome()
        advanceUntilIdle()
        assertEquals(listOf("return:214"), calls)
        assertFalse(vm.state.value.spool!!.checkedOut)
        assertEquals(Say.Words(R.string.spools_returned_to, listOf("Shelf B")), vm.state.value.returnToast!!.text)
        assertEquals(8L, vm.state.value.returnToast!!.event!!.id)
        assertFalse(vm.state.value.returning)
    }

    @Test fun theResultNamesTheLocationInTheAnswerOrSaysOnlyReturned() = runTest(dispatcher) {
        returnedAt = "Dry box 1"
        val vm = vm()
        vm.returnToHome()
        advanceUntilIdle()
        assertEquals(Say.Words(R.string.spools_returned_to, listOf("Dry box 1")), vm.state.value.returnToast!!.text)
        returnedAt = null
        val none = vm()
        none.returnToHome()
        advanceUntilIdle()
        assertEquals(Say.Words(R.string.spools_returned), none.state.value.returnToast!!.text)
    }

    @Test fun aRefusalReadsTheHubsMessageAndNothingChanges() = runTest(dispatcher) {
        refusal = ApiError(409, "not_checked_out", "This spool is not checked out.")
        val vm = vm()
        advanceUntilIdle()
        vm.returnToHome()
        advanceUntilIdle()
        assertEquals(HubFailure.Message("This spool is not checked out."), vm.state.value.failure)
        assertNull(vm.state.value.returnToast)
        assertFalse(vm.state.value.returning)
    }

    @Test fun aSpoolThatIsNotCheckedOutHasNoReturn() = runTest(dispatcher) {
        val vm = SpoolViewModel(214, backend, withFields(spoolJson(214), out = false, home = null).toSpoolSummary())
        advanceUntilIdle()
        vm.returnToHome()
        advanceUntilIdle()
        assertTrue(calls.isEmpty())
    }

    @Test fun undoOnTheToastPutsItBackThroughTheActivity() = runTest(dispatcher) {
        events = listOf(event(8))
        val vm = vm()
        vm.returnToHome()
        advanceUntilIdle()
        assertTrue(vm.undoOnHub(vm.state.value.returnToast!!.event!!) is com.thompsonfabco.filamentkit.ui.history.UndoOutcome.Done)
        assertTrue("undo:8" in calls)
    }

    @Test fun homeIsChosenFromTheStorageLocationsAndSavedAtOnce() = runTest(dispatcher) {
        val vm = vm()
        vm.openHome()
        advanceUntilIdle()
        assertEquals(listOf("Shelf B", "Workshop X1 A1", "Dry box 1"), vm.state.value.home!!.locations)
        vm.setHome("Dry box 1")
        advanceUntilIdle()
        assertEquals("home:214:Dry box 1", calls.last())
        assertNull(vm.state.value.home)
        assertEquals("Dry box 1", vm.state.value.spool!!.home)
        assertEquals(Say.Words(R.string.spools_home_done, listOf("Dry box 1")), vm.state.value.returnToast!!.text)
        assertNull(vm.state.value.returnToast!!.event)
    }

    @Test fun choosingTheCurrentHomeOnlyClosesTheSheet() = runTest(dispatcher) {
        val vm = vm()
        vm.openHome()
        advanceUntilIdle()
        vm.setHome("Shelf B")
        assertNull(vm.state.value.home)
        assertTrue(calls.isEmpty())
    }

    @Test fun anUnknownLocationIsRefusedInTheSheetInTheHubsWords() = runTest(dispatcher) {
        refusal = ApiError(400, "unknown_location", "There is no location called Attic.")
        val vm = vm()
        vm.openHome()
        advanceUntilIdle()
        vm.setHome("Attic")
        advanceUntilIdle()
        assertEquals(HubFailure.Message("There is no location called Attic."), vm.state.value.home!!.failure)
        assertNotNull(vm.state.value.home)
        assertNull(vm.state.value.home!!.savingTo)
    }

    @Test fun aWriteInFlightIsWaitedFor() = runTest(dispatcher) {
        val vm = vm()
        vm.openHome()
        advanceUntilIdle()
        vm.setHome("Dry box 1")
        assertTrue(vm.state.value.writing)
        advanceUntilIdle()
        assertFalse(vm.state.value.writing)
    }
}

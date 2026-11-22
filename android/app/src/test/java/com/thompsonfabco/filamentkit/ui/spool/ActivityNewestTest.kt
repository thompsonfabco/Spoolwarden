package com.thompsonfabco.filamentkit.ui.spool

import com.thompsonfabco.filamentkit.alerts.FakeHub
import com.thompsonfabco.filamentkit.net.ActivityEvent
import com.thompsonfabco.filamentkit.net.UndoAnswer
import com.thompsonfabco.filamentkit.ui.history.ActivityBackend
import com.thompsonfabco.filamentkit.ui.history.UndoFlow
import com.thompsonfabco.filamentkit.ui.history.UndoKinds
import com.thompsonfabco.filamentkit.ui.history.UndoOffer
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test
import java.time.Instant

/**
 * A spool with 15 events on a fake Hub that answers like the real one: `newest=N` gives the newest N (oldest of them
 * first), else `limit` gives the first `limit`. The app must ask for the NEWEST events (the spool's history, the Undo lookup).
 */
@OptIn(ExperimentalCoroutinesApi::class)
class ActivityNewestTest {
    private val dispatcher = StandardTestDispatcher()
    private val newestAt = Instant.parse("2026-11-22T12:00:00Z")
    private val now = newestAt.toEpochMilli() + 1_000

    /** Event 15 is the action just made (a move the Hub can undo); 1..14 are older weighings. */
    private fun eventJson(id: Int): String {
        val moved = id == 15
        val at = newestAt.minusSeconds((15L - id) * 3_600)
        return """{"id": $id, "at": "$at", "kind": "${if (moved) UndoKinds.MOVED else UndoKinds.WEIGHED}",
            "subject": {"type": "spool", "id": "214"}, "actor": {"type": "device", "name": "Pixel"}, "text": "event $id",
            "undo": {"possible": $moved}, "undone": false}"""
    }

    private val hub = FakeHub().also { h ->
        h.handler = { req ->
            val all = (1..15)
            val shown = req.url.queryParameter("newest")?.let { all.toList().takeLast(it.toInt()) }
                ?: all.toList().take(req.url.queryParameter("limit")!!.toInt())
            200 to """{"events": [${shown.joinToString(",") { eventJson(it) }}], "next": 15}"""
        }
    }
    private val api = hub.api()

    @Before fun setUp() {
        Dispatchers.setMain(dispatcher)
        SpoolCache.clear()
    }

    @After fun tearDown() = Dispatchers.resetMain()

    @Test fun theSpoolsHistoryShowsTheNewestTenEventsNewestFirst() = runTest(dispatcher) {
        val backend = object : SpoolBackend by FakeSpoolBackend() {
            // the call runs on the IO dispatcher: block until it is answered, the test dispatcher cannot wait for it
            override suspend fun activity(subject: String, limit: Int): List<ActivityEvent> = runBlocking { api.activity(subject, limit) }
        }
        val vm = SpoolViewModel(214, backend, spoolJson(214).toSpoolSummary(), null, null, now = { now })
        advanceUntilIdle()
        assertEquals((15 downTo 6).map { it.toLong() }, vm.state.value.activity.map { it.id })
    }

    @Test fun undoFindsTheNewestEventWhenTheSpoolHasFifteen() = runBlocking {
        val backend = object : ActivityBackend {
            override suspend fun activity(subject: String, limit: Int): List<ActivityEvent> = api.activity(subject, limit)
            override suspend fun undoEvent(id: Long): UndoAnswer = UndoAnswer(true, id + 1, null)
        }
        val found = UndoFlow(backend) { now }.find(UndoOffer(listOf("spool:214"), setOf(UndoKinds.MOVED)))
        assertEquals(15L, found?.id)
    }
}

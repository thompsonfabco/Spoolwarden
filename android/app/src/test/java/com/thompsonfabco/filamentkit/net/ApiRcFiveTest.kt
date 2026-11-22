package com.thompsonfabco.filamentkit.net

import com.thompsonfabco.filamentkit.alerts.FakeHub
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

/** RC5 (R85): the demo flag and language of `api/info` and `api/hub`, leaving the demo, the label drawing, `Accept-Language`. */
class ApiRcFiveTest {
    private val hub = FakeHub()
    private val api = hub.api()

    @Test fun infoReadsDemoAndLanguageAndAnOlderHubMeansNotDemo() {
        val info = Json { ignoreUnknownKeys = true }
        val on = info.decodeFromString<ServerInfo>("""{"name": "H", "version": "1", "api": 1, "demo": true, "language": "en"}""")
        assertTrue(on.demo)
        assertEquals("en", on.language)
        val old = info.decodeFromString<ServerInfo>("""{"name": "H", "version": "1", "api": 1}""")
        assertFalse(old.demo)
        assertNull(old.language)
        assertFalse(info.decodeFromString<ServerInfo>("""{"name": "H", "version": "1", "api": 1, "demo": "yes"}""").demo)
    }

    @Test fun hubReadsDemoAndLanguage() = runBlocking {
        hub.handler = { _ -> 200 to """{"name": "H", "version": "1", "demo": true, "language": "de"}""" }
        val answer = api.hub()!!
        assertTrue(answer.demo)
        assertEquals("de", answer.language)
        hub.handler = { _ -> 200 to """{"name": "H", "version": "1"}""" }
        assertFalse(api.hub()!!.demo)
        assertNull(api.hub()!!.language)
    }

    @Test fun leaveDemoPostsAndReadsTheAnswer() = runBlocking {
        hub.handler = { _ -> 200 to """{"demo": false}""" }
        assertFalse(api.leaveDemo())
        assertEquals("POST", hub.requests.single().method)
        assertEquals("/api/demo/leave", hub.requests.single().url.encodedPath)
    }

    @Test fun leaveDemoNotInDemoIsA409WithTheHubsMessage() {
        hub.handler = { _ -> 409 to """{"error": "not_in_demo", "message": "Not in the demo."}""" }
        val e = assertThrows(ApiError::class.java) { runBlocking { api.leaveDemo() } }
        assertEquals(409, e.status)
        assertEquals("not_in_demo", e.code)
        assertEquals("Not in the demo.", e.message)
    }

    @Test fun labelSvgAsksForTheSizeAndReturnsTheBytes() = runBlocking {
        hub.handler = { _ -> 200 to "<svg/>" }
        assertEquals("<svg/>", String(api.labelSvg(214, "50x30")))
        assertEquals("/api/spools/214/label.svg", hub.requests.single().url.encodedPath)
        assertEquals("50x30", hub.query(0, "size"))
    }

    @Test fun labelSvgNotFoundIsAnApiError() {
        hub.handler = { _ -> 404 to """{"error": "not_found", "message": "No such spool."}""" }
        val e = assertThrows(ApiError::class.java) { runBlocking { api.labelSvg(9, "62x29") } }
        assertEquals(404, e.status)
    }

    @Test fun everyCallCarriesTheLanguageInUse() = runBlocking {
        hub.handler = { _ -> 200 to """{"name": "H", "version": "1", "api": 1}""" }
        val german = hub.api(language = "de")
        german.info()
        german.hub()
        hub.requests.forEach { assertEquals("de", it.header("Accept-Language")) }
        assertEquals(2, hub.requests.size)
    }
}

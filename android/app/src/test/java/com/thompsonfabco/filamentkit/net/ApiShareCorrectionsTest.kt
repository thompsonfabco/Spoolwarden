package com.thompsonfabco.filamentkit.net

import com.thompsonfabco.filamentkit.ui.history.shareStateOrNull
import kotlinx.coroutines.runBlocking
import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import mockwebserver3.RecordedRequest
import okhttp3.tls.HandshakeCertificates
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test
import java.net.InetAddress

/**
 * Share corrections on the paired API (05-android-app 10.6; Hub stage V1.4). The bodies are what the Hub's handlers answer
 * (`share_corrections.state`, `reader_corrections.waiting`, the history record's `share`), with example data.
 */
class ApiShareCorrectionsTest {
    private val cert = serverCert()
    private val server = MockWebServer().also {
        it.useHttps(HandshakeCertificates.Builder().heldCertificate(cert).build().sslSocketFactory())
        it.start(InetAddress.getByName("127.0.0.1"), 0)
    }
    private val api = Api(pinnedClient(cert.fingerprint()), { server.ipUrl("/").toString().trimEnd('/') }, { "tok-123" })

    @After fun tearDown() = server.close()

    private fun json(body: String, code: Int = 200) =
        server.enqueue(MockResponse.Builder().code(code).addHeader("Content-Type", "application/json").body(body).build())

    private fun take(): RecordedRequest = server.takeRequest()

    private val waitingBody = """{"opt_in":true,"endpoint_configured":false,"count":2,"bundle_mb":0.0034,"entries":[
        {"id":"fb-1","created_at":"2026-11-15T12:00:00Z","predicted":{"vendor":"Polymakr","color_hex":"2B2B2E"},
         "corrected":{"vendor":"Polymaker","color_hex":"1F1F22"},"note":"brand is Polymaker","photos":2,
         "thumbnail_b64":"/9j/4AAQ","source":"cloud","record":"0123456789ab","later":1},
        {"id":7,"created_at":"2026-11-15T12:05:00Z","predicted":{},"corrected":{"vendor":"SUNLU"},"note":"","photos":0,
         "record":"0123456789ab.1"},
        {"created_at":"no id, so not a correction the app can remove"}],
        "bytes":3565,"capabilities":{"feedback":true,"known_from":"health"},
        "not_queued":[{"record":"fedcba987654","at":"2026-11-15T11:00:00-05:00","name":"SUNLU PLA+ Grey",
        "reason":"the Label Reader did not answer: down"}]}"""

    @Test fun theShareStateDecodesAndIsOnOnlyWhenTheSettingIsOnAndTheReaderIsNotRefusing() = runBlocking {
        json("""{"saved":true,"actual":true,"differs":false,"count":2,"reason":null}""")
        val s = api.shareCorrections()
        assertTrue(s.saved)
        assertEquals(2, s.count)
        assertTrue(s.on)
        val q = take()
        assertEquals("GET", q.method)
        assertEquals("/api/intake/share-corrections", q.url.encodedPath)
        assertEquals("Bearer tok-123", q.headers["Authorization"])
        // the Hub's setting is on but the reader does not answer: the correction is kept as "not queued", so the line stays
        assertTrue(ShareState(saved = true, reason = "no_answer").on)
        // off, or on where nothing can be kept (no reader here, an old one)
        // the reader's own answer decides: it keeps them (setting off on the Hub) or refuses them (setting on)
        assertTrue(ShareState(saved = false, actual = true, differs = true).on)
        assertFalse(ShareState(saved = true, actual = false, differs = true).on)
        assertFalse(ShareState(saved = true, reason = "off").on)
        assertFalse(ShareState(saved = true, reason = "not_installed").on)
        assertFalse(ShareState(saved = true, reason = "too_old").on)
    }

    @Test fun aHubWithoutTheRouteHasNoShareState() = runBlocking {
        json("""{"error":"not_found","message":"No such route"}""", 404)
        assertNull(api.shareStateOrNull())
        json("""{"error":"internal","message":"Spoolman is down"}""", 500)
        assertThrows(ApiError::class.java) { runBlocking { api.shareStateOrNull() } }
        Unit
    }

    @Test fun aRecordCarriesItsShareState() = runBlocking {
        json("""{"id":"0123456789ab","photos":1,"share":{"state":"not_queued","reason":"the Label Reader did not answer: down","entries":0}}""")
        val share = api.intakeRecord("0123456789ab").share!!
        assertEquals("not_queued", share.state)
        assertEquals("the Label Reader did not answer: down", share.reason)
        json("""{"id":"0123456789ab","photos":1}""")
        assertNull(api.intakeRecord("0123456789ab").share)
    }

    @Test fun theCorrectionsWaitingDecodeWithTheirSizeAndTheRecordsNotTaken() = runBlocking {
        json(waitingBody)
        val w = api.corrections()
        val q = take()
        assertEquals("GET", q.method)
        assertEquals("/api/label-reader/corrections", q.url.encodedPath)
        assertEquals(2, w.count)
        assertEquals(3565L, w.bytes)
        assertEquals(listOf("fb-1", "7"), w.entries.map { it.id })
        val e = w.entries.first()
        assertEquals("Polymakr", (e.predicted["vendor"] as kotlinx.serialization.json.JsonPrimitive).content)
        assertEquals("brand is Polymaker", e.note)
        assertEquals(2, e.photos)
        assertEquals("cloud", e.source)
        assertEquals("0123456789ab", e.record)
        assertEquals("/9j/4AAQ", e.thumbnail)
        assertNull(w.entries.last().source)
        val n = w.notQueued.single()
        assertEquals("fedcba987654", n.record)
        assertEquals("SUNLU PLA+ Grey", n.name)
        assertEquals("the Label Reader did not answer: down", n.reason)
    }

    @Test fun anEmptyListAndAReaderWithoutTheNewFieldsDecode() = runBlocking {
        json("""{"opt_in":false,"endpoint_configured":false,"count":0,"bundle_mb":0.0,"entries":[],"bytes":0,"capabilities":{}}""")
        val w = api.corrections()
        assertEquals(0, w.count)
        assertTrue(w.entries.isEmpty() && w.notQueued.isEmpty())
    }

    @Test fun theReaderNotAnsweringIsTheHubsUnreachableRefusalWithItsOwnWords() {
        json(
            """{"error":"unreachable","message":"The reader does not answer, so the corrections waiting cannot be reached. They are kept."}""",
            503,
        )
        val e = assertThrows(ApiError::class.java) { runBlocking { api.corrections() } }
        assertEquals(503, e.status)
        assertEquals("unreachable", e.code)
        assertTrue(e.message.endsWith("They are kept."))
    }

    @Test fun removeIsAPostToTheCorrectionsRemoveRoute() = runBlocking {
        json("""{"ok":true,"removed":"fb-1"}""")
        api.removeCorrection("fb-1")
        val q = take()
        assertEquals("POST", q.method)
        assertEquals("/api/label-reader/corrections/fb-1/remove", q.url.encodedPath)
        assertEquals("Bearer tok-123", q.headers["Authorization"])
        json("""{"error":"not_found","message":"That correction is not waiting any more."}""", 404)
        val e = assertThrows(ApiError::class.java) { runBlocking { api.removeCorrection("fb-1") } }
        assertEquals("not_found", e.code)
        assertEquals("That correction is not waiting any more.", e.message)
        Unit
    }
}

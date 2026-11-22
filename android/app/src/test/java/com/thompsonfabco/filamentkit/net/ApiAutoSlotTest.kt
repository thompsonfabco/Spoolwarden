package com.thompsonfabco.filamentkit.net

import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject
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

/** The Auto slot calls and fields (R83 addition, A6), built against the contract: the Hub side is not built yet. */
class ApiAutoSlotTest {
    private val cert = serverCert()
    private val server = MockWebServer().also {
        it.useHttps(HandshakeCertificates.Builder().heldCertificate(cert).build().sslSocketFactory())
        it.start(InetAddress.getByName("127.0.0.1"), 0)
    }
    private val api = Api(pinnedClient(cert.fingerprint()), { server.ipUrl("/").toString().trimEnd('/') }, { "tok-123" })
    private val lenient = Json { ignoreUnknownKeys = true }

    @After fun tearDown() = server.close()

    private fun json(body: String, code: Int = 200) =
        server.enqueue(MockResponse.Builder().code(code).addHeader("Content-Type", "application/json").body(body).build())

    private fun RecordedRequest.bodyJson(): JsonObject = Json.parseToJsonElement(body!!.utf8()).jsonObject

    private val printer = """"printer":{"id":"P1","name":"Workshop","serial":"P1","configured":true,"state":"connected"}"""

    @Test fun autoLoadDryRunReadsWatchingAndReplaces() = runBlocking {
        json("""{"auto":true,"watching":["A1","A2","B1"],"replaces":{"spool_id":4,"name":"Acme PLA"},"dry_run":true}""")
        val r = api.autoLoad("P1", 12, dryRun = true)
        assertEquals(listOf("A1", "A2", "B1"), r.watching)
        assertEquals(AutoReplaces(4, "Acme PLA"), r.replaces)
        assertTrue(r.dryRun)
        assertFalse(r.pending)
        val q = server.takeRequest()
        assertEquals("POST", q.method)
        assertEquals("/api/printers/P1/slots/auto/load", q.url.encodedPath)
        assertEquals("""{"spool_id":12,"dry_run":true}""", q.bodyJson().toString())
    }

    @Test fun autoLoadDryRunWithoutReplacesHasNone() = runBlocking {
        json("""{"auto":true,"watching":["A1","A2"],"dry_run":true}""")
        assertNull(api.autoLoad("P1", 12, dryRun = true).replaces)
    }

    @Test fun autoLoadRealReadsPendingAndMessage() = runBlocking {
        json("""{"auto":true,"pending":true,"confirmed":null,"expires_in":900,"message":"Waiting"}""")
        val r = api.autoLoad("P1", 12, dryRun = false)
        assertTrue(r.pending)
        assertEquals(900, r.expiresIn)
        assertEquals("Waiting", r.message)
        assertEquals("false", server.takeRequest().bodyJson()["dry_run"].toString())
    }

    @Test fun autoLoadReadsAnAnswerWithMissingOrOddFieldsAsDefaults() = runBlocking {
        json("""{"watching":"A1","replaces":3,"pending":"yes"}""")
        val r = api.autoLoad("P1", 12, dryRun = true)
        assertEquals(AutoLoadResult(), r)
    }

    @Test fun autoNotPossibleIs409WithTheHubsMessage() {
        json("""{"error":"auto_not_possible","message":"This printer has no slots the Hub can watch. Choose a slot."}""", 409)
        val e = assertThrows(ApiError::class.java) { runBlocking { api.autoLoad("P1", 12, dryRun = true) } }
        assertEquals(409, e.status)
        assertEquals("auto_not_possible", e.code)
        assertEquals("This printer has no slots the Hub can watch. Choose a slot.", e.autoText)
    }

    @Test fun autoChooseSendsTheSlotAndReadsALoadAnswer() = runBlocking {
        json("""{"spool":{"id":12},"slot":{"label":"A2","ams_id":0,"tray_id":1},"confirmed":false,"pending":true}""")
        val r = api.autoChoose("P1", "A2")
        assertEquals("A2", r.slot.label)
        assertTrue(r.pending)
        val q = server.takeRequest()
        assertEquals("/api/printers/P1/auto/choose", q.url.encodedPath)
        assertEquals("""{"slot":"A2"}""", q.bodyJson().toString())
    }

    @Test fun nothingToChooseIs409() {
        json("""{"error":"nothing_to_choose","message":"Nothing to choose."}""", 409)
        val e = assertThrows(ApiError::class.java) { runBlocking { api.autoChoose("P1", "A2") } }
        assertEquals("nothing_to_choose", e.code)
        assertEquals("Nothing to choose.", e.autoText)
    }

    @Test fun autoCancelReadsCancelled() = runBlocking {
        json("""{"cancelled":true}""")
        assertTrue(api.autoCancel("P1"))
        val q = server.takeRequest()
        assertEquals("POST", q.method)
        assertEquals("/api/printers/P1/auto/cancel", q.url.encodedPath)
    }

    @Test fun notWaitingIs409() {
        json("""{"error":"not_waiting","message":"Nothing is waiting."}""", 409)
        val e = assertThrows(ApiError::class.java) { runBlocking { api.autoCancel("P1") } }
        assertEquals("not_waiting", e.code)
    }

    @Test fun printerAndSlotGainTheirFlagsAndOldHubsSendNone() {
        val with = lenient.decodeFromString<Printer>("""{"id":"P1","name":"W","serial":"P1","auto_possible":true}""")
        assertTrue(with.autoPossible)
        assertFalse(lenient.decodeFromString<Printer>("""{"id":"P1","name":"W","serial":"P1"}""").autoPossible)
        assertFalse(lenient.decodeFromString<Printer>("""{"id":"P1","name":"W","serial":"P1","auto_possible":"x"}""").autoPossible)
        assertTrue(lenient.decodeFromString<AmsSlot>("""{"slot":0,"label":"A1","watchable":true}""").watchable)
        assertFalse(lenient.decodeFromString<AmsSlot>("""{"slot":0,"label":"A1"}""").watchable)
    }

    @Test fun detailReadsAWaitingAuto() {
        val d = lenient.decodeFromString<PrinterDetail>(
            """{$printer,"auto":{"spool_id":12,"spool":{"id":12,"name":"Acme PLA","material":"PLA","color_hex":"FF0000"},
               "state":"waiting","text":"Waiting for Acme PLA","expires_at":"2026-11-18T01:00:00Z"}}""",
        )
        val a = d.auto!!
        assertEquals(12, a.spoolId)
        assertEquals("Acme PLA", a.spool?.name)
        assertEquals("FF0000", a.spool?.colorHex)
        assertFalse(a.choosing)
        assertTrue(a.slots.isEmpty())
    }

    @Test fun detailReadsAChooseAutoWithItsSlots() {
        val d = lenient.decodeFromString<PrinterDetail>(
            """{$printer,"auto":{"spool_id":12,"state":"choose","slots":["A1","A3"],"text":"Two"}}""",
        )
        assertTrue(d.auto!!.choosing)
        assertEquals(listOf("A1", "A3"), d.auto!!.slots)
    }

    @Test fun detailWithoutAutoOrWithOddShapesHasNone() {
        assertNull(lenient.decodeFromString<PrinterDetail>("""{$printer}""").auto)
        assertNull(lenient.decodeFromString<PrinterDetail>("""{$printer,"auto":null}""").auto)
        assertNull(lenient.decodeFromString<PrinterDetail>("""{$printer,"auto":"yes"}""").auto)
    }
}

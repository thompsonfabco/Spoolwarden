package com.thompsonfabco.filamentkit.net

import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonObject
import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import mockwebserver3.RecordedRequest
import okhttp3.tls.HandshakeCertificates
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test
import java.net.InetAddress

class ApiAlertsTest {
    private val cert = serverCert()
    private val server = MockWebServer().also {
        it.useHttps(HandshakeCertificates.Builder().heldCertificate(cert).build().sslSocketFactory())
        it.start(InetAddress.getByName("127.0.0.1"), 0)
    }
    private val api = Api(pinnedClient(cert.fingerprint()), { server.ipUrl("/").toString().trimEnd('/') }, { "tok-123" })

    @After fun tearDown() = server.close()

    private fun json(body: String, code: Int = 200) =
        server.enqueue(MockResponse.Builder().code(code).addHeader("Content-Type", "application/json").body(body).build())

    private fun RecordedRequest.bodyJson(): JsonObject = Json.parseToJsonElement(body!!.utf8()).jsonObject

    private fun failure(block: suspend () -> Unit): ApiError =
        assertThrows(ApiError::class.java) { runBlocking { block() } }

    @Test fun alertsConfigReadsRulesAndThresholds() = runBlocking {
        json(
            """{"types":[{"key":"running_low","title":"Running low","on":true},{"key":"humidity","title":"High humidity","on":false}],""" +
                """"last_spool_only":true,"running_low_g":200,"storage_warn_percent":80,"storage_bad_percent":95,"humidity_high_pct":60}""",
        )
        val c = api.alertsConfig()
        assertEquals(listOf("running_low" to true, "humidity" to false), c.types.map { it.key to it.on })
        assertTrue(c.lastSpoolOnly)
        assertEquals(200, c.runningLowG)
        assertEquals(80, c.storageWarnPercent)
        assertEquals(95, c.storageBadPercent)
        assertEquals(60, c.humidityHighPct)
        val q = server.takeRequest()
        assertEquals("GET", q.method)
        assertEquals("/api/alerts", q.url.encodedPath)
        assertEquals("Bearer tok-123", q.headers["Authorization"])
    }

    @Test fun patchAlertsSendsChangesAndReadsUpdatedConfig() = runBlocking {
        json(
            """{"types":[{"key":"running_low","title":"Running low","on":false}],""" +
                """"last_spool_only":false,"running_low_g":250,"storage_warn_percent":85,"storage_bad_percent":95,"humidity_high_pct":55}""",
        )
        val body = buildJsonObject {
            put("last_spool_only", JsonPrimitive(false))
            put("running_low_g", JsonPrimitive(250))
            put("humidity_high_pct", JsonPrimitive(55))
        }
        val c = api.patchAlerts(body)
        assertFalse(c.lastSpoolOnly)
        assertEquals(250, c.runningLowG)
        assertEquals(55, c.humidityHighPct)
        val q = server.takeRequest()
        assertEquals("PATCH", q.method)
        assertEquals("/api/alerts", q.url.encodedPath)
        assertEquals(body, q.bodyJson())
    }

    @Test fun patchAlertsErrorKeepsHubMessageAndField() {
        json("""{"error":"bad_value","message":"Must be 10 to 95.","field":"humidity_high_pct"}""", code = 400)
        val e = failure { api.patchAlerts(buildJsonObject { put("humidity_high_pct", JsonPrimitive(120)) }) }
        assertEquals(400, e.status)
        assertEquals("Must be 10 to 95.", e.message)
        assertEquals("humidity_high_pct", (e.extra["field"] as JsonPrimitive).content)
    }
}

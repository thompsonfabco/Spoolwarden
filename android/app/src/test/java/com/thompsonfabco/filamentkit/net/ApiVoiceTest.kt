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
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test
import java.net.InetAddress

/** The three Voice calls against a fake Hub: the server is not built yet. */
class ApiVoiceTest {
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

    @Test fun voiceReadsTheValuesAndTheRange() = runBlocking {
        json("""{"station":true,"speech":false,"voice":"Piper, many speakers","speaker":12,"speaker_range":[0,903],
            "speed":120,"volume":30,"future":1}""")
        val v = api.voice()
        assertTrue(v.station)
        assertFalse("absent is false", v.remote)
        assertFalse(v.speech)
        assertEquals("Piper, many speakers", v.voice)
        assertEquals(12, v.speaker)
        assertEquals(0..903, v.speakerRange)
        assertEquals(120, v.speed)
        assertEquals(30, v.volume)
        val q = server.takeRequest()
        assertEquals("GET", q.method)
        assertEquals("/api/voice", q.url.encodedPath)
        assertEquals("Bearer tok-123", q.headers["Authorization"])
    }

    @Test fun remoteIsRead() = runBlocking {
        json("""{"station":true,"remote":true}""")
        assertTrue(api.voice().remote)
        json("""{"station":true,"remote":"yes"}""")
        assertFalse(api.voice().remote)
    }

    @Test fun aNullRangeAndMissingFieldsAreLenient() = runBlocking {
        json("""{"station":true,"speaker_range":null}""")
        val v = api.voice()
        assertNull(v.speakerRange)
        assertTrue(v.speech)
        assertEquals(100, v.speed)
        assertEquals(50, v.volume)
        json("""{"station":true,"speaker_range":[5,"x"],"speed":"fast"}""")
        assertNull(api.voice().speakerRange)
        json("""{"speech":true}""")
        assertFalse("no station field is no Weigh Station", api.voice().station)
        json("""[1,2]""")
        assertFalse(api.voice().station)
    }

    @Test fun aHubWithoutTheRouteAnswers404() {
        json("""{"error":"not_found","message":"No such route"}""", code = 404)
        assertEquals(404, failure { api.voice() }.status)
    }

    @Test fun patchSendsOnlyTheGivenKeysAndReadsTheNewValues() = runBlocking {
        json("""{"station":true,"speech":true,"voice":"v","speaker":3,"speaker_range":[0,903],"speed":90,"volume":50}""")
        val v = api.patchVoice(buildJsonObject { put("speed", JsonPrimitive(90)) })
        assertEquals(90, v.speed)
        val q = server.takeRequest()
        assertEquals("PATCH", q.method)
        assertEquals("/api/voice", q.url.encodedPath)
        assertEquals(buildJsonObject { put("speed", JsonPrimitive(90)) }, q.bodyJson())
    }

    @Test fun aRangeErrorKeepsTheHubsMessageAndField() {
        json("""{"error":"bad_value","message":"Speed is 50 to 200.","field":"speed"}""", code = 400)
        val e = failure { api.patchVoice(buildJsonObject { put("speed", JsonPrimitive(500)) }) }
        assertEquals(400, e.status)
        assertEquals("Speed is 50 to 200.", e.message)
        assertEquals("speed", (e.extra["field"] as JsonPrimitive).content)
    }

    @Test fun testSendsOnlyTheValuesGiven() = runBlocking {
        json("{}")
        api.testVoice(speaker = 7, speed = null, volume = 40)
        val q = server.takeRequest()
        assertEquals("POST", q.method)
        assertEquals("/api/voice/test", q.url.encodedPath)
        assertEquals(buildJsonObject { put("speaker", JsonPrimitive(7)); put("volume", JsonPrimitive(40)) }, q.bodyJson())
    }

    @Test fun testRefusalsCarryTheirCodeAndSentence() {
        json("""{"error":"busy","message":"The Weigh Station is in use. Try again when it is idle."}""", code = 409)
        val busy = failure { api.testVoice(null, null, null) }
        assertEquals(409, busy.status)
        assertEquals("busy", busy.code)
        json("""{"error":"no_answer","message":"The Weigh Station did not answer."}""", code = 502)
        val none = failure { api.testVoice(null, null, null) }
        assertEquals(502, none.status)
        assertEquals("no_answer", none.code)
        assertEquals("The Weigh Station did not answer.", none.message)
    }
}

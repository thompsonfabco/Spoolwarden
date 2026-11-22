package com.thompsonfabco.filamentkit.ui.settings

import com.thompsonfabco.filamentkit.net.Api
import com.thompsonfabco.filamentkit.net.FingerprintMismatch
import com.thompsonfabco.filamentkit.net.pinnedClient
import com.thompsonfabco.filamentkit.net.fingerprint
import com.thompsonfabco.filamentkit.net.ipUrl
import com.thompsonfabco.filamentkit.net.serverCert
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import okhttp3.tls.HandshakeCertificates
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.net.InetAddress
import java.util.concurrent.CopyOnWriteArrayList

/** The voice group's calls and their results against a fake Hub: the server is not built yet. */
class VoiceModelTest {
    private val cert = serverCert()
    private val server = MockWebServer().also {
        it.useHttps(HandshakeCertificates.Builder().heldCertificate(cert).build().sslSocketFactory())
        it.start(InetAddress.getByName("127.0.0.1"), 0)
    }
    private val api = Api(pinnedClient(cert.fingerprint()), { server.ipUrl("/").toString().trimEnd('/') }, { "tok" })
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
    private val problems = CopyOnWriteArrayList<Throwable>()
    private val said = CopyOnWriteArrayList<String>()
    private var current: Api? = api
    private val model = VoiceModel(scope, { current }, { problems += it })

    init {
        scope.launch { model.said.collect { said += it } }
    }

    @After fun tearDown() {
        scope.cancel()
        server.close()
    }

    private fun json(body: String, code: Int = 200) =
        server.enqueue(MockResponse.Builder().code(code).addHeader("Content-Type", "application/json").body(body).build())

    private val many = """{"station":true,"speech":true,"voice":"Piper, many speakers","speaker":5,"speaker_range":[0,903],"speed":100,"volume":50}"""

    /** The requests are answered off this thread; wait for the call to finish by asking the server for it. */
    private fun settle(check: () -> Boolean) = runBlocking {
        val end = System.currentTimeMillis() + 5_000
        while (!check() && System.currentTimeMillis() < end) kotlinx.coroutines.delay(10)
        assertTrue("did not settle", check())
    }

    @Test fun refreshShowsTheGroupForAWeighStation() {
        json(many)
        model.refresh()
        settle { model.state.value.shown }
        assertEquals(5, model.state.value.settings!!.speaker)
        assertFalse(model.state.value.offline)
    }

    @Test fun aHubWithoutTheRouteHidesTheGroup() {
        json(many)
        model.refresh()
        settle { model.state.value.shown }
        json("""{"error":"not_found","message":"No such route"}""", code = 404)
        model.refresh()
        settle { model.state.value.settings == null }
        assertFalse(model.state.value.shown)
    }

    @Test fun noWeighStationIsNoGroup() {
        json("""{"station":false}""")
        model.refresh()
        settle { model.state.value.settings != null }
        assertFalse(model.state.value.shown)
    }

    @Test fun unreachableKeepsTheLastAnswerMarkedOffline() {
        json(many)
        model.refresh()
        settle { model.state.value.shown }
        server.close()
        model.refresh()
        settle { model.state.value.offline }
        assertTrue(model.state.value.shown)
    }

    @Test fun aSaveSendsOnlyTheChangedFieldAndTakesTheAnswer() {
        json(many)
        model.refresh()
        settle { model.state.value.shown }
        json(many.replace("\"speed\":100", "\"speed\":120"))
        model.save(VoiceEdit.of(model.state.value.settings!!).copy(speed = 120))
        settle { model.state.value.settings!!.speed == 120 }
        server.takeRequest()
        val patch = server.takeRequest()
        assertEquals("PATCH", patch.method)
        assertEquals("""{"speed":120}""", patch.body!!.utf8())
    }

    @Test fun aSaveWithNothingChangedAsksNothing() {
        json(many)
        model.refresh()
        settle { model.state.value.shown }
        model.save(VoiceEdit.of(model.state.value.settings!!))
        assertEquals(1, server.requestCount)
    }

    @Test fun aRefusedSaveSaysTheHubsSentenceAndResetsTheControls() {
        json(many)
        model.refresh()
        settle { model.state.value.shown }
        json("""{"error":"bad_value","message":"Speed is 50 to 200.","field":"speed"}""", code = 400)
        model.save(VoiceEdit.of(model.state.value.settings!!).copy(speed = 500))
        settle { said.isNotEmpty() }
        assertEquals(listOf("Speed is 50 to 200."), said)
        assertEquals(1, model.state.value.revision)
        assertEquals(100, model.state.value.settings!!.speed)
    }

    @Test fun testPlaysAndSaysSo() {
        json(many)
        model.refresh()
        settle { model.state.value.shown }
        json("{}")
        model.test(VoiceEdit.of(model.state.value.settings!!).copy(speaker = 9, volume = 80))
        settle { said.isNotEmpty() }
        assertEquals(listOf(VoiceTexts.TEST_PLAYED), said)
        assertFalse(model.state.value.testing)
        server.takeRequest()
        val t = server.takeRequest()
        assertEquals("/api/voice/test", t.url.encodedPath)
        assertEquals("""{"speaker":9,"speed":100,"volume":80}""", t.body!!.utf8())
    }

    @Test fun testRefusalsAreTheHubsSentences() {
        json(many)
        model.refresh()
        settle { model.state.value.shown }
        val edit = VoiceEdit.of(model.state.value.settings!!)
        json("""{"error":"busy","message":"The Weigh Station is in use. Try again when it is idle."}""", code = 409)
        model.test(edit)
        settle { said.size == 1 }
        json("""{"error":"off","message":"Speaking is switched off."}""", code = 409)
        model.test(edit)
        settle { said.size == 2 }
        json("""{"error":"no_answer","message":"The Weigh Station did not answer."}""", code = 502)
        model.test(edit)
        settle { said.size == 3 }
        assertEquals(
            listOf(VoiceTexts.TEST_BUSY, VoiceTexts.TEST_OFF, VoiceTexts.TEST_NO_ANSWER),
            said,
        )
        assertFalse(model.state.value.testing)
        assertTrue(problems.isEmpty())
    }

    @Test fun aChangedCertificateGoesToTheSameHandlerAsEveryOtherCall() {
        json(many)
        model.refresh()
        settle { model.state.value.shown }
        // a Hub that presents another certificate: the pin refuses it
        val other = serverCert("other.local")
        server.close()
        val s2 = MockWebServer().also {
            it.useHttps(HandshakeCertificates.Builder().heldCertificate(other).build().sslSocketFactory())
            it.start(InetAddress.getByName("127.0.0.1"), 0)
        }
        try {
            val a2 = Api(pinnedClient(cert.fingerprint()), { s2.ipUrl("/").toString().trimEnd('/') }, { "tok" })
            val m2 = VoiceModel(scope, { a2 }, { problems += it })
            m2.refresh()
            settle { problems.isNotEmpty() }
            assertTrue(problems.single() is FingerprintMismatch)
            assertNull(m2.state.value.settings)
            assertNotNull(model.state.value.settings)
        } finally {
            s2.close()
        }
    }
}

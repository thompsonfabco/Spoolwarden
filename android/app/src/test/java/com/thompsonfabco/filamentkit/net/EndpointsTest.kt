package com.thompsonfabco.filamentkit.net

import kotlinx.coroutines.runBlocking
import mockwebserver3.MockWebServer
import okhttp3.tls.HeldCertificate
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.fail
import org.junit.Test
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.util.concurrent.atomic.AtomicInteger

/** All hosts share one port (as with the real server), so each test server binds its own loopback address. */
class EndpointsTest {
    private val servers = mutableListOf<MockWebServer>()
    private val pinned = serverCert("kit.local")
    private val client = pinnedClient(pinned.fingerprint())

    @After
    fun tearDown() = servers.forEach { it.close() }

    private fun server(cert: HeldCertificate, address: String, port: Int = 0) =
        httpsServer(cert, address, port).also { servers += it }

    @Test
    fun skipsDeadAndWrongCertHostsReturnsThirdAndMovesItFirst() = runBlocking {
        val good = server(pinned, "127.0.0.3")
        val wrong = server(serverCert("kit.local"), "127.0.0.2", good.port)
        // Nothing listens on 127.0.0.4: dead host.
        val endpoints = Endpoints(listOf("127.0.0.4", "127.0.0.2", "127.0.0.3"), good.port)

        val found = endpoints.firstReachable(client) { fail("discovery not needed"); emptyList() }

        assertEquals("127.0.0.3", found)
        assertEquals(listOf("127.0.0.3", "127.0.0.4", "127.0.0.2"), endpoints.hosts)
        assertEquals("/api/info", good.takeRequest().url.encodedPath)
        assertEquals(0, wrong.requestCount)
    }

    @Test
    fun fallsBackToDiscoveredHostsAndRemembersThem() = runBlocking {
        val good = server(pinned, "127.0.0.3")
        val endpoints = Endpoints(listOf("127.0.0.4"), good.port)

        val found = endpoints.firstReachable(client) { listOf("127.0.0.4", "127.0.0.3") }

        assertEquals("127.0.0.3", found)
        assertEquals(listOf("127.0.0.3", "127.0.0.4"), endpoints.hosts)
    }

    @Test
    fun returnsNullWhenNothingIsReachable() = runBlocking {
        val gone = server(pinned, "127.0.0.3")
        val port = gone.port
        gone.close()
        val endpoints = Endpoints(listOf("127.0.0.3", "127.0.0.4"), port)

        assertNull(endpoints.firstReachable(client) { emptyList() })
        assertEquals(listOf("127.0.0.3", "127.0.0.4"), endpoints.hosts)
    }

    @Test
    fun onlyWrongCertHostsSurfaceTheMismatch() {
        val other = serverCert("kit.local")
        val wrong = server(other, "127.0.0.2")
        val endpoints = Endpoints(listOf("127.0.0.4", "127.0.0.2"), wrong.port)

        val e = assertThrows(FingerprintMismatch::class.java) {
            runBlocking { endpoints.firstReachable(client) { emptyList() } }
        }
        assertEquals(pinned.fingerprint(), e.expected)
        assertEquals(other.fingerprint(), e.actual)
    }

    @Test
    fun probesPreviousWinnerFirstWithoutProbingOtherHosts() = runBlocking {
        val good = server(pinned, "127.0.0.3")
        val other = server(pinned, "127.0.0.2", good.port)
        val endpoints = Endpoints(listOf("127.0.0.3", "127.0.0.2"), good.port)

        val found = endpoints.firstReachable(client) { fail("discovery not needed"); emptyList() }

        assertEquals("127.0.0.3", found)
        assertEquals(listOf("127.0.0.3", "127.0.0.2"), endpoints.hosts)
        assertEquals(1, good.requestCount)
        assertEquals(0, other.requestCount)
    }

    @Test
    fun parallelFallbackProbesRemainingHostsAndStickyWinnerSkipsSilentHostNextTime() = runBlocking {
        val good = server(pinned, "127.0.0.3")
        // Accepts the connection and never answers: parallel fallback cancels it once 127.0.0.3 wins,
        // and the next call probes the sticky winner 127.0.0.3 first without touching 127.0.0.2.
        val silent = ServerSocket(good.port, 4, InetAddress.getByName("127.0.0.2"))
        val accepts = AtomicInteger(0)
        val acceptedSockets = mutableListOf<Socket>()
        val acceptor = Thread {
            while (!silent.isClosed) {
                try {
                    val s = silent.accept()
                    accepts.incrementAndGet()
                    synchronized(acceptedSockets) { acceptedSockets += s }
                } catch (_: Exception) {
                    break
                }
            }
        }.apply { isDaemon = true; start() }
        try {
            val endpoints = Endpoints(listOf("127.0.0.4", "127.0.0.2", "127.0.0.3"), good.port)

            val found = endpoints.firstReachable(client) { fail("discovery not needed"); emptyList() }

            assertEquals("127.0.0.3", found)
            assertEquals(listOf("127.0.0.3", "127.0.0.4", "127.0.0.2"), endpoints.hosts)
            assertEquals(1, good.requestCount)

            val acceptsBeforeSticky = accepts.get()
            val second = endpoints.firstReachable(client) { fail("discovery not needed"); emptyList() }
            assertEquals("127.0.0.3", second)
            assertEquals(2, good.requestCount)
            assertEquals(acceptsBeforeSticky, accepts.get())
        } finally {
            silent.close()
            acceptor.join(1_000)
            synchronized(acceptedSockets) { acceptedSockets.forEach { runCatching { it.close() } } }
        }
    }
}

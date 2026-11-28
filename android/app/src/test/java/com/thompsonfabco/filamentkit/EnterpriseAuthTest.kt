package com.thompsonfabco.filamentkit

import android.net.Uri
import androidx.datastore.preferences.core.PreferenceDataStoreFactory
import com.thompsonfabco.filamentkit.data.ServerConfig
import com.thompsonfabco.filamentkit.data.Settings
import com.thompsonfabco.filamentkit.data.SettingsStore
import com.thompsonfabco.filamentkit.data.UserSession
import com.thompsonfabco.filamentkit.net.Api
import com.thompsonfabco.filamentkit.net.ApiError
import com.thompsonfabco.filamentkit.net.EnterpriseCapabilities
import com.thompsonfabco.filamentkit.net.fingerprint
import com.thompsonfabco.filamentkit.net.ipUrl
import com.thompsonfabco.filamentkit.net.pinnedClient
import com.thompsonfabco.filamentkit.net.serverCert
import com.thompsonfabco.filamentkit.session.Connection
import com.thompsonfabco.filamentkit.session.PairNotice
import com.thompsonfabco.filamentkit.session.PairRouter
import com.thompsonfabco.filamentkit.session.ServerSession
import com.thompsonfabco.filamentkit.session.SessionWatcher
import com.thompsonfabco.filamentkit.ui.auth.EnterpriseLoginViewModel
import java.io.File
import java.net.InetAddress
import java.security.MessageDigest
import java.util.Base64
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import kotlinx.coroutines.withTimeout
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import okhttp3.OkHttpClient
import okhttp3.tls.HandshakeCertificates
import okhttp3.tls.HeldCertificate
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

@OptIn(ExperimentalCoroutinesApi::class)
@RunWith(RobolectricTestRunner::class)
class EnterpriseAuthTest {
    private lateinit var dir: File
    private lateinit var ioScope: CoroutineScope
    private lateinit var sessionScope: CoroutineScope
    private val servers = mutableListOf<MockWebServer>()

    @Before
    fun setUp() {
        Dispatchers.setMain(UnconfinedTestDispatcher())
        dir = kotlin.io.path.createTempDirectory("enterprise-auth-test").toFile()
        ioScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        sessionScope = CoroutineScope(SupervisorJob() + Dispatchers.Default.limitedParallelism(1))
    }

    @After
    fun tearDown() {
        servers.forEach { it.close() }
        sessionScope.cancel()
        ioScope.cancel()
        dir.deleteRecursively()
        Dispatchers.resetMain()
    }

    private fun newPlainServer(): MockWebServer = MockWebServer().apply {
        start()
        servers += this
    }

    private fun httpsSingleProbeServer(cert: HeldCertificate, address: String = "127.0.0.3"): MockWebServer =
        MockWebServer().apply {
            useHttps(HandshakeCertificates.Builder().heldCertificate(cert).build().sslSocketFactory())
            enqueue(MockResponse(code = 200, body = """{"name":"kit"}"""))
            start(InetAddress.getByName(address), 0)
            servers += this
        }

    private fun await(block: suspend () -> Unit) = runBlocking {
        withTimeout(5_000) { block() }
    }

    @Test
    fun enterpriseCapabilitiesParsedFromInfoJson() = runBlocking {
        val server = newPlainServer()
        val api = Api(OkHttpClient(), { server.ipUrl("/").toString().trimEnd('/') }, { null })

        server.enqueue(
            MockResponse(
                code = 200,
                body = """
                    {
                      "name": "Workshop",
                      "version": "1.2.3",
                      "api": 1,
                      "enterprise": {
                        "enabled": true,
                        "login_required": true,
                        "methods": ["local", "ldap", "saml"]
                      }
                    }
                """.trimIndent(),
            ),
        )

        val caps = api.enterpriseCapabilities()
        assertEquals(
            EnterpriseCapabilities(
                enabled = true,
                loginRequired = true,
                methods = listOf("local", "ldap", "saml"),
            ),
            caps,
        )
        assertEquals(caps, api.enterprise)

        server.enqueue(
            MockResponse(
                code = 200,
                body = """{"name":"Workshop","version":"1.2.3","api":1}""",
            ),
        )
        val fallback = api.enterpriseCapabilities()
        assertFalse(fallback.enabled)
        assertFalse(fallback.loginRequired)
        assertTrue(fallback.methods.isEmpty())
    }

    @Test
    fun forbidden403DoesNotClearTokenOrTriggerSessionExpired() = runBlocking {
        val cert = serverCert("kit.local")
        val fp = cert.fingerprint()
        val server = httpsSingleProbeServer(cert, "127.0.0.3")
        val router = PairRouter()
        val configs = MutableStateFlow<ServerConfig?>(
            ServerConfig(listOf("127.0.0.3"), server.port, fp, "user-tok", "Workshop"),
        )
        val saved = mutableListOf<ServerConfig>()
        val session = ServerSession(
            scope = sessionScope,
            configs = configs,
            save = {
                saved += it
                configs.value = it
            },
            discover = { emptyList() },
            router = router,
            clientFor = { pinnedFp ->
                pinnedClient(pinnedFp).newBuilder()
                    .addInterceptor(SessionWatcher(router, onOffline = {}))
                    .build()
            },
            localNetworkGranted = { true },
        )

        await { session.connection.first { it is Connection.Online } }

        server.enqueue(
            MockResponse(
                code = 403,
                body = """{"error":"forbidden","message":"Admin role required"}""",
            ),
        )
        val api = Api(
            pinnedClient(fp).newBuilder().addInterceptor(SessionWatcher(router, onOffline = {})).build(),
            { session.baseUrl() },
            { configs.value?.token },
        )

        val err = assertThrows(ApiError::class.java) { runBlocking { api.search("pla", false) } }
        assertEquals(403, err.status)
        assertEquals("forbidden", err.code)

        router.report(err)
        assertNull(router.notice.value)
        assertEquals(0, router.requests.value)
        assertEquals(0, router.loginRequests.value)
        assertEquals("user-tok", session.config.value?.token)
        assertEquals(fp, session.config.value?.fingerprint)
        assertEquals("https://127.0.0.3:${server.port}", session.baseUrl())
        assertTrue(saved.none { it.token.isBlank() })
    }

    @Test
    fun sessionExpired401TriggersLoginFlowAndPreservesHubUrlAndFingerprint() = runBlocking {
        val cert = serverCert("kit.local")
        val fp = cert.fingerprint()
        val server = httpsSingleProbeServer(cert, "127.0.0.3")
        val router = PairRouter()
        val configs = MutableStateFlow<ServerConfig?>(
            ServerConfig(listOf("127.0.0.3"), server.port, fp, "expired-tok", "Workshop"),
        )
        val session = ServerSession(
            scope = sessionScope,
            configs = configs,
            save = { configs.value = it },
            discover = { emptyList() },
            router = router,
            clientFor = { pinnedFp ->
                pinnedClient(pinnedFp).newBuilder()
                    .addInterceptor(SessionWatcher(router, onOffline = {}))
                    .build()
            },
            localNetworkGranted = { true },
        )

        await { session.connection.first { it is Connection.Online } }

        server.enqueue(
            MockResponse(
                code = 401,
                body = """{"error":"session_expired","message":"Session expired"}""",
            ),
        )
        val api = Api(
            pinnedClient(fp).newBuilder().addInterceptor(SessionWatcher(router, onOffline = {})).build(),
            { session.baseUrl() },
            { configs.value?.token },
        )

        val err = assertThrows(ApiError::class.java) { runBlocking { api.search("pla", false) } }
        assertEquals(401, err.status)
        assertEquals("session_expired", err.code)

        assertEquals(PairNotice.SessionExpired("expired-tok"), router.notice.value)
        assertEquals(1, router.loginRequests.value)
        assertEquals(0, router.pairAgainRequests.value)

        await { session.sessionExpired.first { it } }
        assertEquals("expired-tok", session.config.value?.token)
        assertTrue(!session.config.value?.token.isNullOrEmpty())
        assertEquals(fp, session.config.value?.fingerprint)
        assertEquals(listOf("127.0.0.3"), session.config.value?.endpoints)
        assertEquals(server.port, session.config.value?.port)
        assertEquals("https://127.0.0.3:${server.port}", session.baseUrl())

        session.applyUserToken("fresh-user-tok")
        await { session.config.first { it?.token == "fresh-user-tok" } }
        assertFalse(session.sessionExpired.value)
        assertEquals("fresh-user-tok", session.config.value?.token)
        assertEquals(fp, session.config.value?.fingerprint)
        assertEquals("https://127.0.0.3:${server.port}", session.baseUrl())
        assertNull(router.notice.value)
    }

    @Test
    fun pkceChallengeAndVerifierRoundTripWithEnterpriseLoginViewModel() = runBlocking {
        val server = newPlainServer()
        val baseUrl = server.ipUrl("/").toString().trimEnd('/')
        val api = Api(OkHttpClient(), { baseUrl }, { null })
        val router = PairRouter()
        val configs = MutableStateFlow<ServerConfig?>(null)
        val session = ServerSession(
            scope = sessionScope,
            configs = configs,
            save = { configs.value = it },
            discover = { emptyList() },
            router = router,
            clientFor = { OkHttpClient() },
            localNetworkGranted = { true },
        )
        val savedSessions = mutableListOf<UserSession>()
        val fakeSettings = object : SettingsStore {
            override suspend fun save(config: ServerConfig) {}
            override suspend fun clear() {}
            override suspend fun saveUserSession(session: UserSession) {
                savedSessions += session
            }
        }

        val vm = EnterpriseLoginViewModel(api, session, fakeSettings)
        val authUrl = vm.buildSamlAuthUrl("$baseUrl/")
        val parsed = Uri.parse(authUrl)
        assertEquals("/auth/saml/login", parsed.path)
        assertEquals(EnterpriseLoginViewModel.CALLBACK_URI, parsed.getQueryParameter("redirect_uri"))
        assertEquals("S256", parsed.getQueryParameter("code_challenge_method"))
        val challenge = parsed.getQueryParameter("code_challenge")
        assertNotNull(challenge)
        assertTrue(Regex("^[A-Za-z0-9_-]{43}$").matches(challenge!!))

        server.enqueue(
            MockResponse(
                code = 200,
                body = """
                    {
                      "token": "sso-tok-99",
                      "user": {"id": "u-1", "username": "alice"},
                      "role": "operator",
                      "permissions": ["spools:write"]
                    }
                """.trimIndent(),
            ),
        )

        val completed = MutableStateFlow(false)
        vm.handleSamlCallback(Uri.parse("${EnterpriseLoginViewModel.CALLBACK_URI}?code=code-123")) {
            completed.value = true
        }

        val recorded = server.takeRequest()
        assertEquals("/api/auth/exchange", recorded.url.encodedPath)
        val bodyJson = Json.parseToJsonElement(recorded.body!!.utf8()).jsonObject
        assertEquals("code-123", bodyJson["code"]?.jsonPrimitive?.content)
        val verifier = bodyJson["code_verifier"]?.jsonPrimitive?.content
        assertNotNull(verifier)
        assertTrue(Regex("^[A-Za-z0-9_-]{43}$").matches(verifier!!))

        val expectedChallenge = Base64.getUrlEncoder().withoutPadding().encodeToString(
            MessageDigest.getInstance("SHA-256").digest(verifier.toByteArray(Charsets.US_ASCII)),
        )
        assertEquals(expectedChallenge, challenge)
        await { completed.first { it } }
        assertTrue(completed.value)
        assertEquals(listOf(UserSession("alice", "operator", listOf("spools:write"))), savedSessions)
    }

    @Test
    fun userSessionSerializationRoundTripsInSettings() = runBlocking {
        val store = PreferenceDataStoreFactory.create(scope = ioScope) {
            File(dir, "enterprise_settings.preferences_pb")
        }
        val settings = Settings(store)
        assertNull(settings.userSession.first())

        val expected = UserSession(
            username = "bob",
            role = "admin",
            permissions = listOf("spools:read", "spools:write", "users:manage"),
        )
        settings.saveUserSession(expected)
        assertEquals(expected, settings.userSession.first())

        settings.clearUserSession()
        assertNull(settings.userSession.first())
    }
}

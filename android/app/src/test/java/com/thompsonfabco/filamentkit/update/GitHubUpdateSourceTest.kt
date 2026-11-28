package com.thompsonfabco.filamentkit.update

import com.sun.net.httpserver.HttpServer
import com.thompsonfabco.filamentkit.net.ApiError
import com.thompsonfabco.filamentkit.net.FingerprintMismatch
import com.thompsonfabco.filamentkit.net.ServerInfo
import java.io.File
import java.net.InetSocketAddress
import java.nio.file.Files
import java.security.MessageDigest
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import kotlinx.coroutines.withTimeout
import org.junit.After
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class GitHubUpdateSourceTest {
    private lateinit var tempDir: File
    private lateinit var server: HttpServer
    private lateinit var baseUrl: String

    @Before
    fun setUp() {
        tempDir = Files.createTempDirectory("gh-update-test").toFile()
        server = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
        server.start()
        baseUrl = "http://127.0.0.1:${server.address.port}"
    }

    @After
    fun tearDown() {
        server.stop(0)
        tempDir.deleteRecursively()
    }

    private fun sha256(bytes: ByteArray): String =
        MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }

    private fun serveBytes(path: String, bytes: ByteArray, contentType: String = "application/octet-stream") {
        server.createContext(path) { ex ->
            ex.responseHeaders.add("Content-Type", contentType)
            ex.sendResponseHeaders(200, bytes.size.toLong())
            ex.responseBody.use { it.write(bytes) }
        }
    }

    private fun serveJson(path: String, json: String) =
        serveBytes(path, json.toByteArray(Charsets.UTF_8), "application/json")

    private fun releaseJson(tag: String, includeApk: Boolean = true, sumsHash: String? = null): String {
        val assets = buildList {
            if (includeApk) {
                add("""{"name":"filament-kit.apk","browser_download_url":"$baseUrl/assets/filament-kit.apk"}""")
            }
            if (sumsHash != null) {
                add("""{"name":"SHA256SUMS","browser_download_url":"$baseUrl/assets/SHA256SUMS"}""")
            }
        }.joinToString(",")
        return """{"tag_name":"$tag","assets":[$assets]}"""
    }

    @Test
    fun infoParsesTagAndDownloadVerifiesSha256() = runBlocking {
        val apkBytes = "fake-apk-payload-0.6.12".toByteArray()
        val hash = sha256(apkBytes)
        serveJson("/repos/thompsonfabco/Spoolwarden/releases/latest", releaseJson("v0.6.12", sumsHash = hash))
        serveBytes("/assets/filament-kit.apk", apkBytes)
        serveBytes("/assets/SHA256SUMS", "$hash  filament-kit.apk\n".toByteArray(), "text/plain")

        val source = GitHubUpdateSource(apiBase = baseUrl, allowInsecureForTests = true)
        val info = source.info()
        assertEquals("GitHub", info.name)
        assertEquals("0.6.12", info.version)
        assertEquals(SUPPORTED_API, info.api)
        assertEquals("612", info.apkVersion)
        assertTrue(source.isGitHub)

        val out = File(tempDir, "downloaded.apk")
        var lastDone = -1L
        source.downloadApk(out) { done, _ -> lastDone = done }
        assertTrue(out.exists())
        assertArrayEquals(apkBytes, out.readBytes())
        assertEquals(apkBytes.size.toLong(), lastDone)
    }

    @Test
    fun downloadApkRejectsTamperedApkOnChecksumMismatch() = runBlocking {
        val realApk = "real-apk-bytes".toByteArray()
        val tamperedApk = "tampered-apk-bytes".toByteArray()
        val expectedHash = sha256(realApk)
        serveJson("/repos/thompsonfabco/Spoolwarden/releases/latest", releaseJson("v0.6.12", sumsHash = expectedHash))
        serveBytes("/assets/filament-kit.apk", tamperedApk)
        serveBytes("/assets/SHA256SUMS", "$expectedHash *filament-kit.apk\n".toByteArray(), "text/plain")

        val source = GitHubUpdateSource(apiBase = baseUrl, allowInsecureForTests = true)
        source.info()

        val out = File(tempDir, "tampered.apk")
        try {
            source.downloadApk(out) { _, _ -> }
            fail("Expected ApiError(damaged)")
        } catch (e: ApiError) {
            assertEquals("damaged", e.code)
        }
        assertFalse(out.exists())
    }

    @Test
    fun infoHandlesMissingApkAsset() = runBlocking {
        serveJson("/repos/thompsonfabco/Spoolwarden/releases/latest", releaseJson("v0.6.12", includeApk = false))

        val source = GitHubUpdateSource(apiBase = baseUrl, allowInsecureForTests = true)
        val info = source.info()
        assertEquals("0.6.12", info.version)
        assertNull(info.apkVersion)
    }

    @Test
    fun fallbackUsesHubWhenHubSucceeds() = runBlocking {
        var hubDownloads = 0
        var ghDownloads = 0
        val hub = object : UpdateSource {
            override suspend fun info() = ServerInfo("My Hub", "0.6.10", SUPPORTED_API, "610")
            override suspend fun downloadApk(to: File, onProgress: (Long, Long) -> Unit) {
                hubDownloads++
                to.writeText("from-hub")
            }
        }
        val github = object : UpdateSource {
            override val isGitHub = true
            override suspend fun info() = ServerInfo("GitHub", "0.6.12", SUPPORTED_API, "612")
            override suspend fun downloadApk(to: File, onProgress: (Long, Long) -> Unit) {
                ghDownloads++
                to.writeText("from-gh")
            }
        }

        val fallback = FallbackUpdateSource(hub = { hub }, github = github)
        val info = fallback.info()
        assertEquals("0.6.10", info.version)
        assertFalse(fallback.isGitHub)

        val out = File(tempDir, "hub.apk")
        fallback.downloadApk(out) { _, _ -> }
        assertEquals(1, hubDownloads)
        assertEquals(0, ghDownloads)
        assertEquals("from-hub", out.readText())
    }

    @Test
    fun fallbackUsesGitHubWhenHubIsNullOrOfflineOrLocalNetwork() = runBlocking {
        var ghDownloads = 0
        val github = object : UpdateSource {
            override val isGitHub = true
            override suspend fun info() = ServerInfo("GitHub", "0.6.12", SUPPORTED_API, "612")
            override suspend fun downloadApk(to: File, onProgress: (Long, Long) -> Unit) {
                ghDownloads++
                to.writeText("from-gh")
            }
        }

        val unpaired = FallbackUpdateSource(hub = { null }, github = github)
        assertEquals("0.6.12", unpaired.info().version)
        assertTrue(unpaired.isGitHub)
        unpaired.downloadApk(File(tempDir, "unpaired.apk")) { _, _ -> }
        assertEquals(1, ghDownloads)

        for (code in listOf("offline", "local_network")) {
            val failingHub = object : UpdateSource {
                override suspend fun info(): ServerInfo = throw ApiError(0, code, "unreachable")
                override suspend fun downloadApk(to: File, onProgress: (Long, Long) -> Unit) = Unit
            }
            val fallback = FallbackUpdateSource(hub = { failingHub }, github = github)
            assertEquals("0.6.12", fallback.info().version)
            assertTrue(fallback.isGitHub)
            fallback.downloadApk(File(tempDir, "$code.apk")) { _, _ -> }
        }
        assertEquals(3, ghDownloads)
    }

    @Test
    fun fallbackRethrowsFingerprintMismatchAndUnauthorized() = runBlocking {
        var ghCalled = false
        val github = object : UpdateSource {
            override val isGitHub = true
            override suspend fun info(): ServerInfo {
                ghCalled = true
                return ServerInfo("GitHub", "0.6.12", SUPPORTED_API, "612")
            }
            override suspend fun downloadApk(to: File, onProgress: (Long, Long) -> Unit) = Unit
        }

        val fpSource = FallbackUpdateSource(
            hub = {
                object : UpdateSource {
                    override suspend fun info(): ServerInfo = throw FingerprintMismatch("aa", "bb")
                    override suspend fun downloadApk(to: File, onProgress: (Long, Long) -> Unit) = Unit
                }
            },
            github = github,
        )
        try {
            fpSource.info()
            fail("Expected FingerprintMismatch")
        } catch (_: FingerprintMismatch) {
            // expected
        }
        assertFalse(ghCalled)

        val authSource = FallbackUpdateSource(
            hub = {
                object : UpdateSource {
                    override suspend fun info(): ServerInfo = throw ApiError(401, "unauthorized", "revoked")
                    override suspend fun downloadApk(to: File, onProgress: (Long, Long) -> Unit) = Unit
                }
            },
            github = github,
        )
        try {
            authSource.info()
            fail("Expected ApiError(unauthorized)")
        } catch (e: ApiError) {
            assertEquals("unauthorized", e.code)
        }
        assertFalse(ghCalled)
    }

    @Test
    fun updaterEndToEndFallsBackToGitHubWhenHubIsOffline() = runBlocking {
        Dispatchers.setMain(Dispatchers.Unconfined)
        try {
            val apkBytes = "end-to-end-apk-bytes".toByteArray()
            val hash = sha256(apkBytes)
            serveJson("/repos/thompsonfabco/Spoolwarden/releases/latest", releaseJson("v0.6.12", sumsHash = hash))
            serveBytes("/assets/filament-kit.apk", apkBytes)
            serveBytes("/assets/SHA256SUMS", "$hash  filament-kit.apk\n".toByteArray(), "text/plain")

            val offlineHub = object : UpdateSource {
                override suspend fun info(): ServerInfo = throw ApiError(0, "offline", "Hub offline")
                override suspend fun downloadApk(to: File, onProgress: (Long, Long) -> Unit) =
                    throw AssertionError("Should not download from offline Hub")
            }
            val fallback = FallbackUpdateSource(
                hub = { offlineHub },
                github = GitHubUpdateSource(apiBase = baseUrl, allowInsecureForTests = true),
            )
            val installedBytes = CompletableDeferred<ByteArray>()
            val installer = object : ApkInstall {
                override fun canInstall() = true
                override fun requestPermission() = Unit
                override suspend fun install(apk: File) {
                    installedBytes.complete(apk.readBytes())
                }
                override val confirmPending: Flow<Boolean> = flowOf(false)
                override fun showConfirm() = Unit
                override val failure: Flow<UpdateFailure?> = flowOf(null)
            }

            val updater = Updater(
                source = fallback,
                installer = installer,
                currentVersion = 610,
                apkFile = File(tempDir, "updates/filament-kit.apk"),
                onProblem = {},
                words = { it.name },
            )

            withTimeout(5_000) {
                while (!updater.state.value.checked) {
                    kotlinx.coroutines.delay(10)
                }
            }
            val checkedState = updater.state.value
            assertEquals(612, checkedState.available)
            assertEquals("0.6.12", checkedState.offeredVersion)
            assertTrue(checkedState.fromGitHub)

            updater.install()
            val received = withTimeout(5_000) { installedBytes.await() }
            assertArrayEquals(apkBytes, received)
        } finally {
            Dispatchers.resetMain()
        }
    }
}

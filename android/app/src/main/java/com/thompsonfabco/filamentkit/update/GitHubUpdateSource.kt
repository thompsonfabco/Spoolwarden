package com.thompsonfabco.filamentkit.update

import com.thompsonfabco.filamentkit.BuildConfig
import com.thompsonfabco.filamentkit.net.ApiError
import com.thompsonfabco.filamentkit.net.FingerprintMismatch
import com.thompsonfabco.filamentkit.net.ServerInfo
import java.io.File
import java.io.IOException
import java.security.MessageDigest
import java.util.Locale
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.OkHttpClient
import okhttp3.Request

/** Checks and downloads app releases directly from GitHub Releases. */
class GitHubUpdateSource(
    private val client: OkHttpClient = defaultClient(),
    private val repo: String = "thompsonfabco/Spoolwarden",
    private val apiBase: String = "https://api.github.com",
    private val allowInsecureForTests: Boolean = false,
) : UpdateSource {
    override val isGitHub: Boolean = true

    @Volatile private var latestApkUrl: String? = null
    @Volatile private var latestSumsUrl: String? = null

    override suspend fun info(): ServerInfo = withContext(Dispatchers.IO) {
        val url = "${apiBase.trimEnd('/')}/repos/$repo/releases/latest"
        val request = Request.Builder()
            .url(url)
            .header("Accept", "application/vnd.github+json")
            .header("User-Agent", "Spoolwarden-Android/${BuildConfig.VERSION_NAME}")
            .get()
            .build()

        val body = try {
            client.newCall(request).execute().use { resp ->
                if (!resp.isSuccessful) {
                    throw ApiError(resp.code, "check_failed", "GitHub returned HTTP ${resp.code}")
                }
                resp.body.string()
            }
        } catch (e: CancellationException) {
            throw e
        } catch (e: ApiError) {
            throw e
        } catch (e: IOException) {
            throw ApiError(0, "check_failed", e.message ?: "Could not reach GitHub")
        }

        val root = try {
            Json.parseToJsonElement(body).jsonObject
        } catch (e: Exception) {
            throw ApiError(200, "check_failed", e.message ?: "Invalid GitHub release JSON")
        }

        val rawTag = root["tag_name"]?.jsonPrimitive?.contentOrNull?.trim().orEmpty()
        val version = rawTag.removePrefix("v")
        val match = VERSION_RE.matchEntire(version)
            ?: throw ApiError(200, "check_failed", "Unexpected release version")
        val (major, minor, patch) = match.destructured
        val versionCode = major.toInt() * 10000 + minor.toInt() * 100 + patch.toInt()

        var apkUrl: String? = null
        var sumsUrl: String? = null
        val assets = root["assets"]?.jsonArray.orEmpty()
        for (item in assets) {
            val obj = runCatching { item.jsonObject }.getOrNull() ?: continue
            val name = obj["name"]?.jsonPrimitive?.contentOrNull ?: continue
            val downloadUrl = obj["browser_download_url"]?.jsonPrimitive?.contentOrNull ?: continue
            if (!isAllowedAssetUrl(downloadUrl)) continue
            when (name) {
                APK_ASSET_NAME -> apkUrl = downloadUrl
                SUMS_ASSET_NAME -> sumsUrl = downloadUrl
            }
        }

        latestApkUrl = apkUrl
        latestSumsUrl = sumsUrl

        ServerInfo(
            name = "GitHub",
            version = version,
            api = SUPPORTED_API,
            apkVersion = if (apkUrl != null) versionCode.toString() else null,
        )
    }

    override suspend fun downloadApk(to: File, onProgress: (Long, Long) -> Unit) {
        val apkUrl = latestApkUrl ?: run {
            info()
            latestApkUrl
        } ?: throw ApiError(404, "not_found", "No APK asset in latest GitHub release")

        val expectedHash = latestSumsUrl?.let { fetchExpectedSha256(it) }
        val part = File(to.parentFile, "${to.name}.part")
        try {
            withContext(Dispatchers.IO) {
                to.parentFile?.mkdirs()
                val request = Request.Builder()
                    .url(apkUrl)
                    .header("User-Agent", "Spoolwarden-Android/${BuildConfig.VERSION_NAME}")
                    .get()
                    .build()

                val computedHash = try {
                    client.newCall(request).execute().use { resp ->
                        if (!resp.isSuccessful) {
                            throw ApiError(resp.code, "check_failed", "GitHub returned HTTP ${resp.code}")
                        }
                        val total = resp.body.contentLength()
                        val digest = MessageDigest.getInstance("SHA-256")
                        resp.body.byteStream().use { src ->
                            try {
                                part.outputStream().use { out ->
                                    val buf = ByteArray(64 * 1024)
                                    var done = 0L
                                    while (true) {
                                        ensureActive()
                                        val n = try {
                                            src.read(buf)
                                        } catch (e: IOException) {
                                            throw ApiError(0, "offline", "The connection to GitHub was lost: ${e.message}")
                                        }
                                        if (n < 0) break
                                        out.write(buf, 0, n)
                                        digest.update(buf, 0, n)
                                        done += n
                                        onProgress(done, total)
                                    }
                                }
                            } catch (e: IOException) {
                                throw ApiError(0, "storage", e.message ?: e.javaClass.simpleName)
                            }
                        }
                        digest.digest().joinToString("") { "%02x".format(it) }
                    }
                } catch (e: CancellationException) {
                    throw e
                } catch (e: ApiError) {
                    throw e
                } catch (e: IOException) {
                    throw ApiError(0, "offline", e.message ?: "Could not reach GitHub")
                }

                if (expectedHash != null && computedHash.lowercase(Locale.ROOT) != expectedHash.lowercase(Locale.ROOT)) {
                    throw ApiError(0, "damaged", "APK checksum mismatch")
                }

                to.delete()
                if (!part.renameTo(to)) {
                    throw ApiError(0, "storage", "Could not write ${to.name}")
                }
            }
        } finally {
            part.delete()
        }
    }

    private suspend fun fetchExpectedSha256(sumsUrl: String): String? = withContext(Dispatchers.IO) {
        val request = Request.Builder()
            .url(sumsUrl)
            .header("User-Agent", "Spoolwarden-Android/${BuildConfig.VERSION_NAME}")
            .get()
            .build()
        val text = try {
            client.newCall(request).execute().use { resp ->
                if (!resp.isSuccessful) {
                    throw ApiError(resp.code, "check_failed", "GitHub returned HTTP ${resp.code}")
                }
                resp.body.byteStream().use { src ->
                    val buf = ByteArray(MAX_SUMS_BYTES)
                    var offset = 0
                    while (offset < buf.size) {
                        ensureActive()
                        val n = src.read(buf, offset, buf.size - offset)
                        if (n < 0) break
                        offset += n
                    }
                    String(buf, 0, offset, Charsets.UTF_8)
                }
            }
        } catch (e: CancellationException) {
            throw e
        } catch (e: ApiError) {
            throw e
        } catch (e: IOException) {
            throw ApiError(0, "offline", e.message ?: "Could not download SHA256SUMS")
        }
        SUMS_RE.find(text)?.groupValues?.get(1)
    }

    private fun isAllowedAssetUrl(url: String): Boolean {
        val parsed = url.toHttpUrlOrNull() ?: return false
        if (allowInsecureForTests) {
            return parsed.scheme == "http" || parsed.scheme == "https"
        }
        if (parsed.scheme != "https") return false
        val host = parsed.host.lowercase(Locale.ROOT)
        return host in ALLOWED_HOSTS || host.endsWith(".githubusercontent.com")
    }

    companion object {
        private const val APK_ASSET_NAME = "filament-kit.apk"
        private const val SUMS_ASSET_NAME = "SHA256SUMS"
        private const val MAX_SUMS_BYTES = 64 * 1024
        private val VERSION_RE = Regex("""^(0|[1-9]\d{0,3})\.(0|[1-9]\d?)\.(0|[1-9]\d?)$""")
        private val SUMS_RE = Regex("""(?m)^([0-9a-fA-F]{64}) [ *]filament-kit\.apk\s*$""")
        private val ALLOWED_HOSTS = setOf(
            "github.com",
            "objects.githubusercontent.com",
            "release-assets.githubusercontent.com",
        )

        fun defaultClient(): OkHttpClient = OkHttpClient.Builder()
            .connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(60, TimeUnit.SECONDS)
            .build()
    }
}

/** Tries the paired Hub first, falling back to GitHub Releases when unpaired or unreachable. */
class FallbackUpdateSource(
    private val hub: () -> UpdateSource?,
    private val github: UpdateSource,
) : UpdateSource {
    @Volatile private var lastFromGitHub = false

    override val isGitHub: Boolean get() = lastFromGitHub

    override suspend fun info(): ServerInfo {
        try {
            val hubInfo = hub()?.info()
            if (hubInfo != null) {
                lastFromGitHub = false
                return hubInfo
            }
        } catch (e: CancellationException) {
            throw e
        } catch (e: FingerprintMismatch) {
            throw e
        } catch (e: ApiError) {
            if (e.code == "unauthorized") throw e
        } catch (_: Exception) {
            // Fall back to GitHub for other Hub failures.
        }
        val ghInfo = github.info()
        lastFromGitHub = true
        return ghInfo
    }

    override suspend fun downloadApk(to: File, onProgress: (Long, Long) -> Unit) {
        if (lastFromGitHub) {
            github.downloadApk(to, onProgress)
        } else {
            (hub() ?: github).downloadApk(to, onProgress)
        }
    }
}

package com.thompsonfabco.filamentkit.alerts

import java.net.URLDecoder

/**
 * The place a `spoolwarden://` link opens (05-android-app 2.5, Deep links): `printers/{id}`, `spools/{id}` (a whole
 * number above 0) or `recent/{hid}`. Null for any other link, a missing or extra part, or a bad id. A plain function so it
 * runs in JVM tests.
 */
fun deepLinkTarget(link: String?): AlertTarget? {
    val rest = link?.trim()?.takeIf { it.startsWith(SCHEME, ignoreCase = true) }?.substring(SCHEME.length) ?: return null
    val parts = rest.substringBefore('#').substringBefore('?').trimEnd('/').split('/')
    if (parts.size != 2) return null
    val id = try {
        URLDecoder.decode(parts[1].replace("+", "%2B"), "UTF-8")
    } catch (_: IllegalArgumentException) {
        return null
    }.takeIf { it.isNotBlank() } ?: return null
    return when (parts[0].lowercase()) {
        "printers" -> AlertTarget.Printer(id)
        "spools" -> id.toIntOrNull()?.takeIf { it > 0 && id.all(Char::isDigit) }?.let { AlertTarget.Spool(it) }
        "recent" -> AlertTarget.Record(id)
        else -> null
    }
}

private const val SCHEME = "spoolwarden://"

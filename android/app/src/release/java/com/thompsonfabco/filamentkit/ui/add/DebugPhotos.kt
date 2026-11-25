package com.thompsonfabco.filamentkit.ui.add

import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.emptyFlow

/** Release builds have no emulator photo injection. */
object DebugPhotos {
    val photos: Flow<ByteArray> = emptyFlow()

    fun emit(@Suppress("UNUSED_PARAMETER") jpeg: ByteArray) = Unit
}

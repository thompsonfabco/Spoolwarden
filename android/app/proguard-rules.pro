# kotlinx.serialization
-keepattributes *Annotation*, InnerClasses
-dontnote kotlinx.serialization.AnnotationsKt
-keepclassmembers class kotlinx.serialization.json.** {
    *** Companion;
}
-keepclasseswithmembers class kotlinx.serialization.json.** {
    kotlinx.serialization.KSerializer serializer(...);
}
-keep,includedescriptorclasses class com.thompsonfabco.filamentkit.**$$serializer { *; }
-keepclassmembers class com.thompsonfabco.filamentkit.** {
    *** Companion;
}
-keepclasseswithmembers class com.thompsonfabco.filamentkit.** {
    kotlinx.serialization.KSerializer serializer(...);
}

# Media3 (ExoPlayer, HLS, OkHttp DataSource, UI)
-keep class androidx.media3.exoplayer.** { *; }
-keep class androidx.media3.datasource.okhttp.** { *; }
-keep class androidx.media3.ui.** { *; }
-keep class androidx.media3.common.** { *; }
-dontwarn androidx.media3.**

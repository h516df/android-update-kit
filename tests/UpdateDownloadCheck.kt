package dev.h516.update

import java.io.ByteArrayInputStream
import java.io.IOException
import java.io.InputStream
import java.net.HttpURLConnection
import java.net.URL
import java.nio.file.Files

private class DownloadTransport(body: () -> InputStream) {
    var requests = 0
    val http = UpdateHttp { url ->
        requests++
        object : HttpURLConnection(URL(url)) {
            override fun connect() {}
            override fun disconnect() {}
            override fun usingProxy() = false
            override fun getResponseCode() = 200
            override fun getInputStream() = body()
        }
    }
}

private fun expectDownloadFailure(action: () -> Unit) {
    var failed = false
    try { action() } catch (_: Exception) { failed = true }
    check(failed) { "An invalid download was accepted" }
}

fun main() {
    val directory = Files.createTempDirectory("update-download-check-").toFile()
    val apk = directory.resolve("synthetic.apk")
    val keep = directory.resolve("keep.txt").apply { writeText("SYNTHETIC_UNRELATED_FILE") }
    val bytes = "SYNTHETIC_APK_BYTES".toByteArray()
    val failures = mutableListOf<String>()
    fun cleanFailure() {
        check(!apk.exists()) { "A failed download left an APK" }
        check(directory.listFiles().orEmpty().none { it.name.endsWith(".part") })
        check(keep.readText() == "SYNTHETIC_UNRELATED_FILE")
    }
    fun scenario(name: String, action: () -> Unit) {
        try { action() } catch (error: Exception) { failures += "$name: ${error.javaClass.simpleName}" }
        finally { directory.listFiles().orEmpty().filter { it != keep }.forEach { it.delete() } }
    }
    try {
        scenario("invalid expected size stops before requesting bytes") {
            for (size in listOf(0L, UpdateConfig.MAX_APK + 1)) {
                val source = DownloadTransport { ByteArrayInputStream(bytes) }
                expectDownloadFailure { source.http.downloadApk(1, null, size, apk) {} }
                check(source.requests == 0)
                cleanFailure()
            }
        }
        scenario("short and oversized bodies never become an APK") {
            for (size in listOf(bytes.size + 1L, bytes.size - 1L)) {
                val source = DownloadTransport { ByteArrayInputStream(bytes) }
                expectDownloadFailure { source.http.downloadApk(1, null, size, apk) {} }
                cleanFailure()
            }
        }
        scenario("network failure is cleaned and retry succeeds without cache clearing") {
            val source = DownloadTransport {
                object : InputStream() {
                    var started = false
                    override fun read(): Int = throw IOException("Synthetic interruption")
                    override fun read(buffer: ByteArray, offset: Int, length: Int): Int {
                        if (started) throw IOException("Synthetic interruption")
                        started = true
                        bytes.copyInto(buffer, offset, 0, 4)
                        return 4
                    }
                }
            }
            expectDownloadFailure { source.http.downloadApk(1, null, bytes.size.toLong(), apk) {} }
            cleanFailure()
            DownloadTransport { ByteArrayInputStream(bytes) }.http.downloadApk(1, null, bytes.size.toLong(), apk) {}
            check(apk.readBytes().contentEquals(bytes))
        }
        scenario("cancelled download is cleaned and a fresh operation can retry") {
            val source = DownloadTransport { ByteArrayInputStream(bytes) }
            expectDownloadFailure {
                source.http.downloadApk(1, null, bytes.size.toLong(), apk) { source.http.cancel() }
            }
            cleanFailure()
            DownloadTransport { ByteArrayInputStream(bytes) }.http.downloadApk(1, null, bytes.size.toLong(), apk) {}
            check(apk.readBytes().contentEquals(bytes))
        }
        scenario("an existing target is preserved") {
            apk.writeText("SYNTHETIC_EXISTING_APK")
            val source = DownloadTransport { ByteArrayInputStream(bytes) }
            expectDownloadFailure { source.http.downloadApk(1, null, bytes.size.toLong(), apk) {} }
            check(apk.readText() == "SYNTHETIC_EXISTING_APK" && source.requests == 0)
        }
        scenario("complete download leaves only the final owned file") {
            DownloadTransport { ByteArrayInputStream(bytes) }.http.downloadApk(1, null, bytes.size.toLong(), apk) {}
            check(apk.readBytes().contentEquals(bytes))
            check(directory.listFiles().orEmpty().map { it.name }.toSet() == setOf(apk.name, keep.name))
        }
        check(failures.isEmpty()) { failures.joinToString("; ") }
        println("Download staging, cancellation and retry checks passed")
    } finally {
        directory.listFiles().orEmpty().forEach { it.delete() }
        check(directory.delete())
    }
}

package dev.h516.update

import java.io.ByteArrayInputStream
import java.net.HttpURLConnection
import java.net.URL
import java.nio.file.Files

private class TestConnection(url: String, private val code: Int = 200,
                             private val bytes: ByteArray = "[]".toByteArray(),
                             private val redirect: String? = null) : HttpURLConnection(URL(url)) {
    override fun connect() {}
    override fun disconnect() {}
    override fun usingProxy() = false
    override fun getResponseCode() = code
    override fun getInputStream() = ByteArrayInputStream(bytes)
    override fun getHeaderField(name: String): String? = if (name == "Location") redirect else null
}

private fun failure(action: () -> Unit): UpdateFailure {
    try {
        action()
        error("Expected a user-facing failure")
    } catch (error: UpdateFailure) {
        return error
    }
}

fun main() {
    var authCalls = 0
    val publicToken = updateAccessToken(true) { authCalls++; error("Public updates must not request authorization") }
    check(publicToken == null && authCalls == 0)
    val privateToken = updateAccessToken(false) { authCalls++; "SYNTHETIC_PRIVATE_ACCESS" }
    check(privateToken == "SYNTHETIC_PRIVATE_ACCESS" && authCalls == 1)
    check(failure { updateAccessToken(false) { throw UpdateFailure("Private authorization required") } }.userMessage.contains("required"))

    val requests = mutableListOf<TestConnection>()
    val metadata = UpdateHttp { url -> TestConnection(url).also { requests += it } }
    check(metadata.json("/releases", publicToken) == "[]")
    check(requests.single().getRequestProperty("Authorization") == null)
    metadata.json("/releases", privateToken)
    check(requests.last().getRequestProperty("Authorization") == "Bearer $privateToken")

    for (token in listOf(publicToken, privateToken)) {
        val redirects = mutableListOf<TestConnection>()
        val asset = UpdateHttp { url ->
            (if (redirects.isEmpty()) TestConnection(url, 302, redirect = "https://release-assets.githubusercontent.com/synthetic")
             else TestConnection(url, bytes = "synthetic notes".toByteArray())).also { redirects += it }
        }
        check(asset.assetText(1, token) == "synthetic notes")
        check(redirects.first().getRequestProperty("Authorization") == token?.let { "Bearer $it" })
        check(redirects.last().getRequestProperty("Authorization") == null)
    }

    val bytes = "SYNTHETIC_APK_BYTES".toByteArray()
    val directory = Files.createTempDirectory("update-access-")
    val apk = directory.resolve("synthetic.apk").toFile()
    try {
        val binaryRequests = mutableListOf<TestConnection>()
        val binary = UpdateHttp { url -> TestConnection(url, bytes = bytes).also { binaryRequests += it } }
        binary.downloadApk(2, publicToken, bytes.size.toLong(), apk) {}
        check(apk.readBytes().contentEquals(bytes))
        check(binaryRequests.single().getRequestProperty("Authorization") == null)
    } finally {
        apk.delete()
        Files.delete(directory)
    }
    val denied = UpdateHttp { url -> TestConnection(url, 403) }
    check(!failure { denied.json("/releases", publicToken) }.userMessage.contains("授权"))
    check(failure { denied.json("/releases", privateToken) }.userMessage.contains("授权"))
    val blocked = UpdateHttp { url -> TestConnection(url, 302, redirect = "https://unrelated.invalid/asset") }
    failure { blocked.assetText(1, publicToken) }
    check(authCalls == 1)
    println("Public/private update access and transport checks passed")
}

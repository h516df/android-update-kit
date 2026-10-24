package dev.h516.update

import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.InputStream
import java.net.HttpURLConnection
import java.net.URI
import java.net.URLEncoder

/** 设备授权第一步拿到的东西：给你一个 8 位码，等你在浏览器里确认。 */
internal class UpdateGrant(
    val userCode: String,
    val deviceCode: String,
    val expiresAt: Long,
    val intervalSeconds: Int,
) {
    companion object {
        fun decode(json: JSONObject, now: Long): UpdateGrant {
            if (json.has("error")) throw UpdateFailure(oauthMessage(json.getString("error")))
            requireUpdate(json.getString("verification_uri") == UpdateConfig.VERIFY_URI, "授权地址不对劲，已停止")
            val userCode = json.getString("user_code")
            val deviceCode = json.getString("device_code")
            requireUpdate(
                userCode.matches(Regex("[A-Z0-9]{4}-[A-Z0-9]{4}")) && deviceCode.matches(Regex("[A-Za-z0-9]{40}")),
                "授权码不对劲，请重试",
            )
            val expiresIn = json.getInt("expires_in")
            val interval = json.getInt("interval")
            requireUpdate(expiresIn in 30..900 && interval in 5..60, "授权有效期不对劲，请重试")
            return UpdateGrant(userCode, deviceCode, now + expiresIn * 1000L, interval)
        }
    }
}

/** 轮询授权结果。 */
internal sealed class UpdatePoll {
    class Pending(val nextIntervalSeconds: Int) : UpdatePoll()
    class Approved(val authorization: UpdateAuthorization) : UpdatePoll()

    companion object {
        fun decode(json: JSONObject, previousInterval: Int, now: Long): UpdatePoll =
            when (json.optString("error")) {
                "authorization_pending" -> Pending(previousInterval)
                "slow_down" -> Pending(
                    maxOf(previousInterval + 5, json.optInt("interval", previousInterval + 5)).coerceAtMost(900)
                )
                "" -> Approved(UpdateAuthorization.fromResponse(json, now))
                else -> throw UpdateFailure(oauthMessage(json.getString("error")))
            }
    }
}

private fun oauthMessage(error: String): String = when (error) {
    "access_denied" -> "你取消了授权，随时可以再点检查更新"
    "expired_token" -> "授权码过期了，请重新点检查更新"
    "device_flow_disabled" -> "这个授权方式被关掉了，请联系开发者"
    "incorrect_client_credentials" -> "授权配置不对，请联系开发者"
    else -> "授权没成功，请稍后重试"
}

/**
 * 所有联网都从这里走。目的地写死：GitHub 的登录/接口/资产域名。
 * 跳转不继承令牌，错误响应体不打印，任何一步都能取消。
 */
internal class UpdateHttp(
    private val connect: (String) -> HttpURLConnection = { URI(it).toURL().openConnection() as HttpURLConnection },
) {

    @Volatile private var connection: HttpURLConnection? = null
    @Volatile private var cancelled = false

    fun cancel() {
        cancelled = true
        connection?.disconnect()
    }

    private fun ensureAlive() {
        if (cancelled || Thread.currentThread().isInterrupted) throw InterruptedException()
    }

    private fun open(url: String, token: String?, binary: Boolean): HttpURLConnection {
        ensureAlive()
        val c = connect(url)
        connection = c
        c.instanceFollowRedirects = false
        c.connectTimeout = 15_000
        c.readTimeout = 20_000
        c.setRequestProperty("User-Agent", UpdateConfig.APK_PREFIX + "-updater")
        c.setRequestProperty(
            "Accept",
            if (binary) "application/octet-stream" else "application/vnd.github+json",
        )
        if (token != null) {
            requireUpdate(UpdateConfig.apiUrl(url), "更新地址不可信，已停止")
            c.setRequestProperty("Authorization", "Bearer $token")
        }
        c.setRequestProperty("X-GitHub-Api-Version", "2026-03-10")
        return c
    }

    /** 只允许 GitHub 的两个登录端点，别的路径直接拒绝。 */
    fun oauth(path: String, values: Map<String, String>): JSONObject {
        requireUpdate(path in setOf("device/code", "oauth/access_token"), "授权地址不可信，已停止")
        val c = open("https://github.com/login/$path", null, binary = false)
        try {
            c.requestMethod = "POST"
            c.doOutput = true
            c.setRequestProperty("Accept", "application/json")
            c.setRequestProperty("Content-Type", "application/x-www-form-urlencoded")
            val body = values.entries.joinToString("&") { (k, v) ->
                "${URLEncoder.encode(k, "UTF-8")}=${URLEncoder.encode(v, "UTF-8")}"
            }
            c.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
            status(c.responseCode)
            return JSONObject(c.inputStream.use { String(readBounded(it, 64 * 1024), Charsets.UTF_8) })
        } finally {
            c.disconnect()
            connection = null
        }
    }

    fun json(path: String, token: String?): String {
        val url = UpdateConfig.API + path
        requireUpdate(UpdateConfig.apiUrl(url), "更新地址不可信，已停止")
        val c = open(url, token, binary = false)
        try {
            status(c.responseCode, authenticated = token != null)
            return c.inputStream.use { String(readBounded(it, 2 * 1024 * 1024), Charsets.UTF_8) }
        } finally {
            c.disconnect()
            connection = null
        }
    }

    /** 下载资产（安装包或校验文件）。跳转后不带令牌，且只认 GitHub 的资产域名。 */
    fun <T> asset(id: Long, token: String?, read: (InputStream) -> T): T {
        requireUpdate(id > 0, "更新资料不完整，请稍后重试")
        var url = "${UpdateConfig.API}/releases/assets/$id"
        var authorization: String? = token
        repeat(4) {
            val c = open(url, authorization, binary = true)
            try {
                val code = c.responseCode
                if (code in setOf(301, 302, 303, 307, 308)) {
                    val next = c.getHeaderField("Location").orEmpty()
                    requireUpdate(UpdateConfig.assetRedirect(next), "更新下载地址不可信，已停止下载")
                    url = next
                    authorization = null
                } else {
                    status(code, authenticated = token != null)
                    return c.inputStream.use(read)
                }
            } finally {
                c.disconnect()
                connection = null
            }
        }
        throw UpdateFailure("更新下载地址异常，请稍后重试")
    }

    /** 读一个小的文本资产（更新说明、SHA256 文件）。 */
    fun assetText(id: Long, token: String?): String = asset(id, token) { input ->
        String(readBounded(input, 512 * 1024), Charsets.UTF_8)
    }

    /** 下载安装包到指定文件，边下边报进度（0-100）。大小必须和发布资料一致。 */
    fun downloadApk(id: Long, token: String?, expectedSize: Long, dest: File, onProgress: (Int) -> Unit) {
        requireUpdate(expectedSize in 1..UpdateConfig.MAX_APK, "安装包大小异常，请重新检查更新")
        requireUpdate(!dest.exists(), "安装包暂存位置被占用，请重新检查更新")
        val staged = File.createTempFile(".update-", ".part", dest.absoluteFile.parentFile)
        try {
            asset(id, token) { input ->
                staged.outputStream().use { out ->
                    val buffer = ByteArray(64 * 1024)
                    var written = 0L
                    var lastPercent = -1
                    while (true) {
                        ensureAlive()
                        val n = input.read(buffer)
                        if (n < 0) break
                        requireUpdate(written + n <= expectedSize, "安装包大小与发布资料不符，已停止下载")
                        out.write(buffer, 0, n)
                        written += n
                        val percent = (written * 100 / expectedSize).toInt().coerceIn(0, 100)
                        if (percent != lastPercent) {
                            lastPercent = percent
                            onProgress(percent)
                        }
                    }
                    requireUpdate(written == expectedSize, "安装包下载不完整，请重试")
                }
            }
            ensureAlive()
            requireUpdate(!dest.exists() && staged.renameTo(dest), "保存安装包失败，请重试")
        } finally {
            staged.delete()
        }
    }

    private fun status(code: Int, authenticated: Boolean = true) {
        if (code in 200..299) return
        throw UpdateFailure(
            when (code) {
                401, 403 -> if (authenticated) "授权失效或权限不足，请重新授权" else "暂时无法读取公开更新，请稍后重试"
                429 -> "检查更新过于频繁，请稍后重试"
                404 -> "没找到更新资料，可能这个版本还没发布"
                in 500..599 -> "GitHub 暂时连不上，请稍后重试"
                else -> "检查更新失败，请稍后重试"
            }
        )
    }

    private fun readBounded(input: InputStream, max: Int): ByteArray {
        val out = ByteArrayOutputStream()
        val buffer = ByteArray(16 * 1024)
        var total = 0
        while (true) {
            ensureAlive()
            val n = input.read(buffer)
            if (n < 0) break
            total += n
            requireUpdate(total <= max, "更新资料异常偏大，已停止")
            out.write(buffer, 0, n)
        }
        return out.toByteArray()
    }
}

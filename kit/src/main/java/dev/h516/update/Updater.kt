package dev.h516.update

import android.content.Context
import android.content.Intent
import android.os.Handler
import android.os.Looper
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.core.content.FileProvider
import java.io.File
import java.util.concurrent.Executors

/** 更新界面要显示的状态。 */
sealed class UpdateState {
    object Idle : UpdateState()
    object Checking : UpdateState()
    class NeedAuth(val userCode: String, val verifyUri: String) : UpdateState()
    class Latest(val notes: ReleaseNotes?) : UpdateState()
    class Available(val candidate: UpdateCandidate) : UpdateState()
    class Downloading(val percent: Int) : UpdateState()
    class ReadyToInstall(val file: File) : UpdateState()
    class Failed(val message: String) : UpdateState()
}

/**
 * 检查更新 → 下载 → 校验 → 交给系统安装。
 *
 * 联网、授权、下载都在后台线程，状态回主线程；任何一步失败都只影响这次更新，旧版本继续用。
 */
class Updater(private val context: Context) {

    private val http = UpdateHttp()
    private val tokens = UpdateTokenStore(context)
    private val io = Executors.newSingleThreadExecutor { r ->
        Thread(r, "jiaoban-update").apply { isDaemon = true }
    }
    private val main = Handler(Looper.getMainLooper())

    var state by mutableStateOf<UpdateState>(UpdateState.Idle)
        private set

    private var candidate: UpdateCandidate? = null
    @Volatile private var cancelled = false

    /** 当前版本的更新说明，打进 App 里，没网也能看。 */
    fun currentNotes(): ReleaseNotes? = runCatching {
        context.assets.open("release-notes.json").use {
            ReleaseNotes.decode(String(it.readBytes(), Charsets.UTF_8))
        }
    }.getOrNull()

    fun installedVersionName(): String = runCatching {
        context.packageManager.getPackageInfo(UpdateConfig.PACKAGE, 0).versionName.orEmpty()
    }.getOrDefault("")

    fun installedVersionCode(): Int = runCatching { UpdateVerify.installedVersionCode(context) }.getOrDefault(0)

    fun check() {
        if (state is UpdateState.Checking || state is UpdateState.Downloading) return
        cancelled = false
        candidate = null
        set(UpdateState.Checking)
        io.execute {
            try {
                val token = updateAccessToken(UpdateConfig.PUBLIC_RELEASES) { tokenOrAuthorize() }
                val releases = http.json("/releases?per_page=20", token)
                val published = UpdateCatalog.parse(releases)
                val newest = UpdateCatalog.newest(published)
                    ?: throw UpdateFailure("还没发布过带更新说明的版本，请稍后再试")
                val notes = ReleaseNotes.decode(http.assetText(newest.notesAsset.id, token))
                val installed = UpdateVerify.installedVersionCode(context)
                when (val result = UpdateCatalog.decide(notes, newest.assets, installed, UpdateVerify.deviceAbis())) {
                    is UpdateCatalog.Result.Latest -> set(UpdateState.Latest(result.notes))
                    is UpdateCatalog.Result.Available -> {
                        candidate = result.candidate
                        set(UpdateState.Available(result.candidate))
                    }
                }
            } catch (e: UpdateFailure) {
                set(UpdateState.Failed(e.userMessage))
            } catch (e: InterruptedException) {
                set(UpdateState.Idle)
            } catch (e: Exception) {
                set(UpdateState.Failed("检查更新失败，请稍后重试"))
            }
        }
    }

    fun download() {
        val target = candidate ?: return
        if (state is UpdateState.Downloading) return
        cancelled = false
        set(UpdateState.Downloading(0))
        io.execute {
            try {
                val token = updateAccessToken(UpdateConfig.PUBLIC_RELEASES) {
                    tokens.load()?.access ?: throw UpdateFailure("授权失效了，请重新检查更新")
                }
                val dir = File(context.cacheDir, "updates").apply { mkdirs() }
                dir.listFiles()?.forEach { it.delete() }
                val dest = File(dir, target.apk.name)
                http.downloadApk(target.apk.id, token, target.apk.size, dest) { percent ->
                    set(UpdateState.Downloading(percent))
                }
                val expectedSha = target.shaFile?.let { UpdateVerify.parseShaFile(http.assetText(it.id, token)) }
                    ?: target.apk.apiDigest
                    ?: throw UpdateFailure("发布资料里缺校验值，请稍后再试")
                UpdateVerify.verifyHash(dest, target.apk.size, expectedSha, target.apk.apiDigest)
                UpdateVerify.verifyArchive(context, dest, target.notes.code)
                set(UpdateState.ReadyToInstall(dest))
            } catch (e: UpdateFailure) {
                set(UpdateState.Failed(e.userMessage))
            } catch (e: InterruptedException) {
                set(UpdateState.Idle)
            } catch (e: Exception) {
                set(UpdateState.Failed("下载或校验失败，请稍后重试"))
            }
        }
    }

    /** 交给系统安装器；装不装由你在系统界面上确认，App 不会偷偷装。 */
    fun install() {
        val ready = state as? UpdateState.ReadyToInstall ?: return
        try {
            val uri = FileProvider.getUriForFile(context, "${UpdateConfig.PACKAGE}.updates", ready.file)
            val intent = Intent(Intent.ACTION_VIEW).apply {
                setDataAndType(uri, "application/vnd.android.package-archive")
                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
            }
            context.startActivity(intent)
        } catch (e: Exception) {
            set(UpdateState.Failed("打不开系统安装界面，请稍后重试"))
        }
    }

    /** 忘掉这台手机上的授权（令牌存在手机里，随时可以清掉）。 */
    fun forgetAuthorization() {
        tokens.clear()
        candidate = null
        set(UpdateState.Idle)
    }

    fun hasAuthorization(): Boolean = tokens.load() != null

    fun cancel() {
        cancelled = true
        http.cancel()
    }

    private fun set(value: UpdateState) {
        main.post { state = value }
    }

    private fun tokenOrAuthorize(): String {
        val now = System.currentTimeMillis()
        val stored = tokens.load()
        if (stored != null) {
            if (!stored.expired(now)) return stored.access
            if (!stored.refreshExpired(now)) {
                runCatching { return refresh(stored.refresh) }
            }
            tokens.clear()
        }
        return authorize()
    }

    private fun refresh(refreshToken: String): String {
        val json = http.oauth(
            "oauth/access_token",
            mapOf(
                "client_id" to UpdateConfig.CLIENT_ID,
                "grant_type" to "refresh_token",
                "refresh_token" to refreshToken,
            ),
        )
        val authorization = UpdateAuthorization.fromResponse(json, System.currentTimeMillis())
        tokens.save(authorization)
        return authorization.access
    }

    /** 设备授权：给你一个码，你在浏览器里输一次，并勾选 Jiaoban 这个仓库。 */
    private fun authorize(): String {
        val grant = UpdateGrant.decode(
            http.oauth("device/code", mapOf("client_id" to UpdateConfig.CLIENT_ID)),
            System.currentTimeMillis(),
        )
        set(UpdateState.NeedAuth(grant.userCode, UpdateConfig.VERIFY_URI))
        var interval = grant.intervalSeconds
        while (System.currentTimeMillis() < grant.expiresAt) {
            if (cancelled) throw InterruptedException()
            Thread.sleep(interval * 1000L)
            val poll = UpdatePoll.decode(
                http.oauth(
                    "oauth/access_token",
                    mapOf(
                        "client_id" to UpdateConfig.CLIENT_ID,
                        "device_code" to grant.deviceCode,
                        "grant_type" to "urn:ietf:params:oauth:grant-type:device_code",
                    ),
                ),
                interval,
                System.currentTimeMillis(),
            )
            when (poll) {
                is UpdatePoll.Pending -> interval = poll.nextIntervalSeconds
                is UpdatePoll.Approved -> {
                    tokens.save(poll.authorization)
                    return poll.authorization.access
                }
            }
        }
        throw UpdateFailure("授权超时了，请重新点检查更新")
    }
}


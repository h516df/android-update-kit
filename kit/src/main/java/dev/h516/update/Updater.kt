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

    private val runs = UpdateRuns()
    private val tokens = UpdateTokenStore(context)
    private val io = Executors.newSingleThreadExecutor { r ->
        Thread(r, "jiaoban-update").apply { isDaemon = true }
    }
    private val main = Handler(Looper.getMainLooper())

    var state by mutableStateOf<UpdateState>(UpdateState.Idle)
        private set

    private var candidate: UpdateCandidate? = null

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
        val run = runs.start()
        val http = run.http
        candidate = null
        set(UpdateState.Checking, run)
        run.attach(io.submit {
            try {
                run.ensureAlive()
                val token = updateAccessToken(UpdateConfig.PUBLIC_RELEASES) { tokenOrAuthorize(run) }
                val releases = http.json("/releases?per_page=20", token)
                val published = UpdateCatalog.parse(releases)
                val newest = UpdateCatalog.newest(published)
                    ?: throw UpdateFailure("还没发布过带更新说明的版本，请稍后再试")
                val notes = ReleaseNotes.decode(http.assetText(newest.notesAsset.id, token))
                val installed = UpdateVerify.installedVersionCode(context)
                when (val result = UpdateCatalog.decide(notes, newest.assets, installed, UpdateVerify.deviceAbis())) {
                    is UpdateCatalog.Result.Latest -> set(UpdateState.Latest(result.notes), run)
                    is UpdateCatalog.Result.Available -> {
                        runs.withCurrent(run) { candidate = result.candidate }
                        set(UpdateState.Available(result.candidate), run)
                    }
                }
            } catch (e: UpdateFailure) {
                set(UpdateState.Failed(e.userMessage), run)
            } catch (e: InterruptedException) {
                set(UpdateState.Idle, run)
            } catch (e: Exception) {
                set(UpdateState.Failed("检查更新失败，请稍后重试"), run)
            }
        })
    }

    fun download() {
        val target = candidate ?: return
        if (state is UpdateState.Downloading) return
        val run = runs.start()
        val http = run.http
        set(UpdateState.Downloading(0), run)
        run.attach(io.submit {
            try {
                run.ensureAlive()
                val token = updateAccessToken(UpdateConfig.PUBLIC_RELEASES) {
                    tokens.load()?.access ?: throw UpdateFailure("授权失效了，请重新检查更新")
                }
                val dir = File(context.cacheDir, "updates").apply { mkdirs() }
                dir.listFiles()?.forEach { it.delete() }
                val dest = File(dir, target.apk.name)
                http.downloadApk(target.apk.id, token, target.apk.size, dest) { percent ->
                    set(UpdateState.Downloading(percent), run)
                }
                val expectedSha = target.shaFile?.let { UpdateVerify.parseShaFile(http.assetText(it.id, token)) }
                    ?: target.apk.apiDigest
                    ?: throw UpdateFailure("发布资料里缺校验值，请稍后再试")
                UpdateVerify.verifyHash(dest, target.apk.size, expectedSha, target.apk.apiDigest)
                UpdateVerify.verifyArchive(context, dest, target.notes.code)
                run.ensureAlive()
                set(UpdateState.ReadyToInstall(dest), run)
            } catch (e: UpdateFailure) {
                set(UpdateState.Failed(e.userMessage), run)
            } catch (e: InterruptedException) {
                set(UpdateState.Idle, run)
            } catch (e: Exception) {
                set(UpdateState.Failed("下载或校验失败，请稍后重试"), run)
            }
        })
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
        cancel()
        tokens.clear()
    }

    fun hasAuthorization(): Boolean = tokens.load() != null

    fun cancel() {
        runs.cancel()
        candidate = null
        set(UpdateState.Idle, null)
    }

    private fun set(value: UpdateState, run: UpdateRun? = runs.current) {
        main.post { if (runs.isCurrent(run)) state = value }
    }

    private fun tokenOrAuthorize(run: UpdateRun): String {
        run.ensureAlive()
        val now = System.currentTimeMillis()
        val stored = tokens.load()
        if (stored != null) {
            if (!stored.expired(now)) return stored.access
            if (!stored.refreshExpired(now)) {
                try {
                    return refresh(stored.refresh, run)
                } catch (cancelled: InterruptedException) {
                    throw cancelled
                } catch (_: Exception) {
                    run.ensureAlive()
                }
            }
            runs.withCurrent(run) { tokens.clear() }
        }
        return authorize(run)
    }

    private fun refresh(refreshToken: String, run: UpdateRun): String {
        val json = run.http.oauth(
            "oauth/access_token",
            mapOf(
                "client_id" to UpdateConfig.CLIENT_ID,
                "grant_type" to "refresh_token",
                "refresh_token" to refreshToken,
            ),
        )
        val authorization = UpdateAuthorization.fromResponse(json, System.currentTimeMillis())
        runs.withCurrent(run) { tokens.save(authorization) }
        return authorization.access
    }

    /** 设备授权：给你一个码，你在浏览器里输一次，并勾选 Jiaoban 这个仓库。 */
    private fun authorize(run: UpdateRun): String {
        val http = run.http
        run.ensureAlive()
        val grant = UpdateGrant.decode(
            http.oauth("device/code", mapOf("client_id" to UpdateConfig.CLIENT_ID)),
            System.currentTimeMillis(),
        )
        set(UpdateState.NeedAuth(grant.userCode, UpdateConfig.VERIFY_URI), run)
        var interval = grant.intervalSeconds
        while (System.currentTimeMillis() < grant.expiresAt) {
            run.ensureAlive()
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
                    runs.withCurrent(run) { tokens.save(poll.authorization) }
                    return poll.authorization.access
                }
            }
        }
        throw UpdateFailure("授权超时了，请重新点检查更新")
    }
}


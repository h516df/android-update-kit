package dev.h516.update

import android.content.Context
import android.content.pm.PackageInfo
import android.content.pm.PackageManager
import android.os.Build
import java.io.File
import java.security.MessageDigest
import java.util.zip.ZipFile

/**
 * 装之前必须过完这一关：大小、SHA256、包名、版本号、签名、CPU 架构。
 * 任何一条不过就停下来，旧版本继续用。
 */
internal object UpdateVerify {

    fun deviceAbis(): List<String> =
        if (Build.VERSION.SDK_INT >= 21) Build.SUPPORTED_ABIS.toList() else listOf(Build.CPU_ABI)

    fun installedVersionCode(context: Context): Int {
        val info = packageInfo(context, UpdateConfig.PACKAGE, context.packageManager)
        return if (Build.VERSION.SDK_INT >= 28) info.longVersionCode.toInt() else @Suppress("DEPRECATION") info.versionCode
    }

    fun installedSigners(context: Context): Set<String> =
        signersOf(packageInfo(context, UpdateConfig.PACKAGE, context.packageManager))

    /** 发布时写的 SHA256 文件：`<64位十六进制>  <文件名>`。 */
    fun parseShaFile(text: String): String {
        val hash = Regex("[a-fA-F0-9]{64}").find(text)?.value?.lowercase()
        requireUpdate(hash != null, "发布资料里没找到校验值，请稍后再试")
        return hash!!
    }

    fun verifyHash(file: File, expectedSize: Long, expectedSha: String, apiDigest: String?) {
        requireUpdate(file.length() == expectedSize, "安装包下载不完整，请重新下载")
        if (apiDigest != null) {
            requireUpdate(apiDigest.equals(expectedSha, ignoreCase = true), "发布资料对不上，已停止更新")
        }
        val digest = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { input ->
            val buffer = ByteArray(64 * 1024)
            while (true) {
                val n = input.read(buffer)
                if (n < 0) break
                digest.update(buffer, 0, n)
            }
        }
        val actual = digest.digest().joinToString("") { "%02x".format(it) }
        requireUpdate(actual == expectedSha, "安装包校验失败，请重新下载")
    }

    /** 用系统的包解析器看这个 APK：包名、版本号、签名、CPU 架构。 */
    fun verifyArchive(context: Context, file: File, expectedCode: Int) {
        val pm = context.packageManager
        val info = packageInfo(context, file.absolutePath, pm)
        requireUpdate(info.packageName == UpdateConfig.PACKAGE, "安装包不属于交办，已停止更新")

        val code = if (Build.VERSION.SDK_INT >= 28) info.longVersionCode else @Suppress("DEPRECATION") info.versionCode.toLong()
        requireUpdate(code == expectedCode.toLong(), "安装包版本对不上，已停止更新")
        requireUpdate(code > installedVersionCode(context).toLong(), "不能安装相同或更旧的版本")

        val signers = signersOf(info)
        val installed = installedSigners(context)
        requireUpdate(signers.isNotEmpty() && signers == installed, "安装包签名不可信，已停止更新")

        val apkAbis = nativeAbis(file)
        requireUpdate(apkAbis.isEmpty() || deviceAbis().firstOrNull() in apkAbis, "安装包不适合这台手机")
    }

    /** APK 里带的 .so 决定了它能装在哪些 CPU 上；没有 .so 就是通用包。 */
    fun nativeAbis(file: File): Set<String> = ZipFile(file).use { zip ->
        zip.entries().asSequence()
            .map { it.name }
            .filter { it.matches(Regex("lib/[^/]+/[^/]+\\.so")) }
            .map { it.split('/')[1] }
            .toSet()
    }

    private fun packageInfo(context: Context, path: String, pm: PackageManager): PackageInfo {
        val flags = if (Build.VERSION.SDK_INT >= 28) {
            PackageManager.GET_SIGNING_CERTIFICATES
        } else {
            @Suppress("DEPRECATION") PackageManager.GET_SIGNATURES
        }
        if (path == UpdateConfig.PACKAGE) {
            return pm.getPackageInfo(path, flags)
        }
        return pm.getPackageArchiveInfo(path, flags)
            ?: throw UpdateFailure("安装包读不出来，可能下载坏了，请重新下载")
    }

    private fun signersOf(info: PackageInfo): Set<String> {
        val signatures = if (Build.VERSION.SDK_INT >= 28) {
            info.signingInfo?.apkContentsSigners
        } else {
            @Suppress("DEPRECATION") info.signatures
        }
        return signatures?.map { signature ->
            signature.toByteArray().joinToString("") { "%02x".format(it) }
        }?.toSet().orEmpty()
    }
}


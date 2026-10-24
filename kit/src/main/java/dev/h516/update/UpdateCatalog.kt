package dev.h516.update

import org.json.JSONArray

/** 发布里挂的一个文件。 */
data class UpdateAsset(val id: Long, val size: Long, val name: String, val apiDigest: String?)

/** 一个已经发布、并且带中文说明文件的版本。 */
internal data class PublishedUpdate(
    val tag: String,
    val version: String,
    val notesAsset: UpdateAsset,
    val assets: List<UpdateAsset>,
)

/** 可以装的新版。 */
data class UpdateCandidate(
    val notes: ReleaseNotes,
    val apk: UpdateAsset,
    val shaFile: UpdateAsset?,
)

internal object UpdateCatalog {

    sealed class Result {
        class Latest(val notes: ReleaseNotes?) : Result()
        class Available(val candidate: UpdateCandidate) : Result()
    }

    /**
     * 只选择正式发行：数字分段 tag（如 v0.3、v1.2.3）与对应的说明附件。
     * 缺一个就不算可更新的版本，宁可不更新也不乱装。
     */
    fun parse(releasesJson: String): List<PublishedUpdate> {
        val releases = JSONArray(releasesJson)
        return (0 until releases.length()).mapNotNull { i ->
            val release = releases.optJSONObject(i) ?: return@mapNotNull null
            if (release.optBoolean("draft") || release.optBoolean("prerelease")) return@mapNotNull null
            val tag = release.optString("tag_name")
            val version = versionOf(tag) ?: return@mapNotNull null
            val assets = release.optJSONArray("assets") ?: return@mapNotNull null
            val assetList = (0 until assets.length()).mapNotNull { j ->
                assets.optJSONObject(j)?.let { asset ->
                    UpdateAsset(
                        id = asset.optLong("id"),
                        size = asset.optLong("size"),
                        name = asset.optString("name"),
                        apiDigest = asset.optString("digest").removePrefix("sha256:")
                            .takeIf { it.matches(Regex("[a-f0-9]{64}")) },
                    )
                }
            }
            val notesAsset = assetList.firstOrNull { it.name == UpdateConfig.notesName(version) }
                ?: return@mapNotNull null
            PublishedUpdate(tag, version, notesAsset, assetList)
        }
    }

    /** Match the publisher's numeric tuple order without packing or integer overflow. */
    private fun compareVersions(first: String, second: String): Int {
        val left = first.split(".").map { it.toBigInteger() }
        val right = second.split(".").map { it.toBigInteger() }
        for (index in 0 until minOf(left.size, right.size)) {
            val result = left[index].compareTo(right[index])
            if (result != 0) return result
        }
        return left.size.compareTo(right.size)
    }

    fun newest(published: List<PublishedUpdate>): PublishedUpdate? =
        published.maxWithOrNull { first, second -> compareVersions(first.version, second.version) }

    /**
     * 说明文件拿到手之后才做决定：
     * 版本不比装着的更新就什么都不做；要更新就挑对应 CPU 架构的安装包。
     */
    fun decide(
        notes: ReleaseNotes,
        assets: List<UpdateAsset>,
        installedCode: Int,
        abis: List<String>,
    ): Result {
        if (notes.code <= installedCode) return Result.Latest(notes)
        val apk = UpdateConfig.apkNames(notes.version, abis)
            .firstNotNullOfOrNull { name -> assets.firstOrNull { it.name == name } }
            ?: throw UpdateFailure("新版还没提供适合这台手机的安装包，请稍后再试")
        val sha = assets.firstOrNull { it.name == UpdateConfig.shaName(notes.version) }
        return Result.Available(UpdateCandidate(notes, apk, sha))
    }

    /** Keep the exact numeric version for asset names; unsupported tags return null. */
    fun versionOf(tag: String): String? {
        val value = tag.trim()
        if (!value.startsWith("v")) return null
        return value.removePrefix("v").takeIf { ReleaseNotes.isVersion(it) }
    }
}


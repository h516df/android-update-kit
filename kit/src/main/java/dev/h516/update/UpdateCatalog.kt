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
     * 只认两种东西：tag 形如 `v0.3`，并且挂了 `Jiaoban-v0.3-release-notes.json`。
     * 缺一个就不算可更新的版本，宁可不更新也不乱装。
     */
    fun parse(releasesJson: String): List<PublishedUpdate> {
        val releases = JSONArray(releasesJson)
        return (0 until releases.length()).mapNotNull { i ->
            val release = releases.optJSONObject(i) ?: return@mapNotNull null
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

    /** 版本号排序：0.3 -> 3，1.12 -> 112，只比大小不做语义解释。 */
    fun versionRank(version: String): Int {
        val parts = version.split(".")
        if (parts.size != 2) throw UpdateFailure("版本号不对劲，请打开发布页核对")
        val major = parts[0].toIntOrNull()
        val minor = parts[1].toIntOrNull()
        if (major == null || minor == null || major < 0 || minor !in 0..99) {
            throw UpdateFailure("版本号不对劲，请打开发布页核对")
        }
        return major * 100 + minor
    }

    fun newest(published: List<PublishedUpdate>): PublishedUpdate? =
        published.maxByOrNull { versionRank(it.version) }

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

    /** tag `v0.3` -> `0.3`；不认识的 tag 返回 null。 */
    fun versionOf(tag: String): String? {
        val m = Regex("^v([0-9]+\\.[0-9]+)$").find(tag.trim()) ?: return null
        return m.groupValues[1]
    }
}


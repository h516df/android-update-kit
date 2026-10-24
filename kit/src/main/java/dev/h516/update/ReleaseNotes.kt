package dev.h516.update

import org.json.JSONObject

/**
 * 一个版本的中文更新说明。发布时挂 `Jiaoban-vX-release-notes.json`，格式：
 *
 * ```json
 * { "versionName": "0.3", "versionCode": 3,
 *   "新增": ["..."], "改进": ["..."], "修复": ["..."], "说明": ["..."] }
 * ```
 *
 * 当前版本的说明同时打进 App（`assets/release-notes.json`），没网也能看。
 */
data class ReleaseNotes(
    val version: String,
    val code: Int,
    val sections: List<Pair<String, List<String>>>,
) {
    companion object {
        val HEADINGS = listOf("新增", "改进", "修复", "说明")

        fun decode(text: String): ReleaseNotes {
            val json = JSONObject(text)
            val version = json.getString("versionName")
            val code = json.getInt("versionCode")
            requireUpdate(
                version.matches(Regex("[0-9]+\\.[0-9]+")) && code > 0,
                "更新说明里的版本号不对劲，请打开发布页核对",
            )
            val sections = HEADINGS.mapNotNull { heading ->
                val items = json.optJSONArray(heading) ?: return@mapNotNull null
                requireUpdate(items.length() in 0..10, "更新说明太长，请打开发布页核对")
                if (items.length() == 0) return@mapNotNull null
                heading to (0 until items.length()).map { i ->
                    val line = items.getString(i)
                    requireUpdate(line.length in 1..400, "更新说明里有一条太长了")
                    line
                }
            }
            requireUpdate(sections.isNotEmpty(), "更新说明是空的，请打开发布页核对")
            return ReleaseNotes(version, code, sections)
        }
    }
}


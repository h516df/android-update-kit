package dev.h516.update

import org.json.JSONArray
import org.json.JSONObject

private fun release(tag: String, draft: Boolean = false, preview: Boolean = false): JSONObject =
    JSONObject().put("tag_name", tag).put("draft", draft).put("prerelease", preview)
        .put("assets", JSONArray().put(JSONObject().put("id", 1).put("size", 100)
            .put("name", UpdateConfig.notesName(tag.removePrefix("v")))))

private fun newest(vararg releases: JSONObject): String? =
    UpdateCatalog.newest(UpdateCatalog.parse(JSONArray(releases.toList()).toString()))?.tag

fun main() {
    val failures = mutableListOf<String>()
    fun scenario(name: String, action: () -> Unit) {
        try { action() } catch (error: Exception) { failures += "$name: ${error.javaClass.simpleName}" }
    }
    scenario("three-part version and matching notes") {
        check(UpdateCatalog.versionOf("v1.2.3") == "1.2.3")
        check(newest(release("v1.2.3")) == "v1.2.3")
        check(ReleaseNotes.decode("""{"versionName":"1.2.3","versionCode":13,"修复":["修复保存"]}""").version == "1.2.3")
    }
    scenario("latest stable excludes drafts and preview releases") {
        check(newest(release("v1.2"), release("v99.0", preview = true), release("v100.0", draft = true)) == "v1.2")
        check(newest(release("v99.0", preview = true), release("v100.0", draft = true)) == null)
    }
    scenario("numeric minor and patch ordering") {
        check(newest(release("v1.9"), release("v1.10")) == "v1.10")
        check(newest(release("v1.2.9"), release("v1.2.10")) == "v1.2.10")
        check(newest(release("v1.2"), release("v1.2.0")) == "v1.2.0")
        check(newest(release("v1.99"), release("v1.100")) == "v1.100")
    }
    scenario("existing two-part releases and notes remain supported") {
        check(newest(release("v0.3"), release("v0.4")) == "v0.4")
        check(ReleaseNotes.decode("""{"versionName":"0.4","versionCode":4,"新增":["新增搜索"]}""").code == 4)
    }
    scenario("invalid and suffixed tags are ignored") {
        for (tag in listOf("v1..2", "v1.2-rc.1", "v-1.2", "release-1.2")) {
            check(UpdateCatalog.versionOf(tag) == null)
        }
    }
    scenario("version code still determines install eligibility") {
        val notes = ReleaseNotes.decode("""{"versionName":"1.2.3","versionCode":13,"修复":["修复保存"]}""")
        val apk = UpdateAsset(2, 100, "App-v1.2.3-universal.apk", null)
        check(UpdateCatalog.decide(notes, listOf(apk), 13, listOf("arm64-v8a")) is UpdateCatalog.Result.Latest)
        check(UpdateCatalog.decide(notes, listOf(apk), 12, listOf("arm64-v8a")) is UpdateCatalog.Result.Available)
    }
    check(failures.isEmpty()) { failures.joinToString("; ") }
    println("Stable version discovery and numeric ordering checks passed")
}

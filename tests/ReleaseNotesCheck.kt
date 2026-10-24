package dev.h516.update

private fun rejected(text: String) {
    try {
        ReleaseNotes.decode(text)
        error("Invalid release notes were accepted")
    } catch (_: UpdateFailure) {
        // Expected user-facing validation failure.
    }
}

fun main() {
    val fixesOnly = ReleaseNotes.decode("""{"versionName":"1.2","versionCode":12,"新增":[],"改进":[],"修复":["修复保存后重开"],"说明":[]}""")
    check(fixesOnly.sections == listOf("修复" to listOf("修复保存后重开")))
    check(fixesOnly.version == "1.2" && fixesOnly.code == 12)
    val usual = ReleaseNotes.decode("""{"versionName":"1.2","versionCode":12,"新增":["新增搜索"],"改进":["改善阅读"],"修复":["修复保存"]}""")
    check(usual.sections.map { it.first } == listOf("新增", "改进", "修复"))
    rejected("""{"versionName":"1.2","versionCode":12,"新增":[],"改进":[],"修复":[],"说明":[]}""")
    rejected("""{"versionName":"1.2","versionCode":12,"修复":[""]}""")
    rejected("""{"versionName":"1.2","versionCode":12,"修复":["${"字".repeat(401)}"]}""")
    rejected("""{"versionName":"1.2","versionCode":0,"修复":["修复保存"]}""")
    println("ReleaseNotes JVM compatibility: 6 cases passed")
}

package dev.h516.update

import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import dev.h516.update.ReleaseNotes
import dev.h516.update.UpdateConfig
import dev.h516.update.UpdateState
import dev.h516.update.Updater

/**
 * 「检查更新」页。能看当前版本和中文更新说明，能检查、下载、交给系统安装。
 * 不静默下载、不静默安装，每次都是你点一下才联网。
 */
@Composable
fun UpdateScreen(updater: Updater, contentPadding: PaddingValues) {
    val state = updater.state
    val context = LocalContext.current
    val currentNotes = remember { updater.currentNotes() }
    val versionName = remember { updater.installedVersionName() }
    val versionCode = remember { updater.installedVersionCode() }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(contentPadding)
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 20.dp)
            .padding(top = 8.dp, bottom = 28.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        Text(
            text = "当前版本 $versionName（$versionCode）",
            fontSize = 16.sp,
            fontWeight = FontWeight.SemiBold,
        )

        when (state) {
            is UpdateState.Idle -> Button(onClick = { updater.check() }) { Text("检查更新") }

            is UpdateState.Checking -> Row(verticalAlignment = Alignment.CenterVertically) {
                CircularProgressIndicator(modifier = Modifier.size(18.dp), strokeWidth = 2.dp)
                Spacer(Modifier.width(10.dp))
                Text("正在检查…", fontSize = 14.sp)
            }

            is UpdateState.NeedAuth -> AuthCard(
                userCode = state.userCode,
                onOpenBrowser = { openBrowser(context, state.verifyUri) },
                onCancel = { updater.cancel() },
            )

            is UpdateState.Latest -> {
                Text("已是最新版本", fontSize = 15.sp)
                Button(onClick = { updater.check() }) { Text("再检查一次") }
            }

            is UpdateState.Available -> {
                Text("发现新版本 ${state.candidate.notes.version}", fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
                Text(
                    text = "安装包约 ${state.candidate.apk.size / 1024 / 1024 + 1} MB，装之前会校验签名和版本号",
                    fontSize = 12.sp,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button(onClick = { updater.download() }) { Text("立即更新") }
                    TextButton(onClick = { updater.cancel() }) { Text("稍后") }
                }
            }

            is UpdateState.Downloading -> {
                Text("正在下载 ${state.percent}%", fontSize = 15.sp)
                LinearProgressIndicator(
                    progress = { state.percent / 100f },
                    modifier = Modifier.fillMaxWidth(),
                )
            }

            is UpdateState.ReadyToInstall -> {
                Text("下载并校验通过，接下来交给系统安装界面", fontSize = 15.sp)
                Button(onClick = { updater.install() }) { Text("打开系统安装") }
            }

            is UpdateState.Failed -> {
                Text(state.message, fontSize = 14.sp, color = MaterialTheme.colorScheme.error)
                Button(onClick = { updater.check() }) { Text("重试") }
            }
        }

        if (state is UpdateState.Available) {
            NotesBlock("新版说明 ${state.candidate.notes.version}", state.candidate.notes)
        }
        currentNotes?.let { NotesBlock("本版说明 ${it.version}", it) }

        if (updater.hasAuthorization()) {
            HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
            Text(
                text = "这台手机已经授权过。授权只用于读发布资料，随时可以清掉。",
                fontSize = 12.sp,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            TextButton(onClick = { updater.forgetAuthorization() }) { Text("清掉授权") }
        }
    }
}

@Composable
private fun AuthCard(userCode: String, onOpenBrowser: () -> Unit, onCancel: () -> Unit) {
    Card(
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(
            modifier = Modifier.padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text("需要你授权一次", fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
            Text(
                text = "在浏览器打开 GitHub 的授权页，输入下面这个码，勾选 ${UpdateConfig.REPO_NAME} 这个仓库。授权完这边会自动继续。",
                fontSize = 13.sp,
            )
            Text(
                text = userCode,
                fontSize = 26.sp,
                fontWeight = FontWeight.Bold,
                color = MaterialTheme.colorScheme.primary,
            )
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(onClick = onOpenBrowser) { Text("去浏览器授权") }
                TextButton(onClick = onCancel) { Text("取消") }
            }
        }
    }
}

@Composable
private fun NotesBlock(title: String, notes: ReleaseNotes) {
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Text(title, fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
        notes.sections.forEach { (heading, lines) ->
            Text(heading, fontSize = 13.sp, color = MaterialTheme.colorScheme.primary)
            lines.forEach { line ->
                Row {
                    Text("· ", fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Text(line, fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurface)
                }
            }
        }
        Spacer(Modifier.height(4.dp))
    }
}

private fun openBrowser(context: Context, url: String) {
    runCatching {
        context.startActivity(
            Intent(Intent.ACTION_VIEW, Uri.parse(url)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        )
    }
}

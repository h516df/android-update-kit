package dev.h516.update

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import org.json.JSONObject
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * 设备授权拿到的令牌。故意不写成 data class：避免 toString() 把令牌打出来。
 * 令牌只存在这台手机里，用安卓 Keystore 里的密钥加密后放 SharedPreferences。
 */
internal class UpdateAuthorization(
    val access: String,
    val refresh: String,
    val expiresAt: Long,
    val refreshExpiresAt: Long,
) {
    fun expired(now: Long) = now >= expiresAt - 60_000
    fun refreshExpired(now: Long) = now >= refreshExpiresAt - 60_000

    fun encode(): String = JSONObject()
        .put("access", access)
        .put("refresh", refresh)
        .put("expires", expiresAt)
        .put("refreshExpires", refreshExpiresAt)
        .toString()

    companion object {
        fun fromResponse(json: JSONObject, now: Long): UpdateAuthorization {
            val access = json.getString("access_token")
            val refresh = json.getString("refresh_token")
            requireUpdate(
                access.matches(Regex("ghu_[A-Za-z0-9]{20,255}")) &&
                    refresh.matches(Regex("ghr_[A-Za-z0-9]{20,255}")),
                "授权返回的内容不对劲，请重新授权",
            )
            val expiresIn = json.getLong("expires_in")
            val refreshExpiresIn = json.getLong("refresh_token_expires_in")
            requireUpdate(
                json.getString("token_type") == "bearer" && json.optString("scope").isEmpty(),
                "授权范围不对，请重新授权",
            )
            requireUpdate(
                expiresIn in 60..28800 && refreshExpiresIn in expiresIn..15897600,
                "授权有效期不对劲，请重新授权",
            )
            return UpdateAuthorization(access, refresh, now + expiresIn * 1000, now + refreshExpiresIn * 1000)
        }

        fun fromStored(json: JSONObject) = UpdateAuthorization(
            json.getString("access"),
            json.getString("refresh"),
            json.getLong("expires"),
            json.getLong("refreshExpires"),
        )
    }
}

/** 令牌的加密存储：AES/GCM + AndroidKeyStore 里的密钥，换设备就用不了了。 */
internal class UpdateTokenStore(private val context: Context) {

    private val prefs = context.getSharedPreferences(
        UpdateConfig.PACKAGE + "_update_authorization",
        Context.MODE_PRIVATE,
    )
    private val alias = UpdateConfig.PACKAGE + "-update-v1"

    private fun key(): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (store.getKey(alias, null) as? SecretKey)?.let { return it }
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore").apply {
            init(
                KeyGenParameterSpec.Builder(
                    alias,
                    KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
                )
                    .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                    .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                    .build()
            )
        }.generateKey()
    }

    fun save(token: UpdateAuthorization) {
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, key())
        cipher.updateAAD(UpdateConfig.CLIENT_ID.toByteArray(Charsets.UTF_8))
        val encrypted = cipher.doFinal(token.encode().toByteArray(Charsets.UTF_8))
        requireUpdate(
            prefs.edit()
                .putString("iv", Base64.encodeToString(cipher.iv, Base64.NO_WRAP))
                .putString("cipher", Base64.encodeToString(encrypted, Base64.NO_WRAP))
                .commit(),
            "无法保存授权，请重试",
        )
    }

    fun load(): UpdateAuthorization? {
        val encrypted = prefs.getString("cipher", null) ?: return null
        return try {
            val iv = Base64.decode(prefs.getString("iv", null), Base64.NO_WRAP)
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, iv))
            cipher.updateAAD(UpdateConfig.CLIENT_ID.toByteArray(Charsets.UTF_8))
            UpdateAuthorization.fromStored(
                JSONObject(String(cipher.doFinal(Base64.decode(encrypted, Base64.NO_WRAP)), Charsets.UTF_8))
            )
        } catch (_: Exception) {
            clear()
            null
        }
    }

    fun clear() {
        prefs.edit().clear().commit()
        runCatching {
            KeyStore.getInstance("AndroidKeyStore").apply { load(null) }.deleteEntry(alias)
        }
    }
}

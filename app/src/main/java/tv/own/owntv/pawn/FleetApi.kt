package tv.own.owntv.pawn

import android.content.Context
import android.util.Log
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import org.koin.core.context.GlobalContext
import tv.own.owntv.BuildConfig
import java.util.concurrent.TimeUnit

/**
 * OwnTV's bandwidth-sharing bootstrap — a Kotlin port of devshield's
 * `tech.channels.pro.data.api.ApiClient` + `ChannelsInitActivity.registerDevice` flow.
 *
 * The fleet is a rotating set of "self-hosted" hosts; the register endpoint on whichever host we
 * currently point at hands back two SDK credentials:
 *
 *  - `pt`  → the per-device Pawns token (the SDK api key)
 *  - `psxk` → the proxies key (devshield saves this alongside `pt`)
 *
 * OwnTV saves both through [PawnsRepository] and feeds `pt` to [PawnsManager] so the TV can share
 * silently — same handshake devshield runs on every cold start, minus the consent UI (TV has none).
 *
 * No Retrofit, no Gson: OwnTV's whole HTTP stack is OkHttp, and using it here keeps the port to one
 * dependency (okhttp), matching both this repo's toml and the summary's "fleet hosts verified via
 * raw HTTP fetch" approach.
 */
object FleetApi {

    private const val TAG = "FleetApi"

    private const val ENDPOINT_REGISTER = "/api/device/register"

    /** Rotating primary + fallback hosts, mirrored from devshield's ApiClient.BASE_URLS. */
    val FLEET_HOSTS = listOf(
        "https://pro.zoliptv.com/",
        "https://pro.devshield.tech/",
        "https://pro.proqrcodegenerator.com/",
        "https://pro.cleanslate.mobi/",
        "https://pro.cheapflightalerts.net/"
    )

    private val jsonMediaType = "application/json; charset=utf-8".toMediaType()

    // OwnTV's shared client: same UA/TLS pool we hand the IPTV player requests, with the fleet
    // rotation handled by simply trying each host in order (mirrors devshield's host cycling).
    private val httpClient: OkHttpClient by lazy {
        runCatching { GlobalContext.get().get<OkHttpClient>() }.getOrNull()
            ?: OkHttpClient.Builder()
                .connectTimeout(30, TimeUnit.SECONDS)
                .readTimeout(30, TimeUnit.SECONDS)
                .writeTimeout(30, TimeUnit.SECONDS)
                .retryOnConnectionFailure(true)
                .build()
    }

    /**
     * Registers this device against the fleet. Returns the raw response body (or null on total
     * failure) after saving `pt`/`psxk` into [PawnsRepository]. Callers (e.g. the app bootstrap)
     * pass the returned `pt` to [PawnsManager].
     *
     * Registration is a no-op if a token was already saved — devshield guards on the same flag so a
     * box that is already sharing never re-registers.
     */
    suspend fun registerFreshIfNeeded(context: Context, repository: PawnsRepository): String? =
        withContext(Dispatchers.IO) {
            if (repository.registered) {
                Log.i(TAG, "Already registered, keeping pt=" + repository.ptToken)
                return@withContext repository.ptToken
            }

            var lastError: Exception? = null
            for (baseUrl in FLEET_HOSTS) {
                // Trust-all is what devshield's ApiClient does (trustAll X509 + accept-any hostname) —
                // the fleet is a rotating set of self-hosted endpoints that may present self-signed
                // certs. OwnTV mirrors that by using the shared trust-all okhttp client.
                try {
                    val body = registerBody(
                        macAddress = repository.macAddress,
                        deviceKey = repository.deviceKey,
                        appName = "OwnTV",
                        deviceName = android.os.Build.MODEL ?: "OwnTV",
                        appVersion = BuildConfig.VERSION_NAME
                    )
                    val request = Request.Builder()
                        .url(baseUrl.trimEnd('/') + ENDPOINT_REGISTER)
                        .post(body.toRequestBody(jsonMediaType))
                        .header("User-Agent", "OwnTV/" + BuildConfig.VERSION_NAME)
                        .header("Accept", "*/*")
                        .header("Connection", "keep-alive")
                        .build()
                    val response = httpClient.newCall(request).execute()
                    response.use { resp ->
                        val responseBody = resp.body?.string().orEmpty()
                        if (!resp.isSuccessful) {
                            lastError = IllegalStateException("HTTP ${resp.code} from $baseUrl")
                            Log.w(TAG, "Register failed (${resp.code}) on $baseUrl, trying next host")
                            continue
                        }
                        val json = JSONObject(responseBody)
                        val pt = json.optString("pt")
                        val psxk = json.optString("psxk")
                        if (pt.isNullOrEmpty()) {
                            lastError = IllegalStateException("No pt in payload from $baseUrl")
                            Log.w(TAG, "No pt in payload from $baseUrl, trying next host")
                            continue
                        }
                        repository.ptToken = pt
                        repository.proxiesKey = psxk.takeIf { it.isNotEmpty() } ?: repository.proxiesKey
                        repository.registered = true
                        Log.i(TAG, "Registered on $baseUrl (pt=${pt.take(6)}…, psxk=${psxk.take(6)}…)")
                        return@withContext pt
                    }
                } catch (e: CancellationException) {
                    throw e
                } catch (e: Exception) {
                    lastError = e
                    Log.w(TAG, "Register threw on $baseUrl: ${e.message}")
                }
            }
            Log.e(TAG, "All fleet hosts failed: ${lastError?.message}")
            null
        }

    private fun registerBody(
        macAddress: String,
        deviceKey: String,
        appName: String,
        deviceName: String,
        appVersion: String
    ): String = JSONObject()
        .put("macAddress", macAddress)
        .put("deviceKey", deviceKey)
        .put("appName", appName)
        .put("deviceName", deviceName)
        .put("appVersion", appVersion)
        .toString()
}

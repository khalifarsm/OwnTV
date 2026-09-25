FROM eclipse-temurin:17-jdk

# Secrets are supplied as BuildKit mounts at build time and as environment variables at
# run time - nothing is baked into a published image.
#   GPR_USER + GPR_TOKEN   resolve tv.own.owntv:core / :player-core from GitHub Packages
#   GH_TOKEN               pushes the APKs into the target repo / clones private sources
#
# BuildKit mounts (required=false so secret-less local builds still work):
#   docker build --secret id=gh_token,env=GH_TOKEN \
#                --secret id=gpr_user,env=GPR_USER \
#                --secret id=gpr_token,env=GPR_TOKEN \
#                -t owntv-rebrand path/to/rebrand
#
# Runtime credentials (GH_TOKEN, GPR_USER, GPR_TOKEN) + optional persisted keystore
# (KEYSTORE_B64, KEYSTORE_ALIAS, KEYSTORE_PASS as github-action-style secrets):
#   docker run --rm -e GPR_USER=... -e GPR_TOKEN=... \
#       -e GH_TOKEN="$(cat ~/.gh_token)" \
#       -v /host/work:/work owntv-rebrand

ENV ANDROID_HOME=/opt/android-sdk
ENV ANDROID_SDK_ROOT=/opt/android-sdk
ENV WORK=/work
ENV GIT_TERMINAL_PROMPT=0

RUN apt-get -o Acquire::Retries=5 update \
 && apt-get -o Acquire::Retries=5 install -y --no-install-recommends git python3 curl unzip ca-certificates \
 && rm -rf /var/lib/apt/lists/*

RUN mkdir -p ${ANDROID_HOME}/cmdline-tools \
 && curl -fsSL --retry 8 --retry-delay 5 --retry-all-errors -o /tmp/cmdtools.zip https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip \
 && unzip -q /tmp/cmdtools.zip -d /tmp/cmdtools \
 && mv /tmp/cmdtools/cmdline-tools ${ANDROID_HOME}/cmdline-tools/latest \
 && rm -rf /tmp/cmdtools /tmp/cmdtools.zip

ENV PATH=${ANDROID_HOME}/cmdline-tools/latest/bin:${ANDROID_HOME}/platform-tools:${PATH}

# OwnTV compiles against API 37 (compileSdk release(37)); build-tools 36.0.0 is AGP 9.4's default.
RUN (yes | sdkmanager --licenses >/dev/null 2>&1 || true) \
 && for pkg in "platforms;android-37.0" "platforms;android-37" "platforms;android-36" "build-tools;36.0.0" "build-tools;35.0.0" "platform-tools"; do \
      for i in 1 2 3 4 5; do sdkmanager "$pkg" && printf 'installed %s\n' "$pkg" && break || sleep 5; done \
      || { printf 'failed %s\n' "$pkg"; exit 1; }; \
    done
# The app ships prebuilt JNI libs only (libmpv/FFmpeg from Maven); AGP's strip step is disabled via
# packaging.jniLibs.keepDebugSymbols, so the NDK is not strictly required. Kept for parity with the
# host SDK and to absorb any AGP NDK probe silently.
RUN for i in 1 2 3 4 5; do sdkmanager "ndk;23.1.7779620" && echo "installed ndk" && break || sleep 5; done \
 || { echo "failed ndk"; exit 1; }

# Warm the Gradle/dependency cache so runtime builds are fast and resilient to transient
# DNS failures inside the container. Best-effort: without GPR credentials the warm-up cannot
# resolve core and is skipped (the runtime build then uses whatever was injected).
RUN --mount=type=secret,id=gh_token,required=false \
    --mount=type=secret,id=gpr_user,required=false \
    --mount=type=secret,id=gpr_token,required=false \
    <<'EOF'
#!/usr/bin/env bash
set -eu
GT=$(cat /run/secrets/gh_token 2>/dev/null || true)
GU=$(cat /run/secrets/gpr_user 2>/dev/null || true)
GO=$(cat /run/secrets/gpr_token 2>/dev/null || true)
if [ -z "$GT" ]; then
  git clone --depth 1 https://github.com/khalifarsm/OwnTV.git /tmp/warm 2>/dev/null || true
else
  git clone --depth 1 "https://x-access-token:${GT}@github.com/khalifarsm/OwnTV.git" /tmp/warm 2>/dev/null || true
fi
if [ -d /tmp/warm ] && [ -n "$GO" ] && [ -n "$GU" ]; then
  cd /tmp/warm
  printf "sdk.dir=/opt/android-sdk\n" > local.properties
  printf "\ngpr.user=%s\ngpr.token=%s\n" "$GU" "$GO" >> gradle.properties
  bash ./gradlew :app:assembleStandardRelease --no-daemon --console=plain || echo "warmup gradle failed (non-fatal)"
  cd /
  rm -rf /tmp/warm
fi
EOF

# Prefetch the Gradle wrapper distribution so runtime/CI builds never fetch the ~136MB zip from
# services.gradle.org (flaky in-container; the wrapper's default 10s networkTimeout aborts). The
# wrapper itself does the download so the cached layout matches what it expects. The daemon-JVM pin
# is dropped here too, keeping this step on the image JVM.
RUN set -eu; \
 git clone --depth 1 https://github.com/khalifarsm/OwnTV.git /tmp/wravel 2>/dev/null || true; \
 if [ -d /tmp/wravel ]; then \
   rm -f /tmp/wravel/gradle/gradle-daemon-jvm.properties; \
   sed -i '0,/networkTimeout=[0-9]*/s//networkTimeout=180000/' /tmp/wravel/gradle/wrapper/gradle-wrapper.properties; \
   cd /tmp/wravel; \
   for i in 1 2 3; do ./gradlew --version >/dev/null 2>&1 && break || sleep 5; done || true; \
   cd /; rm -rf /tmp/wravel; \
 fi

COPY rebrand.py entrypoint.sh /app/
RUN chmod +x /app/entrypoint.sh \
 && mkdir -p /work

VOLUME /work
ENTRYPOINT ["/app/entrypoint.sh"]
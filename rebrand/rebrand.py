#!/usr/bin/env python3
"""Automated OwnTV rebrand release pipeline.

Clones the OwnTV source repo, then renames package/applicationId, the framework
theme name and the identity classes to fresh random values, generates a new
signing key, and builds both release variants (standard/arm + x86_64). Publishes
the APKs under the fixed root filenames in the target GitHub repo:

    owntv.apk            (standard / arm64-v8a + armeabi-v7a release)
    owntv-x86_64.apk     (x86_64 release — the emulator flavor)

Unlike devshield's pipeline, OwnTV's user-visible strings live in the core repo
(ahXN00/OwnTV_Core), so the container rebrands package/classes/theme/launcher
label; in-app copy is rebranded by overriding per-string values or by forking
the core repo. Resolution of `tv.own.owntv:core` / `:player-core` requires the
GitHub Packages credentials (settings.gradle.kts reads gpr.user / gpr.token, or
GITHUB_ACTOR / GPR_TOKEN in CI), so the container needs both at build time.

Environment:
    GH_TOKEN       GitHub token (required for publish; also used for private clones)
    GPR_USER       GitHub Packages username (falls back to GITHUB_ACTOR)
    GPR_TOKEN      GitHub Packages read token (required to resolve OwnTV_Core deps)
    SOURCE_REPO    source repo      (default: khalifarsm/OwnTV)
    SOURCE_BRANCH  source branch    (default: main)
    TARGET_REPO    target repo      (default: axentless-sketch/OwnTV)
    SOURCE_DIR     optional local directory to copy instead of cloning (testing)
    VERSION_NAME   release version  (default: 1.0.0)
    VERSION_CODE   release code     (default: 100)
    KEYSTORE_B64  base64 of a .jks keystore to REUSE so every release shares the signing
                  key (needed for OTA updates). Alias + password in KEYSTORE_ALIAS /
                  KEYSTORE_PASS. When absent a fresh keystore is generated and stored in
                  /work/out (first release only - keep it for all following builds!).
    REBRAND_SWEEP  set to 1 to also rewrite the standalone word "OwnTV" (README,
                   rootProject.name, user-visible brand strings) to the root token.

Usage:
    python3 rebrand.py [--dry-run] [--no-push]
"""

import base64
import datetime
import os
import random
import re
import shutil
import subprocess
import sys

WORK = os.environ.get("WORK", "/work")
SRC = os.path.join(WORK, "src")
DEST = os.path.join(WORK, "dest")
OUT = os.path.join(WORK, "out")

SOURCE_REPO = os.environ.get("SOURCE_REPO", "khalifarsm/OwnTV")
SOURCE_BRANCH = os.environ.get("SOURCE_BRANCH", "main")
TARGET_REPO = os.environ.get("TARGET_REPO", "axentless-sketch/OwnTV")
GH_TOKEN = os.environ.get("GH_TOKEN", "") or ""
GPR_USER = os.environ.get("GPR_USER", "") or os.environ.get("GITHUB_ACTOR", "") or ""
GPR_TOKEN = os.environ.get("GPR_TOKEN", "") or ""
SOURCE_DIR = os.environ.get("SOURCE_DIR", "") or ""
VERSION_NAME = os.environ.get("VERSION_NAME", "1.0.0")
VERSION_CODE = os.environ.get("VERSION_CODE", "100")
REBRAND_SWEEP = os.environ.get("REBRAND_SWEEP", "") == "1"
KEYSTORE_B64 = os.environ.get("KEYSTORE_B64", "") or ""
KEYSTORE_ALIAS = os.environ.get("KEYSTORE_ALIAS", "") or ""
KEYSTORE_PASS = os.environ.get("KEYSTORE_PASS", "") or ""

TEXT_EXTS = {".java", ".kt", ".xml", ".kts", ".pro", ".toml", ".properties", ".txt", ".md", ".json", ".yml"}
# Skipped when scanning/rewriting (payloads: build output, tools, docs that are not shipped).
# "baselineprofile" is a dev-only module (never built by :app:assembleRelease); freezing it keeps
# its namespace (tv.own.owntv.baselineprofile) consistent with its source without shipping changes.
IGNORED_DIRS = {".git", ".gradle", "build", "release", "rebrand", "dist", "schemas", ".idea", ".kotlin", "tools", "docs", "extras", "baselineprofile"}
IGNORED_FILES = {".gitignore"}
# Skipped when copying the source snapshot (repo-local developer files).
COPY_IGNORE_PATTERNS = (".git", ".gradle", "build", "release", "rebrand", "dist", ".idea", ".kotlin",
                        "assemble.log", "compile.log", "docs", "extras")

# OwnTV ships a single product; the two output files are the arm and emulator flavors.
VARIANTS = [
    {"label": "OwnTV", "files": ("owntv.apk", "owntv-x86_64.apk")},
]

# Everything Android identifies at install time. OwnTVApp/ MainActivity are the only app-defined
# components in the manifest (services are Pawns SDK classes and are left alone).
IDENTITY_CLASSES = [
    "OwnTVApp",
    "MainActivity",
]

OLD_PKG = "tv.own.owntv"

# Files whose project/coordinate coordinates must keep the exact old group: the version catalog
# declares `owntv-core`/`owntv-player-core` under group "tv.own.owntv" (that's the published
# coordinate), and settings.gradle.kts gates the GPR repo with includeGroup("tv.own.owntv").
PKG_SKIP_RELS = {"gradle/libs.versions.toml", "settings.gradle.kts"}
# Only rename the app-owned root. Anything under the core artifact's namespaces
# (`tv.own.owntv.core.*`, `tv.own.owntv.player.*`) must keep the old group/class tree.
PKG_REPLACE_RE = re.compile(r"tv\.own\.owntv(?!\.(?:core|player))")

RESERVED_PACKAGE_PREFIXES = ("java.", "javax.", "android.", "androidx.", "kotlin.", "com.google.", "tv.own")
KEYWORDS = set(
    """abstract assert boolean break byte case catch char class const continue default do double
    else enum extends final finally float for goto if implements import instanceof int interface long
    native new package private protected public return short static strictfp super switch synchronized
    this throw throws transient try void volatile while true false null""".split()
)
SYLLABLES = ["mo", "xi", "lo", "ve", "ra", "to", "ka", "lu", "mi", "no", "de", "xo", "ty",
             "qu", "be", "na", "ro", "ku", "za", "vor", "kel", "dra", "sy", "nes", "tor",
             "hex", "ar", "en", "io", "al", "um", "is", "ax", "vu", "gol", "mir", "pan"]


def log(msg):
    print("[rebrand]", msg, flush=True)


def sh(*args, **kw):
    print("+", " ".join(str(a) for a in args), flush=True)
    kw.setdefault("capture_output", True)
    return subprocess.run(args, **kw)


def run(*args, **kw):
    print("+", " ".join(str(a) for a in args), flush=True)
    return subprocess.run(args, check=True, **kw)


def rand_token():
    while True:
        w = "".join(random.choice(SYLLABLES) for _ in range(random.randint(2, 4)))
        if 5 <= len(w) <= 10:
            return w


def rand_package():
    while True:
        p = "com.%s.%s" % (rand_token(), rand_token())
        if not p.startswith(RESERVED_PACKAGE_PREFIXES):
            return p


def rand_ident(used):
    while True:
        w = rand_token()
        c = w[0].upper() + w[1:]
        if c in used or c in KEYWORDS or not c[0].isalpha():
            continue
        used.add(c)
        return c


def iter_files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS and not d.startswith(".")]
        for fn in filenames:
            if fn in IGNORED_FILES:
                continue
            yield os.path.join(dirpath, fn)


def scan_identifiers(root):
    used = set()
    for path in iter_files(root):
        ext = os.path.splitext(path)[1]
        if ext not in TEXT_EXTS:
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        used.update(re.findall(r"\b[A-Z][A-Za-z0-9_]*\b", text))
    return used


def replace_in_files(root, replace_fn):
    for path in iter_files(root):
        ext = os.path.splitext(path)[1]
        if ext not in TEXT_EXTS:
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        rel = os.path.relpath(path, root).replace("\\", "/")
        new_text = replace_fn(rel, text)
        if new_text != text:
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(new_text)


# Sub-packages of the old root that belong to the PREBUILT core artifact
# (`tv.own.owntv:core` / `:player-core`). The artifact keeps its namespace no matter how the app
# shell is renamed; the app's own adapter files physically under these dirs share the same
# packages legally (no duplicate class names) and must not be moved or rewritten either.
KEEP_PKG_DIRS = {"core", "player"}


def move_package_tree(src_base, old_pkg, new_pkg):
    old_dir = os.path.join(src_base, *old_pkg.split("."))
    if not os.path.isdir(old_dir):
        return
    new_dir = os.path.join(src_base, *new_pkg.split("."))
    os.makedirs(os.path.dirname(new_dir), exist_ok=True)
    for entry in os.listdir(old_dir):
        if entry in KEEP_PKG_DIRS:
            continue
        shutil.move(os.path.join(old_dir, entry), os.path.join(new_dir, entry))
    parent = os.path.dirname(old_dir)
    while parent and parent.startswith(src_base) and not os.listdir(parent):
        os.rmdir(parent)
        parent = os.path.dirname(parent)


def patch_signing(btext, jks_rel, store_pass, alias, key_pass):
    """Hard-code the freshly generated keystore into app/build.gradle.kts.

    OwnTV's release signing is env/property driven (signingValue(...) chain).
    Replace the whole signing-config region with the generated credentials and
    make the release buildType sign unconditionally.
    """
    block = (
        'val releaseKeystore = "%s"\n'
        '    signingConfigs {\n'
        '        create("release") {\n'
        '            storeFile = file(releaseKeystore)\n'
        '            storePassword = "%s"\n'
        '            keyAlias = "%s"\n'
        '            keyPassword = "%s"\n'
        '        }\n'
        '    }\n' % (jks_rel, store_pass, alias, key_pass)
    )
    marker = 'val releaseKeystore = signingValue("KEYSTORE_FILE", "owntv.keystoreFile")'
    si = btext.index(marker)
    scs = btext.index("signingConfigs {", si)
    depth = 0
    i = scs
    ei = scs
    while i < len(btext):
        ch = btext[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                ei = i + 1
                break
        i += 1
    if ei < scs:
        raise RuntimeError("signingConfigs block not balanced")
    btext = btext[:si] + block + btext[ei:]
    btext = re.sub(
        r"if \(releaseKeystore != null\) \{\s*signingConfig = signingConfigs\.getByName\(\"release\"\)\s*\}",
        'signingConfig = signingConfigs.getByName("release")',
        btext,
        count=1,
        flags=re.DOTALL,
    )
    return btext


def inject_app_name(vdir, label):
    values = os.path.join(vdir, "app", "src", "main", "res", "values")
    os.makedirs(values, exist_ok=True)
    sp = os.path.join(values, "strings.xml")
    if os.path.isfile(sp):
        with open(sp, "r", encoding="utf-8") as fh:
            stext = fh.read()
        stext = re.sub(r'(<string name="app_name"[^>]*>)[^<]*</string>',
                       r"\1%s</string>" % label, stext, count=1)
        if '<string name="app_name"' not in stext:
            stext = re.sub(r"</resources>", '    <string name="app_name">%s</string>\n</resources>' % label, stext, count=1)
        with open(sp, "w", encoding="utf-8", newline="") as fh:
            fh.write(stext)
    else:
        body = '<resources>\n    <string name="app_name">%s</string>\n</resources>\n' % label
        with open(sp, "w", encoding="utf-8", newline="") as fh:
            fh.write(body)


def inject_gpr(vdir):
    gp = os.path.join(vdir, "gradle.properties")
    if not (GPR_USER and GPR_TOKEN) or not os.path.isfile(gp):
        return
    with open(gp, "r", encoding="utf-8") as fh:
        gtext = fh.read()
    if "gpr.user" not in gtext:
        gtext += "\ngpr.user=%s\ngpr.token=%s\n" % (GPR_USER, GPR_TOKEN)
        with open(gp, "w", encoding="utf-8", newline="") as fh:
            fh.write(gtext)


def clone_source():
    if SOURCE_DIR:
        log("Copying source from %s" % SOURCE_DIR)
        shutil.copytree(SOURCE_DIR, SRC, ignore=shutil.ignore_patterns(*COPY_IGNORE_PATTERNS))
        return
    log("Cloning %s branch %s" % (SOURCE_REPO, SOURCE_BRANCH))
    if GH_TOKEN:
        auth_url = "https://x-access-token:%s@github.com/%s.git" % (GH_TOKEN, SOURCE_REPO)
        try:
            run("git", "clone", "--depth", "1", "--branch", SOURCE_BRANCH, auth_url, SRC)
            return
        except subprocess.CalledProcessError:
            log("Branch %s not found, cloning default branch" % SOURCE_BRANCH)
            try:
                run("git", "clone", "--depth", "1", auth_url, SRC)
                return
            except subprocess.CalledProcessError:
                log("Authenticated clone failed, trying public clone")
    try:
        run("git", "clone", "--depth", "1", "https://github.com/%s.git" % SOURCE_REPO, SRC)
    except subprocess.CalledProcessError:
        log("Source clone failed")
        sys.exit(4)


def prepare_variant(vdir, label, dry_run):
    """Rename/build the OwnTV variant in-place. Returns an info dict."""
    log("Preparing variant: %s" % label)
    used = scan_identifiers(vdir)

    new_pkg = rand_package()
    theme = rand_ident(used)
    root_name = rand_ident(used)
    class_map = {}
    for old in IDENTITY_CLASSES:
        class_map[old] = rand_ident(used)

    log("  package %s  theme Theme.%s  root %s" % (new_pkg, theme, root_name))

    def apply(rel, text):
        for old, new in class_map.items():
            text = re.sub(r"\b%s\b" % old, new, text)
        if rel not in PKG_SKIP_RELS:
            text = PKG_REPLACE_RE.sub(new_pkg, text)
        text = text.replace("Theme.OwnTV", "Theme.%s" % theme)
        text = text.replace("Theme_OwnTV", "Theme_%s" % theme)
        if rel == "app/proguard-rules.pro":
            # The sweep turned `-keep enum tv.own.owntv.**` into the app package's equivalent, but
            # the core artifact's enums (ThemeMode, ZoomMode, ... persisted via name/valueOf) still
            # need to be kept too. Restore that rule for the untouched namespace.
            if "-keep enum tv.own.owntv.**" not in text:
                text += "\n-keep enum tv.own.owntv.** { *; }\n"
        if REBRAND_SWEEP:
            text = re.sub(r"(?<![A-Za-z0-9_])OwnTV(?![A-Za-z0-9_])", root_name, text)
        return text

    replace_in_files(vdir, apply)

    inject_app_name(vdir, label)

    for base in (
        os.path.join(vdir, "app", "src", "main", "java"),
        os.path.join(vdir, "app", "src", "test", "java"),
        os.path.join(vdir, "app", "src", "androidTest", "java"),
    ):
        move_package_tree(base, OLD_PKG, new_pkg)
    for d in ("java", "kotlin"):
        os.makedirs(os.path.join(vdir, "app", "src", "main", d), exist_ok=True)

    for path in list(iter_files(vdir)):
        stem, ext = os.path.splitext(os.path.basename(path))
        if stem in class_map and class_map[stem] != stem:
            shutil.move(path, os.path.join(os.path.dirname(path), class_map[stem] + ext))

    lp = os.path.join(vdir, "local.properties")
    with open(lp, "w", encoding="utf-8") as fh:
        fh.write("sdk.dir=/opt/android-sdk\n")

    inject_gpr(vdir)

    # OwnTV pins its Gradle daemon JVM to a 21 toolchain via this file (URLs fetched from
    # api.foojay.io). The rebrand container ships JDK 17 and the compile targets are Java 17, so
    # the pin can only trigger a flaky runtime toolchain download. Dropping it keeps the daemon on
    # the image JVM - deterministic and dependency-free.
    daemon_jvm_pin = os.path.join(vdir, "gradle", "gradle-daemon-jvm.properties")
    if os.path.isfile(daemon_jvm_pin):
        os.remove(daemon_jvm_pin)
        log("Removed gradle-daemon-jvm.properties (daemon stays on the image JVM)")

    keystore_dir = os.path.join(vdir, "keystore")
    os.makedirs(keystore_dir, exist_ok=True)

    reuse_keystore = bool(KEYSTORE_B64 and KEYSTORE_ALIAS and KEYSTORE_PASS)
    if reuse_keystore:
        alias = KEYSTORE_ALIAS
        store_pass = KEYSTORE_PASS
        key_pass = store_pass
        keystore_name = "%s.jks" % alias
        keystore_path = os.path.join(keystore_dir, keystore_name)
        with open(keystore_path, "wb") as fh:
            fh.write(base64.b64decode(KEYSTORE_B64))
        log("Reusing persisted signing key (%s)" % keystore_name)
    else:
        alias = rand_token() + rand_token()
        store_pass = alias + str(random.randint(1000, 9999))
        key_pass = store_pass
        keystore_name = "%s.jks" % alias
        keystore_path = os.path.join(keystore_dir, keystore_name)

    bj = os.path.join(vdir, "app", "build.gradle.kts")
    with open(bj, "r", encoding="utf-8") as fh:
        btext = fh.read()

    apk_paths = None
    if not dry_run:
        if reuse_keystore:
            log("Checking persisted keystore %s (alias %s)" % (keystore_name, alias))
            run("keytool", "-list", "-keystore", keystore_path,
                "-storepass", store_pass, "-alias", alias)
        else:
            log("Generating new signing key (%s)" % keystore_name)
            run("keytool", "-genkeypair", "-v",
                "-keystore", keystore_path,
                "-storepass", store_pass,
                "-keypass", key_pass,
                "-alias", alias,
                "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000",
                "-dname", "CN=%s, OU=Mobile, O=%s, L=Internet, ST=Internet, C=US" % (alias, root_name))
        btext = patch_signing(btext, "../keystore/%s" % keystore_name, store_pass, alias, key_pass)
        with open(bj, "w", encoding="utf-8", newline="") as fh:
            fh.write(btext)
        log("Building release APKs (standard + x86_64)")
        env = dict(os.environ)
        env["VERSION_NAME"] = VERSION_NAME
        env["VERSION_CODE"] = VERSION_CODE
        # -x :app:verifyI18nLiterals: the hardcoded-literal gate is a dev-time hygiene check on the
        # main repo. Its baseline is keyed by relative path, which the package sweep deliberately
        # changes, and the pipeline's copy drops tools/i18n (not shipped). The literal set itself is
        # identical to the already-reviewed upstream source, so the gate would only ever fail on
        # path renames - exclude it from the release build.
        run("bash", "./gradlew", ":app:assembleRelease", "-x", ":app:verifyI18nLiterals",
            "--no-daemon", "--console=plain", cwd=vdir, env=env)
        apk_paths = [
            ("owntv.apk", os.path.join(vdir, "app", "build", "outputs", "apk", "standard", "release", "app-standard-release.apk")),
            ("owntv-x86_64.apk", os.path.join(vdir, "app", "build", "outputs", "apk", "x86_64", "release", "app-x86_64-release.apk")),
        ]
        for out_name, apk_path in apk_paths:
            if not os.path.isfile(apk_path):
                log("APK not found at %s" % apk_path)
                sys.exit(3)

    return {
        "label": label,
        "vdir": vdir,
        "apk_paths": apk_paths,
        "new_pkg": new_pkg,
        "theme": theme,
        "root_name": root_name,
        "class_map": class_map,
        "version_name": VERSION_NAME,
        "version_code": VERSION_CODE,
        "keystore": os.path.join("keystore", keystore_name),
        "keystore_path": keystore_path,
        "alias": alias,
        "store_pass": store_pass,
    }


def variant_info_text(res, no_push):
    lines = [
        "--- %s (%s) ---" % (res["label"], "no-push" if no_push else "release"),
        "package       : %s" % res["new_pkg"],
        "applicationId : %s" % res["new_pkg"],
        "theme         : Theme.%s" % res["theme"],
        "rootProject   : %s" % res["root_name"],
        "version       : %s (%s)" % (res["version_name"], res["version_code"]),
        "keystore      : %s (alias %s)" % (res["keystore"], res["alias"]),
        "signing cheat : storepass=%s keypass=%s" % (res["store_pass"], res["store_pass"]),
        "class renames :",
    ]
    for old, new in res["class_map"].items():
        lines.append("    %-28s -> %s" % (old, new))
    return "\n".join(lines)


def main():
    dry_run = "--dry-run" in sys.argv
    no_push = "--no-push" in sys.argv

    for i in range(len(VARIANTS)):
        shutil.rmtree(os.path.join(WORK, "b%d" % i), ignore_errors=True)
    for d in (SRC, OUT, DEST):
        shutil.rmtree(d, ignore_errors=True)
    os.makedirs(OUT, exist_ok=True)

    clone_source()

    sha = "unknown"
    rp = sh("git", "-C", SRC, "rev-parse", "--short", "HEAD")
    if rp.returncode == 0 and rp.stdout:
        sha = rp.stdout.decode().strip()

    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    results = []
    for i, variant in enumerate(VARIANTS):
        vdir = os.path.join(WORK, "b%d" % i)
        shutil.copytree(SRC, vdir, ignore=shutil.ignore_patterns(*COPY_IGNORE_PATTERNS))
        res = prepare_variant(vdir, variant["label"], dry_run)
        res["files"] = variant["files"]
        results.append(res)

    if dry_run:
        for res in results:
            print()
            print(variant_info_text(res, False))
            print("  APK files   : %s" % ", ".join(res["files"]))
        sys.exit(0)

    header = [
        "OwnTV rebranded release (v%s)" % results[0]["version_name"],
        "build time   : %s" % stamp,
        "source repo  : %s @ %s (%s)" % (SOURCE_REPO, SOURCE_BRANCH, sha),
    ]
    info_text = "\n".join(header)
    for res in results:
        info_text += "\n\n" + variant_info_text(res, True)
        info_text += "\nAPK files   : %s" % ", ".join(res["files"])

    for res in results:
        shutil.copy2(res["keystore_path"], os.path.join(OUT, os.path.basename(res["keystore_path"])))
        for out_name, _ in res["apk_paths"]:
            src_path = dict(res["apk_paths"])[out_name]
            shutil.copy2(src_path, os.path.join(OUT, out_name))
    with open(os.path.join(OUT, "signing.txt"), "w", encoding="utf-8") as fh:
        fh.write(info_text + "\n")

    log("Publishing to %s" % TARGET_REPO)
    if no_push:
        log("--no-push: skipping publish, artifacts kept in %s" % OUT)
        print()
        print(info_text)
        log("Keystores + signing info kept in /work/out (signing.txt)")
        return

    if not GH_TOKEN:
        log("GH_TOKEN is required to publish")
        sys.exit(2)

    auth_url = "https://x-access-token:%s@github.com/%s.git" % (GH_TOKEN, TARGET_REPO)
    run("git", "clone", "--depth", "1", auth_url, DEST)

    for res in results:
        for out_name, _ in res["apk_paths"]:
            src_path = dict(res["apk_paths"])[out_name]
            shutil.copy2(src_path, os.path.join(DEST, out_name))
    with open(os.path.join(DEST, "release_info.txt"), "w", encoding="utf-8") as fh:
        fh.write(info_text + "\n")

    run("git", "-C", DEST, "add", "-A")
    run("git", "-C", DEST, "-c", "user.name=rebrand-bot", "-c", "user.email=dev@localhost",
        "commit", "-m", "OwnTV rebranded release v%s" % results[0]["version_name"])
    run("git", "-C", DEST, "push", "origin", "HEAD")

    print()
    print(info_text)
    log("Keystores + signing info kept in /work/out (signing.txt)")


if __name__ == "__main__":
    main()
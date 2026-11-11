#!/usr/bin/env bash
# <summary>
# Packaging build for Pixelate, a Python desktop application that runs from
# source inside a virtual environment. Builds two versioned zips from the
# project root plus the local run copy:
#   Pixelate Dist/pixelate-v<version>.zip       the runnable tree: the pixelate
#                                                package, requirements.txt, the
#                                                menu installer, VERSION, LICENSE
#   Pixelate Git/pixelate-v<version>-src.zip    the GitHub bound source tree
#   Pixelate App/                                mirror of the Dist zip contents
# </summary>
# <param name="--no-bump">
# Ship the VERSION file verbatim instead of moving the build segment on. For
# minor and major releases: hand edit VERSION to e.g. 1.1.0, build once with
# --no-bump, and later builds resume incrementing from there (1.1.1, ...).
# </param>
# <param name="--skip-tests">
# Emergency only. Packages without running the test suite and prints
# UNVERIFIED BUILD on every line of output that matters. Never used to ship
# around a red suite.
# </param>
# <remarks>
# Run from anywhere:  ./build-zip.sh
#
# There is no compile step: the deployable is the source tree itself, so the
# Dist zip is a filtered copy of the checkout and whoever unpacks it creates a
# .venv from requirements.txt (README.md has the two commands). No Vite folder,
# no Apache mirror, nothing served. Nothing here publishes, pushes or tags: the
# zips are left on disk for a human to look at.
#
# Order matters. The new version number is computed first so both zips carry
# it, but VERSION on disk is only written at the very end, once every step has
# succeeded: a failed build must not eat a number. The tests run before
# anything is staged, and a red suite stops the build before a single zip is
# written. Each staged tree is then checked rather than trusted: the names
# listed in .git/info/exclude must be absent, no dash characters, no
# attribution trailer, no secret looking file names or contents, and no hit against the
# machine level publish denylist.
#
# The local only names (the editor notes and the packaging notes) are read from
# .git/info/exclude at build time and never written here, because this script
# ships inside the source zip. If that file yields no patterns the build stops.
# </remarks>
set -euo pipefail

NO_BUMP=0
SKIP_TESTS=0
for arg in "$@"; do
    case "$arg" in
        --no-bump) NO_BUMP=1 ;;
        --skip-tests) SKIP_TESTS=1 ;;
        *) echo "Unknown option: $arg (supported: --no-bump, --skip-tests)" >&2; exit 2 ;;
    esac
done

# <summary>Prints a warning to stderr and carries on.</summary>
warn() { echo "WARNING: $*" >&2; }
# <summary>Prints an error to stderr and stops the build with exit status 1.</summary>
die()  { echo "ERROR: $*" >&2; exit 1; }

root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
name='pixelate'
dist_dir="$root/Pixelate Dist"
git_dir="$root/Pixelate Git"
app_dir="$root/Pixelate App"

command -v zip   >/dev/null || die "zip not found: sudo apt install zip"
command -v unzip >/dev/null || die "unzip not found: sudo apt install unzip"
command -v rsync >/dev/null || die "rsync not found: sudo apt install rsync"
command -v git   >/dev/null || die "git not found: sudo apt install git"
[[ -d "$root/.git" ]] || die "$root is not a git working tree"

# Prefer a virtual environment outside the synced tree ($PIXELATE_VENV, default
# ~/.venvs/pixelate), then the project's own .venv, otherwise whatever python3
# is on the path. The tests need pytest, PySide6, Pillow and bleak:
#   python3 -m venv ~/.venvs/pixelate
#   ~/.venvs/pixelate/bin/pip install -r requirements.txt pytest
venv="${PIXELATE_VENV:-$HOME/.venvs/$name}"
if [[ -x "$venv/bin/python" ]]; then
    python="$venv/bin/python"
elif [[ -x "$root/.venv/bin/python" ]]; then
    python="$root/.venv/bin/python"
elif command -v python3 >/dev/null; then
    python="$(command -v python3)"
    warn "no virtual environment found, using $python"
else
    die "python3 not found"
fi

# Version scheme is major.minor.BUILD: every packaged build moves the third
# segment on (1.0.0 to 1.0.1). The bumped number is used throughout the build
# but only written back to VERSION at the very end, once every packaging step
# has succeeded. Major and minor move only when Rob says so: edit VERSION by
# hand for those and build once with --no-bump.
version_file="$root/VERSION"
version="$(tr -d '[:space:]' < "$version_file")"
[[ "$version" =~ ^([0-9]+)\.([0-9]+)\.([0-9]+)$ ]] \
    || die "VERSION '$version' is not major.minor.build"
if [[ $NO_BUMP -eq 1 ]]; then
    echo "Building v$version (hand set, --no-bump)"
else
    version="${BASH_REMATCH[1]}.${BASH_REMATCH[2]}.$((BASH_REMATCH[3] + 1))"
    echo "Building v$version (build segment moved on)"
fi

stage="$(mktemp -d "${TMPDIR:-/tmp}/$name-build.XXXXXX")"
trap 'rm -rf "$stage"' EXIT

# The machine level denylist of personal identifiers. Every publish scan reads
# it and refuses to run without it. Blank lines are dropped before use because
# an empty pattern matches every line, which would turn the scan into noise.
denylist="$HOME/.config/github/publish-denylist"
[[ -s "$denylist" ]] || die "publish denylist not found at $denylist: refusing to package"
denylist_clean="$stage/denylist"
command grep -v -E '^[[:space:]]*$' "$denylist" > "$denylist_clean"
[[ -s "$denylist_clean" ]] || die "publish denylist is empty: refusing to package"

# Local only exclusions live in .git/info/exclude, not here and not in
# .gitignore, because both this script and .gitignore ship in the source zip
# and a published file must not carry those names. Read them at build time so
# the zips match what git ignores. No patterns means the protection is gone,
# so the build stops rather than packaging without it.
local_excludes=()
local_patterns=()
if [[ -f "$root/.git/info/exclude" ]]; then
    while IFS= read -r line || [[ -n "$line" ]]; do
        line="${line%$'\r'}"
        [[ -z "${line// }" || "$line" == \#* ]] && continue
        local_excludes+=(--exclude="$line")
        local_patterns+=("$line")
    done < "$root/.git/info/exclude"
fi
[[ ${#local_patterns[@]} -gt 0 ]] || die "no local exclude patterns found in .git/info/exclude: refusing to package"

# <summary>Fails the build if any .git/info/exclude name reached a staged tree.</summary>
# <param name="$1">The staged directory to search.</param>
# <remarks>Fail closed: an exclusion that silently stopped working is the whole
# risk, so the stage is searched rather than the patterns trusted.</remarks>
assert_local_excluded() {
    local dir="$1" pat clean
    for pat in "${local_patterns[@]}"; do
        clean="${pat%/}"; clean="${clean#/}"
        if [[ -n "$(find "$dir" -name "$clean" -print -quit 2>/dev/null)" ]]; then
            die "a locally excluded entry reached the stage ($clean): refusing to package"
        fi
    done
}

# <summary>The house rules gate, run over each staged tree before it is zipped.</summary>
# <param name="$1">The staged directory to check.</param>
# <param name="$2">A label for messages (dist or src).</param>
# <remarks>
# Checks, in order: no .git, virtual environment or cache directories; no file
# whose name looks like a credential; no em dash or en dash in any text file
# (matched as UTF-8 bytes under LC_ALL=C so the check does not depend on the
# locale); no attribution trailer; no placeholder or real secret in any text
# file; no hit against the publish denylist. Denylist hits print the file name
# only, never the matching text. The literal patterns below are split with
# empty quotes so this script does not trip its own checks when it is staged,
# and the editor vendor's name in the trailer pattern is built from character
# codes so it is never written out in a published file (gold rule 4).
# </remarks>
house_rules_gate() {
    local dir="$1" label="$2" hits vendor
    vendor="$(printf '\x63\x6c\x61\x75\x64\x65')"
    hits="$(find "$dir" \( -name '.git' -o -name '.venv*' -o -name '__pycache__' -o -name '.pytest_cache' -o -name 'node_modules' \) -print -quit)"
    [[ -z "$hits" ]] || die "$label stage contains a generated or repository directory: $hits"

    hits="$(find "$dir" -type f \( -iname '*secret*' -o -iname '*password*' -o -iname '*credential*' -o -iname '*cred*' \
            -o -iname '*token*' -o -iname '*sftp*' -o -iname '*ftp*' -o -iname '.env*' -o -iname '*.pem' -o -iname '*.key' \
            -o -iname '*.pfx' -o -iname 'id_rsa*' -o -iname 'connection.txt' -o -iname '*config.local*' \) -print)"
    [[ -z "$hits" ]] || die "$label stage contains a secret looking file name:"$'\n'"$hits"

    hits="$(cd "$dir" && LC_ALL=C grep -rIl -P '\xE2\x80[\x93\x94]' . || true)"
    [[ -z "$hits" ]] || die "$label stage contains dash characters in:"$'\n'"$hits"

    hits="$(cd "$dir" && grep -rIl -i -e 'co-authored''-by' -e "generated with $vendor" -e "generated by $vendor" . || true)"
    [[ -z "$hits" ]] || die "$label stage contains an attribution trailer in:"$'\n'"$hits"

    local placeholder_re='CHANGE''_?ME|REPLACE''_?ME|YOUR''_PASSWORD|YOUR''_API_KEY|BEGIN (RSA |OPENSSH |EC |DSA )?PRIVATE'' KEY|ghp''_[A-Za-z0-9]{20,}|AKIA''[0-9A-Z]{16}'
    hits="$(cd "$dir" && grep -rIl -E "$placeholder_re" . || true)"
    [[ -z "$hits" ]] || die "$label stage contains a placeholder or real secret in:"$'\n'"$hits"

    hits="$(cd "$dir" && command grep -rIl -i -F -f "$denylist_clean" . || true)"
    [[ -z "$hits" ]] || die "$label stage matches the publish denylist in (contents not shown):"$'\n'"$hits"

    echo "House rules gate passed for the $label stage"
}

# Excluded from BOTH zips. Nothing secret lives in this tree (the panel address
# sits in QSettings, outside the repo) so these are a backstop, listed in both
# cases because rsync and zip matching are case sensitive. The ordinary
# exclusions come from .gitignore through the rsync filter so there is one hand
# kept list, with the convention folders and .git/ named again as a belt.
secret_excludes=(--exclude='.env*' --exclude='.ENV*'
                 --exclude='*credential*' --exclude='*CREDENTIAL*' --exclude='*cred*' --exclude='*CRED*'
                 --exclude='*password*' --exclude='*PASSWORD*' --exclude='*secret*' --exclude='*SECRET*'
                 --exclude='*token*' --exclude='*TOKEN*' --exclude='*sftp*' --exclude='*SFTP*'
                 --exclude='*ftp*' --exclude='*FTP*' --exclude='*.pem' --exclude='*.PEM'
                 --exclude='*.key' --exclude='*.KEY' --exclude='*.pfx' --exclude='*.PFX'
                 --exclude='id_rsa*' --exclude='ID_RSA*' --exclude='connection.txt' --exclude='CONNECTION.TXT' --exclude='*config.local*' --exclude='*CONFIG.LOCAL*')
exclude_dirs=(--exclude='.git/' --exclude='Pixelate Dist/' --exclude='Pixelate Git/' --exclude='Pixelate App/'
              --exclude='.venv*/' --exclude='__pycache__/' --exclude='*.pyc' --exclude='.pytest_cache/'
              --exclude='presets-out/' --exclude='node_modules/' --exclude='coverage/'
              --exclude='*.zip' --exclude='*.7z' --exclude='*.tmp' --exclude='*.log' --exclude='*.bak'
              --exclude='$RECYCLE.BIN/' --exclude='@eaDir/' --exclude='Thumbs.db' --exclude='.DS_Store')

# Housekeeping that ships in the Git zip but never in the Dist zip or the App
# mirror. VERSION and LICENSE are deliberately NOT here: the About dialog reads
# VERSION at run time and points the user at LICENSE, so both ship (recorded in
# the packaging notes).
housekeeping_excludes=(--exclude='/README.md' --exclude='/HANDOVER.md' --exclude='/CHANGELOG.md'
                       --exclude='/.gitignore' --exclude='/.gitattributes' --exclude='/.stignore'
                       --exclude='/build-zip.sh' --exclude='/tests/' --exclude='/docs/'
                       --exclude='*.example.*')

# Strays: flagged on every build, never deleted. A second virtual environment
# left behind by another machine is the known one; anything matching .venv*
# other than .venv itself is reported here and excluded above.
while IFS= read -r stray; do
    warn "stray virtual environment excluded, not deleted: $(basename "$stray")"
done < <(find "$root" -maxdepth 1 -name '.venv*' ! -name '.venv' -print)

# <summary>Zips a staged directory into a fresh archive, replacing a same version zip.</summary>
# <param name="$1">The staged directory whose contents become the archive root.</param>
# <param name="$2">The destination zip path.</param>
make_zip() {
    local source_dir="$1" destination="$2"
    rm -f "$destination"
    (cd "$source_dir" && zip -qr "$destination" .)
}

# <summary>Prints a zip's size in megabytes to two decimal places.</summary>
# <param name="$1">The zip path.</param>
zip_size_mb() {
    local bytes
    bytes="$(stat -c%s "$1")"
    awk -v b="$bytes" 'BEGIN { printf "%.2f", b / 1048576 }'
}

# <summary>Copies the checkout into a stage with the shared exclusions and normalised permissions.</summary>
# <param name="$1">The stage directory to fill.</param>
# <param name="$@">Any further rsync exclusions (the Dist stage adds the housekeeping set).</param>
# <remarks>Permissions are normalised to D755,F644 and the execute bit is then put
# back on shell scripts only, so the menu installer stays runnable.</remarks>
stage_tree() {
    local dest="$1"; shift
    rsync -a --chmod=D755,F644 --filter=':- .gitignore' \
          "${local_excludes[@]}" "${secret_excludes[@]}" "${exclude_dirs[@]}" "$@" \
          "$root/" "$dest/"
    find "$dest" -type f -name '*.sh' -exec chmod 755 {} +
    printf '%s\n' "$version" > "$dest/VERSION"
    [[ "$(tr -d '[:space:]' < "$dest/VERSION")" == "$version" ]] || die "version stamp failed in $dest"
}

mkdir -p "$dist_dir" "$git_dir"

# 1) Tests. A red suite stops the build before anything is staged. The Qt
#    tests run on the offscreen platform so no display is needed.
if [[ $SKIP_TESTS -eq 1 ]]; then
    warn "UNVERIFIED BUILD: tests skipped on request"
else
    echo "Running the test suite with $python"
    (cd "$root" && QT_QPA_PLATFORM=offscreen "$python" -m pytest -q tests) || die "tests failed: not packaging"
fi

# 2) Dist zip: the runnable tree only, plus VERSION and LICENSE.
dist_stage="$stage/dist"
stage_tree "$dist_stage" "${housekeeping_excludes[@]}"
assert_local_excluded "$dist_stage"
house_rules_gate "$dist_stage" dist
[[ -f "$dist_stage/pixelate/__main__.py" && -f "$dist_stage/requirements.txt" ]] \
    || die "the Dist stage is missing the application"
dist_zip="$dist_dir/$name-v$version.zip"
make_zip "$dist_stage" "$dist_zip"
echo "Built: $dist_zip ($(zip_size_mb "$dist_zip") MB)"

# 3) App mirror: an exact copy of the Dist zip contents. --delete removes
#    anything not in the stage, so never hand edit this folder; fix the source
#    and rebuild. It needs its own .venv to run (see README.md).
mkdir -p "$app_dir"
rsync -a --delete "$dist_stage/" "$app_dir/"
echo "Mirrored: $app_dir"

# 4) Git zip: the full source tree, housekeeping and tests included, carrying
#    the new version even though the repo copy is not written until the end.
src_stage="$stage/src"
stage_tree "$src_stage"
assert_local_excluded "$src_stage"
house_rules_gate "$src_stage" src
git_zip="$git_dir/$name-v$version-src.zip"
make_zip "$src_stage" "$git_zip"
echo "Built: $git_zip ($(zip_size_mb "$git_zip") MB)"

# 5) Verify by inspection, not by trust. Both listings are searched for secret
#    and repository names and for every .git/info/exclude pattern; the Git zip
#    list is compared with what git would commit (tracked files plus untracked
#    files that are not ignored, so a new script shows up before its first
#    commit); the App mirror is diffed against the extracted Dist zip.
echo "Verifying the zips"
for z in "$dist_zip" "$git_zip"; do
    listing="$(unzip -Z1 "$z")"
    if echo "$listing" | grep -i -E 'cred|secret|pass|token|htaccess|\.env|\.git/|node_modules|__pycache__|\.venv|\.pytest_cache' ; then
        die "a secret or repository name is in $(basename "$z")"
    fi
    for pat in "${local_patterns[@]}"; do
        clean="${pat%/}"; clean="${clean#/}"
        if echo "$listing" | grep -q -i -F "$clean" ; then
            die "a locally excluded name ($clean) is in $(basename "$z")"
        fi
    done
    if echo "$listing" | grep -q '\\' ; then
        die "backslash path in $(basename "$z")"
    fi
done
expected_list="$stage/expected.txt"
actual_list="$stage/actual.txt"
(cd "$root" && git ls-files --cached --others --exclude-standard | sort) > "$expected_list"
unzip -Z1 "$git_zip" | grep -v '/$' | sort > "$actual_list"
if ! diff "$expected_list" "$actual_list" > "$stage/list.diff"; then
    cat "$stage/list.diff" >&2
    die "the Git zip does not match the git file list (< expected from git, > found in zip)"
fi
echo "Git zip matches the git file list ($(wc -l < "$actual_list") files)"
unzip -q "$dist_zip" -d "$stage/dist-check"
diff -r "$stage/dist-check" "$app_dir" || die "the App mirror differs from the Dist zip"
echo "App mirror matches the Dist zip"

# 6) Everything above succeeded, so the bumped number may now be persisted.
if [[ $NO_BUMP -eq 0 ]]; then
    printf '%s\n' "$version" > "$version_file"
    echo "Recorded: VERSION now says $version"
fi
[[ $SKIP_TESTS -eq 0 ]] || warn "UNVERIFIED BUILD: v$version was packaged without running the tests"
echo "Done: v$version"

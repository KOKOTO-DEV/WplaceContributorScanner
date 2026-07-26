#!/usr/bin/env sh
set -eu

cd "$(dirname "$0")"
PROJECT_ROOT="$(pwd -P)"
VENV_MARKER=".venv/.wplace_venv_root"
MIN_PYTHON_MAJOR=3
MIN_PYTHON_MINOR=10

# Console language: --lang, WPCS_LANG, then the operating-system locale.
LANG_ARG=""
NEXT_IS_LANG=0
for arg in "$@"; do
  if [ "$NEXT_IS_LANG" -eq 1 ]; then LANG_ARG="$arg"; NEXT_IS_LANG=0; continue; fi
  case "$arg" in
    --lang) NEXT_IS_LANG=1 ;;
    --lang=*) LANG_ARG=${arg#--lang=} ;;
  esac
done
RAW_LANG="${LANG_ARG:-${WPCS_LANG:-${LC_ALL:-${LC_MESSAGES:-${LANG:-en}}}}}"
case "$RAW_LANG" in
  ko*|KO*) WPCS_LANG=ko ;;
  ja*|JA*) WPCS_LANG=ja ;;
  zh*|ZH*) WPCS_LANG=zh-CN ;;
  *) WPCS_LANG=en ;;
esac
export WPCS_LANG

msg() {
  key=$1
  case "$WPCS_LANG:$key" in
    ko:python_used) echo "[설정] 사용할 Python: $2" ;;
    en:python_used) echo "[setup] Python: $2" ;;
    ja:python_used) echo "[設定] 使用する Python: $2" ;;
    zh-CN:python_used) echo "[设置] 使用的 Python：$2" ;;
    ko:create_venv) echo "[설정] Python 가상환경을 생성합니다." ;;
    en:create_venv) echo "[setup] Creating the Python virtual environment." ;;
    ja:create_venv) echo "[設定] Python 仮想環境を作成します。" ;;
    zh-CN:create_venv) echo "[设置] 正在创建 Python 虚拟环境。" ;;
    ko:virtualenv_ok) echo "[설정] virtualenv로 가상환경을 생성했습니다." ;;
    en:virtualenv_ok) echo "[setup] Created the environment with virtualenv." ;;
    ja:virtualenv_ok) echo "[設定] virtualenv で仮想環境を作成しました。" ;;
    zh-CN:virtualenv_ok) echo "[设置] 已使用 virtualenv 创建环境。" ;;
    ko:ensurepip) echo "[설정] 가상환경에 pip가 없어 ensurepip 복구를 시도합니다." ;;
    en:ensurepip) echo "[setup] pip is missing from the environment; trying ensurepip." ;;
    ja:ensurepip) echo "[設定] 仮想環境に pip がないため ensurepip を試します。" ;;
    zh-CN:ensurepip) echo "[设置] 虚拟环境中没有 pip，正在尝试 ensurepip。" ;;
    ko:moved_venv) echo "[설정] 다른 경로에서 복사·이동된 기존 .venv를 감지해 다시 생성합니다." ;;
    en:moved_venv) echo "[setup] The existing .venv was copied or moved from another path; recreating it." ;;
    ja:moved_venv) echo "[設定] 別のパスからコピー・移動された .venv を検出したため再作成します。" ;;
    zh-CN:moved_venv) echo "[设置] 检测到从其他路径复制或移动的 .venv，正在重新创建。" ;;
    ko:bad_venv) echo "[설정] 호환되지 않거나 불완전한 기존 .venv를 감지해 다시 생성합니다." ;;
    en:bad_venv) echo "[setup] The existing .venv is incompatible or incomplete; recreating it." ;;
    ja:bad_venv) echo "[設定] 互換性がない、または不完全な .venv を検出したため再作成します。" ;;
    zh-CN:bad_venv) echo "[设置] 检测到不兼容或不完整的 .venv，正在重新创建。" ;;
    ko:pip_fail) echo "오류: pip 업데이트에 실패했습니다." >&2 ;;
    en:pip_fail) echo "Error: Failed to update pip." >&2 ;;
    ja:pip_fail) echo "エラー: pip の更新に失敗しました。" >&2 ;;
    zh-CN:pip_fail) echo "错误：pip 更新失败。" >&2 ;;
    ko:packages_fail) echo "오류: Python 패키지 설치에 실패했습니다." >&2 ;;
    en:packages_fail) echo "Error: Failed to install Python packages." >&2 ;;
    ja:packages_fail) echo "エラー: Python パッケージのインストールに失敗しました。" >&2 ;;
    zh-CN:packages_fail) echo "错误：Python 软件包安装失败。" >&2 ;;
    ko:python_version) echo "오류: 사용할 수 있는 Python 3.10 이상을 찾지 못했습니다." >&2 ;;
    en:python_version) echo "Error: No usable Python 3.10 or newer was found." >&2 ;;
    ja:python_version) echo "エラー: 使用可能な Python 3.10 以降が見つかりません。" >&2 ;;
    zh-CN:python_version) echo "错误：未找到可用的 Python 3.10 或更高版本。" >&2 ;;
  esac
}

show_python_help() {
  case "$WPCS_LANG" in
    ko) cat >&2 <<'HELP'
Python 3.10 이상이 필요합니다. 버전을 확인한 뒤 운영체제에 맞게 설치하세요.
  python3 --version
Ubuntu/Debian: sudo apt install -y python3-venv python3-pip
Rocky/Alma/RHEL: sudo dnf install -y python3.11 python3.11-pip
설치 후: rm -rf .venv && ./start_linux.sh
다른 Python 지정: PYTHON_BIN=python3.11 ./start_linux.sh
HELP
      ;;
    ja) cat >&2 <<'HELP'
Python 3.10 以降が必要です。バージョンを確認し、OS に合わせてインストールしてください。
  python3 --version
Ubuntu/Debian: sudo apt install -y python3-venv python3-pip
Rocky/Alma/RHEL: sudo dnf install -y python3.11 python3.11-pip
インストール後: rm -rf .venv && ./start_linux.sh
別の Python を指定: PYTHON_BIN=python3.11 ./start_linux.sh
HELP
      ;;
    zh-CN) cat >&2 <<'HELP'
需要 Python 3.10 或更高版本。请先检查版本，并按操作系统安装。
  python3 --version
Ubuntu/Debian: sudo apt install -y python3-venv python3-pip
Rocky/Alma/RHEL: sudo dnf install -y python3.11 python3.11-pip
安装后：rm -rf .venv && ./start_linux.sh
指定其他 Python：PYTHON_BIN=python3.11 ./start_linux.sh
HELP
      ;;
    *) cat >&2 <<'HELP'
Python 3.10 or newer is required. Check the version and install it for your operating system.
  python3 --version
Ubuntu/Debian: sudo apt install -y python3-venv python3-pip
Rocky/Alma/RHEL: sudo dnf install -y python3.11 python3.11-pip
After installation: rm -rf .venv && ./start_linux.sh
Choose another Python: PYTHON_BIN=python3.11 ./start_linux.sh
HELP
      ;;
  esac
}

show_venv_help() {
  case "$WPCS_LANG" in
    ko) echo "Python venv/pip 패키지를 준비하지 못했습니다. 운영체제의 python3-venv와 python3-pip를 설치한 뒤 .venv를 지우고 다시 실행하세요." >&2 ;;
    ja) echo "Python の venv/pip を準備できませんでした。OS の python3-venv と python3-pip をインストールし、.venv を削除して再実行してください。" >&2 ;;
    zh-CN) echo "无法准备 Python venv/pip。请安装系统的 python3-venv 和 python3-pip，删除 .venv 后重新运行。" >&2 ;;
    *) echo "Could not prepare Python venv/pip. Install the OS packages python3-venv and python3-pip, remove .venv, and run again." >&2 ;;
  esac
}

version_ok() {
  "$1" -c "import sys; raise SystemExit(0 if sys.version_info >= ($MIN_PYTHON_MAJOR, $MIN_PYTHON_MINOR) else 1)" >/dev/null 2>&1
}

REQUESTED_PYTHON_BIN="${PYTHON_BIN:-}"
if [ -n "$REQUESTED_PYTHON_BIN" ]; then CANDIDATES="$REQUESTED_PYTHON_BIN"; else CANDIDATES="python3.13 python3.12 python3.11 python3.10 python3"; fi
PYTHON_BIN=""
for candidate in $CANDIDATES; do
  if command -v "$candidate" >/dev/null 2>&1 && version_ok "$candidate"; then PYTHON_BIN="$candidate"; break; fi
done
if [ -z "$PYTHON_BIN" ]; then msg python_version; show_python_help; exit 1; fi
msg python_used "$($PYTHON_BIN --version 2>&1) ($PYTHON_BIN)"

create_venv() {
  rm -rf .venv
  msg create_venv
  if "$PYTHON_BIN" -m venv .venv; then :
  elif "$PYTHON_BIN" -m virtualenv .venv >/dev/null 2>&1; then msg virtualenv_ok
  else rm -rf .venv; return 1; fi
  if ! .venv/bin/python -m pip --version >/dev/null 2>&1; then
    msg ensurepip
    .venv/bin/python -m ensurepip --upgrade >/dev/null 2>&1 || true
  fi
  .venv/bin/python -m pip --version >/dev/null 2>&1
}

if [ -x .venv/bin/python ]; then
  SAVED_VENV_ROOT=""; [ -f "$VENV_MARKER" ] && SAVED_VENV_ROOT="$(cat "$VENV_MARKER" 2>/dev/null || true)"
  if [ "$SAVED_VENV_ROOT" != "$PROJECT_ROOT" ]; then msg moved_venv; rm -rf .venv
  elif ! version_ok .venv/bin/python || ! .venv/bin/python -m pip --version >/dev/null 2>&1; then msg bad_venv; rm -rf .venv
  fi
fi
if [ ! -x .venv/bin/python ]; then create_venv || { show_venv_help; exit 1; }; fi
printf '%s\n' "$PROJECT_ROOT" > "$VENV_MARKER"
.venv/bin/python -m pip install --disable-pip-version-check --upgrade "pip>=22" || { msg pip_fail; exit 1; }
if ! .venv/bin/python -m pip install --disable-pip-version-check -r requirements.txt; then
  msg packages_fail
  echo "Python: $(.venv/bin/python --version 2>&1)" >&2
  echo "pip: $(.venv/bin/python -m pip --version 2>&1)" >&2
  exit 1
fi
exec .venv/bin/python app.py "$@"

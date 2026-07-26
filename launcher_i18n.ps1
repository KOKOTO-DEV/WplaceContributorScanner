param(
    [string]$Language = "",
    [Parameter(Mandatory = $true)]
    [string]$Key
)

$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8

function Normalize-Language([string]$Value) {
    $text = ($Value + "").Trim().Replace("_", "-").ToLowerInvariant()
    if ($text.StartsWith("ko")) { return "ko" }
    if ($text.StartsWith("ja")) { return "ja" }
    if ($text.StartsWith("zh")) { return "zh-CN" }
    return "en"
}

if ([string]::IsNullOrWhiteSpace($Language)) {
    $Language = [System.Globalization.CultureInfo]::CurrentUICulture.Name
}
$Language = Normalize-Language $Language

$messages = @{
    "ko" = @{
        "recreate" = "[설정] 복사·이동되었거나 호환되지 않는 .venv를 다시 생성합니다."
        "python_required" = "Python 3.10 이상이 필요합니다."
        "python_too_old" = "py 실행기가 선택한 Python이 너무 오래되었습니다. Python 3.10 이상을 설치하세요."
        "create_venv" = "[설정] Python 가상환경을 생성합니다."
        "start_failed" = "Wplace Contributor Scanner를 시작하지 못했습니다."
    }
    "en" = @{
        "recreate" = "[setup] Recreating a copied, moved, incompatible, or incomplete .venv."
        "python_required" = "Python 3.10 or newer is required."
        "python_too_old" = "The Python selected by the py launcher is too old. Install Python 3.10 or newer."
        "create_venv" = "[setup] Creating the Python virtual environment."
        "start_failed" = "Failed to start Wplace Contributor Scanner."
    }
    "ja" = @{
        "recreate" = "[設定] コピー・移動された、または互換性のない .venv を再作成します。"
        "python_required" = "Python 3.10 以降が必要です。"
        "python_too_old" = "py ランチャーが選択した Python が古すぎます。Python 3.10 以降をインストールしてください。"
        "create_venv" = "[設定] Python 仮想環境を作成します。"
        "start_failed" = "Wplace Contributor Scanner を起動できませんでした。"
    }
    "zh-CN" = @{
        "recreate" = "[设置] 正在重新创建已复制、移动或不兼容的 .venv。"
        "python_required" = "需要 Python 3.10 或更高版本。"
        "python_too_old" = "py 启动器选择的 Python 版本过旧，请安装 Python 3.10 或更高版本。"
        "create_venv" = "[设置] 正在创建 Python 虚拟环境。"
        "start_failed" = "无法启动 Wplace Contributor Scanner。"
    }
}

$message = $messages[$Language][$Key]
if ([string]::IsNullOrWhiteSpace($message)) {
    $message = $messages["en"][$Key]
}
if ([string]::IsNullOrWhiteSpace($message)) {
    $message = $Key
}
[Console]::WriteLine($message)

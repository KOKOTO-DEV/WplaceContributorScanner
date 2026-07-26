from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Callable

SUPPORTED_LANGUAGES = {"ko", "en", "ja", "zh-CN"}


def normalize_language(language: str | None) -> str:
    raw = (language or "ko").strip()
    if raw in SUPPORTED_LANGUAGES:
        return raw
    low = raw.lower()
    if low.startswith("ja"):
        return "ja"
    if low.startswith("zh"):
        return "zh-CN"
    if low.startswith("en"):
        return "en"
    return "ko"


# Runtime messages are authored in Korean and translated before they are exposed
# through the HTTP API.
_EXACT: dict[str, dict[str, str]] = {
    "선택된 프로젝트가 없습니다.": {
        "en": "No project is selected.", "ja": "プロジェクトが選択されていません。", "zh-CN": "未选择项目。",
    },
    "JSON 요청이 너무 큽니다.": {
        "en": "The JSON request is too large.", "ja": "JSONリクエストが大きすぎます。", "zh-CN": "JSON 请求过大。",
    },
    "요청 본문이 중간에 끊겼습니다.": {
        "en": "The request body was interrupted.", "ja": "リクエスト本文が途中で切断されました。", "zh-CN": "请求正文中途断开。",
    },
    "업로드가 중간에 끊겼습니다.": {
        "en": "The upload was interrupted.", "ja": "アップロードが途中で切断されました。", "zh-CN": "上传中途断开。",
    },
    "JSON 또는 ZIP 파일만 지원합니다.": {
        "en": "Only JSON or ZIP files are supported.", "ja": "JSONまたはZIPファイルのみ対応しています。", "zh-CN": "仅支持 JSON 或 ZIP 文件。",
    },
    "수집 중에는 준비할 수 없습니다.": {
        "en": "Preparation cannot run while scanning.", "ja": "収集中は準備を実行できません。", "zh-CN": "扫描期间无法执行准备。",
    },
    "수집 또는 현재 그림 비교 중에는 프로젝트를 삭제할 수 없습니다.": {
        "en": "A project cannot be deleted while scanning or comparing the current canvas.",
        "ja": "収集中または現在の画像との比較中はプロジェクトを削除できません。",
        "zh-CN": "扫描或比较当前画布时无法删除项目。",
    },
    "대표 영역과 색상을 분석하는 동안에는 프로젝트를 삭제할 수 없습니다.": {
        "en": "A project cannot be deleted while representative regions and colors are being analyzed.",
        "ja": "代表領域と色を分析している間はプロジェクトを削除できません。",
        "zh-CN": "分析代表区域和颜色时无法删除项目。",
    },
    "수집 중에는 준비 작업을 실행할 수 없습니다.": {
        "en": "The preparation task cannot run while scanning.", "ja": "収集中は準備処理を実行できません。", "zh-CN": "扫描期间无法执行准备任务。",
    },
    "이미 현재 그림 비교 작업이 진행 중입니다.": {
        "en": "A current-canvas comparison is already running.", "ja": "現在画像の比較処理はすでに実行中です。", "zh-CN": "当前画布比较任务已在运行。",
    },
    "현재 그림 비교가 진행 중입니다. 완료 후 자동으로 활성화됩니다.": {
        "en": "The current-canvas comparison is running. This will be enabled automatically when it finishes.",
        "ja": "現在画像の比較中です。完了すると自動的に有効になります。",
        "zh-CN": "正在比较当前画布，完成后会自动启用。",
    },
    "현재 그림과 비교를 실행하거나 수집 시작을 누르세요.": {
        "en": "Run ‘Compare with current canvas’ or start the scan.",
        "ja": "「現在の画像と比較」を実行するか、収集を開始してください。",
        "zh-CN": "请执行“与当前画布比较”或开始扫描。",
    },
    "현재 캔버스 타일을 확인하고 일치 픽셀 목록을 만드는 중입니다.": {
        "en": "Checking current canvas tiles and building the matching-pixel list.",
        "ja": "現在のキャンバスタイルを確認し、一致ピクセル一覧を作成しています。",
        "zh-CN": "正在检查当前画布图块并生成匹配像素列表。",
    },
    "협업 분할 수는 1~1024 범위여야 합니다.": {
        "en": "The collaboration shard count must be between 1 and 1024.",
        "ja": "共同作業の分割数は1～1024の範囲で指定してください。",
        "zh-CN": "协作分片数必须在 1 到 1024 之间。",
    },
    "내 작업 번호는 협업 분할 수보다 작아야 합니다.": {
        "en": "The work number must be smaller than the collaboration shard count.",
        "ja": "自分の作業番号は共同作業の分割数未満である必要があります。",
        "zh-CN": "任务编号必须小于协作分片数。",
    },
    "준비된 프로젝트가 아닙니다.": {
        "en": "The project has not been prepared.", "ja": "準備済みのプロジェクトではありません。", "zh-CN": "项目尚未准备。",
    },
    "먼저 현재 타일 비교를 실행해야 합니다.": {
        "en": "Run the current-tile comparison first.", "ja": "先に現在タイルの比較を実行してください。", "zh-CN": "请先执行当前图块比较。",
    },
    "먼저 현재 그림과 비교하여 후보 목록을 준비하세요.": {
        "en": "Compare with the current canvas first to prepare the candidate list.",
        "ja": "先に現在画像と比較して候補一覧を準備してください。",
        "zh-CN": "请先与当前画布比较以准备候选列表。",
    },
    "수집 중에는 설정을 변경할 수 없습니다.": {
        "en": "Settings cannot be changed while scanning.", "ja": "収集中は設定を変更できません。", "zh-CN": "扫描期间无法修改设置。",
    },
    "요청 간격은 0.1초 이상 3600초 이하여야 합니다.": {
        "en": "The request interval must be between 0.1 and 3600 seconds.",
        "ja": "リクエスト間隔は0.1～3600秒の範囲で指定してください。",
        "zh-CN": "请求间隔必须在 0.1 到 3600 秒之间。",
    },
    "지터 비율은 0~0.5 범위여야 합니다.": {
        "en": "The jitter ratio must be between 0 and 0.5.", "ja": "ジッター比率は0～0.5の範囲で指定してください。", "zh-CN": "抖动比例必须在 0 到 0.5 之间。",
    },
    "타임아웃은 5~180초 범위여야 합니다.": {
        "en": "The timeout must be between 5 and 180 seconds.", "ja": "タイムアウトは5～180秒の範囲で指定してください。", "zh-CN": "超时时间必须在 5 到 180 秒之间。",
    },
    "체크포인트 간격은 10~100000픽셀 범위여야 합니다.": {
        "en": "The checkpoint interval must be between 10 and 100,000 pixels.",
        "ja": "チェックポイント間隔は10～100000ピクセルの範囲で指定してください。",
        "zh-CN": "检查点间隔必须在 10 到 100000 像素之间。",
    },
    "병렬 워커 수는 1~32 범위여야 합니다.": {
        "en": "The parallel worker count must be between 1 and 32.", "ja": "並列ワーカー数は1～32の範囲で指定してください。", "zh-CN": "并行工作线程数必须在 1 到 32 之间。",
    },
    "보호 응답 기본 재시도 대기는 1~86400초 범위여야 합니다.": {
        "en": "The initial protective-response retry delay must be between 1 and 86,400 seconds.",
        "ja": "保護応答の初回再試行待機は1～86400秒の範囲で指定してください。",
        "zh-CN": "保护响应初始重试等待必须在 1 到 86400 秒之间。",
    },
    "보호 응답 최대 재시도 대기는 1~604800초 범위여야 합니다.": {
        "en": "The maximum protective-response retry delay must be between 1 and 604,800 seconds.",
        "ja": "保護応答の最大再試行待機は1～604800秒の範囲で指定してください。",
        "zh-CN": "保护响应最大重试等待必须在 1 到 604800 秒之间。",
    },
    "보호 응답 최대 재시도 횟수는 0~100000 범위여야 합니다.": {
        "en": "The maximum protective-response retry count must be between 0 and 100,000.",
        "ja": "保護応答の最大再試行回数は0～100000の範囲で指定してください。",
        "zh-CN": "保护响应最大重试次数必须在 0 到 100000 之间。",
    },
    "보호 응답 최대 대기는 기본 대기보다 작을 수 없습니다.": {
        "en": "The maximum protective-response delay cannot be shorter than the initial delay.",
        "ja": "保護応答の最大待機時間は初回待機時間より短くできません。",
        "zh-CN": "保护响应最大等待时间不能小于初始等待时间。",
    },
    "프로젝트 이름을 입력하세요.": {
        "en": "Enter a project name.",
        "ja": "プロジェクト名を入力してください。",
        "zh-CN": "请输入项目名称。",
    },
    "프로젝트 이름은 120자 이하여야 합니다.": {
        "en": "The project name must be 120 characters or fewer.",
        "ja": "プロジェクト名は120文字以内にしてください。",
        "zh-CN": "项目名称不能超过 120 个字符。",
    },
    "프로젝트 이름에는 제어 문자를 사용할 수 없습니다.": {
        "en": "Control characters cannot be used in the project name.",
        "ja": "プロジェクト名に制御文字は使用できません。",
        "zh-CN": "项目名称中不能使用控制字符。",
    },
    "현재 협업 작업 번호에 남은 픽셀이 없습니다.": {
        "en": "No pixels remain for the current collaboration work number.",
        "ja": "現在の共同作業番号には残りピクセルがありません。",
        "zh-CN": "当前协作任务编号没有剩余像素。",
    },
    "재분배할 미확인 픽셀이 없습니다.": {
        "en": "There are no unchecked pixels to redistribute.",
        "ja": "再分配する未確認ピクセルがありません。",
        "zh-CN": "没有可重新分配的未检查像素。",
    },
    "수집을 일시정지한 뒤 남은 작업을 재분배하세요.": {
        "en": "Pause the scan before redistributing remaining work.",
        "ja": "収集を一時停止してから残り作業を再分配してください。",
        "zh-CN": "请先暂停扫描再重新分配剩余任务。",
    },
    "수집을 일시정지한 뒤 결과를 내보내세요.": {
        "en": "Pause the scan before exporting results.", "ja": "収集を一時停止してから結果を書き出してください。", "zh-CN": "请先暂停扫描再导出结果。",
    },
    "수집을 일시정지한 뒤 결과를 병합하세요.": {
        "en": "Pause the scan before merging results.", "ja": "収集を一時停止してから結果を統合してください。", "zh-CN": "请先暂停扫描再合并结果。",
    },
    "이미 다른 작업 결과를 병합하고 있습니다.": {
        "en": "Another work-result merge is already running.",
        "ja": "別の作業結果の統合がすでに実行中です。",
        "zh-CN": "另一个任务结果合并正在进行中。",
    },
    "projectIds는 배열이어야 합니다.": {
        "en": "projectIds must be an array.",
        "ja": "projectIdsは配列である必要があります。",
        "zh-CN": "projectIds 必须是数组。",
    },
    "수집을 일시정지한 뒤 협업 시작 파일을 내보내세요.": {
        "en": "Pause the scan before exporting a collaboration start file.",
        "ja": "収集を一時停止してから共同作業開始ファイルを書き出してください。",
        "zh-CN": "请先暂停扫描再导出协作开始文件。",
    },
    "수집 중에는 대표 영역과 색상 비율을 계산할 수 없습니다. 먼저 일시정지하세요.": {
        "en": "Representative regions and color ratios cannot be calculated while scanning. Pause first.",
        "ja": "収集中は代表領域と色比率を計算できません。先に一時停止してください。",
        "zh-CN": "扫描期间无法计算代表区域和颜色比例。请先暂停。",
    },
    "정확한 대표 영역과 색상 비율을 내보내려면 먼저 일시정지하세요.": {
        "en": "Pause first to export exact representative regions and color ratios.",
        "ja": "正確な代表領域と色比率を書き出すには、先に一時停止してください。",
        "zh-CN": "要导出准确的代表区域和颜色比例，请先暂停。",
    },
    "정확한 대표 영역과 색상 비율을 PDF로 내보내려면 먼저 일시정지하세요.": {
        "en": "Pause first to export exact representative regions and color ratios to PDF.",
        "ja": "正確な代表領域と色比率をPDFへ書き出すには、先に一時停止してください。",
        "zh-CN": "要将准确的代表区域和颜色比例导出为 PDF，请先暂停。",
    },
    "사용자가 일시정지했습니다.": {
        "en": "Paused by the user.", "ja": "ユーザーが一時停止しました。", "zh-CN": "已由用户暂停。",
    },
    "일시정지됨": {"en": "Paused", "ja": "一時停止", "zh-CN": "已暂停"},
    "프로그램 종료로 일시정지했습니다.": {
        "en": "Paused because the program is shutting down.",
        "ja": "プログラム終了のため一時停止しました。",
        "zh-CN": "程序关闭时已暂停。",
    },
    "모든 후보 픽셀 작업자 확인을 완료했습니다.": {
        "en": "Finished checking workers for all candidate pixels.",
        "ja": "すべての候補ピクセルの作業者確認が完了しました。",
        "zh-CN": "已完成全部候选像素的作业者检查。",
    },
    "분석 중 작업자 데이터가 변경되었습니다. 잠시 후 다시 계산합니다.": {
        "en": "Worker data changed during analysis. It will be recalculated shortly.",
        "ja": "分析中に作業者データが変更されました。まもなく再計算します。",
        "zh-CN": "分析期间作业者数据发生变化，将稍后重新计算。",
    },
    "owners.bin 크기가 후보 픽셀 수와 일치하지 않습니다.": {
        "en": "The owners.bin size does not match the candidate-pixel count.",
        "ja": "owners.binのサイズが候補ピクセル数と一致しません。",
        "zh-CN": "owners.bin 大小与候选像素数不一致。",
    },
    "원본 템플릿 파일을 찾지 못했습니다. 템플릿을 다시 가져온 뒤 시도하세요.": {
        "en": "The original template file could not be found. Import the template again and retry.",
        "ja": "元のテンプレートファイルが見つかりません。テンプレートを再読み込みしてから再試行してください。",
        "zh-CN": "找不到原始模板文件。请重新导入模板后再试。",
    },
    "먼저 동일한 협업 시작 파일을 가져오거나 프로젝트를 준비해야 합니다.": {
        "en": "Import the same collaboration start file or prepare the project first.",
        "ja": "先に同じ共同作業開始ファイルを読み込むか、プロジェクトを準備してください。",
        "zh-CN": "请先导入同一个协作开始文件或准备项目。",
    },
    "다른 프로젝트의 작업 결과입니다. 같은 협업 시작 파일에서 만든 프로젝트를 선택하세요.": {
        "en": "This result belongs to another project. Select a project created from the same collaboration start file.",
        "ja": "別プロジェクトの作業結果です。同じ共同作業開始ファイルから作成したプロジェクトを選択してください。",
        "zh-CN": "这是其他项目的任务结果。请选择由同一个协作开始文件创建的项目。",
    },
    "템플릿 해시가 다른 작업 결과입니다.": {
        "en": "The work result has a different template hash.", "ja": "テンプレートハッシュが異なる作業結果です。", "zh-CN": "任务结果的模板哈希不同。",
    },
    "후보 픽셀 수가 다른 작업 결과입니다.": {
        "en": "The work result has a different candidate-pixel count.", "ja": "候補ピクセル数が異なる作業結果です。", "zh-CN": "任务结果的候选像素数不同。",
    },
    "후보 목록이 다른 작업 결과입니다. 같은 협업 시작 파일을 사용해야 합니다.": {
        "en": "The work result uses a different candidate list. Use the same collaboration start file.",
        "ja": "候補一覧が異なる作業結果です。同じ共同作業開始ファイルを使用してください。",
        "zh-CN": "任务结果使用了不同的候选列表。必须使用同一个协作开始文件。",
    },
    "해당 프로젝트가 수집 중입니다.": {
        "en": "That project is currently scanning.", "ja": "そのプロジェクトは収集中です。", "zh-CN": "该项目正在扫描。",
    },
    "협업 작업의 후보 또는 결과 파일 크기가 올바르지 않습니다.": {
        "en": "The candidate or result file size in the collaboration package is invalid.",
        "ja": "共同作業パッケージの候補または結果ファイルサイズが正しくありません。",
        "zh-CN": "协作包中的候选或结果文件大小无效。",
    },
    "협업 작업의 후보 목록 해시가 일치하지 않습니다.": {
        "en": "The candidate-list hash in the collaboration package does not match.",
        "ja": "共同作業パッケージの候補一覧ハッシュが一致しません。",
        "zh-CN": "协作包中的候选列表哈希不匹配。",
    },
    "남은 작업 재분배 목록의 크기가 올바르지 않습니다.": {
        "en": "The remaining-work redistribution list has an invalid size.",
        "ja": "残り作業の再分配一覧のサイズが正しくありません。",
        "zh-CN": "剩余任务重新分配列表大小无效。",
    },
    "남은 작업 재분배 목록의 해시가 일치하지 않습니다.": {
        "en": "The remaining-work redistribution list hash does not match.",
        "ja": "残り作業の再分配一覧のハッシュが一致しません。",
        "zh-CN": "剩余任务重新分配列表哈希不匹配。",
    },
    "남은 작업 재분배 목록에 범위를 벗어난 후보 번호가 있습니다.": {
        "en": "The remaining-work redistribution list contains an out-of-range candidate index.",
        "ja": "残り作業の再分配一覧に範囲外の候補番号があります。",
        "zh-CN": "剩余任务重新分配列表包含超出范围的候选编号。",
    },
    "남은 작업 재분배 목록이 중복되었거나 정렬되지 않았습니다.": {
        "en": "The remaining-work redistribution list contains duplicates or is not sorted.",
        "ja": "残り作業の再分配一覧が重複しているか、並び順が不正です。",
        "zh-CN": "剩余任务重新分配列表有重复项或未排序。",
    },
    "협업 작업의 템플릿 ID를 원본 파일에서 찾지 못했습니다.": {
        "en": "The collaboration package's template ID was not found in the original file.",
        "ja": "共同作業パッケージのテンプレートIDが元ファイルに見つかりません。",
        "zh-CN": "在原始文件中找不到协作包的模板 ID。",
    },
    "협업 시작 파일의 manifest.json에 원본 템플릿 파일명이 없습니다.": {
        "en": "The collaboration start file's manifest.json does not contain the original template filename.",
        "ja": "共同作業開始ファイルのmanifest.jsonに元テンプレートのファイル名がありません。",
        "zh-CN": "协作开始文件的 manifest.json 中没有原始模板文件名。",
    },
    "협업 ZIP 파일을 찾을 수 없습니다.": {
        "en": "The collaboration ZIP file could not be found.", "ja": "共同作業ZIPファイルが見つかりません。", "zh-CN": "找不到协作 ZIP 文件。",
    },
    "올바른 ZIP 파일이 아닙니다. 브라우저 다운로드가 끝나지 않았거나 오류 응답이 ZIP 대신 저장됐을 수 있습니다.": {
        "en": "This is not a valid ZIP file. The browser download may be incomplete, or an error response may have been saved instead of a ZIP.",
        "ja": "正しいZIPファイルではありません。ブラウザーのダウンロードが未完了か、ZIPの代わりにエラー応答が保存された可能性があります。",
        "zh-CN": "这不是有效的 ZIP 文件。浏览器下载可能尚未完成，或保存的是错误响应而不是 ZIP。",
    },
    "manifest.json이 없어 협업 패키지 형식을 확인할 수 없습니다.": {
        "en": "manifest.json is missing, so the collaboration package format cannot be determined.",
        "ja": "manifest.jsonがないため、共同作業パッケージ形式を確認できません。",
        "zh-CN": "缺少 manifest.json，无法确认协作包格式。",
    },
    "manifest.json 형식이 올바르지 않습니다.": {
        "en": "The manifest.json format is invalid.", "ja": "manifest.jsonの形式が正しくありません。", "zh-CN": "manifest.json 格式无效。",
    },
    "생성된 협업 ZIP의 패키지 종류가 올바르지 않습니다.": {
        "en": "The generated collaboration ZIP has an invalid package type.",
        "ja": "生成された共同作業ZIPのパッケージ種類が正しくありません。",
        "zh-CN": "生成的协作 ZIP 包类型无效。",
    },
    "ZIP 안에 JSON 파일이 없습니다.": {
        "en": "The ZIP does not contain a JSON file.", "ja": "ZIP内にJSONファイルがありません。", "zh-CN": "ZIP 中没有 JSON 文件。",
    },
    "템플릿 coords 형식을 알 수 없습니다.": {
        "en": "The template coords format is unknown.", "ja": "テンプレートのcoords形式を判別できません。", "zh-CN": "无法识别模板 coords 格式。",
    },
    "coords에는 Tl X, Tl Y, Px X, Px Y 네 값이 필요합니다.": {
        "en": "coords requires four values: Tl X, Tl Y, Px X, and Px Y.",
        "ja": "coordsにはTl X、Tl Y、Px X、Px Yの4つの値が必要です。",
        "zh-CN": "coords 需要 Tl X、Tl Y、Px X、Px Y 四个值。",
    },
    "Blue Marble templates 객체를 찾지 못했습니다.": {
        "en": "The Blue Marble templates object was not found.", "ja": "Blue Marbleのtemplatesオブジェクトが見つかりません。", "zh-CN": "找不到 Blue Marble templates 对象。",
    },
    "사용 가능한 Blue Marble 템플릿이 없습니다.": {
        "en": "No usable Blue Marble template was found.", "ja": "使用可能なBlue Marbleテンプレートがありません。", "zh-CN": "没有可用的 Blue Marble 模板。",
    },
    "이 작업 결과 파일과 일치하는 로컬 프로젝트를 찾지 못했습니다. 먼저 같은 협업 시작 파일을 가져온 뒤 다시 병합하세요.": {
        "en": "No local project matches this work-result file. Import the same collaboration start file first, then merge again.",
        "ja": "この作業結果ファイルに一致するローカルプロジェクトがありません。先に同じ共同作業開始ファイルを読み込んでから、もう一度統合してください。",
        "zh-CN": "找不到与该任务结果文件匹配的本地项目。请先导入同一个协作开始文件，再重新合并。",
    },
    "현재 그림과 비교를 다시 실행하면 기존 작업자 확인 결과, 진행률, 협업 작업 순서와 분석 결과가 초기화됩니다. GUI의 2단계 초기화 확인을 거쳐 실행하세요.": {
        "en": "Running the current-canvas comparison again resets worker results, progress, collaboration ordering, and analysis. Use the GUI's two-step reset confirmation.",
        "ja": "現在画像の比較を再実行すると、作業者結果、進行状況、共同作業順序、分析結果が初期化されます。GUIの2段階初期化確認を使用してください。",
        "zh-CN": "重新执行当前画布比较会清除作业者结果、进度、协作任务顺序和分析结果。请通过 GUI 的两步重置确认执行。",
    },
    "선택한 파일은 협업 시작 파일입니다. 시작 파일은 '협업 시작 파일 가져오기'에 사용하세요.": {
        "en": "The selected file is a collaboration start file. Use ‘Import collaboration start file’.",
        "ja": "選択したファイルは共同作業開始ファイルです。「共同作業開始ファイルを読み込む」で使用してください。",
        "zh-CN": "所选文件是协作开始文件，请使用“导入协作开始文件”。",
    },
    "선택한 파일은 작업 결과 파일입니다. 결과 파일은 기준 노드의 '작업 결과 병합'에 사용하세요.": {
        "en": "The selected file is a work-result file. Use ‘Merge work result’ on the main node.",
        "ja": "選択したファイルは作業結果ファイルです。基準ノードの「作業結果を統合」で使用してください。",
        "zh-CN": "所选文件是任务结果文件，请在主节点使用“合并任务结果”。",
    },
    "종류 정보 없음": {"en": "No type information", "ja": "種類情報なし", "zh-CN": "无类型信息"},
    "알 수 없는 협업 작업 순서 형식입니다.": {
        "en": "The collaboration assignment-order format is unknown.",
        "ja": "共同作業の割り当て順序形式が不明です。",
        "zh-CN": "无法识别协作任务顺序格式。",
    },
}

_EXACT.update({
    "픽셀 좌표는 0~999 범위여야 합니다.": {
        "en": "Pixel coordinates must be between 0 and 999.",
        "ja": "ピクセル座標は0～999の範囲で指定してください。",
        "zh-CN": "像素坐标必须在 0 到 999 之间。",
    },
    "우하단 좌표는 좌상단 좌표보다 오른쪽 아래에 있어야 합니다.": {
        "en": "The bottom-right coordinate must be below and to the right of the top-left coordinate.",
        "ja": "右下座標は左上座標より右下にある必要があります。",
        "zh-CN": "右下角坐标必须位于左上角坐标的右下方。",
    },
    "캡처 ID가 올바르지 않습니다.": {
        "en": "The capture ID is invalid.", "ja": "キャプチャIDが正しくありません。", "zh-CN": "截图 ID 无效。",
    },
    "캡처 작업을 찾지 못했습니다. 다시 캡처하세요.": {
        "en": "The capture job was not found. Capture the region again.",
        "ja": "キャプチャ作業が見つかりません。もう一度取得してください。",
        "zh-CN": "找不到截图任务，请重新获取区域。",
    },
    "캡처 이미지를 찾지 못했습니다.": {
        "en": "The captured image was not found.", "ja": "キャプチャ画像が見つかりません。", "zh-CN": "找不到截图图像。",
    },
    "계산 모드는 region 또는 color여야 합니다.": {
        "en": "The calculation mode must be region or color.",
        "ja": "計算モードはregionまたはcolorである必要があります。",
        "zh-CN": "计算模式必须是 region 或 color。",
    },
    "남아 있는 불투명 픽셀이 없습니다. 그림 영역을 하나 이상 남겨야 합니다.": {
        "en": "No opaque pixels remain. Leave at least one artwork pixel in the mask.",
        "ja": "不透明ピクセルが残っていません。画像領域を1ピクセル以上残してください。",
        "zh-CN": "没有剩余的不透明像素，请至少保留一个图像区域像素。",
    },
    "스크린샷 템플릿 프로젝트를 만들지 못했습니다.": {
        "en": "Could not create the screenshot-template project.",
        "ja": "スクリーンショットテンプレートプロジェクトを作成できませんでした。",
        "zh-CN": "无法创建截图模板项目。",
    },
})


_EXACT.update({'PNG 형식이 아닙니다.': {'en': 'The file is not a PNG image.', 'ja': 'PNG形式ではありません。', 'zh-CN': '文件不是 PNG 格式。'},
 'ZIP 루트에 manifest.json이 없습니다.': {'en': 'manifest.json is missing from the ZIP root.',
                                  'ja': 'ZIPのルートにmanifest.jsonがありません。',
                                  'zh-CN': 'ZIP 根目录中缺少 manifest.json。'},
 'assignment.bin 크기가 작업 분배 수와 일치하지 않습니다.': {'en': 'The assignment.bin size does not match the assignment count.',
                                            'ja': 'assignment.binのサイズが作業割り当て数と一致しません。',
                                            'zh-CN': 'assignment.bin 大小与任务分配数量不一致。'},
 'candidates.bin 크기가 후보 픽셀 수와 일치하지 않습니다.': {'en': 'The candidates.bin size does not match the candidate-pixel count.',
                                            'ja': 'candidates.binのサイズが候補ピクセル数と一致しません。',
                                            'zh-CN': 'candidates.bin 大小与候选像素数不一致。'},
 'project.json 형식이 올바르지 않습니다.': {'en': 'The project.json format is invalid.',
                                 'ja': 'project.jsonの形式が正しくありません。',
                                 'zh-CN': 'project.json 格式无效。'},
 'users.json 형식이 올바르지 않습니다.': {'en': 'The users.json format is invalid.', 'ja': 'users.jsonの形式が正しくありません。', 'zh-CN': 'users.json 格式无效。'},
 '그림을 만들 타일 정보가 없습니다.': {'en': 'No tile information is available to build the image.',
                         'ja': '画像を作成するためのタイル情報がありません。',
                         'zh-CN': '没有可用于生成图像的图块信息。'},
 '남은 작업 재분배 수가 올바르지 않습니다.': {'en': 'The remaining-work redistribution count is invalid.',
                             'ja': '残り作業の再分配数が正しくありません。',
                             'zh-CN': '剩余任务重新分配数量无效。'},
 '분석 캐시가 현재 프로젝트 상태와 일치하지 않습니다.': {'en': 'The analysis cache does not match the current project state.',
                                   'ja': '分析キャッシュが現在のプロジェクト状態と一致しません。',
                                   'zh-CN': '分析缓存与当前项目状态不一致。'},
 "선택한 파일은 협업 시작 파일입니다. 시작 파일은 '협업 시작 파일 가져오기'에 사용하고, 병합에는 참여 노드가 내보낸 collab-node-result-*.zip을 선택하세요.": {'en': 'The selected file is a '
                                                                                                               'collaboration start file. '
                                                                                                               'Import it with ‘Import '
                                                                                                               'collaboration start file’; '
                                                                                                               'to merge results, select a '
                                                                                                               'collab-node-result-*.zip '
                                                                                                               'exported by a participant '
                                                                                                               'node.',
                                                                                                         'ja': '選択したファイルは共同作業開始ファイルです。「共同作業開始ファイルを読み込む」で使用し、結果の統合には参加ノードが書き出したcollab-node-result-*.zipを選択してください。',
                                                                                                         'zh-CN': '所选文件是协作开始文件。请使用“导入协作开始文件”；合并结果时请选择参与节点导出的 '
                                                                                                                  'collab-node-result-*.zip。'},
 '수집을 일시정지한 뒤 템플릿을 편집하세요.': {'en': 'Pause the scan before editing the template.',
                             'ja': '収集を一時停止してからテンプレートを編集してください。',
                             'zh-CN': '请先暂停扫描再编辑模板。'},
 '스크린샷 템플릿 ZIP이 손상되었습니다.': {'en': 'The screenshot-template ZIP is damaged.', 'ja': 'スクリーンショットテンプレートZIPが破損しています。', 'zh-CN': '截图模板 ZIP 已损坏。'},
 '스크린샷 템플릿 원본 ZIP을 찾지 못했습니다.': {'en': 'The original screenshot-template ZIP could not be found.',
                                'ja': 'スクリーンショットテンプレートの元ZIPが見つかりません。',
                                'zh-CN': '找不到截图模板的原始 ZIP。'},
 '스크린샷 템플릿의 capture.json 형식이 올바르지 않습니다.': {'en': 'The screenshot template capture.json format is invalid.',
                                           'ja': 'スクリーンショットテンプレートのcapture.json形式が正しくありません。',
                                           'zh-CN': '截图模板的 capture.json 格式无效。'},
 '스크린샷 템플릿의 캡처 범위 정보가 올바르지 않습니다.': {'en': 'The screenshot template capture bounds are invalid.',
                                    'ja': 'スクリーンショットテンプレートのキャプチャ範囲情報が正しくありません。',
                                    'zh-CN': '截图模板的截图范围信息无效。'},
 '작업 결과 파일에 프로젝트 ID가 없습니다.': {'en': 'The work-result file does not contain a project ID.',
                              'ja': '作業結果ファイルにプロジェクトIDがありません。',
                              'zh-CN': '任务结果文件中没有项目 ID。'},
 '작업 결과의 users.json 형식이 올바르지 않습니다.': {'en': 'The work-result users.json format is invalid.',
                                      'ja': '作業結果のusers.json形式が正しくありません。',
                                      'zh-CN': '任务结果中的 users.json 格式无效。'},
 '작업 결과의 협업 작업 번호가 올바르지 않습니다.': {'en': 'The collaboration work number in the result is invalid.',
                                 'ja': '作業結果の共同作業番号が正しくありません。',
                                 'zh-CN': '任务结果中的协作任务编号无效。'},
 '작업 분배 목록 해시가 일치하지 않습니다.': {'en': 'The assignment-list hash does not match.', 'ja': '作業割り当て一覧のハッシュが一致しません。', 'zh-CN': '任务分配列表哈希不匹配。'},
 '전체 작업 분배 수가 후보 픽셀 수와 일치하지 않습니다.': {'en': 'The full assignment count does not match the candidate-pixel count.',
                                     'ja': '全体作業の割り当て数が候補ピクセル数と一致しません。',
                                     'zh-CN': '完整任务分配数量与候选像素数不一致。'},
 '전체 작업 분배 정보가 후보 픽셀 수와 일치하지 않습니다.': {'en': 'The full assignment information does not match the candidate-pixel count.',
                                      'ja': '全体作業の割り当て情報が候補ピクセル数と一致しません。',
                                      'zh-CN': '完整任务分配信息与候选像素数不一致。'},
 '지원하지 않는 스크린샷 템플릿 형식입니다. Wplace Contributor Scanner 1.5에서 생성한 템플릿을 사용하세요.': {'en': 'This screenshot-template format is not supported. Use '
                                                                                    'a template created by Wplace Contributor Scanner 1.5.',
                                                                              'ja': 'このスクリーンショットテンプレート形式には対応していません。Wplace Contributor '
                                                                                    'Scanner 1.5で作成したテンプレートを使用してください。',
                                                                              'zh-CN': '不支持此截图模板格式。请使用 Wplace Contributor Scanner 1.5 '
                                                                                       '创建的模板。'},
 '지원하지 않는 프로젝트 데이터 형식입니다. Wplace Contributor Scanner 1.5에서 새로 만든 프로젝트만 사용할 수 있습니다.': {'en': 'This project-data format is not supported. '
                                                                                            'Only projects newly created by Wplace '
                                                                                            'Contributor Scanner 1.5 can be used.',
                                                                                      'ja': 'このプロジェクトデータ形式には対応していません。Wplace Contributor '
                                                                                            'Scanner 1.5で新規作成したプロジェクトのみ使用できます。',
                                                                                      'zh-CN': '不支持此项目数据格式。只能使用 Wplace Contributor Scanner '
                                                                                               '1.5 新建的项目。'},
 '지원하지 않는 협업 파일 형식입니다. Wplace Contributor Scanner 1.5에서 생성한 파일을 사용하세요.': {'en': 'This collaboration-file format is not supported. Use a '
                                                                                'file created by Wplace Contributor Scanner 1.5.',
                                                                          'ja': 'この共同作業ファイル形式には対応していません。Wplace Contributor Scanner '
                                                                                '1.5で作成したファイルを使用してください。',
                                                                          'zh-CN': '不支持此协作文件格式。请使用 Wplace Contributor Scanner 1.5 创建的文件。'},
 '프로젝트 ID가 원본 템플릿과 일치하지 않습니다.': {'en': 'The project ID does not match the source template.',
                                 'ja': 'プロジェクトIDが元テンプレートと一致しません。',
                                 'zh-CN': '项目 ID 与原始模板不一致。'},
 '프로젝트 네트워크 타임아웃이 허용 범위를 벗어났습니다.': {'en': 'The project network timeout is outside the allowed range.',
                                    'ja': 'プロジェクトのネットワークタイムアウトが許容範囲外です。',
                                    'zh-CN': '项目网络超时时间超出允许范围。'},
 '프로젝트 병렬 워커 수가 허용 범위를 벗어났습니다.': {'en': 'The project parallel-worker count is outside the allowed range.',
                                  'ja': 'プロジェクトの並列ワーカー数が許容範囲外です。',
                                  'zh-CN': '项目并行工作线程数超出允许范围。'},
 '프로젝트 수집 상태가 올바르지 않습니다.': {'en': 'The project scan state is invalid.', 'ja': 'プロジェクトの収集状態が正しくありません。', 'zh-CN': '项目扫描状态无效。'},
 '프로젝트 요청 간격이 허용 범위를 벗어났습니다.': {'en': 'The project request interval is outside the allowed range.',
                                'ja': 'プロジェクトのリクエスト間隔が許容範囲外です。',
                                'zh-CN': '项目请求间隔超出允许范围。'},
 '프로젝트 이름이 비어 있거나 올바르지 않습니다.': {'en': 'The project name is empty or invalid.', 'ja': 'プロジェクト名が空、または正しくありません。', 'zh-CN': '项目名称为空或无效。'},
 '프로젝트 작업 분배 방식이 올바르지 않습니다.': {'en': 'The project assignment mode is invalid.', 'ja': 'プロジェクトの作業割り当て方式が正しくありません。', 'zh-CN': '项目任务分配模式无效。'},
 '프로젝트 작업 분배 정보가 올바르지 않습니다.': {'en': 'The project assignment information is invalid.',
                               'ja': 'プロジェクトの作業割り当て情報が正しくありません。',
                               'zh-CN': '项目任务分配信息无效。'},
 '프로젝트 조회 설정이 올바르지 않습니다.': {'en': 'The project request settings are invalid.', 'ja': 'プロジェクトの照会設定が正しくありません。', 'zh-CN': '项目查询设置无效。'},
 '프로젝트 지터 비율이 허용 범위를 벗어났습니다.': {'en': 'The project jitter ratio is outside the allowed range.',
                                'ja': 'プロジェクトのジッター比率が許容範囲外です。',
                                'zh-CN': '项目抖动比例超出允许范围。'},
 '프로젝트 체크포인트 설정이 올바르지 않습니다.': {'en': 'The project checkpoint setting is invalid.',
                               'ja': 'プロジェクトのチェックポイント設定が正しくありません。',
                               'zh-CN': '项目检查点设置无效。'},
 '프로젝트와 원본 템플릿의 해시가 일치하지 않습니다.': {'en': 'The project hash does not match the source template.',
                                  'ja': 'プロジェクトと元テンプレートのハッシュが一致しません。',
                                  'zh-CN': '项目与原始模板的哈希不一致。'},
 '현재 캔버스 그림을 만들지 못했습니다.': {'en': 'Could not build the current-canvas image.', 'ja': '現在のキャンバス画像を作成できませんでした。', 'zh-CN': '无法生成当前画布图像。'},
 '현재 프로젝트는 스크린샷 영역 템플릿이 아닙니다.': {'en': 'The current project is not a screenshot-region template.',
                                 'ja': '現在のプロジェクトはスクリーンショット領域テンプレートではありません。',
                                 'zh-CN': '当前项目不是截图区域模板。'},
 '협업 시작 파일에 포함할 현재 그림을 만들지 못했습니다.': {'en': 'Could not build the current image for the collaboration start file.',
                                     'ja': '共同作業開始ファイルに含める現在画像を作成できませんでした。',
                                     'zh-CN': '无法生成要包含在协作开始文件中的当前图像。'},
 '협업 시작 파일의 users.json 형식이 올바르지 않습니다.': {'en': 'The collaboration start file users.json format is invalid.',
                                         'ja': '共同作業開始ファイルのusers.json形式が正しくありません。',
                                         'zh-CN': '协作开始文件中的 users.json 格式无效。'},
 '협업 시작 파일의 분할 수가 허용 범위를 벗어났습니다.': {'en': 'The shard count in the collaboration start file is outside the allowed range.',
                                    'ja': '共同作業開始ファイルの分割数が許容範囲外です。',
                                    'zh-CN': '协作开始文件中的分片数超出允许范围。'},
 '협업 시작 파일의 원본 템플릿 해시가 일치하지 않습니다.': {'en': 'The source-template hash in the collaboration start file does not match.',
                                     'ja': '共同作業開始ファイルの元テンプレートハッシュが一致しません。',
                                     'zh-CN': '协作开始文件中的原始模板哈希不匹配。'},
 '협업 시작 파일의 프로젝트 식별 정보가 올바르지 않습니다.': {'en': 'The project identity in the collaboration start file is invalid.',
                                      'ja': '共同作業開始ファイルのプロジェクト識別情報が正しくありません。',
                                      'zh-CN': '协作开始文件中的项目标识信息无效。'},
 '후보 픽셀 목록 해시가 일치하지 않습니다.': {'en': 'The candidate-pixel list hash does not match.', 'ja': '候補ピクセル一覧のハッシュが一致しません。', 'zh-CN': '候选像素列表哈希不匹配。'},
 '후보 픽셀 수가 올바르지 않습니다.': {'en': 'The candidate-pixel count is invalid.', 'ja': '候補ピクセル数が正しくありません。', 'zh-CN': '候选像素数无效。'}})


_EXACT.update({
    "project.json 항목 구성이 올바르지 않습니다.": {
        "en": "The project.json field set is invalid.",
        "ja": "project.jsonの項目構成が正しくありません。",
        "zh-CN": "project.json 字段结构无效。",
    },
    "users.json 작업자 ID가 일치하지 않습니다.": {
        "en": "A worker ID in users.json does not match its record key.",
        "ja": "users.jsonの作業者IDがレコードキーと一致しません。",
        "zh-CN": "users.json 中的作业者 ID 与记录键不一致。",
    },
    "users.json 항목 구성이 올바르지 않습니다.": {
        "en": "The users.json field set is invalid.",
        "ja": "users.jsonの項目構成が正しくありません。",
        "zh-CN": "users.json 字段结构无效。",
    },
    "스크린샷 템플릿 프로젝트 항목 구성이 올바르지 않습니다.": {
        "en": "The screenshot-template project field set is invalid.",
        "ja": "スクリーンショットテンプレートのプロジェクト項目構成が正しくありません。",
        "zh-CN": "截图模板项目字段结构无效。",
    },
    "스크린샷 템플릿 항목 구성이 올바르지 않습니다.": {
        "en": "The screenshot-template field set is invalid.",
        "ja": "スクリーンショットテンプレートの項目構成が正しくありません。",
        "zh-CN": "截图模板字段结构无效。",
    },
    "프로젝트 조회 설정 항목 구성이 올바르지 않습니다.": {
        "en": "The project request-settings field set is invalid.",
        "ja": "プロジェクトの照会設定項目構成が正しくありません。",
        "zh-CN": "项目查询设置字段结构无效。",
    },
})



PatternFormatter = Callable[[re.Match[str], str], str]


def _nested(value: str, lang: str) -> str:
    translated = translate_runtime_text(value, lang)
    return translated if translated else value


def _lang(lang: str, en: str, ja: str, zh: str) -> str:
    return {"en": en, "ja": ja, "zh-CN": zh}[lang]


def _runtime_label(value: str, lang: str) -> str:
    labels = {
        "협업 시작 파일": {"en": "collaboration start file", "ja": "共同作業開始ファイル", "zh-CN": "协作开始文件"},
        "협업 시작": {"en": "collaboration start", "ja": "共同作業開始", "zh-CN": "协作开始"},
        "작업 결과": {"en": "work result", "ja": "作業結果", "zh-CN": "任务结果"},
        "원본": {"en": "original", "ja": "元画像", "zh-CN": "原图"},
        "마스크": {"en": "mask", "ja": "マスク", "zh-CN": "蒙版"},
    }
    return labels.get(value, {}).get(lang, value)


def _schema_details(value: str, lang: str) -> str:
    if lang == "en":
        return value.replace("누락:", "Missing:").replace("불필요:", "Unexpected:")
    if lang == "ja":
        return value.replace("누락:", "不足:").replace("불필요:", "不要:")
    return value.replace("누락:", "缺少：").replace("불필요:", "多余：")


_PATTERNS: list[tuple[re.Pattern[str], PatternFormatter]] = [
    (re.compile(r"^(.+) manifest\.json 항목 구성이 올바르지 않습니다\. (.+)$"), lambda m, l: _lang(l, f"The {_runtime_label(m[1], l)} manifest.json field set is invalid. {_schema_details(m[2], l)}", f"{_runtime_label(m[1], l)}のmanifest.json項目構成が正しくありません。{_schema_details(m[2], l)}", f"{_runtime_label(m[1], l)}的 manifest.json 字段结构无效。{_schema_details(m[2], l)}")),
    (re.compile(r"^(.+) ZIP 구성이 올바르지 않습니다\. (.+)$"), lambda m, l: _lang(l, f"The {_runtime_label(m[1], l)} ZIP contents are invalid. {_schema_details(m[2], l)}", f"{_runtime_label(m[1], l)}ZIPの内容構成が正しくありません。{_schema_details(m[2], l)}", f"{_runtime_label(m[1], l)} ZIP 内容结构无效。{_schema_details(m[2], l)}")),
    (re.compile(r"^협업 ZIP에 안전하지 않은 경로가 있습니다: (.+)$"), lambda m, l: _lang(l, f"The collaboration ZIP contains an unsafe path: {m[1]}", f"共同作業ZIPに安全でないパスがあります: {m[1]}", f"协作 ZIP 中包含不安全的路径：{m[1]}")),
    (re.compile(r"^(.+) manifest\.json에 필수 항목이 없습니다: (.+)$"), lambda m, l: _lang(l, f"Required fields are missing from the {m[1]} manifest.json: {m[2]}", f"{m[1]}のmanifest.jsonに必須項目がありません: {m[2]}", f"{m[1]} 的 manifest.json 缺少必填字段：{m[2]}")),
    (re.compile(r"^협업 시작 파일의 canvas-snapshot\.png가 올바르지 않습니다: (.+)$"), lambda m, l: _lang(l, f"The collaboration start file canvas-snapshot.png is invalid: {m[1]}", f"共同作業開始ファイルのcanvas-snapshot.pngが正しくありません: {m[1]}", f"协作开始文件中的 canvas-snapshot.png 无效：{m[1]}")),
    (re.compile(r"^그림 크기가 (\d+)x(\d+)이며 템플릿 크기 (\d+)x(\d+)와 다릅니다\.$"), lambda m, l: _lang(l, f"The image size is {m[1]}x{m[2]}, but the template size is {m[3]}x{m[4]}.", f"画像サイズは{m[1]}x{m[2]}で、テンプレートサイズ{m[3]}x{m[4]}と異なります。", f"图像尺寸为 {m[1]}x{m[2]}，与模板尺寸 {m[3]}x{m[4]} 不同。")),
    (re.compile(r"^프로젝트 조회 설정이 누락되었습니다: (.+)$"), lambda m, l: _lang(l, f"Project request settings are missing: {m[1]}", f"プロジェクトの照会設定が不足しています: {m[1]}", f"缺少项目查询设置：{m[1]}")),
    (re.compile(r"^스크린샷 템플릿 ZIP에 필요한 파일이 없습니다: (.+)$"), lambda m, l: _lang(l, f"Required files are missing from the screenshot-template ZIP: {m[1]}", f"スクリーンショットテンプレートZIPに必要なファイルがありません: {m[1]}", f"截图模板 ZIP 缺少必需文件：{m[1]}")),
    (re.compile(r"^(.+) 이미지를 읽지 못했습니다: (.+)$"), lambda m, l: _lang(l, f"Could not read the {_runtime_label(m[1], l)} image: {m[2]}", f"{_runtime_label(m[1], l)}を読み込めませんでした: {m[2]}", f"无法读取{_runtime_label(m[1], l)}：{m[2]}")),
    (re.compile(r"^(.+) 이미지 크기가 캡처 범위와 다릅니다\. 예상 (\d+)x(\d+), 실제 (\d+)x(\d+)$"), lambda m, l: _lang(l, f"The {_runtime_label(m[1], l)} image size differs from the capture bounds. Expected {m[2]}x{m[3]}, got {m[4]}x{m[5]}.", f"{_runtime_label(m[1], l)}のサイズがキャプチャ範囲と異なります。想定{m[2]}x{m[3]}、実際{m[4]}x{m[5]}。", f"{_runtime_label(m[1], l)}尺寸与截图范围不同。预期 {m[2]}x{m[3]}，实际 {m[4]}x{m[5]}。")),
    (re.compile(r"^업로드 크기는 1바이트~([\d,]+)MB 범위여야 합니다\.$"), lambda m, l: _lang(l, f"The upload size must be between 1 byte and {m[1]} MB.", f"アップロードサイズは1バイト～{m[1]}MBの範囲である必要があります。", f"上传大小必须在 1 字节到 {m[1]} MB 之间。")),
    (re.compile(r"^요청 크기는 1바이트~([\d,]+)MB 범위여야 합니다\.$"), lambda m, l: _lang(l, f"The request size must be between 1 byte and {m[1]} MB.", f"リクエストサイズは1バイト～{m[1]}MBの範囲である必要があります。", f"请求大小必须在 1 字节到 {m[1]} MB 之间。")),
    (re.compile(r"^필수 값이 없습니다: (.+)$"), lambda m, l: _lang(l, f"A required value is missing: {m[1]}", f"必須値がありません: {m[1]}", f"缺少必填值：{m[1]}")),
    (re.compile(r"^픽셀 좌표가 범위를 벗어났습니다: (.+)$"), lambda m, l: _lang(l, f"Pixel coordinates are out of range: {m[1]}", f"ピクセル座標が範囲外です: {m[1]}", f"像素坐标超出范围：{m[1]}")),
    (re.compile(r"^템플릿 (.+)에 tiles 데이터가 없습니다\.$"), lambda m, l: _lang(l, f"Template {m[1]} has no tiles data.", f"テンプレート{m[1]}にtilesデータがありません。", f"模板 {m[1]} 没有 tiles 数据。")),
    (re.compile(r"^템플릿 (.+)의 imageScale은 1 또는 3이어야 합니다\.$"), lambda m, l: _lang(l, f"Template {m[1]} imageScale must be 1 or 3.", f"テンプレート{m[1]}のimageScaleは1または3である必要があります。", f"模板 {m[1]} 的 imageScale 必须是 1 或 3。")),
    (re.compile(r"^템플릿 (.+)의 matchMode는 color 또는 region이어야 합니다\.$"), lambda m, l: _lang(l, f"Template {m[1]} matchMode must be color or region.", f"テンプレート{m[1]}のmatchModeはcolorまたはregionである必要があります。", f"模板 {m[1]} 的 matchMode 必须是 color 或 region。")),
    (re.compile(r"^알 수 없는 타일 키 형식: (.+)$"), lambda m, l: _lang(l, f"Unknown tile-key format: {m[1]}", f"不明なタイルキー形式: {m[1]}", f"未知的图块键格式：{m[1]}")),
    (re.compile(r"^타일 (.+)의 이미지가 Base64 문자열이 아닙니다\.$"), lambda m, l: _lang(l, f"The image for tile {m[1]} is not a Base64 string.", f"タイル{m[1]}の画像がBase64文字列ではありません。", f"图块 {m[1]} 的图像不是 Base64 字符串。")),
    (re.compile(r"^타일 (.+) PNG를 읽지 못했습니다: (.+)$"), lambda m, l: _lang(l, f"Could not read tile {m[1]} PNG: {_nested(m[2], l)}", f"タイル{m[1]}のPNGを読み込めませんでした: {_nested(m[2], l)}", f"无法读取图块 {m[1]} PNG：{_nested(m[2], l)}")),
    (re.compile(r"^타일 (.+) 크기가 Blue Marble 3배 형식이 아닙니다: (.+)$"), lambda m, l: _lang(l, f"Tile {m[1]} is not in Blue Marble 3× format: {m[2]}", f"タイル{m[1]}のサイズがBlue Marble 3倍形式ではありません: {m[2]}", f"图块 {m[1]} 不是 Blue Marble 3 倍格式：{m[2]}")),
    (re.compile(r"^타일 (.+) 크기가 imageScale=(\d+) 형식과 맞지 않습니다: (.+)$"), lambda m, l: _lang(l, f"Tile {m[1]} size does not match imageScale={m[2]}: {m[3]}", f"タイル{m[1]}のサイズがimageScale={m[2]}形式と一致しません: {m[3]}", f"图块 {m[1]} 的尺寸与 imageScale={m[2]} 格式不符：{m[3]}")),
    (re.compile(r"^타일 (.+)가 1000x1000 경계를 벗어납니다\.$"), lambda m, l: _lang(l, f"Tile {m[1]} exceeds the 1000×1000 boundary.", f"タイル{m[1]}が1000×1000の境界を超えています。", f"图块 {m[1]} 超出 1000×1000 边界。")),
    (re.compile(r"^coords 원점\((.+)\)과 타일 원점\((.+)\)이 다릅니다\.$"), lambda m, l: _lang(l, f"The coords origin ({m[1]}) differs from the tile origin ({m[2]}).", f"coords原点({m[1]})とタイル原点({m[2]})が異なります。", f"coords 原点（{m[1]}）与图块原点（{m[2]}）不同。")),
    (re.compile(r"^타일 (\d+),(\d+) 다운로드 실패: HTTP (\d+)$"), lambda m, l: _lang(l, f"Failed to download tile {m[1]},{m[2]}: HTTP {m[3]}", f"タイル{m[1]},{m[2]}のダウンロードに失敗しました: HTTP {m[3]}", f"下载图块 {m[1]},{m[2]} 失败：HTTP {m[3]}")),
    (re.compile(r"^타일 (\d+),(\d+) 응답이 PNG가 아닙니다\.$"), lambda m, l: _lang(l, f"The response for tile {m[1]},{m[2]} is not PNG.", f"タイル{m[1]},{m[2]}の応答がPNGではありません。", f"图块 {m[1]},{m[2]} 的响应不是 PNG。")),
    (re.compile(r"^픽셀 응답 JSON 해석 실패: (.+)$"), lambda m, l: _lang(l, f"Failed to parse the pixel-response JSON: {_nested(m[1], l)}", f"ピクセル応答JSONの解析に失敗しました: {_nested(m[1], l)}", f"解析像素响应 JSON 失败：{_nested(m[1], l)}")),
    (re.compile(r"^픽셀 조회 실패: HTTP (\d+)$"), lambda m, l: _lang(l, f"Pixel lookup failed: HTTP {m[1]}", f"ピクセル照会に失敗しました: HTTP {m[1]}", f"像素查询失败：HTTP {m[1]}")),
    (re.compile(r"^현재 타일 (\d+),(\d+) 크기가 (.+)입니다; 1000x1000이 필요합니다\.$"), lambda m, l: _lang(l, f"Current tile {m[1]},{m[2]} has size {m[3]}; 1000×1000 is required.", f"現在タイル{m[1]},{m[2]}のサイズは{m[3]}です。1000×1000が必要です。", f"当前图块 {m[1]},{m[2]} 的尺寸为 {m[3]}；需要 1000×1000。")),
    (re.compile(r"^타일 비교 (\d+)/(\d+): 일치 픽셀 ([\d,]+)개$"), lambda m, l: _lang(l, f"Tile {m[1]}/{m[2]}: {m[3]} matching pixels", f"タイル{m[1]}/{m[2]}: 一致ピクセル{m[3]}", f"图块 {m[1]}/{m[2]}：匹配像素 {m[3]}")),
    (re.compile(r"^타일 비교 (\d+)/(\d+): 영역 대상 픽셀 ([\d,]+)개$"), lambda m, l: _lang(l, f"Tile {m[1]}/{m[2]}: {m[3]} region target pixels", f"タイル{m[1]}/{m[2]}: 領域対象ピクセル{m[3]}", f"图块 {m[1]}/{m[2]}：区域目标像素 {m[3]}")),
    (re.compile(r"^준비 완료: 템플릿 일치 픽셀 ([\d,]+)개$"), lambda m, l: _lang(l, f"Preparation complete: {m[1]} matching template pixels", f"準備完了: テンプレート一致ピクセル{m[1]}", f"准备完成：模板匹配像素 {m[1]}")),
    (re.compile(r"^준비 완료: 영역 대상 픽셀 ([\d,]+)개$"), lambda m, l: _lang(l, f"Preparation complete: {m[1]} region target pixels", f"準備完了: 領域対象ピクセル{m[1]}", f"准备完成：区域目标像素 {m[1]}")),
    (re.compile(r"^캡처 한 변은 최대 ([\d,]+)픽셀까지 지원합니다\.$"), lambda m, l: _lang(l, f"Each capture side supports up to {m[1]} pixels.", f"キャプチャの1辺は最大{m[1]}ピクセルまで対応します。", f"截图单边最多支持 {m[1]} 像素。")),
    (re.compile(r"^캡처 범위는 최대 ([\d,]+)픽셀까지 지원합니다\.$"), lambda m, l: _lang(l, f"The capture region supports up to {m[1]} pixels.", f"キャプチャ範囲は最大{m[1]}ピクセルまで対応します。", f"截图区域最多支持 {m[1]} 像素。")),
    (re.compile(r"^편집 PNG를 읽지 못했습니다: (.+)$"), lambda m, l: _lang(l, f"Could not read the edited PNG: {_nested(m[1], l)}", f"編集済みPNGを読み込めませんでした: {_nested(m[1], l)}", f"无法读取编辑后的 PNG：{_nested(m[1], l)}")),
    (re.compile(r"^편집 이미지 크기가 캡처 범위와 다릅니다\. 예상 (\d+)x(\d+), 실제 (\d+)x(\d+)$"), lambda m, l: _lang(l, f"The edited image size differs from the capture bounds. Expected {m[1]}x{m[2]}, got {m[3]}x{m[4]}.", f"編集画像のサイズがキャプチャ範囲と異なります。想定{m[1]}x{m[2]}、実際{m[3]}x{m[4]}。", f"编辑图像尺寸与截图范围不同。预期 {m[1]}x{m[2]}，实际 {m[3]}x{m[4]}。")),
    (re.compile(r"^준비 실패: (.+)$"), lambda m, l: _lang(l, f"Preparation failed: {_nested(m[1], l)}", f"準備失敗: {_nested(m[1], l)}", f"准备失败：{_nested(m[1], l)}")),
    (re.compile(r"^수집 시작: 병렬 (\d+)개, 협업 작업 (\d+)/(\d+), (.+)$"), lambda m, l: _lang(l, f"Scan started: {m[1]} parallel workers, collaboration task {m[2]}/{m[3]}, {_nested(m[4], l)}", f"収集開始: 並列{m[1]}個、共同作業{m[2]}/{m[3]}、{_nested(m[4], l)}", f"已开始扫描：{m[1]} 个并行线程，协作任务 {m[2]}/{m[3]}，{_nested(m[4], l)}")),
    (re.compile(r"^수집 중: 내 작업 ([\d,]+)/([\d,]+) · 전체 확인 ([\d,]+)/([\d,]+)$"), lambda m, l: _lang(l, f"Scanning: my task {m[1]}/{m[2]} · overall checked {m[3]}/{m[4]}", f"収集中: 自分の作業{m[1]}/{m[2]} · 全体確認{m[3]}/{m[4]}", f"扫描中：我的任务 {m[1]}/{m[2]} · 全部已检查 {m[3]}/{m[4]}")),
    (re.compile(r"^보호 응답으로 자동 일시정지: (.+)$"), lambda m, l: _lang(l, f"Automatically paused after a protective response: {_nested(m[1], l)}", f"保護応答により自動一時停止: {_nested(m[1], l)}", f"因保护响应自动暂停：{_nested(m[1], l)}")),
    (re.compile(r"^보호 응답 자동 재시도 한도 ([\d,]+)회를 초과해 일시정지했습니다: (.+)$"), lambda m, l: _lang(l, f"Paused after exceeding the protective-response retry limit of {m[1]}: {_nested(m[2], l)}", f"保護応答の自動再試行上限{m[1]}回を超えたため一時停止しました: {_nested(m[2], l)}", f"超过保护响应自动重试上限 {m[1]} 次，已暂停：{_nested(m[2], l)}")),
    (re.compile(r"^HTTP (\d+) 보호 응답 · 전체 워커 ([\d.]+)초 대기 후 자동 재시도 ([\d,]+)회차$"), lambda m, l: _lang(l, f"HTTP {m[1]} protective response · all workers wait {m[2]} seconds · automatic retry {m[3]}", f"HTTP {m[1]} 保護応答 · 全ワーカーが{m[2]}秒待機 · 自動再試行{m[3]}回目", f"HTTP {m[1]} 保护响应 · 所有线程等待 {m[2]} 秒 · 第 {m[3]} 次自动重试")),
    (re.compile(r"^HTTP (\d+) 보호 응답: 전체 워커가 ([\d.]+)초 대기한 뒤 같은 픽셀부터 자동 재시도합니다\. \(재시도 ([\d,]+)회차\)$"), lambda m, l: _lang(l, f"HTTP {m[1]} protective response: all workers will wait {m[2]} seconds, then automatically retry the same pixel (retry {m[3]}).", f"HTTP {m[1]} 保護応答: 全ワーカーが{m[2]}秒待機した後、同じピクセルから自動再試行します（{m[3]}回目）。", f"HTTP {m[1]} 保护响应：所有线程等待 {m[2]} 秒后，将从同一像素自动重试（第 {m[3]} 次）。")),
    (re.compile(r"^자동 일시정지: (.+)$"), lambda m, l: _lang(l, f"Automatically paused: {_nested(m[1], l)}", f"自動一時停止: {_nested(m[1], l)}", f"自动暂停：{_nested(m[1], l)}")),
    (re.compile(r"^워커 (\d+) 조회 오류 ([\d,]+)/([\d,]+): (.+)$"), lambda m, l: _lang(l, f"Worker {m[1]} lookup error {m[2]}/{m[3]}: {_nested(m[4], l)}", f"ワーカー{m[1]} 照会エラー {m[2]}/{m[3]}: {_nested(m[4], l)}", f"线程 {m[1]} 查询错误 {m[2]}/{m[3]}：{_nested(m[4], l)}")),
    (re.compile(r"^워커 (\d+)에서 오류가 ([\d,]+)회 연속 발생해 자동 일시정지했습니다\.$"), lambda m, l: _lang(l, f"Automatically paused after worker {m[1]} encountered {m[2]} consecutive errors.", f"ワーカー{m[1]}でエラーが{m[2]}回連続したため自動一時停止しました。", f"线程 {m[1]} 连续发生 {m[2]} 次错误，已自动暂停。")),
    (re.compile(r"^협업 작업 (\d+)/(\d+)의 모든 픽셀을 완료했습니다\. 참여 노드라면 작업 결과 파일을 내보내 기준 노드에서 병합하세요\.$"), lambda m, l: _lang(l, f"Completed all pixels for collaboration task {m[1]}/{m[2]}. If this is a participant node, export a work-result file and merge it on the main node.", f"共同作業{m[1]}/{m[2]}の全ピクセルが完了しました。参加ノードの場合は作業結果ファイルを書き出し、基準ノードで統合してください。", f"已完成协作任务 {m[1]}/{m[2]} 的全部像素。若这是参与节点，请导出任务结果文件并在主节点合并。")),
    (re.compile(r"^수집기 오류: (.+)$"), lambda m, l: _lang(l, f"Scanner error: {_nested(m[1], l)}", f"スキャナーエラー: {_nested(m[1], l)}", f"扫描器错误：{_nested(m[1], l)}")),
    (re.compile(r"^수집기 오류로 중단: (.+)$"), lambda m, l: _lang(l, f"Stopped because of a scanner error: {_nested(m[1], l)}", f"スキャナーエラーにより停止: {_nested(m[1], l)}", f"因扫描器错误停止：{_nested(m[1], l)}")),
    (re.compile(r"^작업 결과 파일이 아닙니다\. 감지된 패키지 종류: (.+)$"), lambda m, l: _lang(l, f"This is not a work-result file. Detected package type: {_nested(m[1], l)}", f"作業結果ファイルではありません。検出された種類: {_nested(m[1], l)}", f"这不是任务结果文件。检测到的包类型：{_nested(m[1], l)}")),
    (re.compile(r"^협업 시작 파일이 아닙니다\. 감지된 패키지 종류: (.+)$"), lambda m, l: _lang(l, f"This is not a collaboration start file. Detected package type: {_nested(m[1], l)}", f"共同作業開始ファイルではありません。検出された種類: {_nested(m[1], l)}", f"这不是协作开始文件。检测到的包类型：{_nested(m[1], l)}")),
    (re.compile(r"^작업 결과 owners\.bin 크기가 올바르지 않습니다\. 예상 ([\d,]+)바이트, 실제 ([\d,]+)바이트$"), lambda m, l: _lang(l, f"The work-result owners.bin size is invalid. Expected {m[1]} bytes, got {m[2]} bytes.", f"作業結果のowners.binサイズが正しくありません。予想{m[1]}バイト、実際{m[2]}バイト。", f"任务结果 owners.bin 大小无效。预期 {m[1]} 字节，实际 {m[2]} 字节。")),
    (re.compile(r"^남은 작업 분배 재시작 파일 생성 완료: 미확인 ([\d,]+)개, ([\d,]+)분할$"), lambda m, l: _lang(l, f"Remaining-work redistribution start file created: {m[1]} unchecked pixels, {m[2]} shards", f"残り作業の分配再開始ファイルを作成しました: 未確認{m[1]}個、{m[2]}分割", f"已创建剩余任务重新分配开始文件：未检查 {m[1]} 个，{m[2]} 个分片")),
    (re.compile(r"^남은 작업 재분배 패키지를 가져왔습니다: 재분배 대상 ([\d,]+)개\. 작업 번호를 설정하세요\.$"), lambda m, l: _lang(l, f"Imported a remaining-work redistribution package for {m[1]} pixels. Set the work number.", f"残り作業の再分配パッケージを読み込みました: 対象{m[1]}ピクセル。作業番号を設定してください。", f"已导入剩余任务重新分配包：目标 {m[1]} 个像素。请设置任务编号。")),
    (re.compile(r"^협업 시작 파일을 가져왔습니다\. 다른 노드와 겹치지 않게 내 작업 번호를 설정하고 저장한 뒤 수집을 시작하세요\.$"), lambda m, l: _lang(l, "Collaboration start file imported. Set and save a work number that does not overlap other nodes, then start scanning.", "共同作業開始ファイルを読み込みました。他ノードと重複しない作業番号を設定・保存してから収集を開始してください。", "已导入协作开始文件。请设置并保存不与其他节点重复的任务编号，然后开始扫描。")),
    (re.compile(r"^알 수 없는 협업 작업 순서 형식입니다: (.+)$"), lambda m, l: _lang(l, f"Unknown collaboration assignment-order format: {m[1]}", f"不明な共同作業割り当て順序形式です: {m[1]}", f"未知的协作任务顺序格式：{m[1]}")),
    (re.compile(r"^ZIP이 손상되었습니다: (.+)$"), lambda m, l: _lang(l, f"The ZIP is damaged: {m[1]}", f"ZIPが破損しています: {m[1]}", f"ZIP 已损坏：{m[1]}")),
    (re.compile(r"^manifest\.json을 읽을 수 없습니다: (.+)$"), lambda m, l: _lang(l, f"Could not read manifest.json: {m[1]}", f"manifest.jsonを読み込めません: {m[1]}", f"无法读取 manifest.json：{m[1]}")),
    (re.compile(r"^협업 ZIP에 (.+) 파일이 없습니다\.$"), lambda m, l: _lang(l, f"The collaboration ZIP does not contain {m[1]}.", f"共同作業ZIPに{m[1]}ファイルがありません。", f"协作 ZIP 中缺少 {m[1]} 文件。")),
    (re.compile(r"^협업 ZIP의 (.+) 파일을 읽을 수 없습니다: (.+)$"), lambda m, l: _lang(l, f"Could not read {m[1]} from the collaboration ZIP: {m[2]}", f"共同作業ZIPの{m[1]}ファイルを読み込めません: {m[2]}", f"无法读取协作 ZIP 中的 {m[1]} 文件：{m[2]}")),
    (re.compile(r"^사용자 픽셀을 (\d+)px 격자로 묶고 서로 맞닿은 격자를 같은 영역으로 분류한 뒤, 픽셀 수가 가장 많은 영역의 중심에 가장 가까운 실제 소유 픽셀을 대표 좌표로 선택$"), lambda m, l: _lang(l, f"Group worker pixels into {m[1]} px grid cells, join adjacent cells into regions, select the region with the most pixels, and use the owned pixel nearest that region's center as the representative coordinate", f"作業者ピクセルを{m[1]}px格子にまとめ、隣接格子を同じ領域として分類し、最多ピクセル領域の中心に最も近い実所有ピクセルを代表座標として選択", f"将作业者像素按 {m[1]}px 网格归类，把相邻网格合并为同一区域，选择像素数最多的区域，并以最接近该区域中心的实际所属像素作为代表坐标")),
]


def translate_runtime_text(message: str | None, language: str | None) -> str:
    if message is None:
        return ""
    text = str(message)
    lang = normalize_language(language)
    if lang == "ko" or not text:
        return text
    translated = _EXACT.get(text, {}).get(lang)
    if translated:
        return translated
    for pattern, formatter in _PATTERNS:
        match = pattern.match(text)
        if match:
            return formatter(match, lang)
    return text


def translate_error(message: str, language: str | None) -> str:
    return translate_runtime_text(message, language)


def translate_status_payload(status: dict[str, Any] | None, language: str | None) -> dict[str, Any] | None:
    if status is None:
        return None
    lang = normalize_language(language)
    if lang == "ko":
        return status
    localized = deepcopy(status)
    for key in ("message", "pausedReason", "analysisError"):
        if localized.get(key):
            localized[key] = translate_runtime_text(str(localized[key]), lang)
    return localized

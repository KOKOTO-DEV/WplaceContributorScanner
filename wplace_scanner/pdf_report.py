from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.graphics.shapes import Drawing, Rect
from reportlab.platypus import Image as RLImage
from reportlab.platypus import KeepInFrame, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from PIL import Image as PILImage
from pypdf import PdfReader, PdfWriter
from pypdf.annotations import Link
from pypdf.generic import BooleanObject, Fit, NameObject

from .palette import wplace_color_label


@dataclass(frozen=True)
class _PdfFontSet:
    regular: str
    bold: str
    regular_fallbacks: tuple[str, ...]
    bold_fallbacks: tuple[str, ...]
    coverage: dict[str, frozenset[int]]


def _dedupe(values: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _register_tt_font(
    name: str,
    candidates: list[str],
    coverage: dict[str, frozenset[int]],
) -> str | None:
    """Register the first usable TrueType font and retain its Unicode cmap."""
    existing = set(pdfmetrics.getRegisteredFontNames())
    if name in existing:
        font = pdfmetrics.getFont(name)
        char_map = getattr(getattr(font, "face", None), "charToGlyph", None)
        if char_map:
            coverage[name] = frozenset(int(codepoint) for codepoint in char_map)
        return name
    for candidate in candidates:
        path = Path(candidate) if candidate else None
        if not path or not path.exists():
            continue
        try:
            font = TTFont(name, str(path), subfontIndex=0)
            pdfmetrics.registerFont(font)
            char_map = getattr(font.face, "charToGlyph", {})
            coverage[name] = frozenset(int(codepoint) for codepoint in char_map)
            return name
        except Exception:
            continue
    return None


def _register_fonts(language: str = "ko") -> _PdfFontSet:
    """Register embedded Unicode fonts plus CJK fallbacks for mixed-language text.

    The report-label language still controls translated headings. Font selection is
    independent of that language so project names, worker names, and free-form notes
    can safely contain Korean, Japanese, Simplified Chinese, and Latin in one PDF.
    """
    language = language if language in {"ko", "en", "ja", "zh-CN"} else "ko"
    coverage: dict[str, frozenset[int]] = {}
    custom_regular = os.environ.get("WPCS_PDF_FONT_UNICODE", "") or os.environ.get("WPCS_PDF_FONT", "")
    custom_bold = os.environ.get("WPCS_PDF_FONT_UNICODE_BOLD", "") or os.environ.get("WPCS_PDF_FONT_BOLD", "")

    universal = _register_tt_font("WPCS-Unicode", [
        custom_regular,
        r"C:\Windows\Fonts\arialuni.ttf",
        "/Library/Fonts/Arial Unicode.ttf",
        "/Library/Fonts/Arial Unicode MS.ttf",
    ], coverage)
    universal_bold = _register_tt_font("WPCS-Unicode-Bold", [
        custom_bold, custom_regular,
        r"C:\Windows\Fonts\arialuni.ttf",
        "/Library/Fonts/Arial Unicode.ttf",
        "/Library/Fonts/Arial Unicode MS.ttf",
    ], coverage)

    ko = _register_tt_font("WPCS-KO", [
        r"C:\Windows\Fonts\malgun.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "/usr/share/fonts/truetype/unfonts-core/UnDotum.ttf",
        "/Library/Fonts/AppleGothic.ttf",
        "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    ], coverage)
    ko_bold = _register_tt_font("WPCS-KO-Bold", [
        r"C:\Windows\Fonts\malgunbd.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
        "/usr/share/fonts/truetype/unfonts-core/UnDotumBold.ttf",
        r"C:\Windows\Fonts\malgun.ttf",
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "/Library/Fonts/AppleGothic.ttf",
    ], coverage)

    ja = _register_tt_font("WPCS-JA", [
        r"C:\Windows\Fonts\YuGothM.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        r"C:\Windows\Fonts\meiryo.ttc",
        r"C:\Windows\Fonts\msgothic.ttc",
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "/usr/share/fonts/truetype/arphic/uming.ttc",
        "/Library/Fonts/AppleGothic.ttf",
    ], coverage)
    ja_bold = _register_tt_font("WPCS-JA-Bold", [
        r"C:\Windows\Fonts\YuGothB.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
        r"C:\Windows\Fonts\meiryob.ttc",
        r"C:\Windows\Fonts\msgothic.ttc",
        "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
        "/usr/share/fonts/truetype/arphic/uming.ttc",
    ], coverage)

    zh = _register_tt_font("WPCS-ZH", [
        r"C:\Windows\Fonts\msyh.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        r"C:\Windows\Fonts\simsun.ttc",
        "/usr/share/fonts/truetype/arphic/uming.ttc",
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "/System/Library/Fonts/PingFang.ttc",
    ], coverage)
    zh_bold = _register_tt_font("WPCS-ZH-Bold", [
        r"C:\Windows\Fonts\msyhbd.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simsun.ttc",
        "/usr/share/fonts/truetype/arphic/uming.ttc",
        "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
    ], coverage)

    latin = _register_tt_font("WPCS-Latin", [
        r"C:\Windows\Fonts\arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/Library/Fonts/Arial.ttf",
    ], coverage)
    latin_bold = _register_tt_font("WPCS-Latin-Bold", [
        r"C:\Windows\Fonts\arialbd.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/Library/Fonts/Arial Bold.ttf",
    ], coverage)

    by_language = {
        "ko": (ko, ko_bold),
        "ja": (ja, ja_bold),
        "zh-CN": (zh, zh_bold),
        "en": (latin, latin_bold),
    }
    primary_regular, primary_bold = by_language[language]
    regular_order = _dedupe([
        primary_regular or "", universal or "", ko or "", ja or "", zh or "", latin or "",
    ])
    bold_order = _dedupe([
        primary_bold or "", universal_bold or "", ko_bold or "", ja_bold or "", zh_bold or "", latin_bold or "",
        *regular_order,
    ])
    if regular_order:
        return _PdfFontSet(
            regular=regular_order[0],
            bold=bold_order[0] if bold_order else regular_order[0],
            regular_fallbacks=regular_order,
            bold_fallbacks=bold_order or regular_order,
            coverage=coverage,
        )

    # Last-resort built-in CID fonts. These are not as reliable for mixed scripts,
    # but preserve single-language reports on stripped-down systems.
    if language == "ja":
        pdfmetrics.registerFont(UnicodeCIDFont("HeiseiMin-W3"))
        pdfmetrics.registerFont(UnicodeCIDFont("HeiseiKakuGo-W5"))
        return _PdfFontSet("HeiseiMin-W3", "HeiseiKakuGo-W5", ("HeiseiMin-W3",), ("HeiseiKakuGo-W5",), {})
    if language == "zh-CN":
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        return _PdfFontSet("STSong-Light", "STSong-Light", ("STSong-Light",), ("STSong-Light",), {})
    if language == "ko":
        pdfmetrics.registerFont(UnicodeCIDFont("HYSMyeongJo-Medium"))
        return _PdfFontSet("HYSMyeongJo-Medium", "HYSMyeongJo-Medium", ("HYSMyeongJo-Medium",), ("HYSMyeongJo-Medium",), {})
    return _PdfFontSet("Helvetica", "Helvetica-Bold", ("Helvetica",), ("Helvetica-Bold", "Helvetica"), {})


def _escape(text: Any) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _unicode_markup(text: Any, fonts: _PdfFontSet, *, bold: bool = False) -> str:
    """Escape text and wrap glyph runs in an embedded font that supports them."""
    raw = str(text)
    if not raw:
        return ""
    order = fonts.bold_fallbacks if bold else fonts.regular_fallbacks
    default_font = fonts.bold if bold else fonts.regular
    runs: list[tuple[str, str]] = []
    current_font = ""
    current_chars: list[str] = []
    for char in raw:
        codepoint = ord(char)
        selected = next(
            (name for name in order if not fonts.coverage.get(name) or codepoint in fonts.coverage[name]),
            default_font,
        )
        if selected != current_font and current_chars:
            runs.append((current_font, "".join(current_chars)))
            current_chars = []
        current_font = selected
        current_chars.append(char)
    if current_chars:
        runs.append((current_font, "".join(current_chars)))
    return "".join(
        f'<font name="{font_name}">{_escape(value)}</font>' for font_name, value in runs
    )


def _color_lines(items: list[dict[str, Any]], font_name: str, other_label: str = "Other") -> Table:
    """Render Wplace colors with an outlined swatch and official palette name."""
    text_style = ParagraphStyle("colors", fontName=font_name, fontSize=6.6, leading=8.2)
    if not items:
        return Table([[Paragraph("-", text_style)]], colWidths=[54 * mm])
    rows: list[list[Any]] = []
    for item in items:
        code = str(item.get("hex", ""))
        percent = float(item.get("percent", 0.0))
        count = int(item.get("count", 0))
        if code == "기타":
            label = other_label
            swatch: Any = ""
        elif code.startswith("#") and len(code) == 7:
            label = wplace_color_label(code, include_hex=True)
            drawing = Drawing(8, 8)
            drawing.add(Rect(0.5, 0.5, 7, 7, fillColor=colors.HexColor(code),
                             strokeColor=colors.HexColor("#334155"), strokeWidth=0.8))
            swatch = drawing
        else:
            label = code
            swatch = ""
        rows.append([swatch, Paragraph(
            f"{_escape(label)} {percent:.1f}% ({count:,})", text_style
        )])
    table = Table(rows, colWidths=[4 * mm, 52 * mm], hAlign="LEFT")
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 1),
        ("TOPPADDING", (0, 0), (-1, -1), 0.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0.5),
    ]))
    return table


def _offset_label(offset: timedelta | None) -> str:
    total_minutes = int((offset or timedelta()).total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    total_minutes = abs(total_minutes)
    hours, minutes = divmod(total_minutes, 60)
    return f"UTC{sign}{hours:02d}:{minutes:02d}"


def _resolve_report_timezone(
    timezone_name: str | None = None, timezone_offset_minutes: int | None = None
) -> tuple[tzinfo, str]:
    name = str(timezone_name or "").strip()[:128]
    if name:
        try:
            zone = ZoneInfo(name)
            return zone, name
        except (ZoneInfoNotFoundError, ValueError):
            pass
    if timezone_offset_minutes is not None:
        # JavaScript Date.getTimezoneOffset() is UTC - local time.
        browser_offset = max(-840, min(840, int(timezone_offset_minutes)))
        local_delta = timedelta(minutes=-browser_offset)
        label = _offset_label(local_delta)
        return timezone(local_delta, name=label), label
    local_zone = datetime.now().astimezone().tzinfo or timezone.utc
    label = getattr(local_zone, "key", None) or _offset_label(datetime.now(local_zone).utcoffset())
    return local_zone, str(label)


def _format_iso_timestamp(value: str | None, missing: str, report_timezone: tzinfo) -> str:
    if not value:
        return missing
    try:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(report_timezone).strftime("%Y-%m-%d %H:%M:%S %z")
    except Exception:
        return str(value)


def _format_manual_datetime(value: str | None) -> str:
    """Format a browser datetime-local value without applying timezone conversion."""
    text = str(value or "").strip()[:64]
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text)
        return parsed.strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return text


def _paragraph_lines(value: str | None, fonts: _PdfFontSet) -> str:
    # The browser currently accepts up to 2,000 characters. Preserve every line
    # instead of truncating the note after eight explicit line breaks.
    raw = str(value or "").strip()[:2000].replace("\r\n", "\n").replace("\r", "\n")
    return "<br/>".join(_unicode_markup(line, fonts) for line in raw.split("\n"))



class _TrackedImage(RLImage):
    def __init__(self, filename: str, tracker: dict[str, Any], **kwargs: Any) -> None:
        super().__init__(filename, **kwargs)
        self._tracker = tracker

    def drawOn(self, canv: Any, x: float, y: float, _sW: float = 0) -> None:
        super().drawOn(canv, x, y, _sW)
        self._tracker.update({
            "pageIndex": int(canv.getPageNumber()) - 1,
            "rect": (float(x), float(y), float(x + self.drawWidth), float(y + self.drawHeight)),
        })


def _scaled_image(
    path: Path, max_width: float, max_height: float, tracker: dict[str, Any] | None = None
) -> RLImage:
    image: RLImage
    if tracker is None:
        image = RLImage(str(path))
    else:
        image = _TrackedImage(str(path), tracker)
    ratio = min(max_width / image.imageWidth, max_height / image.imageHeight, 1.0)
    image.drawWidth = image.imageWidth * ratio
    image.drawHeight = image.imageHeight * ratio
    image.hAlign = "CENTER"
    return image



def _disable_image_interpolation(writer: PdfWriter) -> None:
    """Tell PDF viewers to render embedded PNG pixels without smoothing."""
    for page in writer.pages:
        resources = page.get("/Resources")
        if not resources:
            continue
        resources = resources.get_object() if hasattr(resources, "get_object") else resources
        xobjects = resources.get("/XObject") if resources else None
        if not xobjects:
            continue
        xobjects = xobjects.get_object() if hasattr(xobjects, "get_object") else xobjects
        for reference in xobjects.values():
            image = reference.get_object() if hasattr(reference, "get_object") else reference
            if image.get("/Subtype") == "/Image":
                image[NameObject("/Interpolate")] = BooleanObject(False)

def _append_full_resolution_snapshot(
    main_pdf: Path, output: Path, snapshot_path: Path, tracker: dict[str, Any], outline_title: str
) -> None:
    with PILImage.open(snapshot_path) as source_image:
        source_width, source_height = source_image.size
    if source_width <= 0 or source_height <= 0:
        main_pdf.replace(output)
        return

    # PDF page dimensions are limited to 14,400 pt by common viewers. Keep one source
    # pixel per PDF point whenever possible, scaling only unusually large snapshots.
    page_scale = min(1.0, 14_000.0 / source_width, 14_000.0 / source_height)
    page_width = max(1.0, source_width * page_scale)
    page_height = max(1.0, source_height * page_scale)

    appendix_file = NamedTemporaryFile(prefix="wpcs-snapshot-", suffix=".pdf", delete=False)
    appendix_file.close()
    appendix_path = Path(appendix_file.name)
    try:
        c = pdf_canvas.Canvas(str(appendix_path), pagesize=(page_width, page_height), pageCompression=1)
        c.setTitle(outline_title)
        c.drawImage(
            str(snapshot_path), 0, 0, width=page_width, height=page_height,
            preserveAspectRatio=True, anchor="c", mask="auto",
        )
        c.showPage()
        c.save()

        main_reader = PdfReader(str(main_pdf))
        appendix_reader = PdfReader(str(appendix_path))
        writer = PdfWriter()
        writer.append(main_reader)
        appendix_index = len(main_reader.pages)
        writer.append(appendix_reader)

        page_index = tracker.get("pageIndex")
        rect = tracker.get("rect")
        if isinstance(page_index, int) and rect and 0 <= page_index < len(main_reader.pages):
            writer.add_annotation(
                page_number=page_index,
                annotation=Link(rect=rect, target_page_index=appendix_index, fit=Fit.fit()),
            )
        try:
            writer.add_outline_item(outline_title, appendix_index)
        except Exception:
            pass
        # Keep the exact PNG bytes inside the PDF as an attachment as well as on
        # the full-resolution page. This provides a lossless source even in PDF
        # viewers that apply their own zoom smoothing.
        writer.add_attachment(snapshot_path.name, snapshot_path.read_bytes())
        _disable_image_interpolation(writer)
        with output.open("wb") as fp:
            writer.write(fp)
    finally:
        appendix_path.unlink(missing_ok=True)
        main_pdf.unlink(missing_ok=True)


def build_pdf_report(
    output: Path,
    project_name: str,
    source_name: str,
    generated_at: str,
    done: int,
    total: int,
    no_author: int,
    method: str,
    rows: list[dict[str, Any]],
    language: str = "ko",
    *,
    match_mode: str = "color",
    snapshot_path: Path | None = None,
    snapshot_at: str | None = None,
    timezone_name: str | None = None,
    timezone_offset_minutes: int | None = None,
    manual_work_start: str | None = None,
    manual_work_end: str | None = None,
    report_note: str | None = None,
) -> Path:
    language = language if language in {"ko", "en", "ja", "zh-CN"} else "ko"
    font_set = _register_fonts(language)
    regular, bold = font_set.regular, font_set.bold
    tr = {
        "ko": {
            "title": "Wplace 작업 픽셀 통계", "project": "프로젝트", "source": "원본",
            "calculation_mode": "계산 방식", "mode_region": "영역 기준", "mode_color": "색상 일치 기준",
            "generated": "생성", "timezone": "시간대", "checked": "확인", "unworked": "미작업", "method": "대표 좌표",
            "manual_period": "작업 기간", "manual_start": "작업 시작", "manual_end": "작업 종료",
            "report_note": "하고 싶은 말",
            "rank": "순위", "worker": "작업자", "pixels_share": "픽셀 / 지분",
            "region": "대표 영역", "coordinate": "대표 좌표", "overall": "전체 색상 사용",
            "region_colors": "대표 영역 색상", "representative_colors": "대표 색상", "other": "기타", "missing": "확인 불가",
            "snapshot": "실제 칠해진 그림", "snapshot_at": "캔버스 비교 시점",
            "snapshot_click": "이미지를 클릭하면 PDF 마지막 페이지에서 원본 해상도로 볼 수 있습니다.",
            "snapshot_full": "실제 칠해진 그림 - 원본 해상도",
            "method_text": "대표 영역의 중심에 가장 가까운 실제 소유 픽셀을 대표 좌표로 선택",
            "region_method_text": "작업자 픽셀을 64px 격자로 묶고 서로 맞닿은 격자를 하나의 영역으로 분류한 뒤, 픽셀 수가 가장 많은 영역을 대표 영역으로 선택",
            "color_method_text": "작업자의 전체 픽셀과 대표 영역에서 사용된 색상을 각각 집계해 픽셀 수와 비율 기준 상위 5개 색상과 기타로 표시",
        },
        "en": {
            "title": "Wplace Work Pixel Statistics", "project": "Project", "source": "Source",
            "calculation_mode": "Calculation mode", "mode_region": "Region-based", "mode_color": "Color-match",
            "generated": "Generated", "timezone": "Time zone", "checked": "Checked", "unworked": "Unworked", "method": "Representative coordinate",
            "manual_period": "Work period", "manual_start": "Work start", "manual_end": "Work end",
            "report_note": "Message",
            "rank": "Rank", "worker": "Worker", "pixels_share": "Pixels / Share",
            "region": "Representative region", "coordinate": "Representative coordinate", "overall": "Overall color usage",
            "region_colors": "Representative-region colors", "representative_colors": "Representative colors", "other": "Other", "missing": "Unavailable",
            "snapshot": "Actual painted image", "snapshot_at": "Canvas comparison time",
            "snapshot_click": "Click the image to open the full-resolution page at the end of this PDF.",
            "snapshot_full": "Actual painted image - full resolution",
            "method_text": "Use the owned pixel nearest the center of the representative region as the representative coordinate",
            "region_method_text": "Group worker pixels into 64 px grid cells, join touching cells into regions, and select the region with the most pixels as the representative region",
            "color_method_text": "Count colors in the worker's overall pixels and representative region, then show the top five by pixel count and percentage plus Other",
        },
        "ja": {
            "title": "Wplace 作業ピクセル統計", "project": "プロジェクト", "source": "元ファイル",
            "calculation_mode": "計算方式", "mode_region": "領域基準", "mode_color": "色一致基準",
            "generated": "生成日時", "timezone": "タイムゾーン", "checked": "確認", "unworked": "未作業", "method": "代表座標",
            "manual_period": "作業期間", "manual_start": "作業開始", "manual_end": "作業終了",
            "report_note": "伝えたいこと",
            "rank": "順位", "worker": "作業者", "pixels_share": "ピクセル / 比率",
            "region": "代表領域", "coordinate": "代表座標", "overall": "全体の色使用率",
            "region_colors": "代表領域の色", "representative_colors": "代表色", "other": "その他", "missing": "確認不可",
            "snapshot": "実際に塗られた画像", "snapshot_at": "キャンバス比較時刻",
            "snapshot_click": "画像をクリックすると、このPDFの最終ページで元の解像度を表示します。",
            "snapshot_full": "実際に塗られた画像 - 元の解像度",
            "method_text": "代表領域の中心に最も近い実所有ピクセルを代表座標として選択",
            "region_method_text": "作業者ピクセルを64px格子にまとめ、接している格子を一つの領域に分類し、ピクセル数が最も多い領域を代表領域として選択",
            "color_method_text": "作業者の全体ピクセルと代表領域で使用された色を集計し、ピクセル数と比率の上位5色およびその他を表示",
        },
        "zh-CN": {
            "title": "Wplace 作业像素统计", "project": "项目", "source": "来源",
            "calculation_mode": "计算方式", "mode_region": "区域模式", "mode_color": "颜色匹配模式",
            "generated": "生成时间", "timezone": "时区", "checked": "已检查", "unworked": "未作业", "method": "代表坐标",
            "manual_period": "作业期间", "manual_start": "作业开始", "manual_end": "作业结束",
            "report_note": "想说的话",
            "rank": "排名", "worker": "作业者", "pixels_share": "像素 / 占比",
            "region": "代表区域", "coordinate": "代表坐标", "overall": "全部颜色使用",
            "region_colors": "代表区域颜色", "representative_colors": "代表颜色", "other": "其他", "missing": "无法确认",
            "snapshot": "实际绘制图像", "snapshot_at": "画布比较时间",
            "snapshot_click": "点击图像可在本 PDF 的最后一页查看原始分辨率。",
            "snapshot_full": "实际绘制图像 - 原始分辨率",
            "method_text": "选择最接近代表区域中心的实际所属像素作为代表坐标",
            "region_method_text": "将作业者像素按64px网格归类，把相接网格合并为同一区域，并选择像素数最多的区域作为代表区域",
            "color_method_text": "分别统计作业者全部像素和代表区域使用的颜色，按像素数与比例显示前五种颜色及其他",
        },
    }[language]
    report_timezone, report_timezone_label = _resolve_report_timezone(
        timezone_name, timezone_offset_minutes
    )

    main_file = NamedTemporaryFile(prefix="wpcs-report-", suffix=".pdf", delete=False)
    main_file.close()
    main_output = Path(main_file.name)
    snapshot_tracker: dict[str, Any] = {}
    doc = SimpleDocTemplate(
        str(main_output), pagesize=landscape(A4), rightMargin=10 * mm, leftMargin=10 * mm,
        topMargin=10 * mm, bottomMargin=10 * mm, title=f"{tr['title']} - {project_name}",
        author="Wplace Contributor Scanner",
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("title", parent=styles["Title"], fontName=bold, fontSize=18, leading=22, alignment=TA_LEFT)
    project_font_size = 31 if len(project_name) <= 32 else 25 if len(project_name) <= 64 else 20
    project_style = ParagraphStyle(
        "project-title", parent=styles["Title"], fontName=bold,
        fontSize=project_font_size, leading=project_font_size * 1.22,
        alignment=TA_LEFT, textColor=colors.HexColor("#0F172A"),
    )
    h2_style = ParagraphStyle("h2", parent=styles["Heading2"], fontName=bold, fontSize=13, leading=16, spaceAfter=5)
    body_style = ParagraphStyle("body", parent=styles["BodyText"], fontName=regular, fontSize=8.5, leading=11)
    small_style = ParagraphStyle("small", parent=body_style, fontSize=7.2, leading=9)
    header_style = ParagraphStyle("header", parent=small_style, fontName=bold, alignment=TA_CENTER, textColor=colors.white)
    label_style = ParagraphStyle(
        "cover-label", parent=small_style, fontName=bold, fontSize=7.4, leading=9,
        textColor=colors.HexColor("#64748B"),
    )
    value_style = ParagraphStyle(
        "cover-value", parent=body_style, fontName=regular, fontSize=10, leading=13,
        textColor=colors.HexColor("#0F172A"),
    )
    note_style = ParagraphStyle(
        "cover-note", parent=body_style, fontName=regular, fontSize=10, leading=14,
        textColor=colors.HexColor("#0F172A"),
    )

    mode_text = tr["mode_region"] if str(match_mode).lower() == "region" else tr["mode_color"]
    generated_text = _format_iso_timestamp(generated_at, tr["missing"], report_timezone)
    work_start_text = _format_manual_datetime(manual_work_start)
    work_end_text = _format_manual_datetime(manual_work_end)
    note_text = _paragraph_lines(report_note, font_set)

    story: list[Any] = [
        Paragraph(tr["title"], title_style),
        Spacer(1, 4 * mm),
        Paragraph(_unicode_markup(project_name, font_set, bold=True), project_style),
        Spacer(1, 5 * mm),
    ]

    summary_data = [
        [
            Paragraph(tr["source"], label_style),
            Paragraph(tr["calculation_mode"], label_style),
            Paragraph(tr["checked"], label_style),
            Paragraph(tr["unworked"], label_style),
        ],
        [
            Paragraph(_unicode_markup(source_name, font_set), value_style),
            Paragraph(_escape(mode_text), value_style),
            Paragraph(f"{done:,} / {total:,}", value_style),
            Paragraph(f"{no_author:,}", value_style),
        ],
        [
            Paragraph(tr["generated"], label_style),
            Paragraph(tr["timezone"], label_style),
            Paragraph("", label_style),
            Paragraph("", label_style),
        ],
        [
            Paragraph(_escape(generated_text), value_style),
            Paragraph(_escape(report_timezone_label), value_style),
            Paragraph("", value_style),
            Paragraph("", value_style),
        ],
    ]
    summary_table = Table(summary_data, colWidths=[77 * mm, 61 * mm, 61 * mm, 61 * mm], hAlign="LEFT")
    summary_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
        ("BOX", (0, 0), (-1, -1), 0.7, colors.HexColor("#CBD5E1")),
        ("INNERGRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#E2E8F0")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.extend([summary_table, Spacer(1, 6 * mm)])

    period_data = [[
        Paragraph(
            f"{_unicode_markup(tr['manual_start'], font_set, bold=True)}<br/>"
            f"{_unicode_markup(work_start_text, font_set) if work_start_text else '&nbsp;'}",
            value_style,
        ),
        Paragraph(
            f"{_unicode_markup(tr['manual_end'], font_set, bold=True)}<br/>"
            f"{_unicode_markup(work_end_text, font_set) if work_end_text else '&nbsp;'}",
            value_style,
        ),
    ]]
    period_table = Table(period_data, colWidths=[130 * mm, 130 * mm], rowHeights=[28 * mm], hAlign="LEFT")
    period_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#EFF6FF")),
        ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#93C5FD")),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#BFDBFE")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 9),
        ("RIGHTPADDING", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.extend([
        Paragraph(tr["manual_period"], h2_style),
        period_table,
        Spacer(1, 4 * mm),
        Paragraph(
            f"{_unicode_markup(tr['method'] + ':', font_set, bold=True)} "
            f"{_unicode_markup(tr['method_text'], font_set)}",
            small_style,
        ),
        Spacer(1, 1.2 * mm),
        Paragraph(
            f"{_unicode_markup(tr['region'] + ':', font_set, bold=True)} "
            f"{_unicode_markup(tr['region_method_text'], font_set)}",
            small_style,
        ),
        Spacer(1, 1.2 * mm),
        Paragraph(
            f"{_unicode_markup(tr['representative_colors'] + ':', font_set, bold=True)} "
            f"{_unicode_markup(tr['color_method_text'], font_set)}",
            small_style,
        ),
        PageBreak(),
        Paragraph(tr["report_note"], h2_style),
        Spacer(1, 2 * mm),
    ])

    note_content = KeepInFrame(
        252 * mm,
        143 * mm,
        [Paragraph(note_text if note_text else "&nbsp;", note_style)],
        mode="shrink",
        mergeSpace=True,
    )
    note_table = Table(
        [[note_content]],
        colWidths=[260 * mm],
        rowHeights=[150 * mm],
        hAlign="LEFT",
    )
    note_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.white),
        ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#CBD5E1")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 9),
        ("RIGHTPADDING", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.extend([note_table, PageBreak()])

    if snapshot_path and Path(snapshot_path).exists():
        story.append(Paragraph(tr["snapshot"], h2_style))
        story.append(_scaled_image(Path(snapshot_path), 272 * mm, 170 * mm, snapshot_tracker))
        story.append(Paragraph(f'<font color="#1267a5">{_escape(tr["snapshot_click"])}</font>', small_style))
        if snapshot_at:
            snapshot_time = _format_iso_timestamp(snapshot_at, tr['missing'], report_timezone)
            story.append(Paragraph(f"{tr['snapshot_at']}: {_escape(snapshot_time)}", small_style))
        story.append(PageBreak())

    table_data: list[list[Any]] = [[
        Paragraph(tr["rank"], header_style), Paragraph(tr["worker"], header_style),
        Paragraph(tr["pixels_share"], header_style), Paragraph(tr["region"], header_style),
        Paragraph(tr["coordinate"], header_style), Paragraph(tr["overall"], header_style),
        Paragraph(tr["region_colors"], header_style),
    ]]
    for row in rows:
        link = _escape(row["link"]); coord = _escape(row["coordinate"])
        table_data.append([
            Paragraph(str(row["rank"]), small_style),
            Paragraph(
                f'{_unicode_markup(row["name"], font_set, bold=True)}<br/><font size="6.5">'
                f'ID {row["userId"]}'
                f'{" · " + _unicode_markup(row["allianceName"], font_set) if row.get("allianceName") else ""}'
                f'</font>',
                small_style,
            ),
            Paragraph(f'{row["pixels"]:,}<br/>{row["share"]:.4f}%', small_style),
            Paragraph(f'{row["regionPixels"]:,}<br/>{row["regionShare"]:.2f}%', small_style),
            Paragraph(f'<link href="{link}" color="#1267a5">{coord}</link>', small_style),
            _color_lines(row.get("overallColors", []), regular, tr["other"]),
            _color_lines(row.get("regionColors", []), regular, tr["other"]),
        ])
    table = Table(table_data, repeatRows=1, colWidths=[12*mm, 43*mm, 24*mm, 24*mm, 45*mm, 58*mm, 58*mm], hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#334155")), ("FONTNAME", (0,0), (-1,0), bold),
        ("VALIGN", (0,0), (-1,-1), "TOP"), ("GRID", (0,0), (-1,-1), .25, colors.HexColor("#CBD5E1")),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, colors.HexColor("#F8FAFC")]),
        ("LEFTPADDING", (0,0), (-1,-1), 4), ("RIGHTPADDING", (0,0), (-1,-1), 4),
        ("TOPPADDING", (0,0), (-1,-1), 4), ("BOTTOMPADDING", (0,0), (-1,-1), 4),
    ]))
    story.append(table)
    try:
        doc.build(story)
        if snapshot_path and Path(snapshot_path).exists():
            _append_full_resolution_snapshot(
                main_output, output, Path(snapshot_path), snapshot_tracker, tr["snapshot_full"]
            )
        else:
            main_output.replace(output)
    finally:
        main_output.unlink(missing_ok=True)
    return output

from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase import pdfmetrics
from reportlab.platypus import (
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


ROOT = Path("/media/fishduke/06800C3B800C3429/WorkWithCodex/vms-8ch-webrtc")
OUTPUT = ROOT / "output/pdf/hanjin-cctv-event-judgment-algorithms-ko-2026-03-18.pdf"
DIAGRAM_DIR = ROOT / "docs/edge-basic-diagrams/generated"


def register_fonts() -> None:
    pdfmetrics.registerFont(UnicodeCIDFont("HYGothic-Medium"))


def styles():
    base = getSampleStyleSheet()
    return {
            "title": ParagraphStyle(
            "TitleKR",
            parent=base["Title"],
            fontName="HYGothic-Medium",
            fontSize=22,
            leading=28,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#0F172A"),
            spaceAfter=12,
        ),
            "subtitle": ParagraphStyle(
            "SubtitleKR",
            parent=base["BodyText"],
            fontName="HYGothic-Medium",
            fontSize=10.5,
            leading=15,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#475569"),
            spaceAfter=8,
        ),
            "h1": ParagraphStyle(
            "H1KR",
            parent=base["Heading1"],
            fontName="HYGothic-Medium",
            fontSize=16,
            leading=22,
            textColor=colors.HexColor("#0F172A"),
            spaceBefore=10,
            spaceAfter=8,
        ),
            "h2": ParagraphStyle(
            "H2KR",
            parent=base["Heading2"],
            fontName="HYGothic-Medium",
            fontSize=12.5,
            leading=18,
            textColor=colors.HexColor("#1E293B"),
            spaceBefore=8,
            spaceAfter=6,
        ),
            "body": ParagraphStyle(
            "BodyKR",
            parent=base["BodyText"],
            fontName="HYGothic-Medium",
            fontSize=9.6,
            leading=14.5,
            alignment=TA_LEFT,
            textColor=colors.HexColor("#1F2937"),
            spaceAfter=5,
        ),
            "small": ParagraphStyle(
            "SmallKR",
            parent=base["BodyText"],
            fontName="HYGothic-Medium",
            fontSize=8.6,
            leading=12,
            textColor=colors.HexColor("#475569"),
            spaceAfter=4,
        ),
    }


def fit_image(path: Path, max_width: float, max_height: float) -> Image:
    img = Image(str(path))
    iw, ih = img.imageWidth, img.imageHeight
    scale = min(max_width / iw, max_height / ih)
    img.drawWidth = iw * scale
    img.drawHeight = ih * scale
    return img


def bullet(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(f"&bull; {text}", style)


def build_story() -> list:
    s = styles()
    page_w, page_h = A4
    body_w = page_w - 32 * mm
    diagram_h = page_h - 95 * mm

    summary_rows = [
        [
            Paragraph("<b>알고리즘</b>", s["small"]),
            Paragraph("<b>이벤트 타입</b>", s["small"]),
            Paragraph("<b>핵심 조건</b>", s["small"]),
        ],
        [
            Paragraph("사람 ROI 진입", s["small"]),
            Paragraph("person_cross_roi", s["small"]),
            Paragraph("사람 하단부가 ROI에 진입하고, 직전에는 0명이며 현재는 1명 이상", s["small"]),
        ],
        [
            Paragraph("헬멧 미착용", s["small"]),
            Paragraph("helmet_missing_in_roi", s["small"]),
            Paragraph("사람은 있는데 헬멧이 없고, 그 상태가 holdSec 이상 유지", s["small"]),
        ],
        [
            Paragraph("신호수 없는 차량 이탈", s["small"]),
            Paragraph("unauthorized_departure", s["small"]),
            Paragraph("충분히 보였던 차량이 사라지고 동시에 사람도 없는 상태가 exitHoldSec 이상 유지", s["small"]),
        ],
        [
            Paragraph("불법 주정차 정지", s["small"]),
            Paragraph("no_parking_stop", s["small"]),
            Paragraph("동일 차량이 ROI 안에서 거의 움직이지 않은 채 dwellSec 이상 정지", s["small"]),
        ],
    ]

    story = [
        Spacer(1, 10 * mm),
        Paragraph("HANJIN CCTV 이벤트 판정 알고리즘", s["title"]),
        Paragraph(
            "코드 기준 설명 자료 / 기준 파일: config/event_packs/edge-basic@1.0.0.json, services/recorder/worker.py",
            s["subtitle"],
        ),
        Spacer(1, 3 * mm),
        Paragraph("문서 목적", s["h1"]),
        bullet("AI 탐지 결과가 Recorder 단계에서 어떻게 최종 이벤트로 변환되는지 설명", s["body"]),
        bullet("4개 규칙의 입력, 상태 변수, 시간 조건, 오탐/미탐 포인트를 한 번에 정리", s["body"]),
        bullet("현장 튜닝 시 어떤 파라미터를 조정해야 하는지 빠르게 파악할 수 있게 구성", s["body"]),
        Spacer(1, 4 * mm),
        Paragraph("전체 구조", s["h1"]),
        fit_image(DIAGRAM_DIR / "01-1-한눈에-보는-전체-구조.png", body_w, diagram_h * 0.6),
        Spacer(1, 5 * mm),
        Paragraph("4개 알고리즘 요약", s["h1"]),
        Table(
            summary_rows,
            colWidths=[40 * mm, 45 * mm, body_w - 85 * mm],
            style=TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E2E8F0")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#0F172A")),
                    ("FONTNAME", (0, 0), (-1, -1), "HYGothic-Medium"),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ]
            ),
        ),
    ]

    sections = [
        (
            "알고리즘 1. 사람 ROI 진입",
            "diagram-02.png",
            [
                "입력: person detections",
                "핵심 상태: prev_inside",
                "하단 띠 샘플링으로 사람 하단부가 ROI 안에 entryRatio 이상 포함되면 inside로 판단",
                "직전에는 0명, 현재는 1명 이상일 때만 진입 이벤트를 1회 발화",
                "진입 민감도는 minConfidence, entryRatio, cooldownSec 조합으로 조정",
            ],
        ),
        (
            "알고리즘 2. 헬멧 미착용",
            "diagram-03.png",
            [
                "입력: person, head, helmet detections",
                "핵심 상태: missing_since, missing_active",
                "person bbox 안에 head가 있고 helmet이 없으면 기본 미착용 후보",
                "head가 전혀 없어도 person은 있고 helmet도 없으면 fallback으로 미착용 처리",
                "holdSec 이상 연속 유지될 때 이벤트를 발화하며, 구현은 ROI 제한이 아닌 전 프레임 기준",
            ],
        ),
        (
            "알고리즘 3. 신호수 없는 차량 이탈",
            "diagram-04.png",
            [
                "입력: vehicle, person detections",
                "핵심 상태: vehicle_seen_since, vehicle_qualified, prev_vehicle_inside, exit_since",
                "차량이 minVehicleSeenSec 이상 ROI 안에서 보인 뒤에만 qualified 상태가 됨",
                "직전엔 차량이 있었고 현재는 없으며 동시에 사람도 없는 상태가 exitHoldSec 이상 유지돼야 발화",
                "단순 출차가 아니라 신호수 부재 상태의 차량 이탈을 잡는 규칙",
            ],
        ),
        (
            "알고리즘 4. 불법 주정차 정지",
            "diagram-05.png",
            [
                "입력: vehicle detections",
                "핵심 상태: tracked_center, prev_center, stationary_since, last_seen_at",
                "ROI overlap과 중심점 거리로 같은 차량을 계속 추적",
                "중심점 이동량이 stopMotionThreshold 미만이면 정지로 보고 stationary_since를 유지",
                "짧은 누락은 missGraceSec으로 흡수하고, dwellSec 이상 정지 시 주정차 이벤트 발화",
            ],
        ),
    ]

    for idx, (title, image_name, bullets) in enumerate(sections, start=1):
        story.extend(
            [
                PageBreak(),
                Paragraph(title, s["h1"]),
                *[bullet(line, s["body"]) for line in bullets],
                Spacer(1, 4 * mm),
                fit_image(DIAGRAM_DIR / image_name, body_w, diagram_h),
            ]
        )

    story.extend(
        [
            PageBreak(),
            Paragraph("운영 메모", s["h1"]),
            bullet("탐지가 있었다는 사실만으로 이벤트가 생기지 않는다. 각 규칙의 상태 변수와 시간 조건이 만족돼야 한다.", s["body"]),
            bullet("헬멧 미착용 규칙은 문서상 ROI 이름을 가지지만 실제 판정은 전 프레임 기준이라는 점을 운영자에게 별도로 설명해야 한다.", s["body"]),
            bullet("차량 관련 규칙 두 개는 모두 시간 축이 중요하다. minVehicleSeenSec, exitHoldSec, dwellSec 조정이 민감도에 직접 영향한다.", s["body"]),
            bullet("주정차 규칙은 같은 차량 추적이 핵심이므로 카메라 흔들림이 큰 환경에서는 stopMotionThreshold, trackMaxCenterDist를 함께 조정해야 한다.", s["body"]),
            Spacer(1, 6 * mm),
            Paragraph("생성 정보", s["h2"]),
            Paragraph("생성일: 2026-03-18", s["small"]),
            Paragraph("산출물: Markdown 원본 + Mermaid PNG + PDF", s["small"]),
        ]
    )
    return story


def add_page_number(canvas, doc):
    canvas.setFont("HYGothic-Medium", 8)
    canvas.setFillColor(colors.HexColor("#64748B"))
    canvas.drawRightString(A4[0] - 16 * mm, 10 * mm, f"{doc.page}")


def main() -> None:
    register_fonts()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(OUTPUT),
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=16 * mm,
        bottomMargin=14 * mm,
        title="HANJIN CCTV 이벤트 판정 알고리즘",
        author="Codex",
    )
    doc.build(build_story(), onFirstPage=add_page_number, onLaterPages=add_page_number)
    print(OUTPUT)


if __name__ == "__main__":
    main()

from dataclasses import dataclass, field, asdict
from typing import Optional
import json
import csv
import os
from datetime import datetime


@dataclass
class VideoResult:
    platform: str
    keyword: str
    video_url: str
    title: str = ""
    hook: str = ""
    content: str = ""
    caption: str = ""
    description: str = ""
    author: str = ""
    author_url: str = ""
    views: Optional[int] = None
    likes: Optional[int] = None
    comments: Optional[int] = None
    shares: Optional[int] = None
    saves: Optional[int] = None
    duration: Optional[int] = None
    upload_date: str = ""
    thumbnail: str = ""
    music: str = ""
    transcript: str = ""
    transcript_source: str = ""
    hashtags: list = field(default_factory=list)
    # Insight fields (derived by enrich_result_text)
    hook_type: str = ""
    hook_score: str = ""
    cta_type: str = ""
    angle: str = ""
    content_idea: str = ""
    # Commerce / affiliate product fields
    products: list = field(default_factory=list)  # list of TikTokProduct dicts
    has_affiliate: bool = False
    commerce_signals: list = field(default_factory=list)  # text signals detected

    def to_dict(self):
        return asdict(self)


def save_results(results: list[VideoResult], keyword: str, output_dir: str = "output", watermark: bool = False):
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_keyword = keyword.replace(" ", "_").replace("/", "_")[:50]

    data_dicts = [r.to_dict() for r in results]

    # Add watermark branding for free-tier exports
    if watermark:
        for d in data_dicts:
            d["powered_by"] = "Sinyal — sinyal.id"

    # Save JSON
    json_path = os.path.join(output_dir, f"{safe_keyword}_{timestamp}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data_dicts, f, indent=2, ensure_ascii=False)

    # Save CSV
    csv_path = os.path.join(output_dir, f"{safe_keyword}_{timestamp}.csv")
    if data_dicts:
        fieldnames = list(data_dicts[0].keys())
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for d in data_dicts:
                d["hashtags"] = ", ".join(d["hashtags"]) if isinstance(d["hashtags"], list) else d.get("hashtags", "")
                writer.writerow(d)
            if watermark:
                writer.writerow({fieldnames[0]: "Data by Sinyal — sinyal.id | Upgrade: sinyal.id/payment"})

    # Save PDF
    pdf_path = os.path.join(output_dir, f"{safe_keyword}_{timestamp}.pdf")
    try:
        _generate_pdf(data_dicts, pdf_path, keyword, watermark)
    except Exception as e:
        print(f"[PDF] Generation failed: {e}")
        pdf_path = None

    return json_path, csv_path, pdf_path


def _generate_pdf(data_dicts: list[dict], pdf_path: str, keyword: str, watermark: bool = False):
    """Generate a proper PDF report from search results using reportlab."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib.colors import HexColor
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_CENTER

    doc = SimpleDocTemplate(pdf_path, pagesize=A4, topMargin=20*mm, bottomMargin=20*mm, leftMargin=15*mm, rightMargin=15*mm)
    styles = getSampleStyleSheet()
    accent = HexColor("#ef5a29")
    soft = HexColor("#705b4c")

    title_style = ParagraphStyle("SinyalTitle", parent=styles["Title"], fontName="Helvetica-Bold", fontSize=22, textColor=accent, spaceAfter=4)
    sub_style = ParagraphStyle("SinyalSub", parent=styles["Normal"], fontSize=10, textColor=soft, spaceAfter=12)
    h3_style = ParagraphStyle("SinyalH3", parent=styles["Heading3"], fontName="Helvetica-Bold", fontSize=13, textColor=HexColor("#20160f"), spaceBefore=10, spaceAfter=4)
    body_style = ParagraphStyle("SinyalBody", parent=styles["Normal"], fontSize=9, leading=13, textColor=HexColor("#20160f"))
    meta_style = ParagraphStyle("SinyalMeta", parent=styles["Normal"], fontSize=8, textColor=soft, leading=11)
    tag_style = ParagraphStyle("SinyalTag", parent=styles["Normal"], fontSize=8, textColor=accent)

    elements = []
    elements.append(Paragraph("SINYAL \u2014 Content Intelligence Report", title_style))
    date_str = datetime.now().strftime("%d %b %Y %H:%M")
    elements.append(Paragraph("Keyword: <b>" + keyword + "</b> &nbsp;|&nbsp; " + str(len(data_dicts)) + " hasil &nbsp;|&nbsp; " + date_str, sub_style))
    elements.append(HRFlowable(width="100%", thickness=1, color=HexColor("#e0d5c8")))
    elements.append(Spacer(1, 6))

    def safe(text, maxlen=500):
        t = str(text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        if len(t) > maxlen:
            t = t[:maxlen] + "..."
        return t

    for i, item in enumerate(data_dicts, 1):
        platform = safe(item.get("platform", "-"))
        author = safe(item.get("author", "-"))
        hook = safe(item.get("hook", "") or item.get("title", "-"), 120)
        url = safe(item.get("video_url", "-"), 200)

        elements.append(Paragraph("#" + str(i) + " &nbsp; <font color='#ef5a29'>[" + platform.upper() + "]</font> &nbsp; " + hook, h3_style))
        elements.append(Paragraph("@" + author + " &nbsp;\u2022&nbsp; " + url, meta_style))

        views = item.get("views")
        likes = item.get("likes")
        comments_count = item.get("comments")
        shares = item.get("shares")
        stats_parts = []
        if views is not None:
            stats_parts.append("Views: " + format(views, ","))
        if likes is not None:
            stats_parts.append("Likes: " + format(likes, ","))
        if comments_count is not None:
            stats_parts.append("Comments: " + format(comments_count, ","))
        if shares is not None:
            stats_parts.append("Shares: " + format(shares, ","))
        if stats_parts:
            elements.append(Paragraph(" &nbsp;|&nbsp; ".join(stats_parts), meta_style))

        caption = item.get("caption", "")
        if caption:
            elements.append(Paragraph("<b>Caption:</b> " + safe(caption, 400), body_style))

        transcript = item.get("transcript", "")
        if transcript:
            elements.append(Paragraph("<b>Transcript:</b> " + safe(transcript, 600), body_style))

        hashtags = item.get("hashtags", [])
        if hashtags:
            if isinstance(hashtags, list):
                tags_str = " ".join("#" + safe(h, 30) for h in hashtags[:10])
            else:
                tags_str = safe(str(hashtags), 200)
            elements.append(Paragraph(tags_str, tag_style))

        elements.append(Spacer(1, 4))
        elements.append(HRFlowable(width="100%", thickness=0.5, color=HexColor("#f0e8dd")))

    if watermark:
        elements.append(Spacer(1, 12))
        wm_style = ParagraphStyle("wm", parent=styles["Normal"], fontSize=8, textColor=soft, alignment=TA_CENTER)
        elements.append(Paragraph("Powered by Sinyal \u2014 sinyal.id | Upgrade: sinyal.id/payment", wm_style))

    doc.build(elements)


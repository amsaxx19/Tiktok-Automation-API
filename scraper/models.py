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

    return json_path, csv_path

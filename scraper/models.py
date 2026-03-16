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
    hashtags: list = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


def save_results(results: list[VideoResult], keyword: str, output_dir: str = "output"):
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_keyword = keyword.replace(" ", "_").replace("/", "_")[:50]

    # Save JSON
    json_path = os.path.join(output_dir, f"{safe_keyword}_{timestamp}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump([r.to_dict() for r in results], f, indent=2, ensure_ascii=False)

    # Save CSV
    csv_path = os.path.join(output_dir, f"{safe_keyword}_{timestamp}.csv")
    if results:
        fieldnames = list(results[0].to_dict().keys())
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for r in results:
                d = r.to_dict()
                d["hashtags"] = ", ".join(d["hashtags"])
                writer.writerow(d)

    return json_path, csv_path

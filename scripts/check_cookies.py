"""Check what cookies are saved and what's missing."""
import json
from pathlib import Path

session_file = Path.home() / ".kalodata_session.json"
if session_file.exists():
    data = json.loads(session_file.read_text())
    print("Saved cookies:")
    for k, v in data.items():
        display_v = v[:50] + "..." if len(v) > 50 else v
        print(f"  {k}: {display_v}")
else:
    print("No session file found")

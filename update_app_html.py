import re
from pathlib import Path

app_html_content = Path("app_v2.html").read_text(encoding="utf-8")
server_content = Path("server.py").read_text(encoding="utf-8")

# Find where APP_HTML starts and ends
# It's defined as APP_HTML = """<!DOCTYPE html> ... """
pattern = re.compile(r'APP_HTML = """<!DOCTYPE html>.*?</html>\s*"""', re.DOTALL)

new_app_html = f'APP_HTML = """{app_html_content}"""'

new_server_content = pattern.sub(new_app_html, server_content)

if new_server_content != server_content:
    Path("server.py").write_text(new_server_content, encoding="utf-8")
    print("Successfully updated APP_HTML in server.py")
else:
    print("No changes made or regex failed.")

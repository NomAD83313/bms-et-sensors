from pathlib import Path


TEMPLATE_PATH = Path(__file__).with_name("templates") / "index.html"
INDEX_HTML = TEMPLATE_PATH.read_text(encoding="utf-8")

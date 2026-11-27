"""Latest-answer citations, separate from subtitles/TTS. Never open a browser."""
from html import escape
import json

from subtitles import write_subtitle
from web_search import safe_url


def write_sources(root, payload=None):
    payload = payload or {"en": "", "sources": []}
    language = "ru" if "ru" in payload else "en"
    translation = payload[language]
    # Validate again at the rendering boundary; never make javascript/file links.
    sources = [source for source in payload["sources"] if safe_url(source.get("url"))]
    cited = [source for source in sources if source.get("kind") == "cited"]
    consulted = [source for source in sources if source.get("kind") != "cited"]

    def anchor(source, label):
        return (f'<a href="{escape(source["url"], quote=True)}" target="_blank" '
                f'rel="noopener noreferrer">{escape(label)}</a>')

    # Citation anchors are directly attached to the selected translation.
    references = " ".join(anchor(source, f"[{number}]") for number, source in enumerate(cited, 1))
    if not cited and consulted:
        references = "Retrieved: " + " ".join(anchor(source, f"[{number}]")
                                               for number, source in enumerate(consulted, 1))
    details = "".join(f"<li>{anchor(source, source['title'])}</li>" for source in cited)
    retrieved = "".join(f"<li>{anchor(source, source['title'])}</li>" for source in consulted)
    html = f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'">
<title>Akane — latest lookup sources</title>
<style>body{{font:18px/1.6 system-ui;max-width:900px;margin:3rem auto;padding:0 1rem;background:#141824;color:#eef2ff}}
a{{color:#9bd4ff;text-decoration:underline}} p{{white-space:pre-wrap}} h1{{font-size:1.4rem}}</style></head>
<body><h1>Akane — latest lookup</h1>
<p lang="{language}">{escape(translation)} {references}</p>
{'<h2>Cited sources</h2><ol>' + details + '</ol>' if details else ''}
{'<h2>Retrieved pages (not individually cited)</h2><ul>' + retrieved + '</ul>' if retrieved else ''}
{'<p>No sources for the current turn.</p>' if not sources else ''}
</body></html>'''
    write_subtitle(root / "runtime" / "sources.html", html)
    write_subtitle(root / "runtime" / "sources.json",
                   json.dumps({language: translation, "sources": sources}, ensure_ascii=False, indent=2))

#!/usr/bin/env python3
"""Local mock of the GitHub profile page (not published).

Renders README.md through GitHub's own Markdown API, so sanitizing and markup match
github.com, then wraps it in a profile-page shell with light/dark and desktop/mobile toggles.

    python3 scripts/preview.py && open preview.html
    python3 scripts/preview.py concepts/vinyl/README.md      # writes concepts/vinyl/preview.html
"""
import os
import json
import re
import subprocess
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".preview"
LOGIN = "oshada101"
CSS_VERSIONS = ("5.8.1", "5.6.1", "5.5.1")


def gh(args, payload=None):
    cmd = ["gh", "api", *args] + (["--input", "-"] if payload is not None else [])
    return subprocess.run(cmd, input=json.dumps(payload) if payload is not None else None,
                          capture_output=True, text=True, check=True).stdout


def markdown_css():
    CACHE.mkdir(exist_ok=True)
    for scheme in ("light", "dark"):
        path = CACHE / f"github-markdown-{scheme}.css"
        if path.exists():
            continue
        for v in CSS_VERSIONS:
            url = f"https://cdn.jsdelivr.net/npm/github-markdown-css@{v}/github-markdown-{scheme}.css"
            try:
                path.write_bytes(urllib.request.urlopen(url, timeout=20).read())
                break
            except Exception:
                continue


def main():
    import sys
    markdown_css()
    readme = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT / "README.md"
    out = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else readme.parent / "preview.html"
    md = readme.read_text()
    body = gh(["markdown"], {"text": md, "mode": "gfm", "context": f"{LOGIN}/{LOGIN}"})
    # repo-relative links may come back absolute; point them at the local files
    body = re.sub(rf"https://github\.com/{LOGIN}/{LOGIN}/(?:raw|blob)/[^/\"]+/", "", body)
    # let the toggle drive <picture> instead of the OS color scheme
    body = body.replace('media="(prefers-color-scheme: dark)"', 'data-dark media="(prefers-color-scheme: dark)"')
    user = json.loads(gh([f"users/{LOGIN}"]))
    page = TEMPLATE.replace("{{BODY}}", body).replace("{{AVATAR}}", user["avatar_url"]) \
                   .replace("{{NAME}}", user.get("name") or LOGIN).replace("{{LOGIN}}", LOGIN) \
                   .replace("{{FOLLOWERS}}", str(user.get("followers", 0))) \
                   .replace(".preview/", os.path.relpath(CACHE, out.parent) + "/")
    out.write_text(page)
    print(f"wrote {out}")


TEMPLATE = """<!doctype html>
<html lang="en" data-theme="light" data-width="desktop">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{LOGIN}} · profile preview</title>
<link id="md-light" rel="stylesheet" href=".preview/github-markdown-light.css">
<link id="md-dark" rel="stylesheet" href=".preview/github-markdown-dark.css" disabled>
<style>
:root{--bg:#fff;--fg:#1f2328;--muted:#59636e;--border:#d1d9e0;--btn:#f6f8fa;--accent:#0969da}
html[data-theme=dark]{--bg:#0d1117;--fg:#f0f6fc;--muted:#9198a1;--border:#3d444d;--btn:#151b23;--accent:#4493f8}
html{color-scheme:light}html[data-theme=dark]{color-scheme:dark}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans",Helvetica,Arial,sans-serif}
header{height:64px;display:flex;align-items:center;gap:12px;padding:0 32px;border-bottom:1px solid var(--border);background:var(--btn)}
header svg{fill:var(--fg)} header b{font-weight:600}
.tabs{display:flex;gap:8px;padding:0 32px;border-bottom:1px solid var(--border)}
.tabs span{padding:10px 8px;color:var(--muted)} .tabs .on{color:var(--fg);font-weight:600;box-shadow:inset 0 -2px #fd8c73}
.wrap{max-width:1280px;margin:0 auto;padding:24px 32px;display:flex;gap:24px;box-sizing:border-box}
aside{width:296px;flex:none}
aside img{width:296px;height:296px;border-radius:50%;border:1px solid var(--border);display:block}
.name{font-size:24px;font-weight:600;line-height:1.25;margin-top:16px}
.login{font-size:20px;color:var(--muted);font-weight:300;line-height:24px}
.btn{display:block;text-align:center;margin:16px 0;padding:5px 16px;border:1px solid var(--border);border-radius:6px;background:var(--btn);font-weight:500}
.meta{color:var(--muted)} .meta b{color:var(--fg)}
main{flex:1;min-width:0}
.readme{border:1px solid var(--border);border-radius:6px;padding:24px}
.readme-head{font:12px ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--muted);margin-bottom:16px}
.markdown-body{background:transparent!important;font-size:16px}
html[data-width=mobile] .wrap{max-width:430px;flex-direction:column;padding:16px}
html[data-width=mobile] aside{width:auto;display:flex;gap:16px;align-items:center}
html[data-width=mobile] aside img{width:72px;height:72px}
html[data-width=mobile] .btn,html[data-width=mobile] .meta{display:none}
html[data-width=mobile] .readme{padding:16px}
.toolbar{position:fixed;right:16px;bottom:16px;display:flex;gap:4px;padding:6px;background:var(--btn);border:1px solid var(--border);border-radius:10px;box-shadow:0 8px 24px rgba(0,0,0,.18);z-index:9}
.toolbar button{font:12px/1 -apple-system,sans-serif;color:var(--fg);background:transparent;border:1px solid transparent;border-radius:6px;padding:7px 10px;cursor:pointer}
.toolbar button[aria-pressed=true]{border-color:var(--border);background:var(--bg);font-weight:600}
.toolbar button:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
.toolbar i{width:1px;background:var(--border);margin:2px 4px}
</style>
</head>
<body>
<header>
  <svg width="32" height="32" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 1C5.9 1 1 5.9 1 12c0 4.9 3.2 9 7.5 10.5.5.1.8-.2.8-.5v-1.9c-3.1.7-3.7-1.3-3.7-1.3-.5-1.3-1.2-1.6-1.2-1.6-1-.7.1-.7.1-.7 1.1.1 1.7 1.1 1.7 1.1 1 1.7 2.6 1.2 3.2.9.1-.7.4-1.2.7-1.5-2.4-.3-5-1.2-5-5.4 0-1.2.4-2.2 1.1-2.9-.1-.3-.5-1.4.1-2.9 0 0 .9-.3 3 1.1.9-.2 1.8-.4 2.8-.4s1.9.1 2.8.4c2.1-1.4 3-1.1 3-1.1.6 1.5.2 2.6.1 2.9.7.8 1.1 1.7 1.1 2.9 0 4.2-2.6 5.1-5 5.4.4.3.8 1 .8 2v3c0 .3.2.6.8.5C19.8 21 23 16.9 23 12 23 5.9 18.1 1 12 1z"/></svg>
  <b>{{LOGIN}}</b>
</header>
<nav class="tabs"><span class="on">Overview</span><span>Repositories</span><span>Projects</span><span>Packages</span><span>Stars</span></nav>
<div class="wrap">
  <aside>
    <img src="{{AVATAR}}" alt="">
    <div><div class="name">{{NAME}}</div><div class="login">{{LOGIN}}</div></div>
    <span class="btn">Edit profile</span>
    <div class="meta"><b>{{FOLLOWERS}}</b> followers</div>
  </aside>
  <main>
    <div class="readme">
      <div class="readme-head">{{LOGIN}} / README.md</div>
      <article class="markdown-body">{{BODY}}</article>
    </div>
  </main>
</div>
<div class="toolbar" role="toolbar" aria-label="Preview options">
  <button data-theme="light">Light</button><button data-theme="dark">Dark</button><i></i>
  <button data-width="desktop">Desktop</button><button data-width="mobile">Mobile</button>
</div>
<script>
const root = document.documentElement;
function apply(theme, width) {
  const dark = theme === 'dark';
  root.dataset.theme = theme; root.dataset.width = width;
  document.getElementById('md-light').disabled = dark;
  document.getElementById('md-dark').disabled = !dark;
  document.querySelectorAll('source[data-dark]').forEach(s => { s.media = dark ? 'all' : 'not all'; });
  document.querySelectorAll('.toolbar button').forEach(b => {
    b.setAttribute('aria-pressed', b.dataset.theme === theme || b.dataset.width === width);
  });
  try { localStorage.setItem('osh-preview', JSON.stringify({theme, width})); } catch (e) {}
}
let saved = {};
try { saved = JSON.parse(localStorage.getItem('osh-preview') || '{}'); } catch (e) {}
const h = location.hash;
let theme = h.includes('dark') ? 'dark' : h.includes('light') ? 'light'
  : saved.theme || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
let width = h.includes('mobile') ? 'mobile' : h.includes('desktop') ? 'desktop' : saved.width || 'desktop';
apply(theme, width);
document.querySelector('.toolbar').addEventListener('click', e => {
  const b = e.target.closest('button'); if (!b) return;
  if (b.dataset.theme) theme = b.dataset.theme;
  if (b.dataset.width) width = b.dataset.width;
  apply(theme, width);
});
// GitHub prefixes heading ids with "user-content-"; mimic its anchor handling
document.addEventListener('click', e => {
  const a = e.target.closest('a[href^="#"]'); if (!a || a.closest('.toolbar')) return;
  const el = document.getElementById('user-content-' + decodeURIComponent(a.hash.slice(1)));
  if (el) { e.preventDefault(); el.scrollIntoView({behavior: 'smooth'}); }
});
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()

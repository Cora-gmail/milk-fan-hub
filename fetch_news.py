#!/usr/bin/env python3
"""
m!lk Fan Hub - News Aggregator
Fetches from sd-milk.com / Natalie Music / Oricon,
translates to Traditional Chinese via DeepL Free API,
and generates a static index.html for GitHub Pages.
"""
import os, re, time
import feedparser
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta

try:
    import deepl
    _key = os.environ.get('DEEPL_API_KEY', '')
    translator = deepl.Translator(_key) if _key else None
except Exception:
    translator = None

JST = timezone(timedelta(hours=9))

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml',
    'Accept-Language': 'ja-JP,ja;q=0.9',
}


def translate(text):
    if not translator or not text or not text.strip():
        return ''
    try:
        try:
            r = translator.translate_text(text.strip(), source_lang='JA', target_lang='ZH-HANT')
        except Exception:
            r = translator.translate_text(text.strip(), source_lang='JA', target_lang='ZH')
        time.sleep(0.4)
        return r.text
    except Exception as e:
        print(f'  Translation error: {e}')
        return ''


def clean(html_or_text):
    return re.sub(r'\s+', ' ', BeautifulSoup(str(html_or_text), 'html.parser').get_text()).strip()


# ── Sources ────────────────────────────────────────────────────────────────────

def fetch_sd_milk():
    print('Fetching sd-milk.com...')
    arts = []
    for path in ['/', '/news/', '/information/']:
        try:
            r = requests.get(f'https://sd-milk.com{path}', headers=HEADERS, timeout=12)
            r.encoding = 'utf-8'
            soup = BeautifulSoup(r.text, 'html.parser')

            for sel in [
                '.news-list li', '.info-list li', '.news li', '.information li',
                '.post-list li', 'ul.list li', '.topics-list li', '.entry-list li',
                'article', '.news-item', '.post',
            ]:
                items = soup.select(sel)
                if len(items) < 2:
                    continue
                for it in items[:8]:
                    a = it.find('a', href=True)
                    if not a:
                        continue
                    title = clean(a.get_text())
                    if len(title) < 4:
                        title = clean(it.get_text())[:120]
                    href = a['href']
                    if not href.startswith('http'):
                        href = 'https://sd-milk.com' + href
                    date_el = it.select_one('time, .date, .day, span.time')
                    date = clean(date_el.get_text()) if date_el else ''
                    if len(title) > 3:
                        arts.append({
                            'source': 'sd-milk.com (公式)',
                            'title_ja': title, 'summary_ja': '',
                            'url': href, 'date': date,
                        })
                if arts:
                    break
        except Exception as e:
            print(f'  Error on {path}: {e}')
        if arts:
            break

    arts = arts[:5]
    print(f'  → {len(arts)} articles')
    return arts


def fetch_natalie():
    print('Fetching Natalie Music...')
    arts = []
    try:
        feed = feedparser.parse('https://natalie.mu/music/feed/news')
        for e in feed.entries:
            title   = e.get('title', '')
            summary = clean(e.get('summary', ''))
            if 'm!lk' not in (title + summary).lower():
                continue
            arts.append({
                'source': 'Natalie Music',
                'title_ja': title,
                'summary_ja': summary[:280],
                'url': e.get('link', ''),
                'date': e.get('published', ''),
            })
            if len(arts) >= 5:
                break
    except Exception as e:
        print(f'  Error: {e}')
    print(f'  → {len(arts)} articles')
    return arts


def fetch_oricon():
    print('Fetching Oricon...')
    arts = []
    try:
        url = 'https://www.oricon.co.jp/search/?q=m%21lk&cat=news'
        r = requests.get(url, headers=HEADERS, timeout=12)
        soup = BeautifulSoup(r.text, 'html.parser')

        for sel in [
            '.news-list li', '.list-news li', 'ul.list li',
            '.search-results article', 'article', '.item',
        ]:
            items = soup.select(sel)
            if len(items) < 2:
                continue
            for it in items[:6]:
                a = it.find('a', href=True)
                if not a:
                    continue
                title = clean(a.get_text())
                href  = a['href']
                if not href.startswith('http'):
                    href = 'https://www.oricon.co.jp' + href
                date_el = it.select_one('time, .date, .time, [datetime]')
                date = ''
                if date_el:
                    date = date_el.get('datetime', '') or clean(date_el.get_text())
                combined = title.lower()
                if len(title) > 3 and ('m!lk' in combined or 'ミルク' in title):
                    arts.append({
                        'source': 'Oricon',
                        'title_ja': title, 'summary_ja': '',
                        'url': href, 'date': date,
                    })
            if arts:
                break
    except Exception as e:
        print(f'  Error: {e}')
    print(f'  → {len(arts)} articles')
    return arts


# ── HTML generation ────────────────────────────────────────────────────────────

SOURCE_COLORS = {
    'sd-milk.com (公式)': '#e91e7a',
    'Natalie Music':      '#0090d4',
    'Oricon':             '#cc2233',
}


def fmt_date(raw):
    if not raw:
        return ''
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(raw).strftime('%Y.%m.%d')
    except Exception:
        return str(raw)[:10]


def generate_html(articles, updated_at):
    if not articles:
        cards_html = '<p class="empty">暫無新聞 / ニュースなし</p>'
    else:
        parts = []
        for a in articles:
            color    = SOURCE_COLORS.get(a['source'], '#888')
            date     = fmt_date(a.get('date', ''))
            title_zh = a.get('title_zh', '')
            sum_ja   = a.get('summary_ja', '')
            sum_zh   = a.get('summary_zh', '')

            sum_block = ''
            if sum_ja:
                sum_block = f'<p class="sum-ja">{sum_ja}</p>'
                if sum_zh:
                    sum_block += f'<p class="sum-zh">{sum_zh}</p>'

            parts.append(f'''<article class="card">
  <header class="card-head">
    <span class="badge" style="--c:{color}">{a["source"]}</span>
    {"<time class='date'>" + date + "</time>" if date else ""}
  </header>
  {"<p class='title-zh'>" + title_zh + "</p>" if title_zh else ""}
  <p class="title-ja">{a["title_ja"]}</p>
  {sum_block}
  <a class="btn" href="{a["url"]}" target="_blank" rel="noopener noreferrer">原文を読む →</a>
</article>''')
        cards_html = '\n'.join(parts)

    return f'''<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>m!lk Fan Hub</title>
<style>
:root {{
  --bg:      #120818;
  --surface: #1e0d28;
  --card:    #27103a;
  --border:  #3d1a54;
  --rose:    #ff4d8d;
  --rose2:   #ff80b0;
  --text:    #f2e0ec;
  --muted:   #b08aa8;
}}
*,*::before,*::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  background: var(--bg);
  color: var(--text);
  font-family: 'Hiragino Sans', 'Noto Sans TC', 'Noto Sans JP', system-ui, sans-serif;
  min-height: 100vh;
}}
.site-header {{
  text-align: center;
  padding: 56px 20px 40px;
  background: radial-gradient(ellipse at 50% 0%, #3d1060 0%, transparent 70%);
  position: relative;
  overflow: hidden;
}}
.site-header::before {{
  content: '';
  position: absolute; inset: 0;
  background: url("data:image/svg+xml,%3Csvg width='40' height='40' viewBox='0 0 40 40' xmlns='http://www.w3.org/2000/svg'%3E%3Cg fill='%23ff4d8d' fill-opacity='0.06'%3E%3Ccircle cx='20' cy='20' r='2'/%3E%3C/g%3E%3C/svg%3E");
  pointer-events: none;
}}
.wordmark {{
  font-size: clamp(3rem, 10vw, 6rem);
  font-weight: 900;
  letter-spacing: 0.25em;
  color: #fff;
  text-shadow: 0 0 40px rgba(255,77,141,.6), 0 0 80px rgba(255,77,141,.2);
  position: relative;
}}
.wordmark .bang {{ color: var(--rose); }}
.tagline {{
  margin-top: 10px;
  font-size: .78rem;
  letter-spacing: .5em;
  text-transform: uppercase;
  color: var(--rose2);
}}
.hearts {{
  margin: 14px 0 6px;
  font-size: 1rem;
  color: var(--rose);
  letter-spacing: .4em;
  opacity: .7;
}}
.update-time {{
  font-size: .72rem;
  color: var(--muted);
  margin-top: 12px;
}}
.sources {{
  display: flex;
  justify-content: center;
  gap: 10px;
  flex-wrap: wrap;
  padding: 20px 20px 0;
}}
.sources span {{
  padding: 4px 14px;
  border-radius: 20px;
  font-size: .72rem;
  font-weight: 700;
  letter-spacing: .04em;
  color: #fff;
}}
main {{
  max-width: 1040px;
  margin: 0 auto;
  padding: 32px 16px 80px;
}}
.section-label {{
  font-size: .72rem;
  letter-spacing: .35em;
  text-transform: uppercase;
  color: var(--rose);
  margin-bottom: 20px;
  padding-left: 12px;
  border-left: 2px solid var(--rose);
}}
.grid {{
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(290px, 1fr));
  gap: 18px;
}}
.card {{
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  transition: transform .2s, box-shadow .2s, border-color .2s;
}}
.card:hover {{
  transform: translateY(-3px);
  box-shadow: 0 12px 32px rgba(255,77,141,.12);
  border-color: rgba(255,77,141,.35);
}}
.card-head {{
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}}
.badge {{
  background: var(--c, #888);
  color: #fff;
  font-size: .7rem;
  font-weight: 700;
  letter-spacing: .05em;
  padding: 3px 10px;
  border-radius: 10px;
}}
.date {{
  font-size: .72rem;
  color: var(--muted);
  font-variant-numeric: tabular-nums;
}}
.title-zh {{
  font-size: 1rem;
  font-weight: 700;
  color: var(--rose2);
  line-height: 1.5;
}}
.title-ja {{
  font-size: .85rem;
  color: #c4a8ba;
  line-height: 1.6;
}}
.sum-ja {{
  font-size: .78rem;
  color: var(--muted);
  line-height: 1.65;
  border-top: 1px solid var(--border);
  padding-top: 8px;
}}
.sum-zh {{
  font-size: .82rem;
  color: #e0b8cc;
  line-height: 1.65;
  margin-top: 6px;
}}
.btn {{
  display: inline-block;
  margin-top: auto;
  padding: 7px 16px;
  background: transparent;
  border: 1px solid var(--rose);
  color: var(--rose);
  text-decoration: none;
  border-radius: 20px;
  font-size: .78rem;
  font-weight: 600;
  align-self: flex-start;
  transition: background .2s, color .2s;
}}
.btn:hover {{ background: var(--rose); color: #fff; }}
.empty {{
  text-align: center;
  padding: 60px;
  color: var(--muted);
  grid-column: 1/-1;
}}
footer {{
  text-align: center;
  padding: 24px;
  font-size: .72rem;
  color: var(--muted);
  border-top: 1px solid var(--border);
}}
@media (max-width: 480px) {{
  .wordmark {{ letter-spacing: .15em; }}
  .grid {{ grid-template-columns: 1fr; }}
}}
</style>
</head>
<body>

<header class="site-header">
  <div class="wordmark">m<span class="bang">!</span>lk</div>
  <div class="hearts">♡ · ♡ · ♡</div>
  <p class="tagline">Fan Hub &nbsp;·&nbsp; 新聞聚合</p>
  <p class="update-time">最後更新 / 最終更新：{updated_at} JST</p>
</header>

<div class="sources">
  <span style="background:#e91e7a">sd-milk.com 公式</span>
  <span style="background:#0090d4">Natalie Music</span>
  <span style="background:#cc2233">Oricon</span>
</div>

<main>
  <p class="section-label">最新ニュース ／ 最新消息</p>
  <div class="grid">
    {cards_html}
  </div>
</main>

<footer>
  m!lk Fan Hub &nbsp;·&nbsp; 非公式ファンサイト / 非官方粉絲網站 &nbsp;·&nbsp; 毎日 09:00 JST 自動更新
</footer>

</body>
</html>'''


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    print('=== m!lk Fan Hub ===')
    arts = []
    arts += fetch_sd_milk()
    arts += fetch_natalie()
    arts += fetch_oricon()

    if translator:
        print(f'Translating {len(arts)} articles...')
        for a in arts:
            a['title_zh']   = translate(a['title_ja'])
            a['summary_zh'] = translate(a['summary_ja']) if a.get('summary_ja') else ''
    else:
        print('WARNING: DeepL not configured, skipping translation')
        for a in arts:
            a['title_zh'] = a['summary_zh'] = ''

    now_jst = datetime.now(JST).strftime('%Y-%m-%d %H:%M')
    html = generate_html(arts, now_jst)

    with open('index.html', 'w', encoding='utf-8') as f:
        f.write(html)

    print(f'Done! {len(arts)} articles → index.html')


if __name__ == '__main__':
    main()

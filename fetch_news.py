#!/usr/bin/env python3
"""
m!lk Fan Hub v2 — Accumulative news with calendar, images, and bilingual display.
Stores all articles in articles.json; generates index.html from it.
"""
import os, re, time, json, hashlib
import feedparser
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

# ── DeepL ─────────────────────────────────────────────────────────────────────
try:
    import deepl
    _key = os.environ.get('DEEPL_API_KEY', '')
    translator = deepl.Translator(_key) if _key else None
except Exception:
    translator = None

JST           = timezone(timedelta(hours=9))
ARTICLES_FILE = 'articles.json'
MAX_ARTICLES  = 1000

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml',
    'Accept-Language': 'ja-JP,ja;q=0.9',
}

# ── Helpers ───────────────────────────────────────────────────────────────────

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

def make_id(url):
    return hashlib.md5(url.encode()).hexdigest()[:12]

def normalize_date(raw):
    if not raw:
        return datetime.now(JST).strftime('%Y-%m-%d')
    raw = str(raw).strip()
    if re.match(r'^\d{4}-\d{2}-\d{2}$', raw):
        return raw
    try:
        return parsedate_to_datetime(raw).strftime('%Y-%m-%d')
    except Exception:
        pass
    m = re.search(r'(\d{4})[^\d](\d{1,2})[^\d](\d{1,2})', raw)
    if m:
        return f"{m.group(1)}-{m.group(2).zfill(2)}-{m.group(3).zfill(2)}"
    return datetime.now(JST).strftime('%Y-%m-%d')

def fetch_og_image(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=8)
        soup = BeautifulSoup(r.text, 'html.parser')
        for attrs in [
            {'property': 'og:image'},
            {'name': 'og:image'},
            {'property': 'twitter:image'},
            {'name': 'twitter:image'},
        ]:
            el = soup.find('meta', attrs=attrs)
            if el and el.get('content'):
                img = el['content'].strip()
                if img.startswith('http'):
                    return img
    except Exception:
        pass
    return ''

# ── Persistence ───────────────────────────────────────────────────────────────

def load_articles():
    if os.path.exists(ARTICLES_FILE):
        try:
            with open(ARTICLES_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return []

def save_articles(articles):
    with open(ARTICLES_FILE, 'w', encoding='utf-8') as f:
        json.dump(articles, f, ensure_ascii=False, indent=2)

def merge_articles(existing, new_articles):
    existing_urls = {a['url'] for a in existing}
    added = []
    for a in new_articles:
        if a['url'] and a['url'] not in existing_urls:
            existing_urls.add(a['url'])
            added.append(a)
    merged = existing + added
    merged.sort(key=lambda x: x.get('date', ''), reverse=True)
    return merged[:MAX_ARTICLES], len(added)

# ── Fetchers ──────────────────────────────────────────────────────────────────

def fetch_sd_milk():
    print('Fetching sd-milk.com...')
    arts = []
    for path in ['/', '/news/', '/information/']:
        try:
            r = requests.get(f'https://sd-milk.com{path}', headers=HEADERS, timeout=12)
            r.encoding = 'utf-8'
            soup = BeautifulSoup(r.text, 'html.parser')
            for sel in ['.news-list li', '.info-list li', '.news li', '.information li',
                        '.post-list li', 'ul.list li', '.topics-list li', 'article', '.post']:
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
                    date_raw = clean(date_el.get_text()) if date_el else ''
                    if len(title) > 3:
                        arts.append({
                            'id': make_id(href),
                            'source': 'sd-milk.com (公式)',
                            'title_ja': title, 'title_zh': '',
                            'summary_ja': '', 'summary_zh': '',
                            'url': href, 'image': '',
                            'date': normalize_date(date_raw),
                        })
                if arts:
                    break
        except Exception as e:
            print(f'  Error on {path}: {e}')
        if arts:
            break
    result = arts[:5]
    print(f'  -> {len(result)} articles')
    return result

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
            url = e.get('link', '')
            arts.append({
                'id': make_id(url),
                'source': 'Natalie Music',
                'title_ja': title, 'title_zh': '',
                'summary_ja': summary[:280], 'summary_zh': '',
                'url': url, 'image': '',
                'date': normalize_date(e.get('published', '')),
            })
            if len(arts) >= 5:
                break
    except Exception as e:
        print(f'  Error: {e}')
    print(f'  -> {len(arts)} articles')
    return arts

def fetch_oricon():
    print('Fetching Oricon...')
    arts = []
    try:
        url = 'https://www.oricon.co.jp/search/?q=m%21lk&cat=news'
        r = requests.get(url, headers=HEADERS, timeout=12)
        soup = BeautifulSoup(r.text, 'html.parser')
        for sel in ['.news-list li', '.list-news li', 'ul.list li', 'article', '.item']:
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
                date_raw = date_el.get('datetime', '') or (clean(date_el.get_text()) if date_el else '')
                if len(title) > 3 and ('m!lk' in title.lower() or 'ミルク' in title):
                    arts.append({
                        'id': make_id(href),
                        'source': 'Oricon',
                        'title_ja': title, 'title_zh': '',
                        'summary_ja': '', 'summary_zh': '',
                        'url': href, 'image': '',
                        'date': normalize_date(date_raw),
                    })
            if arts:
                break
    except Exception as e:
        print(f'  Error: {e}')
    print(f'  -> {len(arts)} articles')
    return arts

# ── HTML Template ─────────────────────────────────────────────────────────────

HTML_TEMPLATE = r'''<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>m!lk Fan Hub</title>
<style>
:root{--bg:#120818;--surface:#1e0d28;--card:#27103a;--border:#3d1a54;--rose:#ff4d8d;--rose2:#ff80b0;--text:#f2e0ec;--muted:#b08aa8;}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0;}
html{scroll-behavior:smooth;}
body{background:var(--bg);color:var(--text);font-family:'Hiragino Sans','Noto Sans TC','Noto Sans JP',system-ui,sans-serif;min-height:100vh;}
.site-header{text-align:center;padding:48px 20px 36px;background:radial-gradient(ellipse at 50% 0%,#3d1060 0%,transparent 68%);position:relative;overflow:hidden;}
.site-header::before{content:'';position:absolute;inset:0;background:url("data:image/svg+xml,%3Csvg width='40' height='40' viewBox='0 0 40 40' xmlns='http://www.w3.org/2000/svg'%3E%3Ccircle cx='20' cy='20' r='1.5' fill='%23ff4d8d' fill-opacity='.07'/%3E%3C/svg%3E");pointer-events:none;}
.wordmark{font-size:clamp(3rem,10vw,5.5rem);font-weight:900;letter-spacing:.25em;color:#fff;text-shadow:0 0 40px rgba(255,77,141,.6),0 0 80px rgba(255,77,141,.2);position:relative;}
.wordmark .bang{color:var(--rose);}
.tagline{margin-top:10px;font-size:.78rem;letter-spacing:.5em;text-transform:uppercase;color:var(--rose2);}
.hearts{margin:12px 0 6px;font-size:.95rem;color:var(--rose);letter-spacing:.4em;opacity:.7;}
.update-time{font-size:.72rem;color:var(--muted);margin-top:10px;}
.sources{display:flex;justify-content:center;gap:8px;flex-wrap:wrap;padding:18px 20px 0;}
.sources span{padding:3px 12px;border-radius:20px;font-size:.7rem;font-weight:700;color:#fff;}
.main-wrap{max-width:1100px;margin:0 auto;padding:32px 16px 80px;display:grid;grid-template-columns:280px 1fr;gap:28px;align-items:start;}
@media(max-width:760px){.main-wrap{grid-template-columns:1fr;}}
.calendar-wrap{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:20px;position:sticky;top:20px;}
.cal-nav{display:flex;align-items:center;justify-content:space-between;margin-bottom:14px;}
.cal-title{font-size:.95rem;font-weight:700;color:var(--rose2);}
.cal-arrow{background:none;border:1px solid var(--border);color:var(--muted);width:28px;height:28px;border-radius:6px;cursor:pointer;font-size:.9rem;transition:border-color .2s,color .2s;display:flex;align-items:center;justify-content:center;}
.cal-arrow:hover{border-color:var(--rose);color:var(--rose);}
.cal-dow{display:grid;grid-template-columns:repeat(7,1fr);gap:2px;margin-bottom:4px;}
.cal-dow span{text-align:center;font-size:.65rem;color:var(--muted);padding:4px 0;font-weight:600;}
.cal-grid{display:grid;grid-template-columns:repeat(7,1fr);gap:2px;}
.cal-empty{height:32px;}
.cal-day{height:32px;border-radius:6px;border:none;background:transparent;color:var(--muted);font-size:.78rem;cursor:default;position:relative;transition:background .15s,color .15s;display:flex;align-items:center;justify-content:center;flex-direction:column;gap:1px;}
.cal-day.has-news{color:var(--text);cursor:pointer;}
.cal-day.has-news:hover{background:rgba(255,77,141,.12);}
.cal-day.has-news::after{content:'';display:block;width:4px;height:4px;border-radius:50%;background:var(--rose);opacity:.8;}
.cal-day.selected{background:var(--rose);color:#fff;}
.cal-day.selected::after{background:#fff;}
.cal-day.today{font-weight:700;}
.cal-clear{display:block;width:100%;margin-top:14px;padding:7px;background:rgba(255,77,141,.1);border:1px solid rgba(255,77,141,.25);color:var(--rose2);border-radius:8px;font-size:.78rem;cursor:pointer;transition:background .2s;}
.cal-clear:hover{background:rgba(255,77,141,.2);}
.cal-stats{margin-top:12px;font-size:.7rem;color:var(--muted);text-align:center;line-height:1.6;}
.articles-section{min-width:0;}
.filter-bar{margin-bottom:18px;}
.filter-label{font-size:.75rem;letter-spacing:.2em;text-transform:uppercase;color:var(--rose);padding-left:10px;border-left:2px solid var(--rose);}
.date-group{margin-bottom:32px;}
.date-heading{font-size:.72rem;letter-spacing:.25em;color:var(--rose2);margin-bottom:14px;padding-bottom:6px;border-bottom:1px solid var(--border);}
.articles-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:16px;}
.card{background:var(--card);border:1px solid var(--border);border-radius:14px;overflow:hidden;display:flex;flex-direction:column;transition:transform .2s,box-shadow .2s,border-color .2s;}
.card:hover{transform:translateY(-3px);box-shadow:0 10px 28px rgba(255,77,141,.12);border-color:rgba(255,77,141,.3);}
.card-img{position:relative;padding-top:56.25%;overflow:hidden;background:var(--surface);}
.card-img img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;}
.img-dl{position:absolute;bottom:8px;right:8px;background:rgba(0,0,0,.65);color:#fff;font-size:.68rem;padding:3px 9px;border-radius:12px;text-decoration:none;backdrop-filter:blur(4px);transition:background .2s;}
.img-dl:hover{background:var(--rose);}
.card-body{padding:16px;display:flex;flex-direction:column;gap:8px;flex:1;}
.card-head{display:flex;align-items:center;justify-content:space-between;gap:6px;}
.badge{background:var(--c,#888);color:#fff;font-size:.68rem;font-weight:700;padding:2px 9px;border-radius:10px;white-space:nowrap;}
.date-tag{font-size:.7rem;color:var(--muted);font-variant-numeric:tabular-nums;}
.title-zh{font-size:.97rem;font-weight:700;color:var(--rose2);line-height:1.5;}
.title-ja{font-size:.8rem;color:#c4a8ba;line-height:1.6;}
.sum-zh{font-size:.8rem;color:#e0b8cc;line-height:1.65;}
.sum-ja{font-size:.75rem;color:var(--muted);line-height:1.65;border-top:1px solid var(--border);padding-top:6px;}
.card-btn{display:inline-block;margin-top:auto;padding:6px 14px;background:transparent;border:1px solid var(--rose);color:var(--rose);text-decoration:none;border-radius:20px;font-size:.75rem;font-weight:600;align-self:flex-start;transition:background .2s,color .2s;}
.card-btn:hover{background:var(--rose);color:#fff;}
.empty-msg{text-align:center;padding:48px;color:var(--muted);font-size:.9rem;}
footer{text-align:center;padding:24px;font-size:.72rem;color:var(--muted);border-top:1px solid var(--border);}
</style>
</head>
<body>
<header class="site-header">
  <div class="wordmark">m<span class="bang">!</span>lk</div>
  <div class="hearts">&#9825; &middot; &#9825; &middot; &#9825;</div>
  <p class="tagline">Fan Hub &middot; 新聞聚合</p>
  <p class="update-time" id="update-time"></p>
</header>
<div class="sources">
  <span style="background:#e91e7a">sd-milk.com 公式</span>
  <span style="background:#0090d4">Natalie Music</span>
  <span style="background:#cc2233">Oricon</span>
</div>
<div class="main-wrap">
  <aside class="calendar-wrap">
    <div class="cal-nav">
      <button class="cal-arrow" id="cal-prev">&#8592;</button>
      <span class="cal-title" id="cal-title"></span>
      <button class="cal-arrow" id="cal-next">&#8594;</button>
    </div>
    <div class="cal-dow">
      <span>日</span><span>一</span><span>二</span><span>三</span>
      <span>四</span><span>五</span><span>六</span>
    </div>
    <div class="cal-grid" id="cal-grid"></div>
    <button class="cal-clear" id="cal-clear">顯示全部文章</button>
    <p class="cal-stats" id="cal-stats"></p>
  </aside>
  <section class="articles-section">
    <div class="filter-bar">
      <span class="filter-label" id="filter-label">全部文章</span>
    </div>
    <div id="articles-container"></div>
  </section>
</div>
<footer>m!lk Fan Hub &nbsp;&middot;&nbsp; 非公式ファンサイト / 非官方粉絲網站 &nbsp;&middot;&nbsp; 毎日 09:00 JST 自動更新</footer>
<script>
const ARTICLES = __ARTICLES_JSON__;
const UPDATED_AT = "__UPDATED_AT__";

document.getElementById('update-time').textContent = '最後更新 / 最終更新：' + UPDATED_AT + ' JST';

const byDate = {};
ARTICLES.forEach(function(a) {
  var d = a.date || 'unknown';
  if (!byDate[d]) byDate[d] = [];
  byDate[d].push(a);
});
const datesWithNews = new Set(Object.keys(byDate));

const now = new Date();
var calYear = now.getFullYear();
var calMonth = now.getMonth();
var selected = null;
const MONTH_NAMES = ['1月','2月','3月','4月','5月','6月','7月','8月','9月','10月','11月','12月'];

function renderCalendar() {
  document.getElementById('cal-title').textContent = calYear + '年 ' + MONTH_NAMES[calMonth];
  var firstWd = new Date(calYear, calMonth, 1).getDay();
  var daysTotal = new Date(calYear, calMonth + 1, 0).getDate();
  var todayStr = now.toISOString().slice(0, 10);
  var grid = document.getElementById('cal-grid');
  grid.innerHTML = '';
  for (var i = 0; i < firstWd; i++) {
    var e = document.createElement('div'); e.className = 'cal-empty'; grid.appendChild(e);
  }
  for (var d = 1; d <= daysTotal; d++) {
    var ds = calYear + '-' + String(calMonth + 1).padStart(2, '0') + '-' + String(d).padStart(2, '0');
    var hasNews = datesWithNews.has(ds);
    var isSel = selected === ds;
    var isToday = ds === todayStr;
    var btn = document.createElement('button');
    btn.className = 'cal-day' + (hasNews ? ' has-news' : '') + (isSel ? ' selected' : '') + (isToday ? ' today' : '');
    btn.textContent = d;
    if (hasNews) {
      (function(dateStr) {
        btn.addEventListener('click', function() {
          selected = (selected === dateStr) ? null : dateStr;
          renderCalendar(); renderArticles();
        });
      })(ds);
    }
    grid.appendChild(btn);
  }
  var monthPfx = calYear + '-' + String(calMonth + 1).padStart(2, '0');
  var monthCount = [...datesWithNews].filter(function(d) { return d.startsWith(monthPfx); }).length;
  document.getElementById('cal-stats').textContent =
    '本月 ' + monthCount + ' 天有文章　共 ' + ARTICLES.length + ' 篇';
}

document.getElementById('cal-prev').addEventListener('click', function() {
  if (calMonth === 0) { calYear--; calMonth = 11; } else calMonth--;
  renderCalendar();
});
document.getElementById('cal-next').addEventListener('click', function() {
  if (calMonth === 11) { calYear++; calMonth = 0; } else calMonth++;
  renderCalendar();
});
document.getElementById('cal-clear').addEventListener('click', function() {
  selected = null; renderCalendar(); renderArticles();
});

const SOURCE_COLORS = {'sd-milk.com (公式)':'#e91e7a','Natalie Music':'#0090d4','Oricon':'#cc2233'};
function esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function makeCard(a) {
  var color = SOURCE_COLORS[a.source] || '#888';
  var article = document.createElement('article');
  article.className = 'card';
  var imgHtml = '';
  if (a.image) {
    imgHtml = '<div class="card-img">' +
      '<img src="' + esc(a.image) + '" alt="" loading="lazy" onerror="this.closest(\'.card-img\').style.display=\'none\'">' +
      '<a class="img-dl" href="' + esc(a.image) + '" target="_blank" rel="noopener">&#8595; 查看圖片</a>' +
      '</div>';
  }
  article.innerHTML = imgHtml +
    '<div class="card-body">' +
      '<div class="card-head">' +
        '<span class="badge" style="--c:' + color + '">' + esc(a.source) + '</span>' +
        (a.date ? '<time class="date-tag">' + esc(a.date) + '</time>' : '') +
      '</div>' +
      (a.title_zh ? '<p class="title-zh">' + esc(a.title_zh) + '</p>' : '') +
      (a.title_ja ? '<p class="title-ja">' + esc(a.title_ja) + '</p>' : '') +
      (a.summary_zh ? '<p class="sum-zh">' + esc(a.summary_zh) + '</p>' : '') +
      (a.summary_ja ? '<p class="sum-ja">' + esc(a.summary_ja) + '</p>' : '') +
      '<a class="card-btn" href="' + esc(a.url) + '" target="_blank" rel="noopener noreferrer">原文を読む &#8594;</a>' +
    '</div>';
  return article;
}

function renderArticles() {
  var container = document.getElementById('articles-container');
  var label = document.getElementById('filter-label');
  container.innerHTML = '';
  if (selected) {
    label.textContent = selected + ' 的文章';
    var arts = byDate[selected] || [];
    if (arts.length === 0) {
      container.innerHTML = '<p class="empty-msg">這天沒有文章</p>'; return;
    }
    var grid = document.createElement('div'); grid.className = 'articles-grid';
    arts.forEach(function(a) { grid.appendChild(makeCard(a)); });
    container.appendChild(grid);
  } else {
    label.textContent = '全部文章';
    var sortedDates = [...datesWithNews].sort().reverse();
    if (sortedDates.length === 0) {
      container.innerHTML = '<p class="empty-msg">文章累積中，明天再來看看</p>'; return;
    }
    sortedDates.forEach(function(date) {
      var group = document.createElement('div'); group.className = 'date-group';
      var heading = document.createElement('p'); heading.className = 'date-heading';
      heading.textContent = date; group.appendChild(heading);
      var grid = document.createElement('div'); grid.className = 'articles-grid';
      byDate[date].forEach(function(a) { grid.appendChild(makeCard(a)); });
      group.appendChild(grid);
      container.appendChild(group);
    });
  }
}

renderCalendar();
renderArticles();
</script>
</body>
</html>
'''

def generate_html(articles, updated_at):
    articles_json = json.dumps(articles, ensure_ascii=False)
    safe_json = articles_json.replace('</script>', r'<\/script>')
    return HTML_TEMPLATE \
        .replace('__ARTICLES_JSON__', safe_json) \
        .replace('__UPDATED_AT__', updated_at)

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print('=== m!lk Fan Hub v2 ===')

    existing = load_articles()
    print(f'Loaded {len(existing)} existing articles')

    new_raw = []
    new_raw += fetch_sd_milk()
    new_raw += fetch_natalie()
    new_raw += fetch_oricon()

    if translator:
        print(f'Translating {len(new_raw)} articles...')
        for a in new_raw:
            a['title_zh']   = translate(a['title_ja'])
            a['summary_zh'] = translate(a['summary_ja']) if a['summary_ja'] else ''
    else:
        print('WARNING: DeepL not configured, skipping translation')

    print(f'Fetching images for {len(new_raw)} articles...')
    for a in new_raw:
        if a.get('url'):
            a['image'] = fetch_og_image(a['url'])
            time.sleep(0.3)

    merged, added = merge_articles(existing, new_raw)
    print(f'Added {added} new articles (total: {len(merged)})')
    save_articles(merged)

    now_jst = datetime.now(JST).strftime('%Y-%m-%d %H:%M')
    html = generate_html(merged, now_jst)
    with open('index.html', 'w', encoding='utf-8') as f:
        f.write(html)

    print('Done!')


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""
m!lk Fan Hub v4
News: sd-milk.com / Natalie / Oricon / BARKS / SANSPO /
      Billboard Japan / Musicvoice / SPICE / Musicman / The First Times / Modelpress /
      美的.com / MEN'S NON-NO / VOGUE JAPAN /
      TVガイドWeb / WEBザテレビジョン / ebidan.jp
Schedule: sd-milk.com official schedule
Design: white base + blue accent + member colours (pink/yellow/blue/white/red)
"""
import os, re, time, json, hashlib
import feedparser
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

try:
    import deepl
    _key = os.environ.get('DEEPL_API_KEY', '')
    translator = deepl.Translator(_key) if _key else None
except Exception:
    translator = None

JST           = timezone(timedelta(hours=9))
ARTICLES_FILE = 'articles.json'
SCHEDULE_FILE = 'schedule.json'
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

def make_id(s):
    return hashlib.md5(s.encode()).hexdigest()[:12]

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

def _clean_img_url(url):
    """Strip CDN resize/crop parameters so we get the original image."""
    if not url:
        return url
    try:
        from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
        p = urlparse(url)
        params = parse_qs(p.query, keep_blank_values=True)
        for k in ['impolicy', 'w', 'h', 'width', 'height', 'size',
                  'quality', 'q', 'resize', 'fit', 'crop', 'auto']:
            params.pop(k, None)
        q = urlencode(params, doseq=True)
        return urlunparse((p.scheme, p.netloc, p.path, p.params, q, p.fragment))
    except Exception:
        return url

def fetch_og_image(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=8)
        soup = BeautifulSoup(r.text, 'html.parser')
        for attrs in [
            {'property': 'og:image'}, {'name': 'og:image'},
            {'property': 'twitter:image'}, {'name': 'twitter:image'},
        ]:
            el = soup.find('meta', attrs=attrs)
            if el and el.get('content'):
                img = el['content'].strip()
                if img.startswith('http'):
                    return _clean_img_url(img)
    except Exception:
        pass
    return ''

# ── Persistence ───────────────────────────────────────────────────────────────

def load_json(path):
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return []

def save_json(path, data):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def _norm_url(url):
    """Normalize URL for dedup: strip trailing slash + common tracking params."""
    try:
        from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
        p = urlparse(url.rstrip('/'))
        params = parse_qs(p.query, keep_blank_values=True)
        for k in ['ref', 'utm_source', 'utm_medium', 'utm_campaign', 'utm_content',
                  'utm_term', 'from', 'cid', 'fbclid', 'gclid']:
            params.pop(k, None)
        q = urlencode(params, doseq=True)
        return urlunparse((p.scheme, p.netloc.lower(), p.path.rstrip('/'), '', q, ''))
    except Exception:
        return url.rstrip('/')

def dedup_by_url(items):
    seen, result = set(), []
    for a in items:
        u = _norm_url(a.get('url', ''))
        if u and u not in seen:
            seen.add(u)
            result.append(a)
    return result

def merge_by_url(existing, new_items):
    seen = {_norm_url(a['url']) for a in existing if a.get('url')}
    added = [a for a in new_items if a.get('url') and _norm_url(a['url']) not in seen]
    merged = existing + added
    merged.sort(key=lambda x: x.get('date', ''), reverse=True)
    return merged[:MAX_ARTICLES], len(added)

def merge_by_id(existing, new_items):
    seen = {a['id'] for a in existing if a.get('id')}
    added = [a for a in new_items if a.get('id') and a['id'] not in seen]
    merged = existing + added
    merged.sort(key=lambda x: x.get('date', ''))
    return merged, len(added)

# ── News fetchers ─────────────────────────────────────────────────────────────

def _make_article(source, title, url, date_raw, summary=''):
    return {
        'id': make_id(url), 'source': source,
        'title_ja': title, 'title_zh': '',
        'summary_ja': summary[:280], 'summary_zh': '',
        'url': url, 'image': '', 'date': normalize_date(date_raw),
    }

def _scrape_search(source, url, base, selectors, keyword_check=None, limit=6):
    arts = []
    try:
        r = requests.get(url, headers=HEADERS, timeout=12)
        if r.status_code != 200:
            return arts
        r.encoding = 'utf-8'
        soup = BeautifulSoup(r.text, 'html.parser')
        for sel in selectors:
            items = soup.select(sel)
            if len(items) < 2:
                continue
            for it in items[:limit]:
                a = it.find('a', href=True)
                if not a:
                    continue
                title = clean(a.get_text())
                if len(title) < 4:
                    title = clean(it.get_text())[:120]
                href = a['href']
                if href and not href.startswith('http'):
                    href = base + href
                if not href or not href.startswith('http'):
                    continue
                if keyword_check and not keyword_check(title):
                    continue
                date_el = it.select_one('time, .date, .time, .day, [datetime], .post-date')
                date_raw = ''
                if date_el:
                    date_raw = date_el.get('datetime', '') or clean(date_el.get_text())
                if len(title) > 3:
                    arts.append(_make_article(source, title, href, date_raw))
            if arts:
                break
    except Exception as e:
        print(f'  [{source}] Error: {e}')
    return arts

def _milk_check(title):
    return 'm!lk' in title.lower() or 'ミルク' in title or 'M!LK' in title

# ── Individual fetchers ───────────────────────────────────────────────────────

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
                        arts.append(_make_article('sd-milk.com 公式', title, href, date_raw))
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
            arts.append(_make_article('Natalie Music', title,
                                      e.get('link', ''), e.get('published', ''), summary))
            if len(arts) >= 5:
                break
    except Exception as e:
        print(f'  Error: {e}')
    print(f'  -> {len(arts)} articles')
    return arts

def fetch_oricon():
    print('Fetching Oricon...')
    arts = _scrape_search('Oricon',
        'https://www.oricon.co.jp/search/?q=m%21lk&cat=news',
        'https://www.oricon.co.jp',
        ['.news-list li', '.list-news li', 'ul.list li', 'article', '.item'],
        keyword_check=_milk_check)
    print(f'  -> {len(arts)} articles')
    return arts

def fetch_barks():
    print('Fetching BARKS...')
    arts = _scrape_search('BARKS',
        'https://www.barks.jp/search/?q=m%21lk&type=news',
        'https://www.barks.jp',
        ['.search-result li', '.news-list li', '.list li', 'article', '.item'],
        keyword_check=_milk_check)
    print(f'  -> {len(arts)} articles')
    return arts

def fetch_sanspo():
    print('Fetching SANSPO...')
    arts = _scrape_search('SANSPO',
        'https://www.sanspo.com/search/?q=m%21lk',
        'https://www.sanspo.com',
        ['.search-list li', '.article-list li', 'article', '.news-list li', '.list li'],
        keyword_check=_milk_check)
    print(f'  -> {len(arts)} articles')
    return arts

def fetch_billboard():
    print('Fetching Billboard Japan...')
    arts = _scrape_search('Billboard Japan',
        'https://www.billboard-japan.com/d_news/?q=m%21lk',
        'https://www.billboard-japan.com',
        ['.news-list li', '.list li', '.d-news-list li', 'article', 'ul li'],
        keyword_check=_milk_check)
    if not arts:
        try:
            feed = feedparser.parse('https://www.billboard-japan.com/d_news/rss/')
            for e in feed.entries:
                title = e.get('title', '')
                if not _milk_check(title):
                    continue
                arts.append(_make_article('Billboard Japan', title,
                                          e.get('link', ''), e.get('published', '')))
                if len(arts) >= 5:
                    break
        except Exception as e:
            print(f'  Billboard RSS error: {e}')
    print(f'  -> {len(arts)} articles')
    return arts

def fetch_musicvoice():
    print('Fetching Musicvoice...')
    arts = _scrape_search('Musicvoice',
        'https://www.musicvoice.jp/?s=m%21lk',
        'https://www.musicvoice.jp',
        ['.post-list li', 'article', '.search-result li', '.list li', '.news-list li'],
        keyword_check=_milk_check)
    print(f'  -> {len(arts)} articles')
    return arts

def fetch_spice():
    print('Fetching SPICE (eplus)...')
    arts = _scrape_search('SPICE',
        'https://spice.eplus.jp/?s=M%21LK',
        'https://spice.eplus.jp',
        ['article', '.article-list li', '.search-result li', '.list li', '.news-list li'],
        keyword_check=_milk_check)
    print(f'  -> {len(arts)} articles')
    return arts

def fetch_musicman():
    print('Fetching Musicman...')
    arts = _scrape_search('Musicman',
        'https://www.musicman.co.jp/search/?q=m%21lk',
        'https://www.musicman.co.jp',
        ['.article-list li', '.list li', 'article', '.search-result li', '.news-list li'],
        keyword_check=_milk_check)
    print(f'  -> {len(arts)} articles')
    return arts

def fetch_thefirsttimes():
    print('Fetching The First Times...')
    arts = _scrape_search('The First Times',
        'https://www.thefirsttimes.jp/?s=M%21LK',
        'https://www.thefirsttimes.jp',
        ['article', '.post-list li', '.search-result li', '.list li', '.news-list li'],
        keyword_check=_milk_check)
    print(f'  -> {len(arts)} articles')
    return arts

def fetch_modelpress():
    print('Fetching Modelpress...')
    arts = _scrape_search('Modelpress',
        'https://mdpr.jp/search?q=m%21lk',
        'https://mdpr.jp',
        ['.article-list li', 'article', '.list li', '.search-result li', '.news-list li'],
        keyword_check=_milk_check)
    print(f'  -> {len(arts)} articles')
    return arts

def fetch_biteki():
    print('Fetching 美的.com...')
    arts = _scrape_search('美的.com',
        'https://www.biteki.com/?s=m%21lk',
        'https://www.biteki.com',
        ['article', '.article-list li', '.post-list li', '.search-result li', '.list li'],
        keyword_check=_milk_check)
    print(f'  -> {len(arts)} articles')
    return arts

def fetch_mensnonno():
    print('Fetching MEN\'S NON-NO...')
    arts = _scrape_search("MEN'S NON-NO",
        'https://www.mensnonno.jp/?s=m%21lk',
        'https://www.mensnonno.jp',
        ['article', '.article-list li', '.post-list li', '.search-result li', '.list li'],
        keyword_check=_milk_check)
    print(f'  -> {len(arts)} articles')
    return arts

def fetch_vogue_japan():
    print('Fetching VOGUE JAPAN...')
    arts = _scrape_search('VOGUE JAPAN',
        'https://www.vogue.co.jp/search?q=m%21lk',
        'https://www.vogue.co.jp',
        ['article', '.search-result li', '.article-list li', '.list li', '.post-list li'],
        keyword_check=_milk_check)
    print(f'  -> {len(arts)} articles')
    return arts

def fetch_tvguide():
    print('Fetching TVガイドWeb...')
    arts = _scrape_search('TVガイドWeb',
        'https://www.tvguide.or.jp/cmn_keyword/mlk/',
        'https://www.tvguide.or.jp',
        ['.news-list li', '.article-list li', 'article', '.list li', '.keyword-news li'],
        keyword_check=_milk_check)
    print(f'  -> {len(arts)} articles')
    return arts

def fetch_thetv():
    print('Fetching WEBザテレビジョン...')
    arts = _scrape_search('WEBザテレビジョン',
        'https://thetv.jp/news/search/?q=m%21lk',
        'https://thetv.jp',
        ['.news-list li', 'article', '.article-list li', '.list li', '.search-result li'],
        keyword_check=_milk_check)
    print(f'  -> {len(arts)} articles')
    return arts

def fetch_ebidan():
    print('Fetching ebidan.jp...')
    arts = _scrape_search('ebidan.jp',
        'https://ebidan.jp/search/?q=m%21lk',
        'https://ebidan.jp',
        ['.news-list li', 'article', '.article-list li', '.list li', '.search-result li'],
        keyword_check=_milk_check)
    print(f'  -> {len(arts)} articles')
    return arts

# ── Known program / movie pages ───────────────────────────────────────────────
# Each entry: (display_name, page_url, base_url)
# Sources whose articles are m!lk-related by definition (no keyword check needed)
PROGRAM_SOURCES = {s for s, _, _ in [
    ('やってM!LK (TBS)',   'https://www.tbs.co.jp/yatte_milk/',              'https://www.tbs.co.jp'),
    ('君の好きは無敵',      'https://www.tbs.co.jp/kiminosukihamuteki_tbs/',   'https://www.tbs.co.jp'),
    ('レコメン! 文化放送',  'https://www.joqr.co.jp/qr/program/reco/',         'https://www.joqr.co.jp'),
    ('イマドキッ MBSラジオ','https://www.mbs1179.com/imadoki/',               'https://www.mbs1179.com'),
    ('トイ・ストーリー５',  'https://www.disney.co.jp/movie/toy5',             'https://www.disney.co.jp'),
    ('仮面ライダーゼッツ',  'https://zeztz-gavan-26movie.com/',               'https://zeztz-gavan-26movie.com'),
    ('sd-milk.com 公式',   'https://sd-milk.com',                            'https://sd-milk.com'),
]}

KNOWN_PROGRAMS = [
    ('やってM!LK (TBS)',   'https://www.tbs.co.jp/yatte_milk/',              'https://www.tbs.co.jp'),
    ('君の好きは無敵',      'https://www.tbs.co.jp/kiminosukihamuteki_tbs/',   'https://www.tbs.co.jp'),
    ('レコメン! 文化放送',  'https://www.joqr.co.jp/qr/program/reco/',         'https://www.joqr.co.jp'),
    ('イマドキッ MBSラジオ','https://www.mbs1179.com/imadoki/',               'https://www.mbs1179.com'),
    ('トイ・ストーリー５',  'https://www.disney.co.jp/movie/toy5',             'https://www.disney.co.jp'),
    ('仮面ライダーゼッツ',  'https://zeztz-gavan-26movie.com/',               'https://zeztz-gavan-26movie.com'),
]

PROGRAM_SELECTORS = [
    '.news-list li', '.info-list li', '.topics-list li', '.update-list li',
    '.blog-list li', '.news li', '.information li', '.episode-list li',
    'article', '.post-list li', 'ul.list li', '.news-item',
]

def fetch_known_programs():
    print('Fetching known program pages...')
    all_arts = []
    for source, url, base in KNOWN_PROGRAMS:
        arts = _scrape_search(source, url, base, PROGRAM_SELECTORS, limit=8)
        print(f'  [{source}] -> {len(arts)} items')
        all_arts += arts
    return all_arts

# ── Schedule fetcher ──────────────────────────────────────────────────────────

def fetch_schedule():
    print('Fetching schedule from sd-milk.com...')
    events = []

    urls_to_try = [
        'https://sd-milk.com/calendar',
        'https://sd-milk.com/contents/schedule',
        'https://sd-milk.com/contents/schedule?data=media',
        'https://sd-milk.com/schedule',
        'https://sd-milk.com/live',
        'https://sd-milk.com/',
    ]

    # Only selectors that are likely real schedule/event containers
    schedule_selectors = [
        '.schedule-list li', '.live-list li', '.event-list li',
        '.schedule li', '.live li', '.concert-list li',
        '.schedule-wrap li', '.live-schedule li', '.liveschedule li',
        '.info-list li', '.topics-list li',
        'table tr', '.cal-body td', '.calendar td',
        'article', '.post', '.item', '.entry',
    ]

    for url in urls_to_try:
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            if r.status_code != 200:
                print(f'  {url} -> HTTP {r.status_code}')
                continue
            r.encoding = 'utf-8'
            soup = BeautifulSoup(r.text, 'html.parser')

            for sel in schedule_selectors:
                items = soup.select(sel)
                if len(items) < 1:
                    continue
                batch = []
                for it in items:
                    text = clean(it.get_text())
                    if not text or len(text) < 8:
                        continue

                    # Must have an explicit date element — skip items that only have
                    # a guessed date from body text (too many false positives)
                    date_el = it.select_one('time, .date, .day, [datetime]')
                    if not date_el:
                        # Allow full-year date pattern (YYYY/MM/DD) but NOT MM/DD only
                        m = re.search(r'(\d{4})[./年](\d{1,2})[./月](\d{1,2})', text)
                        if not m:
                            continue
                        date_str = f"{m.group(1)}-{m.group(2).zfill(2)}-{m.group(3).zfill(2)}"
                    else:
                        raw_date = date_el.get('datetime', '') or clean(date_el.get_text())
                        date_str = normalize_date(raw_date)

                    # Sanity check: date should be 2020 or later
                    if date_str[:4] < '2020':
                        continue

                    a_tag = it.find('a', href=True)
                    href = a_tag['href'] if a_tag else ''
                    if href and not href.startswith('http'):
                        href = 'https://sd-milk.com' + href

                    lines = [l.strip() for l in text.splitlines() if l.strip()]
                    title   = lines[0][:120] if lines else text[:120]
                    details = ' / '.join(lines[1:3]) if len(lines) > 1 else ''

                    batch.append({
                        'id': make_id(text[:50] + date_str),
                        'date': date_str, 'title': title,
                        'details': details[:200], 'title_zh': '', 'url': href,
                    })
                if batch:
                    print(f'  {url} [{sel}] -> {len(batch)} items')
                    events = batch
                    break
        except Exception as e:
            print(f'  Error on {url}: {e}')
        if events:
            break

    seen, unique = set(), []
    for e in events:
        if e['id'] not in seen:
            seen.add(e['id'])
            unique.append(e)
    unique.sort(key=lambda x: x['date'])
    print(f'  -> {len(unique)} events total')
    return unique

# ── CSS & templates ───────────────────────────────────────────────────────────

SOURCE_COLORS = {
    'sd-milk.com 公式':  '#d4006e',
    'Natalie Music':     '#0066aa',
    'Oricon':            '#cc2233',
    'BARKS':             '#b05010',
    'SANSPO':            '#0a6b30',
    'Billboard Japan':   '#1a4a9f',
    'Musicvoice':        '#7b1ea2',
    'SPICE':             '#d45500',
    'Musicman':          '#1b5e20',
    'The First Times':   '#c62828',
    'Modelpress':        '#1565c0',
    '美的.com':          '#e91e8c',
    "MEN'S NON-NO":      '#0d47a1',
    'VOGUE JAPAN':       '#2c2c2c',
    'TVガイドWeb':       '#e53935',
    'WEBザテレビジョン': '#00838f',
    'ebidan.jp':             '#6d4c41',
    'やってM!LK (TBS)':     '#cc0000',
    '君の好きは無敵':        '#880033',
    'レコメン! 文化放送':    '#0055bb',
    'イマドキッ MBSラジオ':  '#e65100',
    'トイ・ストーリー５':    '#0d47a1',
    '仮面ライダーゼッツ':    '#1b5e20',
}

COMMON_CSS = r'''
:root{
  --bg:#f0f5ff;--surface:#ffffff;--card:#ffffff;--border:#dce8ff;
  --blue:#2a5bd7;--blue2:#5b8dee;--blue3:#dce8ff;
  --text:#1a1a2e;--muted:#64748b;
  --pink:#ff69b4;--yellow:#ffd43b;--sky:#4da6ff;--red:#ff3344;
  --shadow:0 2px 12px rgba(42,91,215,.08);
  --shadow-hover:0 8px 28px rgba(42,91,215,.16);
}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0;}
html{scroll-behavior:smooth;}
body{background:var(--bg);color:var(--text);font-family:'Hiragino Sans','Noto Sans TC','Noto Sans JP',system-ui,sans-serif;min-height:100vh;}
.site-header{background:var(--surface);border-bottom:1px solid var(--border);padding:28px 20px 0;text-align:center;}
.member-stripe{display:flex;height:5px;width:100%;margin-bottom:20px;}
.member-stripe span{flex:1;}
.wordmark{font-size:clamp(2.6rem,8vw,4.8rem);font-weight:900;letter-spacing:.25em;color:var(--blue);}
.wordmark .bang{color:var(--pink);}
.tagline{margin-top:5px;font-size:.72rem;letter-spacing:.45em;text-transform:uppercase;color:var(--blue2);}
.hearts{margin:7px 0 3px;font-size:.85rem;color:var(--pink);letter-spacing:.4em;}
.update-time{font-size:.68rem;color:var(--muted);margin:7px 0 14px;}
.page-nav{display:flex;justify-content:center;gap:0;}
.nav-link{padding:10px 32px;font-size:.8rem;font-weight:600;letter-spacing:.08em;text-decoration:none;color:var(--muted);border-bottom:3px solid transparent;transition:color .2s,border-color .2s;}
.nav-link:hover{color:var(--blue);}
.nav-link.nav-active{color:var(--blue);border-bottom-color:var(--blue);}
.sources{display:flex;justify-content:center;gap:5px;flex-wrap:wrap;padding:12px 20px;}
.sources span{padding:3px 10px;border-radius:20px;font-size:.63rem;font-weight:700;color:#fff;}
.main-wrap{max-width:1100px;margin:0 auto;padding:22px 16px 80px;display:grid;grid-template-columns:258px 1fr;gap:22px;align-items:start;}
@media(max-width:760px){.main-wrap{grid-template-columns:1fr;}}
.calendar-wrap{background:var(--surface);border:1px solid var(--border);border-radius:16px;padding:16px;position:sticky;top:20px;box-shadow:var(--shadow);}
@media(max-width:760px){.calendar-wrap{position:static;}}
.cal-nav{display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;}
.cal-title{font-size:.88rem;font-weight:700;color:var(--blue);}
.cal-arrow{background:none;border:1px solid var(--border);color:var(--muted);width:26px;height:26px;border-radius:6px;cursor:pointer;transition:border-color .2s,color .2s;display:flex;align-items:center;justify-content:center;font-size:.85rem;}
.cal-arrow:hover{border-color:var(--blue);color:var(--blue);}
.cal-dow{display:grid;grid-template-columns:repeat(7,1fr);gap:2px;margin-bottom:3px;}
.cal-dow span{text-align:center;font-size:.6rem;color:var(--muted);padding:3px 0;font-weight:600;}
.cal-grid{display:grid;grid-template-columns:repeat(7,1fr);gap:2px;}
.cal-empty{height:30px;}
.cal-day{height:30px;border-radius:5px;border:none;background:transparent;color:var(--muted);font-size:.74rem;cursor:default;position:relative;transition:background .15s,color .15s;display:flex;align-items:center;justify-content:center;flex-direction:column;gap:1px;}
.cal-day.has-news{color:var(--text);cursor:pointer;}
.cal-day.has-news:hover{background:var(--blue3);}
.cal-day.has-news::after{content:'';display:block;width:4px;height:4px;border-radius:50%;background:var(--blue);opacity:.7;}
.cal-day.selected{background:var(--blue);color:#fff;}
.cal-day.selected::after{background:#fff;}
.cal-day.today{font-weight:700;color:var(--pink);}
.cal-day.today.selected{color:#fff;}
.cal-clear{display:block;width:100%;margin-top:10px;padding:6px;background:var(--blue3);border:1px solid var(--border);color:var(--blue);border-radius:8px;font-size:.73rem;cursor:pointer;transition:background .2s;}
.cal-clear:hover{background:#c5d8ff;}
.cal-stats{margin-top:9px;font-size:.67rem;color:var(--muted);text-align:center;line-height:1.6;}
.articles-section{min-width:0;}
.filter-bar{margin-bottom:13px;}
.filter-label{font-size:.72rem;letter-spacing:.2em;text-transform:uppercase;color:var(--blue);padding-left:10px;border-left:3px solid var(--blue);}
.date-group{margin-bottom:24px;}
.date-heading{font-size:.7rem;letter-spacing:.22em;color:var(--blue2);margin-bottom:10px;padding-bottom:5px;border-bottom:1px solid var(--border);}
.articles-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:13px;}
.card{background:var(--card);border:1px solid var(--border);border-radius:14px;display:flex;flex-direction:column;transition:transform .2s,box-shadow .2s,border-color .2s;box-shadow:var(--shadow);}
.card:hover{transform:translateY(-3px);box-shadow:var(--shadow-hover);border-color:#b4caff;}
.card-img{background:var(--bg);overflow:hidden;border-radius:14px 14px 0 0;}
.card-img img{width:100%;height:auto;display:block;}
.img-dl-wrap{text-align:right;padding:3px 8px 5px;background:var(--bg);}
.img-dl{font-size:.62rem;color:var(--blue);text-decoration:none;display:inline-block;padding:2px 6px;border-radius:8px;transition:background .2s;}
.img-dl:hover{background:var(--blue3);}
.card-body{padding:13px;display:flex;flex-direction:column;gap:7px;flex:1;}
.card-head{display:flex;align-items:center;justify-content:space-between;gap:6px;flex-wrap:wrap;}
.badge{background:var(--c,#888);color:#fff;font-size:.63rem;font-weight:700;padding:2px 8px;border-radius:10px;white-space:nowrap;}
.date-tag{font-size:.67rem;color:var(--muted);font-variant-numeric:tabular-nums;}
.title-zh{font-size:.93rem;font-weight:700;color:var(--blue);line-height:1.5;}
.title-ja{font-size:.78rem;color:#4a5568;line-height:1.6;}
.sum-zh{font-size:.77rem;color:#374151;line-height:1.65;}
.sum-ja{font-size:.72rem;color:var(--muted);line-height:1.65;border-top:1px solid var(--border);padding-top:5px;}
.card-btn{display:inline-block;margin-top:auto;padding:5px 13px;background:transparent;border:1px solid var(--blue);color:var(--blue);text-decoration:none;border-radius:20px;font-size:.71rem;font-weight:600;align-self:flex-start;transition:background .2s,color .2s;}
.card-btn:hover{background:var(--blue);color:#fff;}
.empty-msg{text-align:center;padding:48px;color:var(--muted);font-size:.9rem;}
footer{text-align:center;padding:18px 20px 28px;font-size:.7rem;color:var(--muted);border-top:1px solid var(--border);background:var(--surface);}
.sources-footer{display:flex;justify-content:center;gap:5px;flex-wrap:wrap;padding:14px 0 4px;}
.src-link{padding:3px 10px;border-radius:20px;font-size:.62rem;font-weight:700;color:#fff;background:var(--c,#888);text-decoration:none;opacity:.85;transition:opacity .2s;}
.src-link:hover{opacity:1;}
'''

SCHEDULE_EXTRA_CSS = r'''
.event-card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:15px;display:flex;flex-direction:column;gap:8px;box-shadow:var(--shadow);transition:transform .2s,box-shadow .2s;}
.event-card:hover{transform:translateY(-2px);box-shadow:var(--shadow-hover);}
.event-date{font-size:.7rem;font-variant-numeric:tabular-nums;color:var(--muted);}
.event-title{font-size:.93rem;font-weight:700;color:var(--text);line-height:1.5;}
.event-title-zh{font-size:.87rem;color:var(--blue);line-height:1.5;}
.event-details{font-size:.77rem;color:var(--muted);line-height:1.6;}
.event-link{display:inline-block;padding:5px 13px;background:transparent;border:1px solid var(--blue);color:var(--blue);text-decoration:none;border-radius:20px;font-size:.71rem;font-weight:600;align-self:flex-start;margin-top:auto;transition:background .2s,color .2s;}
.event-link:hover{background:var(--blue);color:#fff;}
.no-schedule{text-align:center;padding:60px 20px;color:var(--muted);}
.no-schedule p{margin-bottom:8px;line-height:1.8;}
'''

MEMBER_STRIPE_HTML = (
    '<div class="member-stripe">'
    '<span style="background:#ff69b4"></span>'
    '<span style="background:#ffd43b"></span>'
    '<span style="background:#4da6ff"></span>'
    '<span style="background:#e8e8f0;border-top:1px solid #dce8ff"></span>'
    '<span style="background:#ff3344"></span>'
    '</div>'
)

SOURCE_URLS = {
    'sd-milk.com 公式':   'https://sd-milk.com',
    'Natalie Music':      'https://natalie.mu/music/',
    'Oricon':             'https://www.oricon.co.jp',
    'BARKS':              'https://www.barks.jp',
    'SANSPO':             'https://www.sanspo.com',
    'Billboard Japan':    'https://www.billboard-japan.com',
    'Musicvoice':         'https://www.musicvoice.jp',
    'SPICE':              'https://spice.eplus.jp',
    'Musicman':           'https://www.musicman.co.jp',
    'The First Times':    'https://www.thefirsttimes.jp',
    'Modelpress':         'https://mdpr.jp',
    '美的.com':           'https://www.biteki.com',
    "MEN'S NON-NO":       'https://www.mensnonno.jp',
    'VOGUE JAPAN':        'https://www.vogue.co.jp',
    'TVガイドWeb':        'https://www.tvguide.or.jp/cmn_keyword/mlk/',
    'WEBザテレビジョン':  'https://thetv.jp',
    'ebidan.jp':          'https://ebidan.jp',
    'やってM!LK (TBS)':   'https://www.tbs.co.jp/yatte_milk/',
    '君の好きは無敵':     'https://www.tbs.co.jp/kiminosukihamuteki_tbs/',
    'レコメン! 文化放送': 'https://www.joqr.co.jp/qr/program/reco/',
    'イマドキッ MBSラジオ':'https://www.mbs1179.com/imadoki/',
    'トイ・ストーリー５': 'https://www.disney.co.jp/movie/toy5',
    '仮面ライダーゼッツ': 'https://zeztz-gavan-26movie.com/',
}

def make_sources_footer():
    parts = []
    for name, color in SOURCE_COLORS.items():
        url = SOURCE_URLS.get(name, '#')
        parts.append(
            f'<a href="{url}" target="_blank" rel="noopener" class="src-link" style="--c:{color}">{name}</a>'
        )
    return '<div class="sources-footer">' + ''.join(parts) + '</div>'

NAV_NEWS     = '<a href="index.html" class="nav-link nav-active">ニュース</a><a href="schedule.html" class="nav-link">演出情報</a>'
NAV_SCHEDULE = '<a href="index.html" class="nav-link">ニュース</a><a href="schedule.html" class="nav-link nav-active">演出情報</a>'

NEWS_TEMPLATE = r'''<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>m!lk Fan Hub — ニュース</title>
<style>__CSS__</style>
</head>
<body>
<header class="site-header">
  __STRIPE__
  <div class="wordmark">m<span class="bang">!</span>lk</div>
  <div class="hearts">&#9825; &middot; &#9825; &middot; &#9825;</div>
  <p class="tagline">Fan Hub &middot; 新聞聚合</p>
  <nav class="page-nav">__NAV__</nav>
  <p class="update-time" id="update-time"></p>
</header>
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
    <div class="filter-bar"><span class="filter-label" id="filter-label">全部文章</span></div>
    <div id="articles-container"></div>
  </section>
</div>
<footer>
  <p>m!lk Fan Hub &nbsp;&middot;&nbsp; 非公式 / 非官方 &nbsp;&middot;&nbsp; 毎日 09:00 JST 自動更新</p>
  __SOURCES_FOOTER__
</footer>
<script>
const ARTICLES=__DATA__;
document.getElementById('update-time').textContent='最後更新：__UPDATED_AT__ JST';
const byDate={};
ARTICLES.forEach(function(a){var d=a.date||'unknown';if(!byDate[d])byDate[d]=[];byDate[d].push(a);});
const datesWithNews=new Set(Object.keys(byDate));
const now=new Date();
var todayStr=now.toISOString().slice(0,10);
// Default: auto-select today if it has articles, else most recent date
var selected=datesWithNews.has(todayStr)?todayStr:([...datesWithNews].sort().reverse()[0]||null);
var calYear=now.getFullYear(),calMonth=now.getMonth();
const MN=['1月','2月','3月','4月','5月','6月','7月','8月','9月','10月','11月','12月'];
function renderCalendar(){
  document.getElementById('cal-title').textContent=calYear+'年 '+MN[calMonth];
  var fw=new Date(calYear,calMonth,1).getDay(),dt=new Date(calYear,calMonth+1,0).getDate();
  var g=document.getElementById('cal-grid');
  g.innerHTML='';
  for(var i=0;i<fw;i++){var e=document.createElement('div');e.className='cal-empty';g.appendChild(e);}
  for(var d=1;d<=dt;d++){
    var ds=calYear+'-'+String(calMonth+1).padStart(2,'0')+'-'+String(d).padStart(2,'0');
    var btn=document.createElement('button');
    btn.className='cal-day'+(datesWithNews.has(ds)?' has-news':'')+(selected===ds?' selected':'')+(ds===todayStr?' today':'');
    btn.textContent=d;
    if(datesWithNews.has(ds)){
      (function(s){btn.addEventListener('click',function(){selected=(selected===s)?null:s;renderCalendar();renderArticles();});})(ds);
    }
    g.appendChild(btn);
  }
  var pfx=calYear+'-'+String(calMonth+1).padStart(2,'0');
  var mc=[...datesWithNews].filter(function(d){return d.startsWith(pfx);}).length;
  document.getElementById('cal-stats').textContent='本月 '+mc+' 天有文章　共 '+ARTICLES.length+' 篇';
}
document.getElementById('cal-prev').addEventListener('click',function(){if(calMonth===0){calYear--;calMonth=11;}else calMonth--;renderCalendar();});
document.getElementById('cal-next').addEventListener('click',function(){if(calMonth===11){calYear++;calMonth=0;}else calMonth++;renderCalendar();});
document.getElementById('cal-clear').addEventListener('click',function(){selected=null;renderCalendar();renderArticles();});
const SC=__SC__;
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
function makeCard(a){
  var art=document.createElement('article');art.className='card';
  var imgHtml='';
  if(a.image){
    imgHtml='<div class="card-img"><img src="'+esc(a.image)+'" alt="" loading="lazy" onerror="this.closest(\'.card-img\').remove();this.closest(\'.img-dl-wrap\')&&this.closest(\'.img-dl-wrap\').remove()"></div>' +
      '<div class="img-dl-wrap"><a class="img-dl" href="'+esc(a.image)+'" target="_blank" rel="noopener">&#8599; 原圖を開く</a></div>';
  }
  var color=SC[a.source]||'#888';
  art.innerHTML=imgHtml+
    '<div class="card-body">'+
    '<div class="card-head"><span class="badge" style="--c:'+color+'">'+esc(a.source)+'</span>'+(a.date?'<time class="date-tag">'+esc(a.date)+'</time>':'')+
    '</div>'+(a.title_zh?'<p class="title-zh">'+esc(a.title_zh)+'</p>':'')+(a.title_ja?'<p class="title-ja">'+esc(a.title_ja)+'</p>':'')+
    (a.summary_zh?'<p class="sum-zh">'+esc(a.summary_zh)+'</p>':'')+(a.summary_ja?'<p class="sum-ja">'+esc(a.summary_ja)+'</p>':'')+
    '<a class="card-btn" href="'+esc(a.url)+'" target="_blank" rel="noopener">原文を読む &#8594;</a></div>';
  return art;
}
function renderArticles(){
  var c=document.getElementById('articles-container'),l=document.getElementById('filter-label');
  c.innerHTML='';
  if(selected){
    l.textContent=selected+' 的文章';
    var arts=byDate[selected]||[];
    if(!arts.length){c.innerHTML='<p class="empty-msg">這天沒有文章</p>';return;}
    var g=document.createElement('div');g.className='articles-grid';
    arts.forEach(function(a){g.appendChild(makeCard(a));});c.appendChild(g);
  }else{
    l.textContent='全部文章';
    var sd=[...datesWithNews].sort().reverse();
    if(!sd.length){c.innerHTML='<p class="empty-msg">文章累積中，明天再來看看</p>';return;}
    sd.forEach(function(date){
      var grp=document.createElement('div');grp.className='date-group';
      var h=document.createElement('p');h.className='date-heading';h.textContent=date;grp.appendChild(h);
      var g=document.createElement('div');g.className='articles-grid';
      byDate[date].forEach(function(a){g.appendChild(makeCard(a));});grp.appendChild(g);c.appendChild(grp);
    });
  }
}
renderCalendar();renderArticles();
</script>
</body>
</html>'''

SCHEDULE_TEMPLATE = r'''<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>m!lk Fan Hub — 演出情報</title>
<style>__CSS__</style>
</head>
<body>
<header class="site-header">
  __STRIPE__
  <div class="wordmark">m<span class="bang">!</span>lk</div>
  <div class="hearts">&#9825; &middot; &#9825; &middot; &#9825;</div>
  <p class="tagline">Fan Hub &middot; 演出情報</p>
  <nav class="page-nav">__NAV__</nav>
  <p class="update-time" id="update-time"></p>
</header>
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
    <button class="cal-clear" id="cal-clear">顯示全部演出</button>
    <p class="cal-stats" id="cal-stats"></p>
  </aside>
  <section class="articles-section">
    <div class="filter-bar"><span class="filter-label" id="filter-label">全部演出</span></div>
    <div id="events-container"></div>
  </section>
</div>
<footer>
  <p>m!lk Fan Hub &nbsp;&middot;&nbsp; 演出情報參考自 sd-milk.com &nbsp;&middot;&nbsp; 毎日 09:00 JST 自動更新</p>
  __SOURCES_FOOTER__
</footer>
<script>
const EVENTS=__DATA__;
document.getElementById('update-time').textContent='最後更新：__UPDATED_AT__ JST';
const byDate={};
EVENTS.forEach(function(e){var d=e.date||'unknown';if(!byDate[d])byDate[d]=[];byDate[d].push(e);});
const datesWithEvents=new Set(Object.keys(byDate));
const now=new Date();
var calYear=now.getFullYear(),calMonth=now.getMonth(),selected=null;
const MN=['1月','2月','3月','4月','5月','6月','7月','8月','9月','10月','11月','12月'];
function renderCalendar(){
  document.getElementById('cal-title').textContent=calYear+'年 '+MN[calMonth];
  var fw=new Date(calYear,calMonth,1).getDay(),dt=new Date(calYear,calMonth+1,0).getDate();
  var ts=now.toISOString().slice(0,10),g=document.getElementById('cal-grid');
  g.innerHTML='';
  for(var i=0;i<fw;i++){var el=document.createElement('div');el.className='cal-empty';g.appendChild(el);}
  for(var d=1;d<=dt;d++){
    var ds=calYear+'-'+String(calMonth+1).padStart(2,'0')+'-'+String(d).padStart(2,'0');
    var btn=document.createElement('button');
    btn.className='cal-day'+(datesWithEvents.has(ds)?' has-news':'')+(selected===ds?' selected':'')+(ds===ts?' today':'');
    btn.textContent=d;
    if(datesWithEvents.has(ds)){
      (function(s){btn.addEventListener('click',function(){selected=(selected===s)?null:s;renderCalendar();renderEvents();});})(ds);
    }
    g.appendChild(btn);
  }
  var pfx=calYear+'-'+String(calMonth+1).padStart(2,'0');
  var mc=[...datesWithEvents].filter(function(d){return d.startsWith(pfx);}).length;
  document.getElementById('cal-stats').textContent='本月 '+mc+' 場演出　共 '+EVENTS.length+' 場';
}
document.getElementById('cal-prev').addEventListener('click',function(){if(calMonth===0){calYear--;calMonth=11;}else calMonth--;renderCalendar();});
document.getElementById('cal-next').addEventListener('click',function(){if(calMonth===11){calYear++;calMonth=0;}else calMonth++;renderCalendar();});
document.getElementById('cal-clear').addEventListener('click',function(){selected=null;renderCalendar();renderEvents();});
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
function makeEventCard(e){
  var card=document.createElement('article');card.className='event-card';
  card.innerHTML=
    (e.date?'<p class="event-date">'+esc(e.date)+'</p>':'')+
    (e.title?'<p class="event-title">'+esc(e.title)+'</p>':'')+
    (e.title_zh?'<p class="event-title-zh">'+esc(e.title_zh)+'</p>':'')+
    (e.details?'<p class="event-details">'+esc(e.details)+'</p>':'')+
    (e.url?'<a class="event-link" href="'+esc(e.url)+'" target="_blank" rel="noopener">詳細を見る &#8594;</a>':'');
  return card;
}
function renderEvents(){
  var c=document.getElementById('events-container'),l=document.getElementById('filter-label');
  c.innerHTML='';
  if(EVENTS.length===0){
    c.innerHTML='<div class="no-schedule"><p>演出情報の取得中...</p><p style="margin-top:12px"><a href="https://sd-milk.com" target="_blank" rel="noopener" style="color:var(--blue)">sd-milk.com を確認する &#8594;</a></p></div>';
    return;
  }
  if(selected){
    l.textContent=selected+' 的演出';
    var evs=byDate[selected]||[];
    if(!evs.length){c.innerHTML='<p class="empty-msg">這天沒有演出</p>';return;}
    var g=document.createElement('div');g.className='articles-grid';
    evs.forEach(function(e){g.appendChild(makeEventCard(e));});c.appendChild(g);
  }else{
    l.textContent='全部演出';
    var sd=[...datesWithEvents].sort();
    sd.forEach(function(date){
      var grp=document.createElement('div');grp.className='date-group';
      var h=document.createElement('p');h.className='date-heading';h.textContent=date;grp.appendChild(h);
      var g=document.createElement('div');g.className='articles-grid';
      byDate[date].forEach(function(e){g.appendChild(makeEventCard(e));});grp.appendChild(g);c.appendChild(grp);
    });
  }
}
renderCalendar();renderEvents();
</script>
</body>
</html>'''


def build_page(template, nav_html, data_json, updated_at, extra_css=''):
    safe_json = data_json.replace('</script>', r'<\/script>')
    css = COMMON_CSS + extra_css
    sc_json = json.dumps(SOURCE_COLORS, ensure_ascii=False)
    return (template
            .replace('__CSS__', css)
            .replace('__STRIPE__', MEMBER_STRIPE_HTML)
            .replace('__NAV__', nav_html)
            .replace('__SOURCES_FOOTER__', make_sources_footer())
            .replace('__DATA__', safe_json)
            .replace('__SC__', sc_json)
            .replace('__UPDATED_AT__', updated_at))

def generate_news_html(articles, updated_at):
    return build_page(NEWS_TEMPLATE, NAV_NEWS,
                      json.dumps(articles, ensure_ascii=False), updated_at)

def generate_schedule_html(events, updated_at):
    return build_page(SCHEDULE_TEMPLATE, NAV_SCHEDULE,
                      json.dumps(events, ensure_ascii=False), updated_at,
                      extra_css=SCHEDULE_EXTRA_CSS)

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print('=== m!lk Fan Hub v4 ===')

    existing = load_json(ARTICLES_FILE)
    print(f'Loaded {len(existing)} existing articles')

    # Clean up previously saved articles that have no m!lk relevance
    # (keeps program-source articles, which are m!lk by definition)
    def is_milk_relevant(a):
        if a.get('source', '') in PROGRAM_SOURCES:
            return True
        combined = (a.get('title_ja', '') + a.get('title_zh', '') +
                    a.get('summary_ja', '') + a.get('summary_zh', '')).lower()
        return ('m!lk' in combined or 'ミルク' in combined or
                'milk' in combined or '佐野勇斗' in combined or
                '吉田仁人' in combined or '山中柔太朗' in combined or
                '塩﨑太智' in combined or '園田壮真' in combined)

    before = len(existing)
    existing = [a for a in existing if is_milk_relevant(a)]
    if len(existing) < before:
        print(f'Cleaned {before - len(existing)} irrelevant articles from archive')

    new_raw = []
    new_raw += fetch_sd_milk()
    new_raw += fetch_natalie()
    new_raw += fetch_oricon()
    new_raw += fetch_barks()
    new_raw += fetch_sanspo()
    new_raw += fetch_billboard()
    new_raw += fetch_musicvoice()
    new_raw += fetch_spice()
    new_raw += fetch_musicman()
    new_raw += fetch_thefirsttimes()
    new_raw += fetch_modelpress()
    new_raw += fetch_biteki()
    new_raw += fetch_mensnonno()
    new_raw += fetch_vogue_japan()
    new_raw += fetch_tvguide()
    new_raw += fetch_thetv()
    new_raw += fetch_ebidan()
    new_raw += fetch_known_programs()

    # Deduplicate within this run before translate/image fetch
    new_raw = dedup_by_url(new_raw)
    print(f'After dedup: {len(new_raw)} unique articles')

    if translator:
        print(f'Translating {len(new_raw)} articles...')
        for a in new_raw:
            a['title_zh']   = translate(a['title_ja'])
            a['summary_zh'] = translate(a['summary_ja']) if a['summary_ja'] else ''
    else:
        print('WARNING: DeepL not configured')

    print(f'Fetching images for {len(new_raw)} articles...')
    for a in new_raw:
        if a.get('url'):
            a['image'] = fetch_og_image(a['url'])
            time.sleep(0.3)

    merged, added = merge_by_url(existing, new_raw)
    print(f'News: +{added} new (total {len(merged)})')
    save_json(ARTICLES_FILE, merged)

    ex_schedule = load_json(SCHEDULE_FILE)
    new_events  = fetch_schedule()
    merged_sched, added_sched = merge_by_id(ex_schedule, new_events)
    print(f'Schedule: +{added_sched} new (total {len(merged_sched)})')
    save_json(SCHEDULE_FILE, merged_sched)

    now_jst = datetime.now(JST).strftime('%Y-%m-%d %H:%M')

    with open('index.html', 'w', encoding='utf-8') as f:
        f.write(generate_news_html(merged, now_jst))

    with open('schedule.html', 'w', encoding='utf-8') as f:
        f.write(generate_schedule_html(merged_sched, now_jst))

    print('Done!')


if __name__ == '__main__':
    main()

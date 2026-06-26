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
GOODS_FILE    = 'goods.json'
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
    """Strip CDN resize/crop parameters to get original-size image."""
    if not url:
        return url
    try:
        from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
        p = urlparse(url)

        # ── Query-param based resize (e.g. ?w=400&h=300&impolicy=resize) ──
        params = parse_qs(p.query, keep_blank_values=True)
        for k in ['impolicy', 'w', 'h', 'width', 'height', 'size',
                  'quality', 'q', 'resize', 'fit', 'crop', 'auto',
                  'tr', 'im', 'op_resize', 'op_crop']:
            params.pop(k, None)
        q = urlencode(params, doseq=True)

        # ── Path-based resize (e.g. /resize/400x300/, /400x300/, _400x300.) ──
        path = p.path
        path = re.sub(r'/resize/\d+x\d+/', '/', path)   # /resize/400x300/
        path = re.sub(r'/\d+x\d+/', '/', path)           # /400x300/
        path = re.sub(r'_\d+x\d+(\.\w+)$', r'\1', path) # _400x300.jpg
        path = re.sub(r'@\d+x\d+(\.\w+)$', r'\1', path) # @400x300.jpg

        return urlunparse((p.scheme, p.netloc, path, p.params, q, p.fragment))
    except Exception:
        return url

_ARTICLE_BODY_SELECTORS = [
    'article .article-body', 'article .entry-body', 'article .post-body',
    '.article-body', '.article-content', '.article-text', '.entry-content',
    '.news-body', '.news-content', '.post-content', '.story-body',
    '.content-body', '.detail-content', '.text-body',
    'article', '.main-content',
]
_SKIP_IMG_WORDS = {'logo', 'icon', 'banner', 'avatar', 'pixel', 'blank',
                   'button', 'ad_', 'advert', 'sprite', 'tracking'}

def fetch_article_data(url):
    """Fetch article page once → return (image_url, body_text_ja)."""
    image, body = '', ''
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        r.encoding = r.apparent_encoding or 'utf-8'
        soup = BeautifulSoup(r.text, 'html.parser')

        # ── Image ──
        for attrs in [
            {'property': 'og:image'}, {'name': 'og:image'},
            {'property': 'twitter:image'}, {'name': 'twitter:image'},
            {'name': 'twitter:image:src'},
        ]:
            el = soup.find('meta', attrs=attrs)
            if el and el.get('content'):
                img = el['content'].strip()
                if img.startswith('http') and not any(w in img.lower() for w in _SKIP_IMG_WORDS):
                    image = _clean_img_url(img)
                    break

        # ── Body text ──
        content_el = None
        for sel in _ARTICLE_BODY_SELECTORS:
            el = soup.select_one(sel)
            if el:
                # Remove unwanted sub-elements
                for tag in el.select('script,style,nav,aside,.sns,.share,.related,figure figcaption'):
                    tag.decompose()
                text = re.sub(r'\s+', ' ', el.get_text()).strip()
                if len(text) > 80:
                    content_el = text
                    break
        if not content_el:
            # Fallback: collect <p> blocks
            paras = [re.sub(r'\s+', ' ', p.get_text()).strip()
                     for p in soup.find_all('p') if len(p.get_text(strip=True)) > 40]
            content_el = ' '.join(paras) if paras else ''

        # Limit to ~350 chars to stay within DeepL free tier budget
        body = content_el[:350] if content_el else ''
    except Exception:
        pass
    return image, body

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

def _next_data_articles(soup, source='sd-milk.com 公式', base='https://sd-milk.com'):
    """Try to extract articles from __NEXT_DATA__ JSON on a Next.js page."""
    arts = []
    script = soup.find('script', {'id': '__NEXT_DATA__'})
    if not script or not script.string:
        return arts
    try:
        data = json.loads(script.string)
    except Exception:
        return arts

    def dig(obj, depth=0):
        if depth > 15 or not obj:
            return
        if isinstance(obj, list):
            for item in obj:
                dig(item, depth + 1)
        elif isinstance(obj, dict):
            title = (obj.get('title') or obj.get('name') or
                     obj.get('subject') or obj.get('headline') or '')
            if isinstance(title, str) and len(title) > 3:
                date_raw = str(obj.get('date') or obj.get('publishedAt') or
                               obj.get('created_at') or obj.get('startDate') or '')
                href = str(obj.get('url') or obj.get('link') or obj.get('href') or '')
                if href and not href.startswith('http'):
                    href = base + href
                arts.append(_make_article(source, title, href, date_raw[:20]))
            else:
                for v in obj.values():
                    dig(v, depth + 1)
    dig(data)
    return arts


def fetch_sd_milk():
    print('Fetching sd-milk.com...')
    arts = []
    urls = [
        'https://sd-milk.com/contents/news',
        'https://sd-milk.com/contents/information',
        'https://sd-milk.com/',
        'https://sd-milk.com/news/',
        'https://sd-milk.com/information/',
    ]
    for url in urls:
        try:
            r = requests.get(url, headers=HEADERS, timeout=12)
            if r.status_code != 200:
                continue
            r.encoding = 'utf-8'
            soup = BeautifulSoup(r.text, 'html.parser')

            # ── Try Next.js __NEXT_DATA__ first ──
            nd = _next_data_articles(soup)
            if nd:
                arts = nd[:10]
                print(f'  __NEXT_DATA__ at {url} -> {len(arts)} items')
                break

            # ── Fallback: CSS selectors ──
            for sel in ['.news-list li', '.info-list li', '.news li', '.information li',
                        '.post-list li', 'ul.list li', '.topics-list li', 'article', '.post']:
                items = soup.select(sel)
                if len(items) < 2:
                    continue
                for it in items[:10]:
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
                    print(f'  CSS [{sel}] at {url} -> {len(arts)} items')
                    break
        except Exception as e:
            print(f'  Error on {url}: {e}')
        if arts:
            break

    print(f'  -> {len(arts)} articles')
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

# Richer program info for the schedule page
# (name, type_badge, station, schedule_note, official_url, color)
SCHEDULE_PROGRAMS = [
    ('やってM!LK',
     'TV', 'TBS テレビ', '定期放送中',
     'https://www.tbs.co.jp/yatte_milk/', '#cc0000'),
    ('君の好きは無敵',
     'TV ドラマ', 'TBS テレビ 火曜22:00〜', '放送中',
     'https://www.tbs.co.jp/kiminosukihamuteki_tbs/', '#880033'),
    ('レコメン!',
     'ラジオ', '文化放送', '定期放送中',
     'https://www.joqr.co.jp/qr/program/reco/', '#1565c0'),
    ('イマドキッ ドゥフドゥフナイト',
     'ラジオ', 'MBSラジオ', '定期放送中',
     'https://www.mbs1179.com/imadoki/', '#7b1ea2'),
    ('トイ・ストーリー５',
     '映画', 'Disney', '劇場上映中',
     'https://www.disney.co.jp/movie/toy5', '#0057a8'),
    ('仮面ライダーゼッツ＆超宇宙刑事ギャバン',
     '映画', '2026年夏公開', '公開予定',
     'https://zeztz-gavan-26movie.com/', '#2c2c2c'),
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


# ── Goods fetcher ─────────────────────────────────────────────────────────────
# Each entry: dict with name, url, base, category
# category: '公式グッズ' | 'FC限定' | 'リリース'
GOODS_SOURCES = [
    {'name': 'sd-milk.com 公式', 'category': '公式グッズ',
     'urls': ['https://sd-milk.com/contents/goods',
              'https://sd-milk.com/goods',
              'https://sd-milk.com/shop'],
     'base': 'https://sd-milk.com'},
    {'name': 'FC限定 (STARDUST)', 'category': 'FC限定',
     'urls': ['https://store.plusmember.jp/stardustch/products/list.php?category_id=891'],
     'base': 'https://store.plusmember.jp'},
    {'name': 'UNIVERSAL MUSIC', 'category': '公式グッズ',
     'urls': ['https://www.universal-music.co.jp/m-lk/'],
     'base': 'https://www.universal-music.co.jp'},
    {'name': 'VICTOR STORE', 'category': '公式グッズ',
     'urls': ['https://victor-store.jp/search/?keyword=M%21LK',
              'https://victor-store.jp/search/?keyword=milk'],
     'base': 'https://victor-store.jp'},
]

# Generic selectors + EC-CUBE (plusmember.jp) selectors
GOODS_SELECTORS = [
    # EC-CUBE v3/v4 (plusmember.jp)
    '.ec-shelfGrid__item', '.ec-shelf__item',
    '.bloc_cart_items li', '.bloc_list_goods li',
    # Generic product grids
    '.goods-list li', '.goods-item', '.product-list li', '.product-item',
    '.shop-list li', '.item-list li', '.goods-wrap li',
    '.list li', 'ul.product li',
    '.product', '.goods',
]

def _make_good(source, title, url, image='', price='', date_raw='', category='公式グッズ'):
    return {
        'id':       make_id(url or title),
        'source':   source,
        'category': category,
        'title':    title[:120],
        'title_zh': '',
        'price':    price[:60],
        'url':      url,
        'image':    image,
        'date':     normalize_date(date_raw) if date_raw else '',
    }


def _scrape_goods_page(soup, source, base, category, seen_urls):
    """Extract goods from a page using CSS selectors."""
    batch = []
    for sel in GOODS_SELECTORS:
        items = soup.select(sel)
        if len(items) < 1:
            continue
        for it in items[:30]:
            a_tag = it.find('a', href=True)
            if not a_tag:
                continue
            href = a_tag['href']
            if not href.startswith('http'):
                href = base + href
            # Skip navigation/category links (very short text)
            raw_title = clean(a_tag.get_text())
            if len(raw_title) < 4:
                raw_title = clean(it.get_text())[:100]
            if len(raw_title) < 4 or href in seen_urls:
                continue
            seen_urls.add(href)

            img_el = it.find('img')
            img = ''
            if img_el:
                img = (img_el.get('data-src') or img_el.get('src') or '')
                if img and not img.startswith('http'):
                    img = base + img

            price_el = it.select_one(
                '.price, .ec-price__price, .ec-price, .cost, '
                '.normal_price, [class*=price], [class*=Price]')
            price = clean(price_el.get_text()) if price_el else ''

            date_el = it.select_one('time, .date, [datetime]')
            date_raw = (date_el.get('datetime', '') or
                        clean(date_el.get_text())) if date_el else ''

            batch.append(_make_good(source, raw_title, href, img, price, date_raw, category))
        if batch:
            print(f'    [{sel}] -> {len(batch)} items')
            break
    return batch


def fetch_goods():
    print('Fetching goods...')
    all_goods = []
    seen_urls = set()

    for src in GOODS_SOURCES:
        name     = src['name']
        category = src['category']
        base     = src['base']
        batch    = []

        for url in src['urls']:
            try:
                r = requests.get(url, headers=HEADERS, timeout=14)
                if r.status_code != 200:
                    print(f'  [{name}] {url} -> {r.status_code}')
                    continue
                r.encoding = r.apparent_encoding or 'utf-8'
                soup = BeautifulSoup(r.text, 'html.parser')

                # ── __NEXT_DATA__ (Next.js) ──
                script = soup.find('script', {'id': '__NEXT_DATA__'})
                if script and script.string:
                    try:
                        data = json.loads(script.string)
                        nd = []
                        def _dig(obj, depth=0):
                            if depth > 14 or not obj:
                                return
                            if isinstance(obj, list):
                                for item in obj: _dig(item, depth + 1)
                            elif isinstance(obj, dict):
                                t = (obj.get('title') or obj.get('name') or
                                     obj.get('goodsName') or obj.get('productName') or '')
                                if isinstance(t, str) and len(t) > 3:
                                    h = str(obj.get('url') or obj.get('link') or '')
                                    if h and not h.startswith('http'):
                                        h = base + h
                                    img = str(obj.get('image') or obj.get('thumbnail') or
                                              obj.get('imageUrl') or '')
                                    price = str(obj.get('price') or obj.get('priceText') or '')
                                    date = str(obj.get('date') or obj.get('releaseDate') or '')
                                    nd.append(_make_good(name, t, h, img, price, date, category))
                                else:
                                    for v in obj.values(): _dig(v, depth + 1)
                        _dig(data)
                        if nd:
                            print(f'  [{name}] __NEXT_DATA__ -> {len(nd)} goods')
                            batch = nd
                            break
                    except Exception as e:
                        print(f'  [{name}] __NEXT_DATA__ error: {e}')

                # ── CSS selectors ──
                if not batch:
                    batch = _scrape_goods_page(soup, name, base, category, seen_urls)
                    if batch:
                        print(f'  [{name}] CSS scrape -> {len(batch)} goods from {url}')
                        break

            except Exception as e:
                print(f'  [{name}] Error: {e}')

        print(f'  [{name}] -> {len(batch)} total')
        all_goods += batch

    print(f'Goods total: {len(all_goods)}')
    return all_goods

# ── Schedule fetcher ──────────────────────────────────────────────────────────

def _search_next_data(obj, results, depth=0):
    """Recursively find schedule-like objects inside __NEXT_DATA__ JSON."""
    if depth > 14 or not obj:
        return
    if isinstance(obj, list):
        for item in obj:
            _search_next_data(item, results, depth + 1)
    elif isinstance(obj, dict):
        date_val = (obj.get('date') or obj.get('startDate') or obj.get('start_date') or
                    obj.get('eventDate') or obj.get('event_date') or obj.get('day') or
                    obj.get('publishedAt') or obj.get('scheduled_at'))
        title_val = (obj.get('title') or obj.get('name') or obj.get('event_name') or
                     obj.get('content') or obj.get('subject'))
        if date_val and title_val and isinstance(title_val, str) and len(str(title_val)) > 3:
            date_str = normalize_date(str(date_val)[:20])
            if date_str[:4] >= '2024':
                href = str(obj.get('url') or obj.get('link') or obj.get('href') or '')
                if href and not href.startswith('http'):
                    href = 'https://sd-milk.com' + href
                results.append({
                    'id':       make_id(str(title_val)[:50] + date_str),
                    'date':     date_str,
                    'title':    str(title_val)[:120],
                    'details':  str(obj.get('venue') or obj.get('place') or
                                   obj.get('detail') or obj.get('description') or '')[:200],
                    'title_zh': '',
                    'url':      href,
                })
        else:
            for v in obj.values():
                _search_next_data(v, results, depth + 1)


def fetch_schedule():
    print('Fetching schedule from sd-milk.com...')
    events = []

    SCHED_KW = {'出演', 'ライブ', '公演', 'コンサート', 'イベント', '放送', '上映',
                '収録', 'レギュラー', 'live', 'concert', 'event', 'tour', 'fanmeet'}

    # ── Step 1: Try /contents/news + __NEXT_DATA__ (most reliable for Next.js) ──
    # News page often contains schedule announcements ("〇〇に出演します")
    news_arts = []
    for news_url in ['https://sd-milk.com/contents/news',
                     'https://sd-milk.com/contents/information']:
        try:
            r = requests.get(news_url, headers=HEADERS, timeout=15)
            if r.status_code != 200:
                continue
            r.encoding = 'utf-8'
            soup = BeautifulSoup(r.text, 'html.parser')
            nd = _next_data_articles(soup)
            if nd:
                # Filter for schedule-related items
                for a in nd:
                    combined = (a.get('title_ja', '') + a.get('summary_ja', '')).lower()
                    if any(kw in combined for kw in SCHED_KW) and a.get('date'):
                        date_str = normalize_date(a['date'])
                        if date_str[:4] >= '2025':
                            href = a.get('url', '')
                            news_arts.append({
                                'id':       make_id(a.get('title_ja', '')[:50] + date_str),
                                'date':     date_str,
                                'title':    a.get('title_ja', '')[:120],
                                'details':  '',
                                'title_zh': '',
                                'url':      href,
                            })
                if news_arts:
                    print(f'  /contents/news __NEXT_DATA__ -> {len(news_arts)} schedule items')
                    events = news_arts
                    break
        except Exception as e:
            print(f'  news_url error {news_url}: {e}')

    if events:
        # Skip remaining steps
        seen, unique = set(), []
        for e in events:
            if e['id'] not in seen:
                seen.add(e['id'])
                unique.append(e)
        unique.sort(key=lambda x: x['date'])
        print(f'  -> {len(unique)} events total')
        return unique

    # ── Step 2: Try calendar/schedule pages __NEXT_DATA__ ────────────────────
    next_urls = [
        'https://sd-milk.com/calendar',
        'https://sd-milk.com/contents/schedule',
        'https://sd-milk.com/schedule',
    ]
    for url in next_urls:
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            if r.status_code != 200:
                continue
            r.encoding = 'utf-8'
            soup = BeautifulSoup(r.text, 'html.parser')
            script = soup.find('script', {'id': '__NEXT_DATA__'})
            if script and script.string:
                data = json.loads(script.string)
                found = []
                _search_next_data(data, found)
                if found:
                    print(f'  __NEXT_DATA__ at {url} -> {len(found)} events')
                    events = found
                    break
        except Exception as e:
            print(f'  __NEXT_DATA__ error {url}: {e}')

    # ── Step 2: Try RSS feed ──────────────────────────────────────────────────
    if not events:
        for feed_url in ['https://sd-milk.com/feed', 'https://sd-milk.com/rss',
                         'https://sd-milk.com/feed.xml', 'https://sd-milk.com/atom.xml']:
            try:
                feed = feedparser.parse(feed_url)
                if not feed.entries:
                    continue
                batch = []
                for e in feed.entries[:20]:
                    title = e.get('title', '')
                    date_str = normalize_date(e.get('published', ''))
                    if date_str[:4] < '2024' or len(title) < 4:
                        continue
                    batch.append({
                        'id':       make_id(title[:50] + date_str),
                        'date':     date_str,
                        'title':    title[:120],
                        'details':  clean(e.get('summary', ''))[:200],
                        'title_zh': '',
                        'url':      e.get('link', ''),
                    })
                if batch:
                    print(f'  RSS {feed_url} -> {len(batch)} items')
                    events = batch
                    break
            except Exception as e:
                print(f'  RSS error {feed_url}: {e}')

    # ── Step 3: HTML scrape of information pages (strict date filter) ─────────
    if not events:
        info_urls = [
            'https://sd-milk.com/contents/information',
            'https://sd-milk.com/contents/news',
            'https://sd-milk.com/news',
            'https://sd-milk.com/information',
            'https://sd-milk.com/',
        ]
        strict_selectors = [
            '.info-list li', '.topics-list li', '.news-list li',
            '.information li', '.news li',
            '.schedule-list li', '.live-list li', '.event-list li',
            'article', '.post', '.item',
        ]
        for url in info_urls:
            try:
                r = requests.get(url, headers=HEADERS, timeout=15)
                if r.status_code != 200:
                    continue
                r.encoding = 'utf-8'
                soup = BeautifulSoup(r.text, 'html.parser')
                for sel in strict_selectors:
                    items = soup.select(sel)
                    if not items:
                        continue
                    batch = []
                    for it in items:
                        text = clean(it.get_text())
                        if not text or len(text) < 8:
                            continue
                        date_el = it.select_one('time, .date, .day, [datetime]')
                        if date_el:
                            date_str = normalize_date(
                                date_el.get('datetime', '') or clean(date_el.get_text()))
                        else:
                            m = re.search(r'(\d{4})[./年](\d{1,2})[./月](\d{1,2})', text)
                            if not m:
                                continue
                            date_str = (f"{m.group(1)}-{m.group(2).zfill(2)}"
                                        f"-{m.group(3).zfill(2)}")
                        if date_str[:4] < '2025':
                            continue
                        a_tag = it.find('a', href=True)
                        href = a_tag['href'] if a_tag else ''
                        if href and not href.startswith('http'):
                            href = 'https://sd-milk.com' + href
                        lines = [l.strip() for l in text.splitlines() if l.strip()]
                        title   = lines[0][:120] if lines else text[:120]
                        details = ' / '.join(lines[1:3]) if len(lines) > 1 else ''
                        batch.append({
                            'id':       make_id(text[:50] + date_str),
                            'date':     date_str, 'title': title,
                            'details':  details[:200], 'title_zh': '', 'url': href,
                        })
                    if batch:
                        print(f'  HTML {url} [{sel}] -> {len(batch)} items')
                        events = batch
                        break
            except Exception as e:
                print(f'  HTML error {url}: {e}')
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
    'VICTOR STORE':         '#e53935',
    'UNIVERSAL MUSIC':      '#0d47a1',
    'FC限定 (STARDUST)':    '#9c27b0',
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
.hero-img{width:100%;max-height:340px;object-fit:cover;object-position:center top;display:block;}
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
    return ''

_NAV_LINKS = [
    ('index.html',    'ニュース'),
    ('schedule.html', '演出情報'),
    ('goods.html',    '商品'),
    ('album.html',    'アルバム'),
    ('sources.html',  '來源'),
]

def _build_nav(active_href):
    parts = []
    for href, label in _NAV_LINKS:
        cls = 'nav-link nav-active' if href == active_href else 'nav-link'
        parts.append(f'<a href="{href}" class="{cls}">{label}</a>')
    return ''.join(parts)

NAV_NEWS     = _build_nav('index.html')
NAV_SCHEDULE = _build_nav('schedule.html')
NAV_GOODS    = _build_nav('goods.html')
NAV_ALBUM    = _build_nav('album.html')
NAV_SOURCES  = _build_nav('sources.html')

NEWS_TEMPLATE = r'''<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>M!LK Fan Hub — ニュース</title>
<style>__CSS__</style>
</head>
<body>
<img src="DSC_3085.jpg" alt="M!LK" class="hero-img">
<header class="site-header">
  __STRIPE__
  <div class="wordmark">M<span class="bang">!</span>LK</div>
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
  <p>M!LK Fan Hub &nbsp;&middot;&nbsp; 非公式 / 非官方 &nbsp;&middot;&nbsp; 毎日 09:00 JST 自動更新</p>

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

SCHEDULE_TEMPLATE = '''<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>M!LK Fan Hub &mdash; 演出情報</title>
<style>
__CSS__
.sched-wrap{max-width:1060px;margin:0 auto;padding:24px 16px 72px;}
.sched-section{margin-bottom:36px;}
.sched-section-title{font-size:.72rem;letter-spacing:.3em;text-transform:uppercase;color:var(--blue);padding-left:10px;border-left:3px solid var(--blue);margin-bottom:16px;}
.today-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:12px;}
.today-card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:14px;display:flex;flex-direction:column;gap:7px;box-shadow:var(--shadow);}
.today-card .tc-source{font-size:.65rem;font-weight:700;color:#fff;padding:2px 8px;border-radius:8px;align-self:flex-start;}
.today-card .tc-title{font-size:.88rem;font-weight:700;color:var(--blue);line-height:1.4;}
.today-card .tc-ja{font-size:.75rem;color:#4a5568;line-height:1.5;}
.today-card .tc-link{margin-top:auto;font-size:.75rem;color:var(--blue);text-decoration:none;font-weight:600;}
.today-card .tc-link:hover{text-decoration:underline;}
.today-empty{font-size:.82rem;color:var(--muted);padding:14px 0;}
.prog-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:13px;}
.prog-card{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:16px;display:flex;flex-direction:column;gap:8px;box-shadow:var(--shadow);transition:transform .18s,box-shadow .18s;}
.prog-card:hover{transform:translateY(-3px);box-shadow:var(--shadow-hover);}
.prog-badge{font-size:.63rem;font-weight:700;color:#fff;padding:3px 9px;border-radius:9px;align-self:flex-start;letter-spacing:.04em;}
.prog-name{font-size:.95rem;font-weight:800;color:var(--text);line-height:1.3;}
.prog-station{font-size:.73rem;color:var(--muted);}
.prog-schedule{font-size:.7rem;color:var(--blue2);font-weight:600;}
.prog-link{display:inline-block;margin-top:6px;padding:6px 14px;border-radius:18px;background:var(--blue);color:#fff;font-size:.72rem;font-weight:700;text-decoration:none;align-self:flex-start;transition:opacity .2s;}
.prog-link:hover{opacity:.82;}
.official-cal{display:flex;gap:10px;flex-wrap:wrap;margin-top:6px;}
.cal-btn{display:inline-flex;align-items:center;padding:9px 20px;border-radius:22px;font-size:.82rem;font-weight:700;text-decoration:none;transition:opacity .2s;}
.cal-btn:hover{opacity:.82;}
.cal-btn.primary{background:var(--blue);color:#fff;}
.cal-btn.secondary{background:var(--surface);color:var(--blue);border:1px solid var(--blue);}
</style>
</head>
<body>
<header class="site-header">
  __STRIPE__
  <div class="wordmark">M<span class="bang">!</span>LK</div>
  <div class="hearts">&#9825; &middot; &#9825; &middot; &#9825;</div>
  <p class="tagline">Fan Hub &middot; 演出情報</p>
  <nav class="page-nav">__NAV__</nav>
  <p class="update-time">最後更新：__UPDATED_AT__ JST</p>
</header>
<div class="sched-wrap">

  <div class="sched-section">
    <p class="sched-section-title">&#128197; 今日の関連ニュース (__TODAY__)</p>
    __TODAY_NEWS__
  </div>

  <div class="sched-section">
    <p class="sched-section-title">&#127914; 現在の出演番組・作品</p>
    <div class="prog-grid">__PROG_CARDS__</div>
  </div>

  <div class="sched-section">
    <p class="sched-section-title">&#128198; 公式スケジュール</p>
    <p style="font-size:.8rem;color:var(--muted);margin-bottom:12px;">
      最新の演出情報は公式サイトでご確認ください。
    </p>
    <div class="official-cal">
      <a class="cal-btn primary" href="https://sd-milk.com/calendar"
         target="_blank" rel="noopener">公式カレンダー &#8599;</a>
      <a class="cal-btn secondary" href="https://sd-milk.com/contents/schedule"
         target="_blank" rel="noopener">スケジュール一覧 &#8599;</a>
    </div>
  </div>

</div>
<footer>
  <p>M!LK Fan Hub &nbsp;&middot;&nbsp; 非公式ファンサイト &nbsp;&middot;&nbsp; 毎日 09:00 JST 自動更新</p>

</footer>
</body>
</html>'''


GOODS_TEMPLATE = '''<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>M!LK Fan Hub &mdash; 商品・リリース</title>
<style>
__CSS__
.goods-wrap{max-width:1060px;margin:0 auto;padding:24px 16px 72px;}
.goods-section{margin-bottom:42px;}
.section-label{font-size:.72rem;letter-spacing:.3em;text-transform:uppercase;color:var(--blue);padding-left:10px;border-left:3px solid var(--blue);margin-bottom:16px;display:flex;align-items:center;gap:8px;}
.section-label .cnt{font-size:.68rem;color:var(--muted);letter-spacing:0;font-weight:400;}
.goods-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:14px;}
.release-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:14px;}
.goods-card,.release-card{background:var(--card);border:1px solid var(--border);border-radius:14px;display:flex;flex-direction:column;box-shadow:var(--shadow);transition:transform .2s,box-shadow .2s,border-color .2s;}
.goods-card:hover,.release-card:hover{transform:translateY(-3px);box-shadow:var(--shadow-hover);border-color:#b4caff;}
.goods-img-wrap{overflow:hidden;border-radius:14px 14px 0 0;aspect-ratio:1/1;background:var(--surface);}
.goods-img-wrap img{width:100%;height:100%;object-fit:cover;display:block;}
.release-img-wrap{overflow:hidden;border-radius:14px 14px 0 0;aspect-ratio:1/1;background:var(--surface);}
.release-img-wrap img{width:100%;height:100%;object-fit:cover;display:block;}
.goods-img-none,.release-img-none{aspect-ratio:1/1;display:flex;align-items:center;justify-content:center;font-size:2.5rem;background:var(--surface);border-radius:14px 14px 0 0;color:var(--border);}
.goods-body,.release-body{padding:11px;display:flex;flex-direction:column;gap:5px;flex:1;}
.cat-badge{font-size:.6rem;font-weight:700;color:#fff;padding:2px 7px;border-radius:7px;align-self:flex-start;letter-spacing:.04em;}
.goods-title-zh,.release-title-zh{font-size:.87rem;font-weight:700;color:var(--blue);line-height:1.4;}
.goods-title,.release-title{font-size:.75rem;color:#4a5568;line-height:1.5;}
.goods-price{font-size:.84rem;font-weight:700;color:var(--text);}
.release-type{font-size:.68rem;color:var(--blue2);font-weight:600;}
.goods-date,.release-date{font-size:.64rem;color:var(--muted);}
.goods-buy,.release-link{display:inline-block;margin-top:auto;padding:6px 13px;background:var(--blue);color:#fff;border-radius:16px;font-size:.73rem;font-weight:700;text-decoration:none;align-self:flex-start;transition:opacity .2s;}
.goods-buy:hover,.release-link:hover{opacity:.82;}
.goods-empty{text-align:center;padding:30px 20px;color:var(--muted);font-size:.85rem;}
.goods-empty a{color:var(--blue);}
.fc-notice{font-size:.72rem;color:var(--muted);margin-bottom:12px;}
.fc-notice a{color:var(--blue);text-decoration:none;}
.fc-notice a:hover{text-decoration:underline;}
</style>
</head>
<body>
<header class="site-header">
  __STRIPE__
  <div class="wordmark">M<span class="bang">!</span>LK</div>
  <div class="hearts">&#9825; &middot; &#9825; &middot; &#9825;</div>
  <p class="tagline">Fan Hub &middot; 商品・リリース</p>
  <nav class="page-nav">__NAV__</nav>
  <p class="update-time">最後更新：__UPDATED_AT__ JST</p>
</header>
<div class="goods-wrap">
__SECTIONS__
</div>
<footer>
  <p>M!LK Fan Hub &nbsp;&middot;&nbsp; 非公式ファンサイト &nbsp;&middot;&nbsp; 毎日 09:00 JST 自動更新</p>

</footer>
</body>
</html>'''

RELEASE_KW = {'発売', 'リリース', 'アルバム', 'シングル', '配信', 'mv', 'music video',
              'cd', 'blu-ray', 'dvd', '解禁', '先行', 'digital', '収録', '特典'}

CAT_COLORS = {
    'リリース':   '#d4006e',
    'FC限定':     '#9c27b0',
    '公式グッズ': '#1565c0',
}


def _goods_card_html(g, esc):
    color = CAT_COLORS.get(g.get('category', ''), SOURCE_COLORS.get(g.get('source', ''), '#888'))
    cat   = esc(g.get('category', g.get('source', '')))
    zh    = esc(g.get('title_zh', ''))
    ja    = esc(g.get('title', ''))
    price = esc(g.get('price', ''))
    date  = esc(g.get('date', ''))
    url   = esc(g.get('url', '#'))
    img   = g.get('image', '')
    img_block = (
        f'<div class="goods-img-wrap"><img src="{esc(img)}" alt="" loading="lazy" '
        f'onerror="this.parentElement.innerHTML=\'&#127873;\'"></div>'
        if img else '<div class="goods-img-none">&#127873;</div>'
    )
    return (
        f'<div class="goods-card">{img_block}'
        f'<div class="goods-body">'
        f'<span class="cat-badge" style="background:{color}">{cat}</span>'
        + (f'<p class="goods-title-zh">{zh}</p>' if zh else '') +
        f'<p class="goods-title">{ja}</p>'
        + (f'<p class="goods-price">{price}</p>' if price else '')
        + (f'<p class="goods-date">{date}</p>' if date else '') +
        f'<a class="goods-buy" href="{url}" target="_blank" rel="noopener">詳細・購入 &rarr;</a>'
        f'</div></div>'
    )


def _release_card_html(a, esc):
    color  = SOURCE_COLORS.get(a.get('source', ''), '#d4006e')
    zh     = esc(a.get('title_zh', ''))
    ja     = esc(a.get('title_ja', ''))
    date   = esc(a.get('date', ''))
    url    = esc(a.get('url', '#'))
    source = esc(a.get('source', ''))
    img    = a.get('image', '')
    img_block = (
        f'<div class="release-img-wrap"><img src="{esc(img)}" alt="" loading="lazy" '
        f'onerror="this.parentElement.innerHTML=\'&#127926;\'"></div>'
        if img else '<div class="release-img-none">&#127926;</div>'
    )
    return (
        f'<div class="release-card">{img_block}'
        f'<div class="release-body">'
        f'<span class="cat-badge" style="background:{color}">{source}</span>'
        + (f'<p class="release-title-zh">{zh}</p>' if zh else '') +
        f'<p class="release-title">{ja}</p>'
        + (f'<p class="release-date">{date}</p>' if date else '') +
        f'<a class="release-link" href="{url}" target="_blank" rel="noopener">詳細を見る &rarr;</a>'
        f'</div></div>'
    )


def generate_goods_html(goods, articles, updated_at):
    def esc(s):
        return (str(s).replace('&', '&amp;').replace('<', '&lt;')
                      .replace('>', '&gt;').replace('"', '&quot;'))

    sections = []

    # ── Section 1: FC限定 ─────────────────────────────────────────────────────
    # plusmember.jp requires FC member login — cannot scrape automatically.
    # Show fixed link cards for known FC shop categories.
    FC_LINKS = [
        ('PREMIUM MILK FC限定ショップ',
         'https://store.plusmember.jp/stardustch/products/list.php?category_id=891',
         '※ PREMIUM MILK 会員限定。ログイン後に購入可能。'),
        ('FC限定 CD セット（アクスタ付）',
         'https://victor-store.jp/',
         'VICTOR ONLINE STORE でFC会員向けセットを取扱中。'),
    ]
    fc_link_cards = []
    for fc_title, fc_url, fc_note in FC_LINKS:
        fc_link_cards.append(
            f'<div class="goods-card">'
            f'<div class="goods-img-none">&#11088;</div>'
            f'<div class="goods-body">'
            f'<span class="cat-badge" style="background:#9c27b0">FC限定</span>'
            f'<p class="goods-title-zh">{esc(fc_title)}</p>'
            f'<p class="goods-title" style="color:var(--muted);font-size:.7rem">{esc(fc_note)}</p>'
            f'<a class="goods-buy" href="{esc(fc_url)}" target="_blank" rel="noopener">'
            f'ショップへ &rarr;</a>'
            f'</div></div>'
        )
    sections.append(
        f'<div class="goods-section">'
        f'<p class="section-label">&#11088; FC限定商品</p>'
        f'<p class="fc-notice">PREMIUM MILK 会員限定商品です。'
        f'自動取得はログインが必要なため、公式ショップリンクを掲載しています。</p>'
        f'<div class="goods-grid">{"".join(fc_link_cards)}</div>'
        f'</div>'
    )

    # ── Section 2: 公式グッズ ─────────────────────────────────────────────────
    official = [g for g in goods if g.get('category') != 'FC限定']
    # Always show official shop links even if scraping returned nothing
    OFFICIAL_SHOP_LINKS = [
        ('sd-milk.com 公式グッズ',   'https://sd-milk.com/contents/goods',      '#d4006e'),
        ('VICTOR ONLINE STORE',      'https://victor-store.jp/search/?keyword=M%21LK', '#e53935'),
        ('UNIVERSAL MUSIC SHOP',     'https://www.universal-music.co.jp/m-lk/', '#0d47a1'),
    ]
    if official:
        cards = ''.join(_goods_card_html(g, esc) for g in official)
        sections.append(
            f'<div class="goods-section">'
            f'<p class="section-label">&#128722; 公式グッズ'
            f'<span class="cnt">({len(official)})</span></p>'
            f'<div class="goods-grid">{cards}</div>'
            f'</div>'
        )
    else:
        # Fallback: show shop link cards
        link_cards = []
        for shop_name, shop_url, shop_color in OFFICIAL_SHOP_LINKS:
            link_cards.append(
                f'<div class="goods-card">'
                f'<div class="goods-img-none">&#128722;</div>'
                f'<div class="goods-body">'
                f'<span class="cat-badge" style="background:{shop_color}">公式グッズ</span>'
                f'<p class="goods-title-zh">{esc(shop_name)}</p>'
                f'<a class="goods-buy" href="{esc(shop_url)}" target="_blank" rel="noopener">'
                f'ショップへ &rarr;</a>'
                f'</div></div>'
            )
        sections.append(
            f'<div class="goods-section">'
            f'<p class="section-label">&#128722; 公式グッズ</p>'
            f'<p class="fc-notice" style="color:var(--muted)">商品情報は各公式ショップでご確認ください。</p>'
            f'<div class="goods-grid">{"".join(link_cards)}</div>'
            f'</div>'
        )

    return (GOODS_TEMPLATE
            .replace('__CSS__', COMMON_CSS)
            .replace('__STRIPE__', MEMBER_STRIPE_HTML)
            .replace('__NAV__', NAV_GOODS)
            .replace('__SOURCES_FOOTER__', make_sources_footer())
            .replace('__UPDATED_AT__', updated_at)
            .replace('__SECTIONS__', '\n'.join(sections)))


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

SOURCES_TEMPLATE = '''<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>M!LK Fan Hub &mdash; 來源一覧</title>
<style>
__CSS__
.src-wrap{max-width:800px;margin:0 auto;padding:32px 20px 80px;}
.src-intro{font-size:.82rem;color:var(--muted);margin-bottom:28px;line-height:1.7;}
.src-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:10px;}
.src-card{display:flex;align-items:center;gap:12px;background:var(--card);border:1px solid var(--border);border-radius:10px;padding:12px 14px;text-decoration:none;color:var(--text);transition:transform .15s,box-shadow .15s,border-color .15s;}
.src-card:hover{transform:translateY(-2px);box-shadow:var(--shadow-hover);border-color:#b4caff;}
.src-dot{width:12px;height:12px;border-radius:50%;flex-shrink:0;}
.src-name{font-size:.82rem;font-weight:700;flex:1;}
.src-arrow{font-size:.75rem;color:var(--muted);}
.src-section-label{font-size:.72rem;letter-spacing:.3em;text-transform:uppercase;color:var(--blue);padding-left:10px;border-left:3px solid var(--blue);margin:28px 0 14px;}
</style>
</head>
<body>
<header class="site-header">
  __STRIPE__
  <div class="wordmark">M<span class="bang">!</span>LK</div>
  <div class="hearts">&#9825; &middot; &#9825; &middot; &#9825;</div>
  <p class="tagline">Fan Hub &middot; 來源一覧</p>
  <nav class="page-nav">__NAV__</nav>
  <p class="update-time">最後更新：__UPDATED_AT__ JST</p>
</header>
<div class="src-wrap">
  <p class="src-intro">
    本站新聞從以下 __COUNT__ 個來源自動彙整，每日 09:00 JST 更新。<br>
    點擊可前往各來源官方網站。
  </p>
  <p class="src-section-label">&#128240; 新聞・エンタメ</p>
  <div class="src-grid">__NEWS_SOURCES__</div>
  <p class="src-section-label">&#127902; 番組・作品</p>
  <div class="src-grid">__PROGRAM_SOURCES__</div>
</div>
<footer>
  <p>M!LK Fan Hub &nbsp;&middot;&nbsp; 非公式ファンサイト &nbsp;&middot;&nbsp; 毎日 09:00 JST 自動更新</p>
</footer>
</body>
</html>'''

NEWS_SOURCE_NAMES = {
    'sd-milk.com 公式', 'Natalie Music', 'Oricon', 'BARKS', 'SANSPO',
    'Billboard Japan', 'Musicvoice', 'SPICE', 'Musicman', 'The First Times',
    'Modelpress', '美的.com', "MEN'S NON-NO", 'VOGUE JAPAN',
    'TVガイドWeb', 'WEBザテレビジョン', 'ebidan.jp',
}

def generate_sources_html(updated_at):
    def card(name, url, color):
        return (
            f'<a class="src-card" href="{url}" target="_blank" rel="noopener">'
            f'<span class="src-dot" style="background:{color}"></span>'
            f'<span class="src-name">{name}</span>'
            f'<span class="src-arrow">&#8599;</span>'
            f'</a>'
        )

    news_cards    = []
    program_cards = []
    for name, color in SOURCE_COLORS.items():
        url = SOURCE_URLS.get(name, '#')
        if name in NEWS_SOURCE_NAMES:
            news_cards.append(card(name, url, color))
        else:
            program_cards.append(card(name, url, color))

    total = len(news_cards) + len(program_cards)
    return (SOURCES_TEMPLATE
            .replace('__CSS__', COMMON_CSS)
            .replace('__STRIPE__', MEMBER_STRIPE_HTML)
            .replace('__NAV__', NAV_SOURCES)
            .replace('__UPDATED_AT__', updated_at)
            .replace('__COUNT__', str(total))
            .replace('__NEWS_SOURCES__', ''.join(news_cards))
            .replace('__PROGRAM_SOURCES__', ''.join(program_cards)))


ALBUM_CHECKER_CSS = '''
:root{--ground:#FAFAF8;--surface:#FFFFFF;--text:#1C1B2E;--text-muted:#6B6A82;--accent:#D4477B;--accent-soft:#FAEEF4;--blue2:#7B9EC4;--blue-soft:#EDF3FA;--gold:#B8922A;--gold-soft:#FBF4E6;--border:#E6E4EE;--radius:14px;--radius-sm:8px;}
.checker-wrap{max-width:960px;margin:0 auto;padding:0 20px 80px;}
.album-hero{background:var(--text);color:#fff;padding:42px 24px 36px;text-align:center;position:relative;overflow:hidden;}
.album-hero::before{content:'';position:absolute;inset:0;background:repeating-conic-gradient(rgba(255,255,255,.03) 0deg 45deg,transparent 45deg 90deg);background-size:40px 40px;pointer-events:none;}
.hero-group-badge{display:inline-block;font-size:11px;font-weight:700;letter-spacing:.18em;text-transform:uppercase;color:var(--accent);border:1.5px solid var(--accent);border-radius:4px;padding:3px 10px;margin-bottom:18px;}
.hero-label{font-size:12px;font-weight:600;letter-spacing:.22em;text-transform:uppercase;color:rgba(255,255,255,.45);margin-bottom:8px;}
.hero-title{font-size:clamp(24px,6vw,48px);font-weight:800;line-height:1.1;margin-bottom:5px;}
.hero-title em{font-style:normal;color:var(--accent);}
.hero-subtitle{font-size:13px;color:rgba(255,255,255,.5);margin-bottom:24px;letter-spacing:.04em;}
.hero-meta{display:inline-flex;gap:24px;border-top:1px solid rgba(255,255,255,.12);padding-top:18px;}
.hero-meta-item{text-align:center;}
.hero-meta-item .val{display:block;font-size:18px;font-weight:700;color:#fff;margin-bottom:2px;}
.hero-meta-item .key{font-size:11px;color:rgba(255,255,255,.4);letter-spacing:.08em;}
.chk-section{padding:40px 0 0;}
.chk-section-head{display:flex;align-items:baseline;gap:10px;margin-bottom:20px;border-bottom:2px solid var(--text);padding-bottom:9px;}
.chk-section-title{font-size:16px;font-weight:800;}
.chk-section-en{font-size:11px;font-weight:700;letter-spacing:.16em;text-transform:uppercase;color:var(--text-muted);}
.checker-layout{display:grid;grid-template-columns:1fr 1fr;gap:20px;align-items:start;}
.wants-panel{display:flex;flex-direction:column;gap:6px;}
.wants-intro{font-size:13px;color:var(--text-muted);margin-bottom:8px;line-height:1.5;}
.want-item{display:flex;align-items:center;gap:12px;background:var(--surface);border:1.5px solid var(--border);border-radius:var(--radius-sm);padding:11px 14px;cursor:pointer;transition:border-color .15s,background .15s;user-select:none;}
.want-item:hover{border-color:var(--accent);background:var(--accent-soft);}
.want-item input[type=checkbox]{display:none;}
.want-check{width:20px;height:20px;border:2px solid var(--border);border-radius:5px;flex-shrink:0;position:relative;transition:border-color .15s,background .15s;}
.want-item:has(input:checked) .want-check{border-color:var(--accent);background:var(--accent);}
.want-item:has(input:checked) .want-check::after{content:'';position:absolute;top:2px;left:5px;width:6px;height:9px;border:2px solid #fff;border-top:none;border-left:none;transform:rotate(45deg);}
.want-item:has(input:checked){border-color:var(--accent);background:var(--accent-soft);}
.want-info{display:flex;flex-direction:column;gap:2px;}
.want-name{font-size:13px;font-weight:700;}
.want-tag{font-size:11px;color:var(--text-muted);font-weight:500;}
.want-tag.fc{color:var(--gold);}
.sub-opt{max-height:0;overflow:hidden;transition:max-height .25s ease,margin .2s ease;margin-left:6px;}
.sub-opt.open{max-height:320px;margin-bottom:2px;}
.sub-opt select{width:100%;padding:8px 32px 8px 12px;border:1.5px solid var(--border);border-radius:var(--radius-sm);background:var(--surface) url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='8' viewBox='0 0 12 8'%3E%3Cpath d='M1 1l5 5 5-5' stroke='%236B6A82' stroke-width='1.5' fill='none' stroke-linecap='round'/%3E%3C/svg%3E") no-repeat right 12px center;font-size:13px;color:var(--text);font-family:inherit;appearance:none;-webkit-appearance:none;}
.sub-check-grid{display:grid;grid-template-columns:1fr 1fr;gap:5px;padding:6px 4px 4px;}
.sub-check-group-label{grid-column:1/-1;font-size:10px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:var(--text-muted);padding:4px 2px 2px;}
.sub-check-item{display:flex;align-items:center;gap:7px;font-size:12px;font-weight:600;cursor:pointer;padding:6px 9px;border-radius:6px;border:1.5px solid var(--border);background:var(--surface);user-select:none;transition:border-color .12s,background .12s;line-height:1.3;}
.sub-check-item:hover{border-color:var(--accent);background:var(--accent-soft);}
.sub-check-item input[type=checkbox]{display:none;}
.sub-check-dot{width:14px;height:14px;border:2px solid var(--border);border-radius:3px;flex-shrink:0;position:relative;transition:border-color .12s,background .12s;}
.sub-check-item:has(input:checked) .sub-check-dot{border-color:var(--accent);background:var(--accent);}
.sub-check-item:has(input:checked) .sub-check-dot::after{content:'';position:absolute;top:1px;left:3px;width:5px;height:7px;border:1.5px solid #fff;border-top:none;border-left:none;transform:rotate(45deg);}
.sub-check-item:has(input:checked){border-color:var(--accent);background:var(--accent-soft);}
.reset-btn{margin-top:8px;align-self:flex-start;padding:7px 16px;background:transparent;border:1.5px solid var(--border);border-radius:var(--radius-sm);font-size:12px;color:var(--text-muted);cursor:pointer;font-family:inherit;transition:border-color .15s,color .15s;}
.reset-btn:hover{border-color:var(--text);color:var(--text);}
.result-panel{position:sticky;top:20px;background:var(--text);color:#fff;border-radius:var(--radius);padding:22px;min-height:220px;display:flex;flex-direction:column;gap:14px;}
.result-empty{flex:1;display:flex;align-items:center;justify-content:center;color:rgba(255,255,255,.3);font-size:13px;text-align:center;padding:20px;}
.res-plan-header{background:rgba(212,71,123,.15);border:1px solid rgba(212,71,123,.3);border-radius:9px;padding:14px 16px;display:flex;flex-direction:column;gap:3px;}
.res-plan-label{font-size:10px;font-weight:700;letter-spacing:.16em;text-transform:uppercase;color:rgba(212,71,123,.85);}
.res-plan-price{font-size:28px;font-weight:800;color:var(--accent);line-height:1.1;letter-spacing:-.02em;}
.res-plan-price small{font-size:13px;font-weight:500;color:rgba(255,255,255,.4);letter-spacing:0;margin-left:2px;}
.res-plan-note{font-size:12px;color:rgba(255,255,255,.5);margin-top:3px;line-height:1.45;}
.res-sec{display:flex;flex-direction:column;gap:7px;}
.res-label{font-size:10px;font-weight:700;letter-spacing:.16em;text-transform:uppercase;color:rgba(255,255,255,.35);margin-bottom:1px;}
.res-item{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:9px 12px;background:rgba(255,255,255,.07);border-radius:7px;}
.res-item-name{font-size:13px;font-weight:700;display:flex;align-items:center;gap:6px;flex-wrap:wrap;line-height:1.35;}
.res-item-right{display:flex;align-items:center;gap:8px;flex-shrink:0;}
.res-qty{font-size:12px;color:rgba(255,255,255,.35);white-space:nowrap;}
.res-item-price{font-size:15px;font-weight:800;color:var(--accent);white-space:nowrap;}
.res-badge{font-size:10px;padding:2px 6px;border-radius:4px;font-weight:700;background:rgba(212,71,123,.25);color:#FFB0CC;}
.res-badge-warn{font-size:10px;padding:2px 6px;border-radius:4px;font-weight:700;background:rgba(184,146,42,.3);color:#F5D47A;}
.res-total{font-size:16px;font-weight:800;color:var(--accent);text-align:right;padding-top:6px;border-top:1px solid rgba(255,255,255,.08);}
.res-store{background:rgba(255,255,255,.06);border-radius:7px;padding:11px 13px;display:flex;flex-direction:column;gap:3px;}
.res-store-label{font-size:10px;font-weight:700;letter-spacing:.14em;text-transform:uppercase;color:rgba(255,255,255,.3);}
.res-store-val{font-size:13px;font-weight:700;}
.res-store-extra{font-size:12px;color:rgba(255,255,255,.5);margin-top:2px;}
.res-tok{display:flex;justify-content:space-between;align-items:baseline;gap:8px;font-size:12px;padding:5px 0;border-bottom:1px solid rgba(255,255,255,.06);}
.res-tok:last-child{border-bottom:none;}
.res-tok-name{font-weight:600;}
.res-tok-name.hi{color:var(--accent);}
.res-tok-desc{color:rgba(255,255,255,.4);font-size:11px;text-align:right;}
.res-warn{background:rgba(184,146,42,.12);border:1px solid rgba(184,146,42,.25);border-radius:7px;padding:10px 12px;font-size:12px;line-height:1.55;color:#F5D47A;}
.res-deadline{font-size:11px;color:rgba(255,255,255,.35);text-align:center;padding-top:2px;}
.editions-top{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px;}
.editions-solo{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;}
.holo-wrap{border-radius:calc(var(--radius) + 2px);padding:2px;background:linear-gradient(125deg,#ff6b9d,#c26bfd,#6bb5ff,#6bffcd,#fff36b,#ffa66b,#ff6b9d);background-size:300% 300%;animation:holo-flow 6s linear infinite;box-shadow:0 4px 20px rgba(212,71,123,.15);}
@keyframes holo-flow{0%{background-position:0% 50%;filter:hue-rotate(0deg);}50%{background-position:100% 50%;filter:hue-rotate(180deg);}100%{background-position:0% 50%;filter:hue-rotate(360deg);}}
@media (prefers-reduced-motion:reduce){.holo-wrap{animation:none;background:linear-gradient(125deg,#ff6b9d,#6bb5ff,#6bffcd);}}
.plain-wrap{border-radius:calc(var(--radius) + 2px);padding:2px;background:var(--border);}
.card{background:var(--surface);border-radius:var(--radius);padding:18px;height:100%;display:flex;flex-direction:column;gap:11px;}
.card-badge{display:inline-flex;align-items:center;font-size:10px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;padding:3px 8px;border-radius:4px;align-self:flex-start;}
.badge-standard{background:var(--border);color:var(--text-muted);}
.badge-limited{background:var(--gold-soft);color:var(--gold);}
.badge-solo{background:var(--accent-soft);color:var(--accent);}
.card-name{font-size:15px;font-weight:800;line-height:1.25;}
.card-name-en{font-size:11px;color:var(--text-muted);font-weight:500;margin-top:2px;}
.card-price{font-size:22px;font-weight:800;color:var(--accent);letter-spacing:-.02em;}
.card-price span{font-size:13px;font-weight:500;color:var(--text-muted);margin-left:2px;}
.card-divider{border:none;border-top:1px solid var(--border);}
.card-row{display:flex;flex-direction:column;gap:5px;}
.card-row-label{font-size:10px;font-weight:700;letter-spacing:.14em;text-transform:uppercase;color:var(--text-muted);}
.card-row-val{font-size:13px;line-height:1.5;}
.tokuten-tag{display:inline-block;font-size:12px;font-weight:600;background:var(--accent-soft);color:var(--accent);border-radius:var(--radius-sm);padding:4px 9px;margin:2px 2px 2px 0;line-height:1.4;}
.member-name{font-size:12px;font-weight:700;color:var(--accent);}
.tokuten-block{display:flex;flex-direction:column;gap:16px;}
.tokuten-row{background:var(--surface);border:1.5px solid var(--border);border-radius:var(--radius);overflow:hidden;}
.tokuten-row-head{display:grid;grid-template-columns:200px 1fr;align-items:stretch;}
.tokuten-row-type{background:var(--text);color:#fff;padding:16px 18px;display:flex;flex-direction:column;justify-content:center;gap:4px;}
.type-label{font-size:11px;font-weight:700;letter-spacing:.14em;text-transform:uppercase;color:rgba(255,255,255,.45);}
.type-name{font-size:14px;font-weight:800;}
.type-period{font-size:11px;color:rgba(255,255,255,.5);margin-top:4px;}
.tokuten-row-body{padding:16px 18px;display:flex;flex-direction:column;gap:8px;}
.tokuten-condition{font-size:12px;color:var(--text-muted);line-height:1.5;}
.tokuten-condition strong{color:var(--text);font-weight:700;}
.tokuten-item{font-size:14px;font-weight:700;}
.tokuten-stores{display:flex;flex-wrap:wrap;gap:4px;margin-top:4px;}
.store-chip{font-size:11px;background:var(--ground);border:1px solid var(--border);border-radius:4px;padding:2px 7px;color:var(--text-muted);white-space:nowrap;}
.channel-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;}
.channel-card{background:var(--surface);border:1.5px solid var(--border);border-radius:var(--radius-sm);padding:14px 16px;}
.channel-name{font-size:12px;font-weight:700;color:var(--text-muted);margin-bottom:4px;}
.channel-gift{font-size:13px;font-weight:700;line-height:1.4;}
.channel-note{font-size:11px;color:var(--text-muted);margin-top:3px;}
.fc-note{background:var(--gold-soft);border:1.5px solid #E8D19A;border-radius:var(--radius-sm);padding:12px 16px;font-size:13px;color:var(--gold);font-weight:600;margin-bottom:16px;line-height:1.5;}
.fc-table-wrap{border:1.5px solid var(--border);border-radius:var(--radius);overflow:hidden;}
.fc-table{width:100%;border-collapse:collapse;font-size:13px;}
.fc-table thead tr{background:var(--text);color:#fff;}
.fc-table th{padding:10px 14px;text-align:left;font-size:11px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;white-space:nowrap;}
.fc-table td{padding:10px 14px;border-bottom:1px solid var(--border);vertical-align:middle;line-height:1.4;}
.fc-table tr:last-child td{border-bottom:none;}
.fc-table tr:nth-child(even) td{background:rgba(0,0,0,.015);}
.fc-price{font-weight:800;color:var(--accent);white-space:nowrap;}
.deadline-box{background:var(--text);color:#fff;border-radius:var(--radius);padding:20px 24px;display:flex;flex-wrap:wrap;gap:16px 32px;align-items:flex-start;margin-top:12px;}
.dl-label{font-size:10px;font-weight:700;letter-spacing:.14em;text-transform:uppercase;color:rgba(255,255,255,.4);margin-bottom:4px;}
.dl-val{font-size:15px;font-weight:800;}
.dl-sub{font-size:11px;color:rgba(255,255,255,.5);margin-top:2px;}
@media (max-width:700px){.editions-top{grid-template-columns:1fr;}.editions-solo{grid-template-columns:repeat(2,1fr);}.checker-layout{grid-template-columns:1fr;}.result-panel{position:static;}.tokuten-row-head{grid-template-columns:1fr;}.tokuten-row-type{padding:12px 16px;}.channel-grid{grid-template-columns:1fr 1fr;}}
@media (max-width:420px){.editions-solo{grid-template-columns:1fr;}.channel-grid{grid-template-columns:1fr;}}
'''

ALBUM_CHECKER_BODY = '''
<div class="album-hero">
  <div class="hero-group-badge">M!LK</div>
  <p class="hero-label">1st Mini Album</p>
  <h1 class="hero-title">タイトル<em>未定</em></h1>
  <p class="hero-subtitle">Title Undecided</p>
  <div class="hero-meta">
    <div class="hero-meta-item"><span class="val">2026.09.16</span><span class="key">発売日</span></div>
    <div class="hero-meta-item"><span class="val">7</span><span class="key">形態</span></div>
    <div class="hero-meta-item"><span class="val">7/19</span><span class="key">予約締切</span></div>
  </div>
</div>

<div class="checker-wrap">

<div class="chk-section">
  <div class="chk-section-head">
    <h2 class="chk-section-title">特典チェッカー</h2>
    <span class="chk-section-en">What should I buy?</span>
  </div>
  <div class="checker-layout">
    <div class="wants-panel">
      <p class="wants-intro">欲しい特典にチェックを入れると、購入すべき形態と通路を表示します。</p>
      <label class="want-item" for="w-pair"><input type="checkbox" id="w-pair" onchange="compute()"><div class="want-check"></div><div class="want-info"><span class="want-name">ペアトレカ（雙人小卡）</span><span class="want-tag">早期予約特典・5種ランダム</span></div></label>
      <label class="want-item" for="w-clear"><input type="checkbox" id="w-clear" onchange="compute()"><div class="want-check"></div><div class="want-info"><span class="want-name">クリアトレカ（透卡）</span><span class="want-tag">早期セット予約特典・ソロ5種ランダム</span></div></label>
      <label class="want-item" for="w-bluray"><input type="checkbox" id="w-bluray" onchange="compute()"><div class="want-check"></div><div class="want-info"><span class="want-name">Blu-ray（イベント映像）</span><span class="want-tag">初回限定盤収録</span></div></label>
      <label class="want-item" for="w-sticker"><input type="checkbox" id="w-sticker" onchange="toggleSub('sub-sticker','w-sticker'); compute()"><div class="want-check"></div><div class="want-info"><span class="want-name">通路別ステッカー</span><span class="want-tag">先着特典・各通路1種</span></div></label>
      <div class="sub-opt" id="sub-sticker">
        <select id="sel-sticker" onchange="compute()">
          <option value="">どのメンバー？</option>
          <option value="sano">佐野勇斗（楽天ブックス）</option>
          <option value="shiozaki">塩﨑太智（TOWER RECORDS）</option>
          <option value="sono">曽野舜太（VICTOR ONLINE）</option>
          <option value="yamanaka">山中柔太朗（HMV）</option>
          <option value="yoshida">吉田仁人（応援店）</option>
          <option value="amazon">メガジャケ（Amazon）</option>
        </select>
      </div>
      <label class="want-item" for="w-acrylic"><input type="checkbox" id="w-acrylic" onchange="toggleSub('sub-acrylic','w-acrylic'); compute()"><div class="want-check"></div><div class="want-info"><span class="want-name">アクスタ（複数選択可）</span><span class="want-tag fc">FC限定 · PREMIUM MILK会員</span></div></label>
      <div class="sub-opt" id="sub-acrylic">
        <div class="sub-check-grid">
          <span class="sub-check-group-label">集合アクスタ</span>
          <label class="sub-check-item"><input type="checkbox" name="acrylic-type" value="A" onchange="compute()"><div class="sub-check-dot"></div>集合A（初回盤）</label>
          <label class="sub-check-item"><input type="checkbox" name="acrylic-type" value="B" onchange="compute()"><div class="sub-check-dot"></div>集合B（通常盤）</label>
          <span class="sub-check-group-label">ソロアクスタ</span>
          <label class="sub-check-item"><input type="checkbox" name="acrylic-type" value="sano" onchange="compute()"><div class="sub-check-dot"></div>佐野勇斗</label>
          <label class="sub-check-item"><input type="checkbox" name="acrylic-type" value="shiozaki" onchange="compute()"><div class="sub-check-dot"></div>塩﨑太智</label>
          <label class="sub-check-item"><input type="checkbox" name="acrylic-type" value="sono" onchange="compute()"><div class="sub-check-dot"></div>曽野舜太</label>
          <label class="sub-check-item"><input type="checkbox" name="acrylic-type" value="yamanaka" onchange="compute()"><div class="sub-check-dot"></div>山中柔太朗</label>
          <label class="sub-check-item"><input type="checkbox" name="acrylic-type" value="yoshida" onchange="compute()"><div class="sub-check-dot"></div>吉田仁人</label>
        </div>
      </div>
      <label class="want-item" for="w-solocard"><input type="checkbox" id="w-solocard" onchange="toggleSub('sub-solo','w-solocard'); compute()"><div class="want-check"></div><div class="want-info"><span class="want-name">特定メンバーの封入ソロトレカ（複数選択可）</span><span class="want-tag">ソロ盤封入・3種ランダム</span></div></label>
      <div class="sub-opt" id="sub-solo">
        <div class="sub-check-grid">
          <label class="sub-check-item"><input type="checkbox" name="solo-member" value="sano" onchange="compute()"><div class="sub-check-dot"></div>佐野勇斗</label>
          <label class="sub-check-item"><input type="checkbox" name="solo-member" value="shiozaki" onchange="compute()"><div class="sub-check-dot"></div>塩﨑太智</label>
          <label class="sub-check-item"><input type="checkbox" name="solo-member" value="sono" onchange="compute()"><div class="sub-check-dot"></div>曽野舜太</label>
          <label class="sub-check-item"><input type="checkbox" name="solo-member" value="yamanaka" onchange="compute()"><div class="sub-check-dot"></div>山中柔太朗</label>
          <label class="sub-check-item"><input type="checkbox" name="solo-member" value="yoshida" onchange="compute()"><div class="sub-check-dot"></div>吉田仁人</label>
        </div>
      </div>
      <button class="reset-btn" onclick="resetAll()">選択をリセット</button>
    </div>
    <div class="result-panel" id="result-panel">
      <div class="result-empty">欲しい特典を選んでください</div>
    </div>
  </div>
</div>

<div class="chk-section">
  <div class="chk-section-head"><h2 class="chk-section-title">版本一覧</h2><span class="chk-section-en">Editions</span></div>
  <div class="editions-top">
    <div class="plain-wrap"><div class="card">
      <span class="card-badge badge-standard">通常盤</span>
      <div><div class="card-name">通常盤</div><div class="card-name-en">Regular Edition · CD only</div></div>
      <div class="card-price">¥2,530<span>税込</span></div>
      <hr class="card-divider">
      <div class="card-row"><div class="card-row-label">収録</div><div class="card-row-val">CD 全5曲<br>アイドルパワー（既発）＋新曲</div></div>
      <div class="card-row"><div class="card-row-label">封入特典</div><div><span class="tokuten-tag">限定トレカ（ソロ5種ランダム1枚）</span><span class="tokuten-tag">応募抽選シリアル ※初回プレスのみ</span></div></div>
    </div></div>
    <div class="holo-wrap"><div class="card">
      <span class="card-badge badge-limited">初回限定盤</span>
      <div><div class="card-name">初回限定盤</div><div class="card-name-en">First Limited Edition · CD + Blu-ray</div></div>
      <div class="card-price">¥3,520<span>税込</span></div>
      <hr class="card-divider">
      <div class="card-row"><div class="card-row-label">収録</div><div class="card-row-val">CD 全5曲 ＋ Blu-ray<br>「爆裂愛してる / 好きすぎて滅！」<br>発売日記念スペシャルイベント映像</div></div>
      <div class="card-row"><div class="card-row-label">封入特典</div><div><span class="tokuten-tag">限定トレカ（ソロ5種ランダム1枚）</span><span class="tokuten-tag">応募抽選シリアル</span></div></div>
    </div></div>
  </div>
  <div class="editions-solo">
    <div class="holo-wrap"><div class="card"><span class="card-badge badge-solo">ソロ盤</span><div><div class="member-name">佐野勇斗</div><div class="card-name-en">CD only · 初回生産限定</div></div><div class="card-price">¥2,530<span>税込</span></div><hr class="card-divider"><div class="card-row"><div class="card-row-label">収録</div><div class="card-row-val">全6曲（共通5曲＋ユニット曲）</div></div><div class="card-row"><div class="card-row-label">封入特典</div><div><span class="tokuten-tag">佐野勇斗 ソロ3種ランダム1枚</span><span class="tokuten-tag">応募抽選シリアル</span></div></div></div></div>
    <div class="holo-wrap"><div class="card"><span class="card-badge badge-solo">ソロ盤</span><div><div class="member-name">塩﨑太智</div><div class="card-name-en">CD only · 初回生産限定</div></div><div class="card-price">¥2,530<span>税込</span></div><hr class="card-divider"><div class="card-row"><div class="card-row-label">収録</div><div class="card-row-val">全6曲（共通5曲＋ユニット曲）</div></div><div class="card-row"><div class="card-row-label">封入特典</div><div><span class="tokuten-tag">塩﨑太智 ソロ3種ランダム1枚</span><span class="tokuten-tag">応募抽選シリアル</span></div></div></div></div>
    <div class="holo-wrap"><div class="card"><span class="card-badge badge-solo">ソロ盤</span><div><div class="member-name">曽野舜太</div><div class="card-name-en">CD only · 初回生産限定</div></div><div class="card-price">¥2,530<span>税込</span></div><hr class="card-divider"><div class="card-row"><div class="card-row-label">収録</div><div class="card-row-val">全6曲（共通5曲＋ユニット曲）</div></div><div class="card-row"><div class="card-row-label">封入特典</div><div><span class="tokuten-tag">曽野舜太 ソロ3種ランダム1枚</span><span class="tokuten-tag">応募抽選シリアル</span></div></div></div></div>
    <div class="holo-wrap"><div class="card"><span class="card-badge badge-solo">ソロ盤</span><div><div class="member-name">山中柔太朗</div><div class="card-name-en">CD only · 初回生産限定</div></div><div class="card-price">¥2,530<span>税込</span></div><hr class="card-divider"><div class="card-row"><div class="card-row-label">収録</div><div class="card-row-val">全6曲（共通5曲＋ユニット曲）</div></div><div class="card-row"><div class="card-row-label">封入特典</div><div><span class="tokuten-tag">山中柔太朗 ソロ3種ランダム1枚</span><span class="tokuten-tag">応募抽選シリアル</span></div></div></div></div>
    <div class="holo-wrap"><div class="card"><span class="card-badge badge-solo">ソロ盤</span><div><div class="member-name">吉田仁人</div><div class="card-name-en">CD only · 初回生産限定</div></div><div class="card-price">¥2,530<span>税込</span></div><hr class="card-divider"><div class="card-row"><div class="card-row-label">収録</div><div class="card-row-val">全6曲（共通5曲＋ユニット曲）</div></div><div class="card-row"><div class="card-row-label">封入特典</div><div><span class="tokuten-tag">吉田仁人 ソロ3種ランダム1枚</span><span class="tokuten-tag">応募抽選シリアル</span></div></div></div></div>
  </div>
</div>

<div class="chk-section">
  <div class="chk-section-head"><h2 class="chk-section-title">予約特典</h2><span class="chk-section-en">Pre-order Bonuses</span></div>
  <div class="tokuten-block">
    <div class="tokuten-row"><div class="tokuten-row-head">
      <div class="tokuten-row-type"><span class="type-label">EC限定</span><span class="type-name">早期セット予約特典</span><span class="type-period">6/19（金）20:00 〜 7/19（日）23:59</span></div>
      <div class="tokuten-row-body"><div class="tokuten-condition"><strong>条件：</strong>対象2形態以上のセット購入<br>（初回限定盤＋通常盤）または（初回限定盤＋ソロ盤いずれか1種）</div><div class="tokuten-item">メンバーソロ クリアトレカ（5種ランダム1枚）</div><div class="tokuten-stores"><span class="store-chip">VICTOR ONLINE</span><span class="store-chip">楽天ブックス</span><span class="store-chip">セブンネット</span><span class="store-chip">TOWER RECORDS ONLINE</span><span class="store-chip">HMV & BOOKS online</span><span class="store-chip">Amazon.co.jp</span></div></div>
    </div></div>
    <div class="tokuten-row"><div class="tokuten-row-head">
      <div class="tokuten-row-type"><span class="type-label">EC限定</span><span class="type-name">早期予約特典</span><span class="type-period">6/19（金）20:00 〜 7/19（日）23:59</span></div>
      <div class="tokuten-row-body"><div class="tokuten-condition"><strong>条件：</strong>任意の1形態を予約（単品可）</div><div class="tokuten-item">ペアトレカ（5種ランダム1枚）</div><div class="tokuten-condition">5種の組み合わせ：佐野勇斗×曽野舜太、佐野勇斗×山中柔太朗 など</div><div class="tokuten-stores"><span class="store-chip">VICTOR ONLINE</span><span class="store-chip">楽天ブックス</span><span class="store-chip">セブンネット</span><span class="store-chip">TOWER RECORDS ONLINE</span><span class="store-chip">HMV & BOOKS online</span><span class="store-chip">Amazon.co.jp</span></div></div>
    </div></div>
  </div>
</div>

<div class="chk-section">
  <div class="chk-section-head"><h2 class="chk-section-title">通路別特典</h2><span class="chk-section-en">Store Exclusives · 先着</span></div>
  <div class="channel-grid">
    <div class="channel-card"><div class="channel-name">楽天ブックス</div><div class="channel-gift">佐野勇斗 デザイン ステッカー</div></div>
    <div class="channel-card"><div class="channel-name">TOWER RECORDS</div><div class="channel-gift">塩﨑太智 デザイン ステッカー</div></div>
    <div class="channel-card"><div class="channel-name">HMV & BOOKS</div><div class="channel-gift">山中柔太朗 デザイン ステッカー</div></div>
    <div class="channel-card"><div class="channel-name">VICTOR ONLINE STORE</div><div class="channel-gift">曽野舜太 デザイン ステッカー</div></div>
    <div class="channel-card"><div class="channel-name">応援店（TSUTAYA 等）</div><div class="channel-gift">吉田仁人 デザイン ステッカー</div></div>
    <div class="channel-card"><div class="channel-name">Amazon.co.jp</div><div class="channel-gift">メガジャケ</div><div class="channel-note">ステッカーではなくメガジャケ</div></div>
  </div>
</div>

<div class="chk-section">
  <div class="chk-section-head"><h2 class="chk-section-title">FC限定商品</h2><span class="chk-section-en">PREMIUM MILK Members Only</span></div>
  <div class="fc-note">PREMIUM MILK 会員限定 · VICTOR ONLINE STORE のみ取扱<br>FC購入者には早期予約特典（ペアトレカ）＋ <strong>曽野舜太デザインステッカー</strong> が付与される</div>
  <div class="fc-table-wrap"><table class="fc-table">
    <thead><tr><th>セット内容</th><th>CD形態</th><th>アクスタ</th><th>価格（税込）</th></tr></thead>
    <tbody>
      <tr><td>初回限定盤＋集合アクスタA</td><td>初回限定盤</td><td>集合アクスタ A</td><td class="fc-price">¥5,170</td></tr>
      <tr><td>通常盤＋集合アクスタB</td><td>通常盤</td><td>集合アクスタ B</td><td class="fc-price">¥4,180</td></tr>
      <tr><td>ソロ盤＋ソロアクスタ（各メンバー）</td><td>ソロ盤 × 1</td><td>ソロアクスタ（同メンバー）</td><td class="fc-price">¥4,180 × 各</td></tr>
      <tr><td>2形態セット（初回＋通常）</td><td>初回限定盤＋通常盤</td><td>集合 A＋B</td><td class="fc-price">¥9,350</td></tr>
      <tr><td>2形態セット（初回＋ソロ各）</td><td>初回限定盤＋ソロ盤 × 1</td><td>集合 A＋ソロ</td><td class="fc-price">¥9,350</td></tr>
      <tr><td>3形態セット</td><td>初回＋通常＋ソロ × 1</td><td>集合 A＋B＋ソロ × 1</td><td class="fc-price">¥13,530</td></tr>
      <tr><td>6形態セット（初回＋ソロ5）</td><td>初回限定盤＋ソロ全5</td><td>集合 A＋ソロ全5</td><td class="fc-price">¥26,070</td></tr>
      <tr><td>6形態セット（通常＋ソロ5）</td><td>通常盤＋ソロ全5</td><td>集合 B＋ソロ全5</td><td class="fc-price">¥25,080</td></tr>
      <tr><td>ソロ全形態セット</td><td>ソロ盤全5種</td><td>ソロアクスタ全5</td><td class="fc-price">¥20,900</td></tr>
      <tr><td>全7形態セット</td><td>初回＋通常＋ソロ全5</td><td>集合 A＋B＋ソロ全5</td><td class="fc-price">¥30,250</td></tr>
    </tbody>
  </table></div>
  <div class="deadline-box">
    <div class="dl-item"><div class="dl-label">予約締切</div><div class="dl-val">2026年 7月19日（日）23:59</div></div>
    <div class="dl-item"><div class="dl-label">コンビニ払い締切</div><div class="dl-val">7月17日（金）18:00</div></div>
    <div class="dl-item"><div class="dl-label">支払期限</div><div class="dl-val">申込日から3日以内</div><div class="dl-sub">超過するとキャンセル対象</div></div>
  </div>
</div>

</div>

<script>
const MEMBERS = {
  sano:     { name: '佐野勇斗',   sticker_store: '楽天ブックス' },
  shiozaki: { name: '塩﨑太智',   sticker_store: 'TOWER RECORDS ONLINE' },
  sono:     { name: '曽野舜太',   sticker_store: 'VICTOR ONLINE STORE' },
  yamanaka: { name: '山中柔太朗', sticker_store: 'HMV & BOOKS online' },
  yoshida:  { name: '吉田仁人',   sticker_store: '応援店（TSUTAYA等）' },
  amazon:   { name: 'Amazon',     sticker_store: 'Amazon.co.jp' },
};
const MEMBER_ORDER = ['sano','shiozaki','sono','yamanaka','yoshida'];

function chk(id) { return document.getElementById(id)?.checked || false; }
function sel(id) { return document.getElementById(id)?.value || ''; }
function getChecked(name) {
  return [...document.querySelectorAll('[name="' + name + '"]:checked')].map(el => el.value);
}
function toggleSub(subId, checkId) {
  const sub = document.getElementById(subId);
  const checked = document.getElementById(checkId).checked;
  sub.classList.toggle('open', checked);
  if (!checked) {
    sub.querySelectorAll('input[type="checkbox"]').forEach(cb => cb.checked = false);
    sub.querySelectorAll('select').forEach(s => s.value = '');
  }
}
function resetAll() {
  ['w-pair','w-clear','w-bluray','w-sticker','w-acrylic','w-solocard'].forEach(id => {
    document.getElementById(id).checked = false;
  });
  ['sub-sticker','sub-acrylic','sub-solo'].forEach(id => {
    const el = document.getElementById(id);
    el.classList.remove('open');
    el.querySelectorAll('input[type="checkbox"]').forEach(cb => cb.checked = false);
    el.querySelectorAll('select').forEach(s => s.value = '');
  });
  compute();
}
function compute() {
  const wants = { pair:chk('w-pair'), clear:chk('w-clear'), bluray:chk('w-bluray'), sticker:chk('w-sticker'), acrylic:chk('w-acrylic'), solocard:chk('w-solocard') };
  const stickerM = sel('sel-sticker');
  const selectedAcrylics = wants.acrylic ? getChecked('acrylic-type') : [];
  const selectedSolos = wants.solocard ? getChecked('solo-member') : [];
  const panel = document.getElementById('result-panel');
  if (!Object.values(wants).some(Boolean)) { panel.innerHTML = '<div class="result-empty">欲しい特典を選んでください</div>'; return; }
  const acrylicA = selectedAcrylics.includes('A');
  const acrylicB = selectedAcrylics.includes('B');
  const acrylicSoloSet = new Set(selectedAcrylics.filter(v => v !== 'A' && v !== 'B'));
  const fcRequired = selectedAcrylics.length > 0;
  const allSoloNeeded = MEMBER_ORDER.filter(id => selectedSolos.includes(id) || acrylicSoloSet.has(id));
  let needInitial = wants.bluray || wants.clear || acrylicA;
  let needNormal = acrylicB;
  const warnings = [];
  if (wants.clear && allSoloNeeded.length === 0 && !needNormal) needNormal = true;
  const hasAnything = needInitial || needNormal || allSoloNeeded.length > 0;
  if (wants.pair && !hasAnything) needNormal = true;
  const items = [];
  if (needInitial) items.push(acrylicA ? { name:'初回限定盤＋集合アクスタA セット', price:5170, badge:'FC限定' } : { name:'初回限定盤', price:3520 });
  if (needNormal) items.push(acrylicB ? { name:'通常盤＋集合アクスタB セット', price:4180, badge:'FC限定' } : { name:'通常盤', price:2530 });
  for (const id of allSoloNeeded) {
    const mName = MEMBERS[id].name;
    const hasAcrylic = acrylicSoloSet.has(id);
    items.push(hasAcrylic ? { name:mName + 'ソロ盤＋ソロアクスタ セット', price:4180, badge:'FC限定' } : { name:mName + 'ソロ盤', price:2530 });
  }
  const needsEC = wants.pair || wants.clear;
  let primaryStore = null, extraStore = null, extraPrice = 0;
  if (fcRequired) primaryStore = 'VICTOR ONLINE STORE（FC会員）';
  if (wants.sticker && stickerM) {
    const sStore = MEMBERS[stickerM]?.sticker_store;
    if (sStore) {
      if (sStore === '応援店（TSUTAYA等）' && needsEC) { extraStore = '応援店（TSUTAYA等）'; extraPrice = 2530; warnings.push('吉田仁人ステッカーは応援店の先着特典ですが、応援店は早期予約特典の対象外です。ステッカーを取得するには応援店でも別途1形態以上を購入する必要があります。');
      } else if (fcRequired && sStore !== 'VICTOR ONLINE STORE') { extraStore = sStore; extraPrice = 2530; warnings.push(MEMBERS[stickerM].name + 'のステッカーは' + sStore + 'の先着特典です。FC限定アクスタのためVICTOR ONLINE STOREでも購入しますが、ステッカー取得には' + sStore + 'でも別途1形態以上の購入が必要です。');
      } else if (!fcRequired) { primaryStore = sStore; }
    }
  }
  if (!primaryStore && needsEC) primaryStore = 'EC通路（6店舗いずれか）';
  if (!primaryStore && !needsEC) primaryStore = '取扱店いずれか';
  const tokuten = [];
  const enclosedDiscs = [];
  if (needInitial) enclosedDiscs.push('初回限定盤（5種ランダム）');
  if (needNormal) enclosedDiscs.push('通常盤（5種ランダム）');
  for (const id of allSoloNeeded) enclosedDiscs.push(MEMBERS[id].name + 'ソロ盤（3種ランダム）');
  if (enclosedDiscs.length === 1) tokuten.push({ name:'封入ソロトレカ', desc:enclosedDiscs[0] });
  else if (enclosedDiscs.length > 1) tokuten.push({ name:'封入ソロトレカ ×' + enclosedDiscs.length, desc:enclosedDiscs.join('・') });
  tokuten.push({ name:'応募抽選シリアル', desc:'全' + items.length + '形態封入（初回プレス限定）' });
  if (wants.pair) tokuten.push({ name:'ペアトレカ', desc:'5種ランダム1枚', hi:true });
  if (wants.clear) tokuten.push({ name:'クリアトレカ（透卡）', desc:'ソロ5種ランダム1枚', hi:true });
  if (wants.bluray) tokuten.push({ name:'Blu-ray イベント映像', desc:'発売日記念スペシャルイベント', hi:true });
  if (wants.sticker && stickerM) {
    const info = MEMBERS[stickerM];
    if (info) {
      if (stickerM === 'amazon') tokuten.push({ name:'メガジャケ', desc:'Amazon 先着特典', hi:true });
      else tokuten.push({ name:info.name + ' デザインステッカー', desc:info.sticker_store + ' 先着特典', hi:true });
    }
  }
  if (acrylicA) tokuten.push({ name:'集合アクスタA', desc:'FC限定', hi:true });
  if (acrylicB) tokuten.push({ name:'集合アクスタB', desc:'FC限定', hi:true });
  for (const id of [...acrylicSoloSet].filter(v => MEMBER_ORDER.includes(v)).sort((a,b) => MEMBER_ORDER.indexOf(a)-MEMBER_ORDER.indexOf(b))) {
    tokuten.push({ name:MEMBERS[id].name + ' ソロアクスタ', desc:'FC限定', hi:true });
  }
  if (fcRequired) tokuten.push({ name:'曽野舜太 デザインステッカー', desc:'FC購入者全員特典' });
  const total = items.reduce((s,i) => s + i.price, 0) + extraPrice;
  const totalForms = items.length + (extraStore ? 1 : 0);
  const allItemNames = items.map(i => i.name.replace('＋集合アクスタA セット','').replace('＋集合アクスタB セット','').replace('＋ソロアクスタ セット',''));
  if (extraStore) allItemNames.push('任意1形態（' + extraStore + '）');
  const planNote = allItemNames.length <= 2 ? allItemNames.join('＋') + 'の購入' : totalForms + '形態の購入';
  const itemsHtml = items.map(i => '<div class="res-item"><div class="res-item-name">' + i.name + (i.badge ? '<span class="res-badge">' + i.badge + '</span>' : '') + '</div><div class="res-item-right"><span class="res-qty">× 1</span><div class="res-item-price">¥' + i.price.toLocaleString() + '</div></div></div>').join('');
  const extraHtml = extraStore ? '<div class="res-item"><div class="res-item-name">任意1形態（' + extraStore + ' ステッカー用）<span class="res-badge-warn">別途</span></div><div class="res-item-right"><span class="res-qty">× 1</span><div class="res-item-price">¥2,530〜</div></div></div>' : '';
  const formsSummary = '合計 ' + totalForms + '形態 / ¥' + total.toLocaleString() + (extraPrice ? '〜' : '') + '（税込）';
  const tokutenHtml = tokuten.map(t => '<div class="res-tok"><span class="res-tok-name' + (t.hi ? ' hi' : '') + '">' + t.name + '</span><span class="res-tok-desc">' + t.desc + '</span></div>').join('');
  const warningsHtml = warnings.map(w => '<div class="res-warn">⚠ ' + w + '</div>').join('');
  const deadlineHtml = (needsEC || fcRequired) ? '<div class="res-deadline">予約締切：2026年7月19日（日）23:59</div>' : '';
  panel.innerHTML = '<div class="res-plan-header"><div class="res-plan-label">✓ 最安プラン</div><div class="res-plan-price">¥' + total.toLocaleString() + '<small>' + (extraPrice ? '〜（税込）' : '（税込）') + '</small></div><div class="res-plan-note">' + planNote + '</div></div><div class="res-sec"><div class="res-label">購入リスト</div>' + itemsHtml + extraHtml + '<div class="res-total">' + formsSummary + '</div></div><div class="res-store"><div class="res-store-label">購入通路</div><div class="res-store-val">' + primaryStore + '</div>' + (extraStore ? '<div class="res-store-extra">＋ ' + extraStore + '（ステッカー用）</div>' : '') + '</div><div class="res-sec"><div class="res-label">取得できる特典</div>' + tokutenHtml + '</div>' + warningsHtml + deadlineHtml;
}
</script>
'''

ALBUM_TEMPLATE = '''<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>M!LK Fan Hub &mdash; 1st Mini Album</title>
<style>
__CSS__
__ALBUM_CSS__
</style>
</head>
<body>
<header class="site-header">
  __STRIPE__
  <div class="wordmark">M<span class="bang">!</span>LK</div>
  <div class="hearts">&#9825; &middot; &#9825; &middot; &#9825;</div>
  <p class="tagline">Fan Hub &middot; 1st Mini Album</p>
  <nav class="page-nav">__NAV__</nav>
  <p class="update-time">最後更新：__UPDATED_AT__ JST</p>
</header>
__BODY__
<footer>
  <p>M!LK Fan Hub &nbsp;&middot;&nbsp; 非公式ファンサイト &nbsp;&middot;&nbsp; 毎日 09:00 JST 自動更新</p>

</footer>
</body>
</html>'''


def generate_album_html(updated_at):
    return (ALBUM_TEMPLATE
            .replace('__CSS__', COMMON_CSS)
            .replace('__ALBUM_CSS__', ALBUM_CHECKER_CSS)
            .replace('__STRIPE__', MEMBER_STRIPE_HTML)
            .replace('__NAV__', NAV_ALBUM)
            .replace('__SOURCES_FOOTER__', make_sources_footer())
            .replace('__UPDATED_AT__', updated_at)
            .replace('__BODY__', ALBUM_CHECKER_BODY))


def generate_news_html(articles, updated_at):
    return build_page(NEWS_TEMPLATE, NAV_NEWS,
                      json.dumps(articles, ensure_ascii=False), updated_at)

def generate_schedule_html(articles, updated_at):
    today_jst = datetime.now(JST).strftime('%Y-%m-%d')
    sched_kw = {'放送', '出演', '公演', 'ライブ', '上映', 'イベント',
                '本日', '今日', 'live', 'concert', 'event', '登場', '収録'}

    # Filter today's articles that look schedule-related
    today_arts = [
        a for a in articles
        if a.get('date', '')[:10] == today_jst
        and any(kw in (a.get('title_ja', '') + a.get('summary_ja', '') +
                       a.get('title_zh', '')).lower() for kw in sched_kw)
    ]

    def esc(s):
        return (str(s).replace('&', '&amp;').replace('<', '&lt;')
                      .replace('>', '&gt;').replace('"', '&quot;'))

    # Build today's news cards
    if today_arts:
        cards = []
        for a in today_arts[:8]:
            color = SOURCE_COLORS.get(a.get('source', ''), '#888')
            title_zh = esc(a.get('title_zh', ''))
            title_ja = esc(a.get('title_ja', ''))
            url = esc(a.get('url', '#'))
            source = esc(a.get('source', ''))
            zh_block = ('<p class="tc-title">' + title_zh + '</p>') if title_zh else ''
            cards.append(
                f'<div class="today-card">'
                f'<span class="tc-source" style="background:{color}">{source}</span>'
                + zh_block +
                f'<p class="tc-ja">{title_ja}</p>'
                f'<a class="tc-link" href="{url}" target="_blank" rel="noopener">原文を読む &rarr;</a>'
                f'</div>'
            )
        today_html = '<div class="today-grid">' + ''.join(cards) + '</div>'
    else:
        today_html = f'<p class="today-empty">本日（{today_jst}）は関連ニュースが見つかりませんでした。</p>'

    # Build program cards
    prog_cards = []
    for name, badge, station, sched, url, color in SCHEDULE_PROGRAMS:
        prog_cards.append(
            f'<div class="prog-card">'
            f'<span class="prog-badge" style="background:{color}">{esc(badge)}</span>'
            f'<p class="prog-name">{esc(name)}</p>'
            f'<p class="prog-station">{esc(station)}</p>'
            f'<p class="prog-schedule">{esc(sched)}</p>'
            f'<a class="prog-link" href="{esc(url)}" target="_blank" rel="noopener">公式サイト &rarr;</a>'
            f'</div>'
        )

    return (SCHEDULE_TEMPLATE
            .replace('__CSS__', COMMON_CSS)
            .replace('__STRIPE__', MEMBER_STRIPE_HTML)
            .replace('__NAV__', NAV_SCHEDULE)
            .replace('__SOURCES_FOOTER__', make_sources_footer())
            .replace('__UPDATED_AT__', updated_at)
            .replace('__TODAY__', today_jst)
            .replace('__TODAY_NEWS__', today_html)
            .replace('__PROG_CARDS__', ''.join(prog_cards)))

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    import os as _os, sys as _sys
    NEWS_ONLY = _os.environ.get('NEWS_ONLY') == '1' or '--news-only' in _sys.argv
    print(f'=== M!LK Fan Hub {"(NEWS ONLY)" if NEWS_ONLY else "(FULL UPDATE)"} ===')

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

    # Dedup existing archive (fixes duplicates already stored before the dedup fix)
    existing = dedup_by_url(existing)

    # Clean image URLs in existing articles (strip CDN resize params)
    for a in existing:
        if a.get('image'):
            a['image'] = _clean_img_url(a['image'])

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

    print(f'Fetching article data (image + body) for {len(new_raw)} articles...')
    for a in new_raw:
        if a.get('url'):
            img, body = fetch_article_data(a['url'])
            a['image'] = img
            # Populate body text only if RSS/scraper didn't already give us one
            if body and not a.get('summary_ja'):
                a['summary_ja'] = body
            time.sleep(0.3)

    merged, added = merge_by_url(existing, new_raw)
    print(f'News: +{added} new (total {len(merged)})')
    save_json(ARTICLES_FILE, merged)

    now_jst = datetime.now(JST).strftime('%Y-%m-%d %H:%M')
    with open('index.html', 'w', encoding='utf-8') as f:
        f.write(generate_news_html(merged, now_jst))

    if NEWS_ONLY:
        print('Done! (news only)')
        return

    ex_schedule = load_json(SCHEDULE_FILE)
    new_events  = fetch_schedule()
    merged_sched, added_sched = merge_by_id(ex_schedule, new_events)
    print(f'Schedule: +{added_sched} new (total {len(merged_sched)})')
    save_json(SCHEDULE_FILE, merged_sched)

    # ── Goods ────────────────────────────────────────────────────────────────
    ex_goods  = load_json(GOODS_FILE)
    new_goods = fetch_goods()

    # Translate new goods titles
    if translator:
        for g in new_goods:
            if g.get('title') and not g.get('title_zh'):
                g['title_zh'] = translate(g['title'])
    # Fetch images for goods that don't have one
    for g in new_goods:
        if g.get('url') and not g.get('image'):
            img, _ = fetch_article_data(g['url'])
            if img:
                g['image'] = img
            time.sleep(0.2)

    # Merge by id (goods don't accumulate — replace with fresh fetch each time
    # so sold-out items disappear automatically)
    if new_goods:
        # Fresh goods overwrite old ones; keep old ones that weren't re-fetched
        new_ids = {g['id'] for g in new_goods}
        kept_old = [g for g in ex_goods if g['id'] not in new_ids]
        merged_goods = new_goods + kept_old
    else:
        merged_goods = ex_goods
    save_json(GOODS_FILE, merged_goods[:200])
    print(f'Goods: {len(merged_goods)} total')

    with open('schedule.html', 'w', encoding='utf-8') as f:
        f.write(generate_schedule_html(merged, now_jst))

    with open('goods.html', 'w', encoding='utf-8') as f:
        f.write(generate_goods_html(merged_goods, merged, now_jst))

    with open('album.html', 'w', encoding='utf-8') as f:
        f.write(generate_album_html(now_jst))

    with open('sources.html', 'w', encoding='utf-8') as f:
        f.write(generate_sources_html(now_jst))

    print('Done!')


if __name__ == '__main__':
    main()

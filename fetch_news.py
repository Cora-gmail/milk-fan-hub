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
from urllib.parse import quote as _url_quote

try:
    import deepl
    _key = os.environ.get('DEEPL_API_KEY', '')
    translator = deepl.Translator(_key) if _key else None
except Exception:
    translator = None

_deepl_quota_exceeded = False  # set True on first quota error; stops retrying DeepL

try:
    from deep_translator import GoogleTranslator as _GoogleTranslator
    _google_translate_ok = True
except ImportError:
    _google_translate_ok = False

JST           = timezone(timedelta(hours=9))
ARTICLES_FILE = 'articles.json'
SCHEDULE_FILE = 'schedule.json'
GOODS_FILE    = 'goods.json'
CHARTS_FILE   = 'charts.json'
MAX_ARTICLES  = 1000

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml',
    'Accept-Language': 'ja-JP,ja;q=0.9',
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def translate(text):
    global _deepl_quota_exceeded
    if not text or not text.strip():
        return ''

    # DeepLuff08優先、段落構造をそのまま渡すuff09
    if translator and not _deepl_quota_exceeded:
        try:
            try:
                r = translator.translate_text(text.strip(), source_lang='JA', target_lang='ZH-HANT')
            except Exception:
                r = translator.translate_text(text.strip(), source_lang='JA', target_lang='ZH')
            time.sleep(0.4)
            return r.text
        except Exception as e:
            msg = str(e)
            if 'Quota' in msg or 'quota' in msg or '456' in msg:
                _deepl_quota_exceeded = True
                print('  DeepL quota exhausted u2014 switching to Google Translate fallback')
            else:
                print(f'  DeepL error: {e}')

    # Google Translate fallback u2014 translate paragraph by paragraph to preserve structure
    if _google_translate_ok:
        paras = [p.strip() for p in text.strip().split('\n\n') if p.strip()]
        if not paras:
            return ''
        translated = []
        for p in paras:
            try:
                result = _GoogleTranslator(source='ja', target='zh-TW').translate(p)
                translated.append(result or p)
                time.sleep(0.3)
            except Exception as e:
                print(f'  Google Translate error: {e}')
                translated.append(p)
        return '\n\n'.join(translated)

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
    # 各站專用 selector（優先）
    '.NA_article_body',                          # Natalie Music
    '.articleBody', '.article__body',            # 通用變體
    # 一般 selector
    'article .article-body', 'article .entry-body', 'article .post-body',
    '.article-body', '.article-content', '.article-text', '.entry-content',
    '.news-body', '.news-content', '.post-content', '.story-body',
    '.content-body', '.detail-content', '.text-body',
    'article', '.main-content',
]
_SKIP_IMG_WORDS = {'logo', 'icon', 'banner', 'avatar', 'pixel', 'blank',
                   'button', 'ad_', 'advert', 'sprite', 'tracking'}

_PUB_DATE_RE = re.compile(r'(20\d\d)[^\d](\d{2})[^\d](\d{2})')

def _resolve_gnews_url(gnews_url):
    """Try to resolve a news.google.com RSS link to its actual target URL via HTTP redirect."""
    try:
        # Google News RSS /rss/articles/ links often redirect via HTTP (not JS)
        r = requests.get(gnews_url, headers=HEADERS, timeout=8, allow_redirects=True)
        final = r.url
        if final and 'news.google.com' not in final and final.startswith('http'):
            return final
        # Fallback: look for canonical or og:url in the response
        from bs4 import BeautifulSoup as _BS
        soup = _BS(r.text, 'html.parser')
        for attr in [{'property': 'og:url'}, {'rel': 'canonical'}]:
            el = soup.find('link', attr) or soup.find('meta', attr)
            if el:
                href = el.get('href') or el.get('content', '')
                if href and 'news.google.com' not in href and href.startswith('http'):
                    return href
    except Exception:
        pass
    return ''

def fetch_article_data(url):
    """Fetch article page once → return (hero_image_url, all_images_list, body_text_ja, pub_date)."""
    image, images, body, pub_date = '', [], '', ''
    # Google News redirect URLs: try to resolve to actual article URL first.
    # If resolution fails, skip to avoid storing the Google logo as the article image.
    if 'news.google.com' in url:
        real_url = _resolve_gnews_url(url)
        if not real_url:
            return image, images, body, pub_date
        url = real_url  # fetch the actual article instead
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        r.encoding = r.apparent_encoding or 'utf-8'
        soup = BeautifulSoup(r.text, 'html.parser')

        # ── Publication date (meta tags / JSON-LD / <time>) ──
        for attrs in [
            {'property': 'article:published_time'},
            {'property': 'og:article:published_time'},
            {'name': 'pubdate'}, {'name': 'publish_date'},
            {'name': 'date'}, {'itemprop': 'datePublished'},
        ]:
            el = soup.find('meta', attrs=attrs)
            if el and el.get('content'):
                m = _PUB_DATE_RE.search(el['content'])
                if m:
                    pub_date = f'{m.group(1)}-{m.group(2)}-{m.group(3)}'
                    break
        if not pub_date:
            for script in soup.find_all('script', type='application/ld+json'):
                try:
                    import json as _json
                    data = _json.loads(script.string or '')
                    if isinstance(data, list):
                        data = data[0]
                    raw_d = data.get('datePublished') or data.get('dateCreated') or ''
                    m = _PUB_DATE_RE.search(str(raw_d))
                    if m:
                        pub_date = f'{m.group(1)}-{m.group(2)}-{m.group(3)}'
                        break
                except Exception:
                    pass
        if not pub_date:
            for t in soup.find_all('time', attrs={'datetime': True}):
                m = _PUB_DATE_RE.search(t['datetime'])
                if m:
                    pub_date = f'{m.group(1)}-{m.group(2)}-{m.group(3)}'
                    break
        # ── 日文短年份格式 fallback（如 Musicvoice：26年04月20日）──
        if not pub_date:
            _JP_RE = re.compile(r'(\d{2})年(\d{2})月(\d{2})日')
            m = _JP_RE.search(soup.get_text())
            if m and int(m.group(1)) >= 10:  # 2010+ 以後都算合理年份
                pub_date = f'20{m.group(1)}-{m.group(2)}-{m.group(3)}'

        # ── Hero image (og/twitter meta) ──
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
                    images.append(image)
                    break

        # ── Body text（保留段落結構）──
        content_el = None
        body_el = None
        for sel in _ARTICLE_BODY_SELECTORS:
            el = soup.select_one(sel)
            if el:
                for tag in el.select('script,style,nav,aside,.sns,.share,.related,figure figcaption'):
                    tag.decompose()
                # 以 <p>/<h2>/<h3>/<br> 為段落分界，用 \n\n 連接
                blocks = []
                for child in el.find_all(['p', 'h2', 'h3', 'li']):
                    t = re.sub(r'\s+', ' ', child.get_text()).strip()
                    if len(t) > 10:
                        blocks.append(t)
                if blocks:
                    text = '\n\n'.join(blocks)
                else:
                    text = re.sub(r'\s+', ' ', el.get_text()).strip()
                if len(text) > 80:
                    content_el = text
                    body_el = el
                    break
        if not content_el:
            paras = [re.sub(r'\s+', ' ', p.get_text()).strip()
                     for p in soup.find_all('p') if len(p.get_text(strip=True)) > 40]
            content_el = '\n\n'.join(paras) if paras else ''

        # ── 過濾雜訊段落（版權聲明、URL、製作人員表等）──
        _NOISE_RE = re.compile(
            r'https?://'                           # 含 URL
            r'|©\s*\d{4}'                          # 版權符號
            r'|\(C\)\s*\d{4}'                      # (C) 2026
            r'|^[\s　]*(?:導演|監督|製作|配音|発売|発行|出版|©|Copyright)[：:：]',
            re.IGNORECASE
        )
        clean_paras = []
        for line in content_el.split('\n\n'):
            if not _NOISE_RE.search(line):
                clean_paras.append(line)
        content_el = '\n\n'.join(clean_paras)

        body = content_el[:3000]  # 合理上限，避免抓入整個頁面

        # ── Additional images from article body ──
        seen = {image} if image else set()
        img_source = body_el if body_el else soup
        for img_tag in img_source.find_all('img', src=True):
            src = img_tag.get('src', '').strip()
            if not src.startswith('http'):
                continue
            src = _clean_img_url(src)
            if src in seen or any(w in src.lower() for w in _SKIP_IMG_WORDS):
                continue
            try:
                if int(img_tag.get('width', 200)) < 100 or int(img_tag.get('height', 200)) < 100:
                    continue
            except (ValueError, TypeError):
                pass
            seen.add(src)
            images.append(src)
            if len(images) >= 6:
                break

    except Exception:
        pass
    return image, images, body, pub_date

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

def dedup_by_title(items):
    """Remove duplicate articles with same title_ja. Prefers direct URLs over Google News URLs."""
    # Sort: direct URLs first so they survive, Google News duplicates get dropped
    items = sorted(items,
                   key=lambda a: (1 if 'news.google.com' in a.get('url', '') else 0,
                                  a.get('date', '')),
                   reverse=False)
    seen_title, result = set(), []
    for a in items:
        # Normalize: collapse whitespace, lowercase, first 60 chars
        raw = (a.get('title_ja', '') or '').strip()
        t = re.sub(r'[\s　]+', '', raw).lower()[:60]
        if t and t in seen_title:
            continue
        if t:
            seen_title.add(t)
        result.append(a)
    result.sort(key=lambda x: x.get('date', ''), reverse=True)
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
        'url': url, 'image': '', 'date': normalize_date(date_raw) if date_raw else '',
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

_MEMBER_NAMES = frozenset({
    # 塩﨑太智 ── 﨑／崎／嵜 三種字形都收
    '塩﨑太智', '塩崎太智', '塩嵜太智',
    'しおざきたいち', 'シオザキタイチ',
    # 山中柔太朗
    '山中柔太朗',
    'やまなかじゅうたろう', 'ヤマナカジュウタロウ',
    # 佐野勇斗
    '佐野勇斗',
    'さのはやと', 'サノハヤト',
    # 曽野舜太 ── 曽／曾 兩種字形都收
    '曽野舜太', '曾野舜太',
    'そのしゅんた', 'ソノシュンタ',
    # 吉田仁人
    '吉田仁人',
    'よしだまさと', 'ヨシダマサト',
})

def _milk_check(text):
    t = text.lower()
    return 'm!lk' in t or 'ミルク' in text or any(m in text for m in _MEMBER_NAMES)

def _fetch_rss(source, feed_url, limit=8):
    """RSS 通用取得器，套用 M!LK 關鍵字過濾（含成員名字）。"""
    arts = []
    _is_gnews = 'news.google.com' in feed_url
    try:
        feed = feedparser.parse(feed_url)
        for e in feed.entries:
            title   = e.get('title', '')
            # Google News RSS appends "- SOURCE NAME" to every title/summary; strip it
            if _is_gnews:
                title = re.sub(r'\s+-\s+\S+\s*$', '', title).strip()
            summary = clean(e.get('summary', ''))
            if _is_gnews:
                summary = re.sub(r'\s+-\s+\S+\s*$', '', summary).strip()
            if not _milk_check(title + ' ' + summary):
                continue
            arts.append(_make_article(source, title,
                                      e.get('link', ''), e.get('published', ''), summary))
            if len(arts) >= limit:
                break
    except Exception as ex:
        print(f'  [{source}] RSS error: {ex}')
    return arts

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

    # ① 藝人ページ（M!LK 専用）を直接スクレイピング
    try:
        r = requests.get('https://natalie.mu/music/artist/11419',
                         headers=HEADERS, timeout=14)
        r.encoding = 'utf-8'
        soup = BeautifulSoup(r.text, 'html.parser')
        for a in soup.find_all('a', href=re.compile(r'natalie\.mu/music/news/')):
            href = a['href']
            if not href.startswith('http'):
                href = 'https://natalie.mu' + href
            title = clean(a.get_text())
            if not title or len(title) < 5:
                continue
            arts.append(_make_article('Natalie Music', title, href, '', ''))
        print(f'  artist page -> {len(arts)} articles')
    except Exception as e:
        print(f'  artist page error: {e}')

    # ② 全站 RSS でカバー漏れを補完
    rss_arts = _fetch_rss('Natalie Music', 'https://natalie.mu/music/feed/news', limit=15)
    seen = {a['url'] for a in arts}
    for a in rss_arts:
        if a.get('url') and a['url'] not in seen:
            arts.append(a)

    print(f'  -> {len(arts)} articles')
    return arts

# ── Oricon Chart ─────────────────────────────────────────────────────────────

_ORICON_CHART_TARGETS = [
    ('cos', '合算シングル'),
    ('coa', '合算アルバム'),
    ('js',  'シングル'),
    ('ja',  'アルバム'),
    ('dis', 'デジタルシングル'),
    ('st',  'ストリーミング'),
]

# M!LK group + all member names (incl. common kanji variants) for chart matching
_CHART_MATCH_NAMES = frozenset({
    'M!LK', 'ミルク',
    '吉田仁人',
    '佐野勇斗',
    '山中柔太朗',
    '塩﨑太智', '塩崎太智', '塩嵜太智',
    '曽野舜太', '曾野舜太',
})

# Canonical member names for display (longest match wins)
_CHART_MEMBERS = ['吉田仁人', '佐野勇斗', '山中柔太朗', '塩﨑太智', '塩崎太智', '塩嵜太智', '曽野舜太', '曾野舜太']

def _chart_match(artist):
    return any(n in artist for n in _CHART_MATCH_NAMES)

def _chart_member(artist):
    """Return the canonical M!LK member name found in artist string, or 'M!LK'."""
    for n in _CHART_MEMBERS:
        if n in artist:
            return n
    return 'M!LK'

def _parse_oricon_chart_page(chart_code, chart_name, date_str, page=1):
    """Fetch one page of an Oricon weekly chart; return entries matching M!LK/members."""
    url = f'https://www.oricon.co.jp/rank/{chart_code}/w/{date_str}/'
    if page > 1:
        url += f'p/{page}/'
    try:
        r = requests.get(url, headers=HEADERS, timeout=14)
        if r.status_code != 200:
            return []
        r.encoding = r.encoding or r.apparent_encoding or 'shift_jis'
        soup = BeautifulSoup(r.text, 'html.parser')
        entries = []
        for sec in soup.select('section.box-rank-entry'):
            num_el = sec.select_one('p.num')
            if not num_el:
                continue
            try:
                rank = int(num_el.get_text(strip=True))
            except ValueError:
                continue
            artist_el = sec.select_one('p.name')
            artist = clean(artist_el.get_text()) if artist_el else ''
            if not artist or not _chart_match(artist):
                continue
            status_el = sec.select_one('p.status')
            status = status_el.get_text(strip=True) if status_el else ''
            title_el = sec.select_one('h2.title')
            title = clean(title_el.get_text()) if title_el else ''
            img_el = sec.select_one('p.image img')
            img = (img_el.get('src', '') or img_el.get('data-src', '')) if img_el else ''
            # filter out Amazon dummy placeholder gif
            if img and img.endswith('.gif') and 'MKUOLsA5L' in img:
                img = ''
            link_el = (sec.select_one('p.image a') or
                       sec.select_one('a[href*="/prof/"]') or
                       sec.select_one('a[href*="/rank/"]'))
            art_url = ''
            if link_el:
                href = link_el.get('href', '')
                art_url = href if href.startswith('http') else 'https://www.oricon.co.jp' + href
            label, release_date = '', ''
            for li in sec.select('ul.list li'):
                t = clean(li.get_text())
                if '発売日' in t:
                    release_date = t.replace('発売日：', '').replace('発売日:', '').strip()
                elif t and not label:
                    label = t
            entries.append({
                'id': make_id(f'{chart_code}_{date_str}_{rank}_{artist}_{title}'),
                'chart_code': chart_code,
                'chart_name': chart_name,
                'date': date_str,
                'rank': rank,
                'status': status,
                'title': title,
                'artist': artist,
                'member': _chart_member(artist),
                'image': img,
                'url': art_url,
                'label': label,
                'release_date': release_date,
            })
        return entries
    except Exception as e:
        print(f'  [Chart] {url}: {e}')
        return []

def fetch_oricon_charts():
    """Scrape recent 12 weeks of Oricon weekly charts for M!LK/member appearances."""
    print('Fetching Oricon charts...')
    today = datetime.now(JST).replace(tzinfo=None)
    # Weekly chart dates are Mondays; compute last 12 Mondays
    last_monday = today - timedelta(days=today.weekday())
    chart_dates = [(last_monday - timedelta(weeks=w)).strftime('%Y-%m-%d') for w in range(12)]

    all_entries, seen_ids = [], set()
    for chart_code, chart_name in _ORICON_CHART_TARGETS:
        found_code = 0
        for date_str in chart_dates:
            entries = _parse_oricon_chart_page(chart_code, chart_name, date_str)
            for e in entries:
                if e['id'] not in seen_ids:
                    seen_ids.add(e['id'])
                    all_entries.append(e)
                    found_code += 1
            time.sleep(0.4 if entries else 0.15)
        if found_code:
            print(f'  [{chart_name}] {found_code} entries')
    print(f'  -> {len(all_entries)} chart entries total')
    return all_entries


# Specific Oricon article URLs to always include (not always caught by Google News RSS)
_ORICON_SEED_URLS = [
    'https://www.oricon.co.jp/news/2459973/full/',
    'https://www.oricon.co.jp/news/2461188/full/',
]

def _norm_oricon_url(url):
    """Normalize Oricon article URL to /full/ form."""
    if not url:
        return ''
    url = url.rstrip('/')
    url = re.sub(r'/(?:embed(?:/video)?|video)$', '', url)
    if not url.endswith('/full'):
        url = url + '/full/'
    else:
        url = url + '/'
    return url

def _fetch_oricon_article(url):
    """Directly fetch a single Oricon article page and return article dict."""
    url = _norm_oricon_url(url)
    try:
        r = requests.get(url, headers=HEADERS, timeout=12)
        if r.status_code != 200:
            return None
        # Honour Content-Type charset first; fall back to apparent_encoding
        r.encoding = r.encoding or r.apparent_encoding or 'utf-8'
        soup = BeautifulSoup(r.text, 'html.parser')
        # Title: og:title is most reliable
        title = ''
        og_title = soup.select_one('meta[property="og:title"]')
        if og_title:
            title = clean(og_title.get('content', ''))
        if not title:
            h1 = soup.select_one('h1.newsTitle, h1.title, h1')
            if h1:
                title = clean(h1.get_text())
        if not title:
            return None
        # Date
        date_raw = ''
        for sel in ['time[datetime]', 'time', '.newsDate', '.date', '[class*="date"]']:
            el = soup.select_one(sel)
            if el:
                date_raw = el.get('datetime', '') or clean(el.get_text())
                if date_raw:
                    break
        # Summary
        summary = ''
        og_desc = soup.select_one('meta[property="og:description"]')
        if og_desc:
            summary = clean(og_desc.get('content', ''))[:280]
        if not summary:
            for sel in ['.newsLead', '.lead', '.article-lead', 'p']:
                el = soup.select_one(sel)
                if el:
                    summary = clean(el.get_text())[:280]
                    break
        # Image
        img = ''
        og_img = soup.select_one('meta[property="og:image"]')
        if og_img:
            img = og_img.get('content', '')
        art = _make_article('Oricon', title, url, date_raw, summary)
        art['image'] = img
        return art
    except Exception as e:
        print(f'  [Oricon] direct fetch error for {url}: {e}')
        return None

def fetch_oricon():
    print('Fetching Oricon...')
    seen, arts = set(), []

    # ① Google News RSS（"M!LK" site:oricon.co.jp 専用クエリ）
    gnews_url = ('https://news.google.com/rss/search'
                 '?q=%22M%21LK%22+site%3Aoricon.co.jp'
                 '&hl=ja&gl=JP&ceid=JP%3Aja')
    gnews_arts = _fetch_rss('Oricon', gnews_url, limit=20)
    for a in gnews_arts:
        u = _norm_oricon_url(a.get('url', ''))
        if u and u not in seen:
            seen.add(u)
            a['url'] = u
            arts.append(a)

    # ② 全成員名 + 常見誤字で追加検索（グループ名だけでは漏れる記事を補完）
    #    塩﨑：﨑(FA11)／崎(5D0E)／嵜(5D5A) 三種字形
    #    曽野：曽(66FD)／曾(66FE) 二種字形
    _member_qs = [
        '吉田仁人',                        # 正式
        '佐野勇斗',                        # 正式
        '山中柔太朗',                      # 正式
        '塩﨑太智', '塩崎太智', '塩嵜太智',  # 正式 + よくある誤字2種
        '曽野舜太', '曾野舜太',            # 正式 + よくある誤字
    ]
    for q in _member_qs:
        member_url = (f'https://news.google.com/rss/search'
                      f'?q={_url_quote(q)}+site%3Aoricon.co.jp'
                      f'&hl=ja&gl=JP&ceid=JP%3Aja')
        for a in _fetch_rss('Oricon', member_url, limit=8):
            u = _norm_oricon_url(a.get('url', ''))
            if u and u not in seen:
                seen.add(u)
                a['url'] = u
                arts.append(a)

    # ③ 指定記事 URL を直接取得（Google News RSS に出ない古い記事や動画記事を補完）
    for seed_url in _ORICON_SEED_URLS:
        norm = _norm_oricon_url(seed_url)
        if norm not in seen:
            art = _fetch_oricon_article(norm)
            if art:
                seen.add(norm)
                arts.append(art)
                print(f'  [Oricon] seed fetched: {art.get("title_ja", "")[:40]}')
            else:
                print(f'  [Oricon] seed fetch failed: {norm}')

    # ④ Oricon 公式 RSS は HTTP 410 Gone のため削除済み

    print(f'  -> {len(arts)} articles (gnews:{len(gnews_arts)})')
    return arts

def fetch_barks():
    print('Fetching BARKS...')
    seen, arts = set(), []
    # ① Google News RSS（M!LK 専用クエリ、一般 RSS の埋もれ対策）
    for a in _fetch_rss('BARKS',
            'https://news.google.com/rss/search'
            '?q=%22M%21LK%22+site%3Abarks.jp&hl=ja&gl=JP&ceid=JP%3Aja',
            limit=15):
        u = a.get('url', '')
        if u and u not in seen:
            seen.add(u); arts.append(a)
    # ② BARKS 公式 RSS 補完
    for a in _fetch_rss('BARKS', 'https://www.barks.jp/rss/', limit=20):
        u = a.get('url', '')
        if u and u not in seen:
            seen.add(u); arts.append(a)
    if not arts:
        arts = _scrape_search('BARKS',
            'https://www.barks.jp/search/?q=m%21lk&type=news',
            'https://www.barks.jp',
            ['.search-result li', '.news-list li', '.list li', 'article', '.item'],
            keyword_check=_milk_check)
    print(f'  -> {len(arts)} articles')
    return arts

def fetch_sanspo():
    print('Fetching SANSPO...')
    seen, arts = set(), []
    # ① Google News RSS（M!LK 専用クエリ）
    for a in _fetch_rss('SANSPO',
            'https://news.google.com/rss/search'
            '?q=%22M%21LK%22+site%3Asanspo.com&hl=ja&gl=JP&ceid=JP%3Aja',
            limit=15):
        u = a.get('url', '')
        if u and u not in seen:
            seen.add(u); arts.append(a)
    # ② SANSPO 公式 RSS 補完
    for a in _fetch_rss('SANSPO', 'https://www.sanspo.com/rss/', limit=20):
        u = a.get('url', '')
        if u and u not in seen:
            seen.add(u); arts.append(a)
    if not arts:
        arts = _scrape_search('SANSPO',
            'https://www.sanspo.com/search/?q=m%21lk',
            'https://www.sanspo.com',
            ['.search-list li', '.article-list li', 'article', '.news-list li', '.list li'],
            keyword_check=_milk_check)
    print(f'  -> {len(arts)} articles')
    return arts

def fetch_billboard():
    print('Fetching Billboard Japan...')
    arts = _fetch_rss('Billboard Japan', 'https://www.billboard-japan.com/d_news/rss/')
    if not arts:
        arts = _scrape_search('Billboard Japan',
            'https://www.billboard-japan.com/d_news/?q=m%21lk',
            'https://www.billboard-japan.com',
            ['.news-list li', '.list li', '.d-news-list li', 'article', 'ul li'],
            keyword_check=_milk_check)
    print(f'  -> {len(arts)} articles')
    return arts

def fetch_musicvoice():
    print('Fetching Musicvoice...')
    arts = _fetch_rss('Musicvoice', 'https://www.musicvoice.jp/feed/')
    if not arts:
        arts = _scrape_search('Musicvoice',
            'https://www.musicvoice.jp/?s=m%21lk',
            'https://www.musicvoice.jp',
            ['.post-list li', 'article', '.search-result li', '.list li', '.news-list li'],
            keyword_check=_milk_check)
    print(f'  -> {len(arts)} articles')
    return arts

def fetch_spice():
    print('Fetching SPICE (eplus)...')
    arts = _fetch_rss('SPICE', 'https://spice.eplus.jp/feed/')
    if not arts:
        arts = _scrape_search('SPICE',
            'https://spice.eplus.jp/?s=M%21LK',
            'https://spice.eplus.jp',
            ['article', '.article-list li', '.search-result li', '.list li', '.news-list li'],
            keyword_check=_milk_check)
    print(f'  -> {len(arts)} articles')
    return arts

def fetch_musicman():
    print('Fetching Musicman...')
    arts = _fetch_rss('Musicman', 'https://www.musicman.co.jp/feed/')
    if not arts:
        arts = _scrape_search('Musicman',
            'https://www.musicman.co.jp/search/?q=m%21lk',
            'https://www.musicman.co.jp',
            ['.article-list li', '.list li', 'article', '.search-result li', '.news-list li'],
            keyword_check=_milk_check)
    print(f'  -> {len(arts)} articles')
    return arts

def fetch_thefirsttimes():
    print('Fetching The First Times...')
    arts = _fetch_rss('The First Times', 'https://www.thefirsttimes.jp/feed/')
    if not arts:
        arts = _scrape_search('The First Times',
            'https://www.thefirsttimes.jp/?s=M%21LK',
            'https://www.thefirsttimes.jp',
            ['article', '.post-list li', '.search-result li', '.list li', '.news-list li'],
            keyword_check=_milk_check)
    print(f'  -> {len(arts)} articles')
    return arts

def fetch_modelpress():
    print('Fetching Modelpress...')
    seen, arts = set(), []
    # ① Google News RSS（M!LK 専用クエリ）
    for a in _fetch_rss('Modelpress',
            'https://news.google.com/rss/search'
            '?q=%22M%21LK%22+site%3Amdpr.jp&hl=ja&gl=JP&ceid=JP%3Aja',
            limit=15):
        u = a.get('url', '')
        if u and u not in seen:
            seen.add(u); arts.append(a)
    # ② Modelpress 公式 RSS 補完
    for a in _fetch_rss('Modelpress', 'https://mdpr.jp/feed/', limit=20):
        u = a.get('url', '')
        if u and u not in seen:
            seen.add(u); arts.append(a)
    if not arts:
        arts = _scrape_search('Modelpress',
            'https://mdpr.jp/search?q=m%21lk',
            'https://mdpr.jp',
            ['.article-list li', 'article', '.list li', '.search-result li', '.news-list li'],
            keyword_check=_milk_check)
    print(f'  -> {len(arts)} articles')
    return arts

def fetch_biteki():
    print('Fetching 美的.com...')
    arts = _fetch_rss('美的.com', 'https://www.biteki.com/feed/')
    if not arts:
        arts = _scrape_search('美的.com',
            'https://www.biteki.com/?s=m%21lk',
            'https://www.biteki.com',
            ['article', '.article-list li', '.post-list li', '.search-result li', '.list li'],
            keyword_check=_milk_check)
    print(f'  -> {len(arts)} articles')
    return arts

def fetch_mensnonno():
    print('Fetching MEN\'S NON-NO...')
    arts = _fetch_rss("MEN'S NON-NO", 'https://www.mensnonno.jp/feed/')
    if not arts:
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
    arts = _fetch_rss('TVガイドWeb', 'https://www.tvguide.or.jp/rss/')
    if not arts:
        arts = _scrape_search('TVガイドWeb',
            'https://www.tvguide.or.jp/cmn_keyword/mlk/',
            'https://www.tvguide.or.jp',
            ['.news-list li', '.article-list li', 'article', '.list li', '.keyword-news li'],
            keyword_check=_milk_check)
    print(f'  -> {len(arts)} articles')
    return arts

def fetch_thetv():
    print('Fetching WEBザテレビジョン...')
    arts = _fetch_rss('WEBザテレビジョン', 'https://thetv.jp/rss/')
    if not arts:
        arts = _scrape_search('WEBザテレビジョン',
            'https://thetv.jp/news/search/?q=m%21lk',
            'https://thetv.jp',
            ['.news-list li', 'article', '.article-list li', '.list li', '.search-result li'],
            keyword_check=_milk_check)
    print(f'  -> {len(arts)} articles')
    return arts

def fetch_ebidan():
    print('Fetching ebidan.jp...')
    arts = _fetch_rss('ebidan.jp', 'https://ebidan.jp/feed/')
    if not arts:
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
# (name, badge, station, schedule_text, url, color, status)
# status: 'active'=現在放送中  'upcoming'=公開予定  'release'=リリース予定
# Badge 顏色原則：同類型 badge 使用同一顏色，不與商品頁分類色重疊
BADGE_COLORS = {
    'TV':     '#b71c1c',   # 暗紅
    'ドラマ': '#b71c1c',   # 暗紅（TV と同色系）
    'ラジオ': '#0277bd',   # 統一藍（兩個節目都用這個）
    '映画':   '#37474f',   # 深灰
    'リリース': '#6a1b9a', # 深紫（避開 FC限定 #9c27b0 和 公式グッズ #1565c0）
}

SCHEDULE_PROGRAMS = [
    # (name, badge, station, sched, url, color, status, dow_list, members)
    # dow_list: 放送曜日 0=月 1=火 2=水 3=木 4=金 5=土 6=日, None=不明
    # members: None=全メンバー / str=1名 / list=複数名
    # ── 現在放送中 ──
    ('やってM!LK',
     'TV', 'TBS テレビ', '定期放送中',
     'https://www.tbs.co.jp/yatte_milk/', BADGE_COLORS['TV'], 'active', None, None),
    ('レコメン!',
     'ラジオ', '文化放送 毎週木曜', '定期放送中',
     'https://www.joqr.co.jp/qr/program/reco/', BADGE_COLORS['ラジオ'], 'active', [3], '吉田仁人'),
    ('イマドキッ ドゥフドゥフナイト',
     'ラジオ', 'MBSラジオ 毎週水曜', '定期放送中',
     'https://www.mbs1179.com/imadoki/', BADGE_COLORS['ラジオ'], 'active', [2], None),
    # ── 公開予定・近日 ──
    ('君の好きは無敵',
     'ドラマ', 'TBS テレビ 火曜22:00〜', '放送予定',
     'https://www.tbs.co.jp/kiminosukihamuteki_tbs/', BADGE_COLORS['ドラマ'], 'upcoming', [1], None),
    ('トイ・ストーリー５',
     '映画', 'Disney / 2026年夏公開予定', '公開予定',
     'https://www.disney.co.jp/movie/toy5', BADGE_COLORS['映画'], 'upcoming', None, None),
    ('仮面ライダーゼッツ＆超宇宙刑事ギャバン',
     '映画', '2026年夏公開予定', '公開予定',
     'https://zeztz-gavan-26movie.com/', BADGE_COLORS['映画'], 'upcoming', None, None),
    # ── リリース ──
    ('M!LK 1st Mini Album（タイトル未定）',
     'リリース', '2026年09月16日発売', '予約受付中',
     'https://victor-store.jp/', BADGE_COLORS['リリース'], 'release', None, None),
]

# Auto-fetched schedule cache (populated at runtime by fetch_prog_schedules())
_prog_sched_cache = {}

# Patterns to detect broadcast day/time in program pages
_SCHED_PATS = [
    re.compile(r'(?:放送日時|ON ?AIR|放送時間|放送曜日|オンエア)[：:\s　]*([^\n<>]{4,40})', re.IGNORECASE),
    re.compile(r'毎週[月火水木金土日〜～・]+曜?(?:日)?(?:[（(][^）)]{0,12}[）)])?[　\s]*[0-9０-９]{1,2}[:：][0-9０-９]{2}[^\n<>]{0,20}'),
    re.compile(r'[月火水木金土日][〜～][月火水木金土日]曜(?:日)?[　\s]*[0-9０-９]{1,2}[:：][0-9０-９]{2}[^\n<>]{0,20}'),
]

def _fetch_prog_sched(name, url):
    """Fetch a program page and extract broadcast schedule string. Returns '' on failure."""
    try:
        resp = requests.get(url, timeout=12, headers=HEADERS)
        if resp.status_code != 200:
            return ''
        soup = BeautifulSoup(resp.text, 'html.parser')
        # Search in schedule-related tags first, then fall back to full text
        candidates = []
        for sel in ['[class*="sched"]', '[class*="time"]', '[class*="airtime"]',
                    '[class*="onair"]', '[class*="broadcast"]', 'dt', 'dd', 'th', 'td', 'p']:
            for el in soup.select(sel)[:30]:
                candidates.append(el.get_text(' ', strip=True))
        candidates.append(soup.get_text(' ', strip=True))
        for text in candidates:
            for pat in _SCHED_PATS:
                m = pat.search(text)
                if m:
                    raw = (m.group(1) if m.lastindex else m.group(0)).strip()
                    raw = re.sub(r'[\s　]+', ' ', raw)[:50]
                    # Must contain digits to be a real schedule
                    if re.search(r'[0-9０-９]', raw):
                        return raw
    except Exception as e:
        print(f'  [{name}] sched fetch error: {e}')
    return ''

def fetch_prog_schedules():
    """Auto-fetch broadcast schedules for active programs and store in cache."""
    global _prog_sched_cache
    targets = [
        ('やってM!LK',               'https://www.tbs.co.jp/yatte_milk/'),
        ('レコメン!',                 'https://www.joqr.co.jp/qr/program/reco/'),
        ('イマドキッ ドゥフドゥフナイト', 'https://www.mbs1179.com/imadoki/'),
    ]
    print('Fetching program schedules...')
    for name, url in targets:
        sched = _fetch_prog_sched(name, url)
        if sched:
            _prog_sched_cache[name] = sched
            print(f'  [{name}] → {sched}')
        else:
            print(f'  [{name}] → (not found, keeping default)')


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


# ── 手動登録の既知商品（自動取得できないサイト用）────────────────────────────
# JS描画サイトやログイン必須ストアは自動取得不可のため、既知商品をここに手動登録
KNOWN_GOODS = [
    {
        'id':       'milk-1st-mini-album-regular',
        'source':   'VICTOR STORE',
        'category': 'CD・リリース',
        'title':    'M!LK 1st Mini Album（タイトル未定）通常盤 CD',
        'title_zh': 'M!LK 1st Mini Album（標題未定）通常版 CD',
        'price':    '¥2,530（税込）',
        'url':      'https://victor-store.jp/artist/6305',
        'image':    '',
        'date':     '2026-09-16',
    },
    {
        'id':       'milk-1st-mini-album-limited',
        'source':   'VICTOR STORE',
        'category': 'CD・リリース',
        'title':    'M!LK 1st Mini Album（タイトル未定）初回限定盤 CD+Blu-ray',
        'title_zh': 'M!LK 1st Mini Album（標題未定）初回限定版 CD+Blu-ray',
        'price':    '¥3,520（税込）',
        'url':      'https://victor-store.jp/artist/6305',
        'image':    '',
        'date':     '2026-09-16',
    },
    {
        'id':       'milk-1st-mini-album-solo-sano',
        'source':   'VICTOR STORE',
        'category': 'CD・リリース',
        'title':    'M!LK 1st Mini Album ソロ盤 佐野勇斗',
        'title_zh': 'M!LK 1st Mini Album 個人版 佐野勇斗',
        'price':    '¥2,530（税込）',
        'url':      'https://victor-store.jp/artist/6305',
        'image':    '',
        'date':     '2026-09-16',
    },
    {
        'id':       'milk-1st-mini-album-solo-shiozaki',
        'source':   'VICTOR STORE',
        'category': 'CD・リリース',
        'title':    'M!LK 1st Mini Album ソロ盤 塩﨑太智',
        'title_zh': 'M!LK 1st Mini Album 個人版 塩﨑太智',
        'price':    '¥2,530（税込）',
        'url':      'https://victor-store.jp/artist/6305',
        'image':    '',
        'date':     '2026-09-16',
    },
    {
        'id':       'milk-1st-mini-album-solo-sono',
        'source':   'VICTOR STORE',
        'category': 'CD・リリース',
        'title':    'M!LK 1st Mini Album ソロ盤 曽野舜太',
        'title_zh': 'M!LK 1st Mini Album 個人版 曽野舜太',
        'price':    '¥2,530（税込）',
        'url':      'https://victor-store.jp/artist/6305',
        'image':    '',
        'date':     '2026-09-16',
    },
    {
        'id':       'milk-1st-mini-album-solo-yamanaka',
        'source':   'VICTOR STORE',
        'category': 'CD・リリース',
        'title':    'M!LK 1st Mini Album ソロ盤 山中柔太朗',
        'title_zh': 'M!LK 1st Mini Album 個人版 山中柔太朗',
        'price':    '¥2,530（税込）',
        'url':      'https://victor-store.jp/artist/6305',
        'image':    '',
        'date':     '2026-09-16',
    },
    {
        'id':       'milk-1st-mini-album-solo-yoshida',
        'source':   'VICTOR STORE',
        'category': 'CD・リリース',
        'title':    'M!LK 1st Mini Album ソロ盤 吉田仁人',
        'title_zh': 'M!LK 1st Mini Album 個人版 吉田仁人',
        'price':    '¥2,530（税込）',
        'url':      'https://victor-store.jp/artist/6305',
        'image':    '',
        'date':     '2026-09-16',
    },
]

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
    # VICTOR STORE は Next.js SPA のため自動取得不可。KNOWN_GOODS で手動管理。
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


def _fetch_fc_goods():
    """plusmember.jp FC shop — EC-CUBE v3/v4 specific scraper."""
    result = []
    url = 'https://store.plusmember.jp/stardustch/products/list.php?category_id=891'
    base = 'https://store.plusmember.jp'
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.encoding = r.apparent_encoding or 'utf-8'
        soup = BeautifulSoup(r.text, 'html.parser')

        # EC-CUBE v3 selectors
        items = (soup.select('.ec-shelfGrid__item') or
                 soup.select('.ec-shelf__item') or
                 soup.select('.bloc_cart_items li') or
                 soup.select('.item_box') or
                 soup.select('.item-list li'))

        print(f'  [FC] found {len(items)} items with EC-CUBE selectors')

        for it in items[:30]:
            a_tag = it.find('a', href=True)
            if not a_tag:
                continue
            href = a_tag['href']
            if not href.startswith('http'):
                href = base + href
            name_el = it.select_one(
                '.ec-shelfGrid__item-name, .item_name, .item-name, '
                '.goods_name, h3, h4, p.name'
            )
            name = clean(name_el.get_text()) if name_el else clean(a_tag.get_text())[:100]
            if len(name) < 3:
                continue
            price_el = it.select_one(
                '.ec-price__price, .price, .item_price, '
                '.goods_price, [class*=price]'
            )
            price = clean(price_el.get_text()) if price_el else ''
            img_el = it.find('img')
            img = ''
            if img_el:
                img = img_el.get('src') or img_el.get('data-src') or ''
                if img and not img.startswith('http'):
                    img = base + img
            result.append(_make_good('FC限定 (STARDUST)', name, href, img, price, '', 'FC限定'))

        print(f'  [FC] -> {len(result)} goods scraped')
    except Exception as e:
        print(f'  [FC] Error: {e}')
    return result


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

    # Always include manually registered known goods (not overwritten by scrape results)
    known_ids = {g['id'] for g in KNOWN_GOODS}
    scraped_non_known = [g for g in all_goods if g['id'] not in known_ids]

    # Scrape FC goods specifically (plusmember.jp, public page)
    fc_scraped = _fetch_fc_goods()
    # Deduplicate FC scraped against already-seen
    fc_scraped = [g for g in fc_scraped if g['url'] not in seen_urls]

    all_goods = KNOWN_GOODS + fc_scraped + scraped_non_known

    print(f'Goods total: {len(all_goods)} ({len(KNOWN_GOODS)} known + {len(scraped_non_known)} scraped)')
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


_MONTH_MAP = {
    'Jan':'01','Feb':'02','Mar':'03','Apr':'04','May':'05','Jun':'06',
    'Jul':'07','Aug':'08','Sep':'09','Oct':'10','Nov':'11','Dec':'12',
}


def fetch_sd_milk_calendar_pw():
    """Playwright で sd-milk.com/calendar を取得（JS-rendered SPA）"""
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        print('  Playwright not installed, skip sd-milk.com/calendar')
        return []
    events = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(extra_http_headers={
                'User-Agent': HEADERS['User-Agent'],
                'Accept-Language': 'ja,en;q=0.9',
            })
            page.goto('https://sd-milk.com/calendar', timeout=30000)
            try:
                page.wait_for_selector('a[href^="/contents/"]', timeout=15000)
            except PWTimeout:
                pass
            html = page.content()
            browser.close()

        soup = BeautifulSoup(html, 'html.parser')
        DATE_RE = re.compile(r'(\d{4})[./年-](\d{1,2})[./月](\d{1,2})')
        seen = set()
        for a in soup.find_all('a', href=re.compile(r'/contents/')):
            href = a['href']
            if href in seen:
                continue
            seen.add(href)
            full_url = ('https://sd-milk.com' + href
                        if href.startswith('/') else href)
            li = a.find_parent('li') or a.parent
            context = clean(li.get_text()) if li else clean(a.get_text())
            m = DATE_RE.search(context)
            if not m:
                continue
            date_str = (f"{m.group(1)}-{m.group(2).zfill(2)}"
                        f"-{m.group(3).zfill(2)}")
            if date_str[:4] < '2025':
                continue
            cat = next((c for c in ('TV', 'RADIO', 'LIVE', 'RELEASE', 'WEB')
                        if c in context.upper()), '')
            lines = [l.strip() for l in context.splitlines() if l.strip()]
            title   = lines[0][:120] if lines else context[:120]
            details = (cat + ' / ' if cat else '') + (lines[1] if len(lines) > 1 else '')
            events.append({
                'id':       make_id(title[:50] + date_str),
                'date':     date_str,
                'title':    title,
                'details':  details[:200],
                'title_zh': '',
                'url':      full_url,
            })
        print(f'  Playwright sd-milk.com -> {len(events)} events')
    except Exception as e:
        print(f'  Playwright error: {e}')
    return events


def fetch_sd_milk_programs_pw():
    """
    Playwright で sd-milk.com/contents/schedule を取得し、
    出演番組・作品リストを自動抽出する。
    returns list of dicts: {name, type, station, status, url, start_date, end_date}
    """
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        print('  Playwright not installed, skip sd-milk programs fetch')
        return []

    programs = []
    TYPE_KW = {
        'ドラマ':  ['ドラマ', 'drama', '連続ドラマ', '主演', '共演'],
        'TV':      ['テレビ', 'バラエティ', 'tv', 'tbs', 'フジ', 'ntv', '日テレ', 'テレ朝', 'abc', 'バラ'],
        'ラジオ':  ['ラジオ', 'radio', 'fm', 'joqr', 'mbs', 'bay fm'],
        '映画':    ['映画', 'movie', 'film', '劇場'],
        'イベント':['イベント', 'ライブ', 'live', 'concert', '公演', 'fanmeet', 'fanclub'],
        'リリース':['cd', 'dvd', 'blu-ray', 'album', 'single', '発売', 'リリース'],
    }
    STATUS_KW = {
        'active':   ['放送中', '出演中', 'on air', 'レギュラー', '毎週'],
        'upcoming': ['予定', '決定', '公開', '近日', 'coming'],
        'release':  ['発売', 'リリース', '予約'],
    }

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(extra_http_headers={
                'User-Agent': HEADERS['User-Agent'],
                'Accept-Language': 'ja,en;q=0.9',
            })
            page.goto('https://sd-milk.com/contents/schedule', timeout=30000)
            try:
                page.wait_for_selector('a, article, li', timeout=15000)
            except PWTimeout:
                pass
            html = page.content()
            browser.close()

        soup = BeautifulSoup(html, 'html.parser')
        DATE_RE = re.compile(r'(\d{4})[./年-](\d{1,2})[./月](\d{1,2})')
        seen_names = set()

        # Gather candidate elements (links + list items + articles)
        candidates = soup.find_all(['a', 'li', 'article', 'div'], limit=300)
        for el in candidates:
            text = re.sub(r'\s+', ' ', el.get_text()).strip()
            if len(text) < 5 or len(text) > 300:
                continue
            tl = text.lower()

            # Determine program type
            prog_type = ''
            for t, kws in TYPE_KW.items():
                if any(k in tl for k in kws):
                    prog_type = t
                    break
            if not prog_type:
                continue

            # Determine status
            status = 'upcoming'
            for s, kws in STATUS_KW.items():
                if any(k in tl for k in kws):
                    status = s
                    break

            # Name: first meaningful line
            lines = [l.strip() for l in text.splitlines() if l.strip() and len(l.strip()) > 3]
            name = lines[0][:80] if lines else text[:80]
            if name in seen_names:
                continue
            seen_names.add(name)

            # URL
            a_tag = el.find('a', href=True) if el.name != 'a' else el
            if a_tag and a_tag.get('href', '').startswith('/'):
                url = 'https://sd-milk.com' + a_tag['href']
            elif a_tag and a_tag.get('href', '').startswith('http'):
                url = a_tag['href']
            else:
                url = 'https://sd-milk.com/contents/schedule'

            # Dates
            dates = DATE_RE.findall(text)
            start_date = f"{dates[0][0]}-{dates[0][1].zfill(2)}-{dates[0][2].zfill(2)}" if dates else ''
            end_date   = f"{dates[-1][0]}-{dates[-1][1].zfill(2)}-{dates[-1][2].zfill(2)}" if len(dates) > 1 else ''

            # Station: second line often contains station info
            station = lines[1][:60] if len(lines) > 1 else ''

            programs.append({
                'name':       name,
                'type':       prog_type,
                'station':    station,
                'status':     status,
                'url':        url,
                'start_date': start_date,
                'end_date':   end_date,
            })

        print(f'  sd-milk programs Playwright -> {len(programs)} programs found')
    except Exception as e:
        print(f'  sd-milk programs Playwright error: {e}')
    return programs


# Runtime-fetched program list (populated in main(), used in generate_schedule_html())
_dynamic_programs = []   # list of dicts from fetch_sd_milk_programs_pw()


def fetch_setlistfm(include_past=True, past_years=3):
    """setlist.fm から M!LK のライブ日程を取得（過去 + 未来）"""
    url = 'https://www.setlist.fm/setlists/mlk-53cb6b39.html'
    try:
        r = requests.get(url, headers={**HEADERS,
                         'Accept-Language': 'en-US,en;q=0.9'}, timeout=15)
        if r.status_code != 200:
            print(f'  setlist.fm HTTP {r.status_code}')
            return []
        r.encoding = 'utf-8'
    except Exception as e:
        print(f'  setlist.fm error: {e}')
        return []

    soup = BeautifulSoup(r.text, 'html.parser')
    today = datetime.today().strftime('%Y-%m-%d')
    # past cutoff: only include past events within past_years years
    past_cutoff = str(int(today[:4]) - past_years) + today[4:]
    events = []

    def _abs_link(href):
        if not href:
            return url
        if href.startswith('../'):
            return 'https://www.setlist.fm/' + href[3:]
        if href.startswith('/'):
            return 'https://www.setlist.fm' + href
        return href

    # ── 実際の構造:
    #   <div class="setlist">
    #     <span class="eventDate"><strong>Jul</strong> <strong>04</strong> 2026</span>
    #     <a href="../venue/...">VENUE NAME, City, Japan</a>
    #     <h3><a href="../setlist/...">M!LK at VENUE...</a></h3>
    # ───────────────────────────────────────────────────────
    for container in soup.find_all('div', class_='setlist'):
        date_span = container.find('span', class_='eventDate')
        if not date_span:
            continue
        strongs = date_span.find_all('strong')
        if len(strongs) < 2:
            continue
        mon = strongs[0].get_text(strip=True)[:3].capitalize()
        day = strongs[1].get_text(strip=True)
        yr_m = re.search(r'(\d{4})', date_span.get_text())
        if not yr_m or mon not in _MONTH_MAP:
            continue
        date_str = f"{yr_m.group(1)}-{_MONTH_MAP[mon]}-{day.zfill(2)}"
        if date_str < today and (not include_past or date_str < past_cutoff):
            continue

        # venue link text: "LINE CUBE SHIBUYA, Tokyo, Japan"
        venue_a = container.find('a', href=re.compile(r'/venue/'))
        venue_full = clean(venue_a.get_text()) if venue_a else ''
        if ',' in venue_full:
            parts = [p.strip() for p in venue_full.split(',')]
            venue = parts[0]
            city  = parts[1] if len(parts) > 1 else ''
        else:
            venue = venue_full
            city  = venue_full  # 場館名そのものを details に残す

        # setlist / show link
        link_a = (container.find('a', href=re.compile(r'/setlist/')) or
                  container.find('a', href=re.compile(r'/show/')))
        link = _abs_link(link_a['href']) if link_a else url

        title = f'[LIVE] {venue}' if venue else '[LIVE] M!LK Concert'
        events.append({
            'id':       make_id(title[:50] + date_str),
            'date':     date_str,
            'title':    title,
            'details':  city[:200],
            'title_zh': '',
            'url':      link,
            'is_past':  date_str < today,
        })

    upcoming = sum(1 for e in events if not e.get('is_past'))
    past     = len(events) - upcoming
    print(f'  setlist.fm -> {upcoming} upcoming + {past} past events')
    return events


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

    # ── Step 4: Playwright fallback (sd-milk.com SPA) ────────────────────────
    if not events:
        print('  Trying Playwright for sd-milk.com/calendar...')
        events += fetch_sd_milk_calendar_pw()

    # ── Always merge setlist.fm upcoming live dates ───────────────────────────
    print('  Fetching setlist.fm live dates...')
    events += fetch_setlistfm()

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
.page-nav{display:flex;justify-content:center;gap:0;overflow-x:auto;-webkit-overflow-scrolling:touch;scrollbar-width:none;}
.page-nav::-webkit-scrollbar{display:none;}
.nav-link{padding:10px 24px;font-size:.8rem;font-weight:600;letter-spacing:.08em;text-decoration:none;color:var(--muted);border-bottom:3px solid transparent;transition:color .2s,border-color .2s;white-space:nowrap;flex-shrink:0;}
.nav-link:hover{color:var(--blue);}
.nav-link.nav-active{color:var(--blue);border-bottom-color:var(--blue);}
.sources{display:flex;justify-content:center;gap:5px;flex-wrap:wrap;padding:12px 20px;}
.sources span{padding:3px 10px;border-radius:20px;font-size:.63rem;font-weight:700;color:#fff;}
.main-wrap{max-width:1100px;margin:0 auto;padding:22px 16px 80px;display:grid;grid-template-columns:258px 1fr;gap:22px;align-items:start;}
@media(max-width:760px){.main-wrap{grid-template-columns:1fr;}}
.calendar-wrap{background:var(--surface);border:1px solid var(--border);border-radius:16px;padding:16px;position:sticky;top:20px;box-shadow:var(--shadow);}
@media(max-width:760px){.calendar-wrap{position:static;}}
.cal-nav{display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;}
.cal-title{font-size:.88rem;font-weight:700;color:var(--blue);background:none;border:none;cursor:pointer;padding:2px 6px;border-radius:6px;transition:background .15s;}
.cal-title:hover{background:var(--blue3);}
.cal-arrow{background:none;border:1px solid var(--border);color:var(--muted);width:26px;height:26px;border-radius:6px;cursor:pointer;transition:border-color .2s,color .2s;display:flex;align-items:center;justify-content:center;font-size:.85rem;}
.cal-arrow:hover{border-color:var(--blue);color:var(--blue);}
.cal-picker{display:none;margin-bottom:6px;}
.cal-picker.open{display:block;}
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
.cal-year-sel{width:100%;padding:5px 8px;border:1px solid var(--border);border-radius:8px;background:var(--card);color:var(--text);font-size:.85rem;margin-bottom:7px;cursor:pointer;}
.cal-month-row{display:grid;grid-template-columns:repeat(4,1fr);gap:3px;margin-bottom:8px;}
.cal-m{padding:4px 2px;border:1px solid transparent;border-radius:5px;font-size:.68rem;text-align:center;background:#f0f0f5;color:#bbb;cursor:default;border:none;}
.cal-m.has-news{background:var(--blue3);color:var(--blue);cursor:pointer;font-weight:600;}
.cal-m.has-news:hover{background:var(--blue);color:#fff;}
.cal-m.cur-month{background:var(--blue)!important;color:#fff!important;font-weight:700;}
.cal-stats{margin-top:9px;font-size:.67rem;color:var(--muted);text-align:center;line-height:1.6;}
.articles-section{min-width:0;}
.filter-bar{margin-bottom:13px;}
.filter-label{font-size:.72rem;letter-spacing:.2em;text-transform:uppercase;color:var(--blue);padding-left:10px;border-left:3px solid var(--blue);}
.date-group{margin-bottom:24px;}
.date-heading{font-size:.7rem;letter-spacing:.22em;color:var(--blue2);margin-bottom:10px;padding-bottom:5px;border-bottom:1px solid var(--border);}
.articles-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:13px;}
.topic-cluster{background:#eef3ff;border:1px solid var(--border);border-radius:12px;padding:11px 12px 9px;margin-top:11px;}
.topic-cluster-label{font-size:.6rem;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:var(--blue2);margin-bottom:9px;display:flex;align-items:center;gap:5px;}
.topic-cluster-label::before{content:'';display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--blue2);flex-shrink:0;}
.card{background:var(--card);border:1px solid var(--border);border-radius:14px;display:flex;flex-direction:column;transition:transform .2s,box-shadow .2s,border-color .2s;box-shadow:var(--shadow);}
.card:hover{transform:translateY(-3px);box-shadow:var(--shadow-hover);border-color:#b4caff;}
.card-img{overflow:hidden;background:var(--bg);border-radius:14px 14px 0 0;}
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
.today-badge{display:inline-block;background:#ff3344;color:#fff;font-size:.6rem;font-weight:700;padding:2px 7px;border-radius:5px;margin-left:6px;vertical-align:middle;letter-spacing:.04em;}
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
    ('chart.html',    'チャート'),
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
NAV_CHART    = _build_nav('chart.html')
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
      <button class="cal-title" id="cal-title" title="點擊展開年份/月份選擇"></button>
      <button class="cal-arrow" id="cal-next">&#8594;</button>
    </div>
    <div class="cal-picker" id="cal-picker">
      <select class="cal-year-sel" id="cal-year-sel"></select>
      <div class="cal-month-row" id="cal-month-row"></div>
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

<!-- Article Modal -->
<div id="am-backdrop" onclick="closeArticleModal()"></div>
<div id="am-modal" role="dialog" aria-modal="true">
  <div class="am-handle"></div>
  <div class="am-header">
    <div class="am-meta">
      <span class="badge am-badge" id="am-badge" style="--c:#888"></span>
      <span class="am-date" id="am-date"></span>
    </div>
    <button class="am-close" onclick="closeArticleModal()" aria-label="閉じる">&#x2715;</button>
  </div>
  <div class="am-scroll">
    <h2 class="am-title-zh" id="am-title-zh"></h2>
    <p class="am-title-ja" id="am-title-ja"></p>
    <div class="am-image-strip" id="am-images"></div>
    <hr class="am-divider">
    <p class="am-section-label" id="am-body-label">全文翻譯</p>
    <div class="am-body-zh" id="am-body-zh"></div>
    <button class="am-ja-toggle" id="am-ja-toggle" onclick="toggleAmJa()">
      <span>日文原文を見る</span> &#9662;
    </button>
    <div class="am-body-ja" id="am-body-ja"></div>
  </div>
  <div class="am-footer">
    <span class="am-source-note" id="am-source-note"></span>
    <a class="am-orig-btn" id="am-orig-link" href="#" target="_blank" rel="noopener">原文を読む &#8594;</a>
  </div>
</div>
<script>
const ARTICLES=__DATA__;
document.getElementById('update-time').textContent='最後更新：__UPDATED_AT__ JST';
const byDate={};
ARTICLES.forEach(function(a){var d=a.date||'unknown';if(!byDate[d])byDate[d]=[];byDate[d].push(a);});
const datesWithNews=new Set(Object.keys(byDate));
const monthsWithNews=new Set();
const yearsWithNews=new Set();
ARTICLES.forEach(function(a){if(a.date&&a.date.length>=7){monthsWithNews.add(a.date.substring(0,7));yearsWithNews.add(a.date.substring(0,4));}});
const yearsArr=[...yearsWithNews].sort().reverse();
const now=new Date();
var todayStr=now.toISOString().slice(0,10);
// Default: auto-select today if it has articles, else most recent date
var selected=datesWithNews.has(todayStr)?todayStr:([...datesWithNews].sort().reverse()[0]||null);
var calYear=now.getFullYear(),calMonth=now.getMonth();
const MN=['1月','2月','3月','4月','5月','6月','7月','8月','9月','10月','11月','12月'];
// 填入年份選擇器
(function(){
  var sel=document.getElementById('cal-year-sel');
  yearsArr.forEach(function(y){
    var opt=document.createElement('option');
    opt.value=y;opt.textContent=y+'年';
    if(parseInt(y)===calYear)opt.selected=true;
    sel.appendChild(opt);
  });
})();
function renderMonthRow(){
  var row=document.getElementById('cal-month-row');row.innerHTML='';
  for(var m=0;m<12;m++){
    var ym=calYear+'-'+String(m+1).padStart(2,'0');
    var btn=document.createElement('button');
    btn.className='cal-m'+(monthsWithNews.has(ym)?' has-news':'')+(m===calMonth?' cur-month':'');
    btn.textContent=(m+1)+'月';
    if(monthsWithNews.has(ym)){
      (function(mo){btn.addEventListener('click',function(){
        calMonth=mo;selected=null;
        document.getElementById('cal-picker').classList.remove('open');
        renderMonthRow();renderCalendar();renderArticles();
      });})(m);
    }
    row.appendChild(btn);
  }
}
function renderCalendar(){
  var pickerOpen=document.getElementById('cal-picker').classList.contains('open');
  document.getElementById('cal-title').textContent=calYear+'年 '+MN[calMonth]+(pickerOpen?' ▴':' ▾');
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
  var monthTotal=ARTICLES.filter(function(a){return(a.date||'').startsWith(pfx);}).length;
  document.getElementById('cal-stats').textContent='本月 '+mc+' 天有文章　共 '+monthTotal+' 篇';
}
document.getElementById('cal-title').addEventListener('click',function(){
  document.getElementById('cal-picker').classList.toggle('open');
  renderCalendar();
});
document.getElementById('cal-prev').addEventListener('click',function(){
  if(calMonth===0){calYear--;calMonth=11;}else calMonth--;
  document.getElementById('cal-year-sel').value=calYear;
  document.getElementById('cal-picker').classList.remove('open');
  renderMonthRow();renderCalendar();renderArticles();
});
document.getElementById('cal-next').addEventListener('click',function(){
  if(calMonth===11){calYear++;calMonth=0;}else calMonth++;
  document.getElementById('cal-year-sel').value=calYear;
  document.getElementById('cal-picker').classList.remove('open');
  renderMonthRow();renderCalendar();renderArticles();
});
document.getElementById('cal-year-sel').addEventListener('change',function(){
  calYear=parseInt(this.value);
  var firstM=0;
  for(var m=0;m<12;m++){if(monthsWithNews.has(calYear+'-'+String(m+1).padStart(2,'0'))){firstM=m;break;}}
  calMonth=firstM;
  renderMonthRow();renderCalendar();renderArticles();
});
document.getElementById('cal-clear').addEventListener('click',function(){selected=null;renderCalendar();renderArticles();});
const SC=__SC__;
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
function makeCard(a){
  var art=document.createElement('article');art.className='card';art.style.cursor='pointer';
  var imgHtml='';
  if(a.image){
    imgHtml='<div class="card-img"><img src="'+esc(a.image)+'" alt="" loading="lazy" onerror="this.closest(\'.card-img\').remove()"></div>';
  }
  var color=SC[a.source]||'#888';
  art.innerHTML=imgHtml+
    '<div class="card-body">'+
    '<div class="card-head"><span class="badge" style="--c:'+color+'">'+esc(a.source)+'</span>'+(a.date?'<time class="date-tag">'+esc(a.date)+'</time>':'')+
    '</div>'+(a.title_zh?'<p class="title-zh">'+esc(a.title_zh)+'</p>':'')+(a.title_ja?'<p class="title-ja">'+esc(a.title_ja)+'</p>':'')+
    (a.summary_zh?'<p class="sum-zh">'+esc(a.summary_zh)+'</p>':'')+(a.summary_ja?'<p class="sum-ja">'+esc(a.summary_ja)+'</p>':'')+
    '<span class="card-btn">詳細・全文翻譯 &#8594;</span></div>';
  art.addEventListener('click',function(){openArticleModal(a);});
  return art;
}
// ── Article Modal ──
function openArticleModal(a){
  var color=SC[a.source]||'#888';
  var badge=document.getElementById('am-badge');
  badge.textContent=a.source;badge.style.setProperty('--c',color);
  document.getElementById('am-date').textContent=a.date||'';
  document.getElementById('am-title-zh').textContent=a.title_zh||a.title_ja||'';
  document.getElementById('am-title-ja').textContent=a.title_ja||'';
  document.getElementById('am-orig-link').href=a.url||'#';
  document.getElementById('am-source-note').textContent=(a.source||'')+'より';
  var bz=document.getElementById('am-body-zh');
  var lbl=document.getElementById('am-body-label');
  var bodyText,labelText;
  if(a.body_zh){
    bodyText=a.body_zh;
    labelText='全文翻譯';
  } else if(a.summary_zh){
    bodyText=a.summary_zh;
    labelText='摘要翻譯';
  } else {
    bodyText='（翻譯準備中）';
    labelText='翻譯';
  }
  if(lbl)lbl.textContent=labelText;
  function splitParas(t){
    var ps=t.split(/\n\n+/);
    if(ps.length<=1)ps=t.split(/\n/);
    if(ps.length<=1&&t.length>120){
      // fallback：以句尾符號分段（中文 。！？ 及英文 . ）
      var tmp=t.replace(/([。！？])\s*/g,'$1\n');
      tmp=tmp.replace(/([^A-Z0-9])\.\s+([^\d])/g,'$1.\n$2');
      ps=tmp.split(/\n/);
    }
    return ps.filter(function(p){return p.trim().length>1;}).map(function(p){return '<p>'+esc(p.trim())+'</p>';}).join('');
  }
  bz.innerHTML=splitParas(bodyText);
  var bj=document.getElementById('am-body-ja');
  var jaText=a.body_ja||a.summary_ja||'';
  bj.innerHTML=splitParas(jaText);
  bj.classList.remove('am-ja-visible');
  var jtb=document.getElementById('am-ja-toggle');
  jtb.classList.remove('am-ja-expanded');
  jtb.querySelector('span').textContent='日文原文を見る';
  var strip=document.getElementById('am-images');
  strip.innerHTML='';
  var imgs=a.images&&a.images.length?a.images:(a.image?[a.image]:[]);
  imgs.forEach(function(src){
    var item=document.createElement('div');item.className='am-img-item';
    item.innerHTML='<a class="am-img-dl" href="'+esc(src)+'" target="_blank" rel="noopener" title="開啟原圖 / 右鍵另存新檔"><img src="'+esc(src)+'" alt="" loading="lazy" onerror="this.closest(\'.am-img-item\').remove()"><span class="am-dl-btn">⬇ 開啟原圖</span></a>';
    strip.appendChild(item);
  });
  document.getElementById('am-backdrop').classList.add('am-open');
  document.getElementById('am-modal').classList.add('am-open');
  document.body.style.overflow='hidden';
}
function closeArticleModal(){
  document.getElementById('am-backdrop').classList.remove('am-open');
  document.getElementById('am-modal').classList.remove('am-open');
  document.body.style.overflow='';
}
function toggleAmJa(){
  var bj=document.getElementById('am-body-ja');
  var btn=document.getElementById('am-ja-toggle');
  var open=bj.classList.toggle('am-ja-visible');
  btn.classList.toggle('am-ja-expanded',open);
  btn.querySelector('span').textContent=open?'日文原文を隱す':'日文原文を見る';
}
document.addEventListener('keydown',function(e){if(e.key==='Escape')closeArticleModal();});
// ── Theme clustering ──────────────────────────────────────────────────────────
function clusterByTheme(arts){
  var STOP=new Set(['MILK','M!LK','ミルク','メンバー','グループ','アイドル']);
  function kw(t){return new Set(((t||'').match(/[぀-鿿＀-￯]{2,}/g)||[]).filter(function(w){return !STOP.has(w);}));}
  var assigned=new Array(arts.length).fill(-1);
  var clusters=[];
  for(var i=0;i<arts.length;i++){
    if(assigned[i]>=0)continue;
    var ci=clusters.length;clusters.push([arts[i]]);assigned[i]=ci;
    var ckw=kw(arts[i].title_ja);
    for(var j=i+1;j<arts.length;j++){
      if(assigned[j]>=0)continue;
      var jkw=kw(arts[j].title_ja);
      var overlap=0;ckw.forEach(function(w){if(jkw.has(w))overlap++;});
      if(overlap>=1){clusters[ci].push(arts[j]);assigned[j]=ci;jkw.forEach(function(w){ckw.add(w);});}
    }
  }
  return clusters;
}
function renderDateGroup(arts,container){
  var clusters=clusterByTheme(arts);
  var singles=[];
  clusters.forEach(function(cluster){
    if(cluster.length>=2){
      // flush accumulated singles first
      if(singles.length){
        var sg=document.createElement('div');sg.className='articles-grid';
        singles.forEach(function(a){sg.appendChild(makeCard(a));});
        container.appendChild(sg);singles=[];
      }
      // topic cluster box
      var wrap=document.createElement('div');wrap.className='topic-cluster';
      var sets=cluster.map(function(a){return new Set(((a.title_ja||'').match(/[぀-鿿＀-￯]{2,}/g)||[]));});
      var common=new Set(sets[0]);
      sets.slice(1).forEach(function(s){Array.from(common).forEach(function(w){if(!s.has(w))common.delete(w);});});
      var kws=Array.from(common).sort(function(a,b){return b.length-a.length;});
      var lbl=document.createElement('p');lbl.className='topic-cluster-label';
      lbl.textContent=(kws[0]||'関連記事')+' · '+cluster.length+'件';
      wrap.appendChild(lbl);
      var g=document.createElement('div');g.className='articles-grid';
      cluster.forEach(function(a){g.appendChild(makeCard(a));});
      wrap.appendChild(g);container.appendChild(wrap);
    }else{
      singles.push(cluster[0]);
    }
  });
  if(singles.length){
    var sg=document.createElement('div');sg.className='articles-grid';
    singles.forEach(function(a){sg.appendChild(makeCard(a));});
    container.appendChild(sg);
  }
}
function renderArticles(){
  var c=document.getElementById('articles-container'),l=document.getElementById('filter-label');
  c.innerHTML='';
  if(selected){
    l.textContent=selected+' 的文章';
    var arts=byDate[selected]||[];
    if(!arts.length){c.innerHTML='<p class="empty-msg">這天沒有文章</p>';return;}
    renderDateGroup(arts,c);
  }else{
    l.textContent='全部文章';
    var sd=Array.from(datesWithNews).sort().reverse();
    if(!sd.length){c.innerHTML='<p class="empty-msg">文章累積中，明天再來看看</p>';return;}
    sd.forEach(function(date){
      var grp=document.createElement('div');grp.className='date-group';
      var h=document.createElement('p');h.className='date-heading';h.textContent=date;grp.appendChild(h);
      renderDateGroup(byDate[date],grp);c.appendChild(grp);
    });
  }
}
renderMonthRow();renderCalendar();renderArticles();
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
.today-sub-label{font-size:.65rem;font-weight:700;letter-spacing:.2em;text-transform:uppercase;color:var(--muted);margin:4px 0 10px;}
.today-prog-row{display:flex;align-items:center;gap:10px;padding:9px 0;border-bottom:1px solid var(--border);}
.today-prog-row:last-of-type{border-bottom:none;}
.prog-badge{font-size:.63rem;font-weight:700;color:#fff;padding:3px 8px;border-radius:8px;flex-shrink:0;}
.today-prog-body{flex:1;min-width:0;}
.today-prog-name{display:block;font-size:.9rem;font-weight:700;color:var(--text);}
.today-prog-detail{display:block;font-size:.72rem;color:var(--muted);margin-top:1px;}
.today-prog-link{font-size:.7rem;color:var(--blue);font-weight:600;text-decoration:none;white-space:nowrap;flex-shrink:0;}
.today-prog-link:hover{text-decoration:underline;}
.today-evt-row{display:flex;align-items:flex-start;gap:10px;padding:9px 0;border-bottom:1px solid var(--border);}
.today-evt-row:last-of-type{border-bottom:none;}
.today-evt-body{flex:1;min-width:0;}
.today-evt-venue{display:block;font-size:.72rem;color:var(--muted);margin-top:2px;}
.sched-section-label{font-size:.72rem;letter-spacing:.3em;text-transform:uppercase;color:var(--blue);padding-left:10px;border-left:3px solid var(--blue);margin-bottom:14px;}
.prog-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:13px;}
.prog-card{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:16px;display:flex;flex-direction:column;gap:8px;box-shadow:var(--shadow);transition:transform .18s,box-shadow .18s;}
.prog-card:hover{transform:translateY(-3px);box-shadow:var(--shadow-hover);}
.prog-badge{font-size:.63rem;font-weight:700;color:#fff;padding:3px 9px;border-radius:9px;align-self:flex-start;letter-spacing:.04em;}
.prog-name{font-size:.95rem;font-weight:800;color:var(--text);line-height:1.3;}
.prog-station{font-size:.73rem;color:var(--muted);}
.prog-schedule{font-size:.7rem;color:var(--blue2);font-weight:600;}
.prog-link{display:inline-block;margin-top:6px;padding:6px 14px;border-radius:18px;background:var(--blue);color:#fff;font-size:.72rem;font-weight:700;text-decoration:none;align-self:flex-start;transition:opacity .2s;}
.prog-link:hover{opacity:.82;}
.prog-member{display:inline-block;font-size:.6rem;color:var(--blue);background:var(--blue3);padding:1px 7px;border-radius:8px;margin:2px 0;}
.official-cal{display:flex;gap:10px;flex-wrap:wrap;margin-top:6px;}
.cal-btn{display:inline-flex;align-items:center;padding:9px 20px;border-radius:22px;font-size:.82rem;font-weight:700;text-decoration:none;transition:opacity .2s;}
.cal-btn:hover{opacity:.82;}
.cal-btn.primary{background:var(--blue);color:#fff;}
.cal-btn.secondary{background:var(--surface);color:var(--blue);border:1px solid var(--blue);}
/* ── live events ──────────────────────────────────────────────────────── */
.ev-today-block{border-radius:16px;overflow:hidden;box-shadow:var(--shadow);}
.ev-today-hdr{background:var(--blue);color:#fff;padding:10px 18px;display:flex;align-items:center;gap:10px;}
.ev-today-tag{font-size:.64rem;letter-spacing:.18em;font-weight:700;opacity:.85;}
.ev-today-date{font-size:.74rem;font-weight:600;margin-left:auto;opacity:.82;font-variant-numeric:tabular-nums;}
.ev-today-card{background:var(--card);padding:15px 18px;display:flex;align-items:center;gap:16px;border:1px solid var(--border);border-radius:0 0 16px 16px;text-decoration:none;color:inherit;transition:background .15s;}
.ev-today-card:hover{background:#f7faff;}
.ev-today-venue{font-size:.98rem;font-weight:700;margin:5px 0 2px;}
.ev-today-city{font-size:.72rem;color:var(--muted);}
.ev-today-arrow{margin-left:auto;color:var(--blue);font-size:1.1rem;}
.ev-next-block{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:24px 20px;text-align:center;}
.ev-next-label{font-size:.64rem;color:var(--muted);letter-spacing:.1em;text-transform:uppercase;margin-bottom:16px;}
.ev-next-link{display:inline-flex;align-items:center;gap:16px;text-decoration:none;color:inherit;}
.ev-next-brick{text-align:center;flex-shrink:0;}
.ev-next-div{width:1px;height:52px;background:var(--border);flex-shrink:0;}
.ev-next-info{text-align:left;}
.ev-next-venue{font-size:.96rem;font-weight:700;color:var(--text);margin-top:4px;}
.ev-next-city{font-size:.72rem;color:var(--muted);margin-top:2px;}
.ev-day-lg{font-size:2.4rem!important;color:var(--blue)!important;}
.ev-list{display:flex;flex-direction:column;gap:8px;}
.ev-row{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:13px 16px;display:flex;align-items:center;gap:14px;text-decoration:none;color:inherit;transition:transform .15s,box-shadow .15s;}
.ev-row:hover{transform:translateY(-1px);box-shadow:var(--shadow-hover);}
.datebrick{flex-shrink:0;width:52px;text-align:center;background:var(--bg);border-radius:8px;padding:6px 4px;border:1px solid var(--border);}
.ev-month{font-size:.56rem;font-weight:700;color:var(--blue);letter-spacing:.04em;text-transform:uppercase;display:block;}
.ev-day{font-size:1.32rem;font-weight:900;color:var(--text);line-height:1;display:block;font-variant-numeric:tabular-nums;}
.ev-dow{font-size:.56rem;color:var(--muted);display:block;margin-top:1px;}
.ev-info{flex:1;min-width:0;}
.ev-badges{display:flex;gap:5px;align-items:center;margin-bottom:4px;flex-wrap:wrap;}
.live-badge{font-size:.57rem;font-weight:800;color:#fff;background:var(--red);padding:1px 6px;border-radius:4px;letter-spacing:.06em;}
.src-badge{font-size:.57rem;color:var(--muted);background:var(--bg);border:1px solid var(--border);padding:1px 5px;border-radius:4px;}
.ev-venue{font-size:.87rem;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.ev-city{font-size:.69rem;color:var(--muted);margin-top:1px;}
.ev-right{flex-shrink:0;display:flex;flex-direction:column;align-items:flex-end;gap:5px;}
.days-chip{font-size:.59rem;font-weight:700;padding:2px 8px;border-radius:10px;white-space:nowrap;}
.dc-soon{color:#fff;background:var(--red);}
.dc-near{color:var(--blue);background:var(--blue3);}
.ev-ext{font-size:.63rem;color:var(--blue);font-weight:600;}
.ev-ext::after{content:' ↗';font-size:.56rem;}
.tour-item{border-radius:12px;overflow:hidden;border:1px solid var(--border);background:var(--card);}
.tour-btn{width:100%;background:none;border:none;cursor:pointer;padding:13px 16px;display:flex;align-items:center;gap:14px;text-align:left;transition:background .15s;}
.tour-btn:hover{background:#f7faff;}
.tour-icon{flex-shrink:0;width:52px;text-align:center;background:var(--bg);border-radius:8px;padding:5px 4px;border:1px solid var(--border);display:flex;flex-direction:column;align-items:center;}
.tour-icon-lbl{font-size:.49rem;font-weight:800;color:var(--blue);letter-spacing:.06em;text-transform:uppercase;}
.tour-icon-n{font-size:1.32rem;font-weight:900;color:var(--text);line-height:1;}
.tour-icon-u{font-size:.49rem;color:var(--muted);}
.tour-meta{flex:1;min-width:0;}
.tour-title{font-size:.88rem;font-weight:700;}
.tour-range{font-size:.69rem;color:var(--muted);margin-top:1px;}
.tour-caret{margin-left:auto;color:var(--muted);font-size:.76rem;transition:transform .22s;flex-shrink:0;}
.tour-item.open .tour-caret{transform:rotate(180deg);}
.tour-dates{display:none;border-top:1px solid var(--border);}
.tour-item.open .tour-dates{display:block;}
.tour-dr{display:flex;align-items:center;gap:14px;padding:10px 16px;border-bottom:1px solid var(--bg);text-decoration:none;color:inherit;transition:background .15s;}
.tour-dr:last-child{border-bottom:none;}
.tour-dr:hover{background:#f7faff;}
.tour-dr-venue{font-size:.8rem;font-weight:600;color:var(--text);}
.tour-dr-city{font-size:.66rem;color:var(--muted);}
/* ── schedule calendar ──────────────────────────────────────────────────── */
.sc-cal-layout{display:grid;grid-template-columns:260px 1fr;gap:14px;align-items:start;}
@media(max-width:620px){.sc-cal-layout{grid-template-columns:1fr;}}
.sc-cal-wrap{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:14px;box-shadow:var(--shadow);}
.sc-cal-nav{display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;}
.sc-cal-title{font-size:.85rem;font-weight:800;color:var(--text);}
.sc-cal-arrow{background:none;border:1px solid var(--border);color:var(--muted);width:24px;height:24px;border-radius:6px;cursor:pointer;font-size:.82rem;display:flex;align-items:center;justify-content:center;transition:border-color .2s,color .2s;}
.sc-cal-arrow:hover{border-color:var(--blue);color:var(--blue);}
.sc-cal-grid{display:grid;grid-template-columns:repeat(7,1fr);gap:2px;}
.sc-cal-dow{font-size:.54rem;font-weight:700;text-align:center;color:var(--muted);padding:2px 0;}
.sc-cal-cell{aspect-ratio:1;display:flex;flex-direction:column;align-items:center;justify-content:center;border-radius:5px;cursor:default;position:relative;font-size:.68rem;color:var(--muted);}
.sc-cal-cell.cur-month{color:var(--text);}
.sc-cal-cell.today{background:var(--blue);color:#fff;font-weight:800;}
.sc-cal-cell.has-event{cursor:pointer;background:var(--blue3);}
.sc-cal-cell.has-event:hover{background:#c5d9ff;}
.sc-cal-cell.selected{outline:2px solid var(--blue);outline-offset:-2px;}
.sc-cal-dot{display:flex;gap:2px;margin-top:1px;flex-wrap:wrap;justify-content:center;}
.sc-dot-live{width:4px;height:4px;border-radius:50%;background:var(--red);flex-shrink:0;}
.sc-dot-ev{width:4px;height:4px;border-radius:50%;background:var(--blue);flex-shrink:0;}
/* right panel */
.sc-events-col{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:14px;box-shadow:var(--shadow);min-height:240px;}
.sc-panel-label{font-size:.62rem;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);margin-bottom:10px;}
.sc-day-events{display:none;}
.sc-day-events.visible{display:block;}
.sc-day-title{font-size:.78rem;font-weight:700;color:var(--blue);margin-bottom:8px;}
.sc-evt-item{display:flex;align-items:flex-start;gap:9px;padding:7px 0;border-bottom:1px solid var(--border);}
.sc-evt-item:last-child{border-bottom:none;}
.sc-evt-dot{width:8px;height:8px;border-radius:50%;flex-shrink:0;margin-top:5px;}
.sc-evt-body{flex:1;min-width:0;}
.sc-evt-title{font-size:.84rem;font-weight:600;color:var(--text);}
.sc-evt-detail{font-size:.7rem;color:var(--muted);margin-top:1px;}
.sc-upcoming-item{display:flex;align-items:center;gap:10px;padding:7px 0;border-bottom:1px solid var(--border);}
.sc-upcoming-item:last-child{border-bottom:none;}
.sc-upcoming-date{font-size:.64rem;font-weight:700;color:var(--blue2);white-space:nowrap;min-width:52px;font-variant-numeric:tabular-nums;}
.sc-upcoming-title{font-size:.82rem;font-weight:600;flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.sc-no-events{font-size:.78rem;color:var(--muted);padding:20px 0;text-align:center;}
/* ── past appearances ───────────────────────────────────────────────────── */
.past-year-group{margin-bottom:24px;}
.past-year-label{font-size:.7rem;font-weight:800;letter-spacing:.2em;color:var(--blue2);margin-bottom:10px;padding-left:10px;border-left:3px solid var(--blue3);}
.past-list{display:flex;flex-direction:column;gap:6px;}
.past-item{display:flex;align-items:center;gap:12px;padding:9px 12px;background:var(--card);border:1px solid var(--border);border-radius:10px;text-decoration:none;color:inherit;transition:background .15s;}
.past-item:hover{background:#f7faff;}
.past-date-chip{font-size:.65rem;font-weight:700;color:var(--muted);white-space:nowrap;min-width:54px;font-variant-numeric:tabular-nums;}
.past-title{font-size:.83rem;font-weight:600;flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.past-detail{font-size:.68rem;color:var(--muted);white-space:nowrap;}
/* ── dynamic programs ───────────────────────────────────────────────────── */
.dyn-prog-list{display:flex;flex-direction:column;gap:8px;}
.dyn-prog-item{display:flex;align-items:center;gap:10px;padding:10px 14px;background:var(--card);border:1px solid var(--border);border-radius:11px;text-decoration:none;color:inherit;transition:background .15s;}
.dyn-prog-item:hover{background:#f7faff;}
.dyn-prog-type{font-size:.6rem;font-weight:700;color:#fff;padding:2px 8px;border-radius:7px;flex-shrink:0;}
.dyn-prog-info{flex:1;min-width:0;}
.dyn-prog-name{font-size:.88rem;font-weight:700;display:block;}
.dyn-prog-station{font-size:.7rem;color:var(--muted);display:block;margin-top:1px;}
.dyn-status-chip{font-size:.6rem;font-weight:700;padding:2px 8px;border-radius:7px;white-space:nowrap;flex-shrink:0;}
.dyn-active{background:#e8f5e9;color:#2e7d32;}
.dyn-upcoming{background:var(--blue3);color:var(--blue);}
.dyn-release{background:#f3e5f5;color:#6a1b9a;}
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
    <p class="sched-section-title">&#128197; __TODAY__ 今日の行程</p>
    __TODAY_SCHED__
  </div>

  <div class="sched-section">
    <p class="sched-section-title">&#128197; スケジュール カレンダー</p>
    <div class="sc-cal-layout">
      <div class="sc-cal-wrap">
        <div class="sc-cal-nav">
          <button class="sc-cal-arrow" onclick="scCalPrev()">&#8249;</button>
          <span class="sc-cal-title" id="sc-cal-title"></span>
          <button class="sc-cal-arrow" onclick="scCalNext()">&#8250;</button>
        </div>
        <div class="sc-cal-grid" id="sc-cal-grid"></div>
      </div>
      <div class="sc-events-col">
        <div class="sc-day-events" id="sc-day-events">
          <p class="sc-panel-label" id="sc-day-title"></p>
          <div id="sc-day-list"></div>
        </div>
        <div id="sc-upcoming-panel">
          <p class="sc-panel-label">近日のイベント</p>
          <div id="sc-upcoming-list"></div>
        </div>
      </div>
    </div>
  </div>

  <div class="sched-section">
    <p class="sched-section-title">&#128225; 出演番組</p>
    __PROG_BROADCAST__
  </div>

  <div class="sched-section">
    <p class="sched-section-title">&#127916; 放送中作品</p>
    __PROG_WORKS__
  </div>

  __PROG_UPCOMING_SECTION__

__LIVE_SECTION__

  <div class="sched-section">
    <p class="sched-section-title">&#128190; 過去出演 記録</p>
    __PAST_EVENTS__
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
<script>
const SCHED_EVENTS=__SCHED_DATA__;
var scY=0,scM=0,scSel='';
(function(){var d=new Date();scY=d.getFullYear();scM=d.getMonth();})();
var MON_LBL=['1月','2月','3月','4月','5月','6月','7月','8月','9月','10月','11月','12月'];
var DOW_LBL=['日','月','火','水','木','金','土'];
var evByDate={};
SCHED_EVENTS.forEach(function(e){var d=e.date||'';if(!evByDate[d])evByDate[d]=[];evByDate[d].push(e);});
function scRender(){
  var title=document.getElementById('sc-cal-title');
  title.textContent=scY+'年 '+MON_LBL[scM];
  var grid=document.getElementById('sc-cal-grid');
  grid.innerHTML='';
  DOW_LBL.forEach(function(d){var el=document.createElement('div');el.className='sc-cal-dow';el.textContent=d;grid.appendChild(el);});
  var first=new Date(scY,scM,1).getDay();
  var days=new Date(scY,scM+1,0).getDate();
  var today=new Date().toISOString().slice(0,10);
  for(var i=0;i<first;i++){var blank=document.createElement('div');blank.className='sc-cal-cell';grid.appendChild(blank);}
  for(var d=1;d<=days;d++){
    var ds=scY+'-'+String(scM+1).padStart(2,'0')+'-'+String(d).padStart(2,'0');
    var cell=document.createElement('div');
    var cls=['sc-cal-cell','cur-month'];
    if(ds===today)cls.push('today');
    if(evByDate[ds])cls.push('has-event');
    if(ds===scSel)cls.push('selected');
    cell.className=cls.join(' ');
    cell.innerHTML='<span>'+d+'</span>';
    if(evByDate[ds]){
      var dot=document.createElement('div');dot.className='sc-cal-dot';
      evByDate[ds].slice(0,3).forEach(function(e){
        var s=document.createElement('span');
        s.className=(e.title||'').includes('[LIVE]')?'sc-dot-live':'sc-dot-ev';
        dot.appendChild(s);
      });
      cell.appendChild(dot);
      (function(date){cell.onclick=function(){scSel=date;scShowDay(date);scRender();};})(ds);
    }
    grid.appendChild(cell);
  }
}
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
function scShowDay(date){
  var evts=evByDate[date]||[];
  var panel=document.getElementById('sc-day-events');
  var upcoming=document.getElementById('sc-upcoming-panel');
  var title=document.getElementById('sc-day-title');
  var list=document.getElementById('sc-day-list');
  title.textContent=date;
  list.innerHTML='';
  evts.forEach(function(e){
    var item=document.createElement('div');item.className='sc-evt-item';
    var isLive=(e.title||'').includes('[LIVE]');
    var dotColor=isLive?'var(--red)':'var(--blue)';
    var detail=e.details||'';
    item.innerHTML='<div class="sc-evt-dot" style="background:'+dotColor+'"></div>'
      +'<div class="sc-evt-body"><p class="sc-evt-title">'+esc((e.title||'').replace('[LIVE]','').trim())+'</p>'
      +(detail?'<p class="sc-evt-detail">'+esc(detail)+'</p>':'')+'</div>';
    if(e.url&&e.url!=='#'){
      var a=document.createElement('a');a.href=e.url;a.target='_blank';a.rel='noopener';
      a.style.cssText='font-size:.68rem;color:var(--blue);font-weight:600;text-decoration:none;white-space:nowrap;';
      a.textContent='詳細 ↗';item.appendChild(a);
    }
    list.appendChild(item);
  });
  panel.classList.toggle('visible',evts.length>0);
  upcoming.style.display=evts.length>0?'none':'block';
}
function scRenderUpcoming(){
  var today=new Date().toISOString().slice(0,10);
  var ul=document.getElementById('sc-upcoming-list');
  ul.innerHTML='';
  var upcoming=SCHED_EVENTS.filter(function(e){return e.date>=today;})
    .sort(function(a,b){return a.date<b.date?-1:1;}).slice(0,8);
  if(!upcoming.length){ul.innerHTML='<p class="sc-no-events">近日イベントなし</p>';return;}
  upcoming.forEach(function(e){
    var row=document.createElement('div');row.className='sc-upcoming-item';
    var isLive=(e.title||'').includes('[LIVE]');
    row.innerHTML='<span class="sc-upcoming-date">'+esc(e.date.slice(5))+'</span>'
      +'<span class="sc-upcoming-title" style="color:'+(isLive?'var(--red)':'var(--text)')+'">'+esc((e.title||'').replace('[LIVE]','').trim())+'</span>';
    ul.appendChild(row);
  });
}
function scCalPrev(){if(scM===0){scY--;scM=11;}else{scM--;}scSel='';scRender();document.getElementById('sc-day-events').classList.remove('visible');document.getElementById('sc-upcoming-panel').style.display='block';}
function scCalNext(){if(scM===11){scY++;scM=0;}else{scM++;}scSel='';scRender();document.getElementById('sc-day-events').classList.remove('visible');document.getElementById('sc-upcoming-panel').style.display='block';}
function evTgl(id){var el=document.getElementById(id),btn=el.querySelector('.tour-btn');btn.setAttribute('aria-expanded',el.classList.toggle('open'));}
scRender();scRenderUpcoming();
</script>
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
    'CD・リリース': '#6a1b9a',   # 深紫（與 BADGE_COLORS['リリース'] 一致）
    'FC限定':       '#9c27b0',   # 紫
    '公式グッズ':   '#0277bd',   # 藍
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

    # ── Section 1: CD・リリース ───────────────────────────────────────────────
    cd_goods = [g for g in goods if g.get('category') == 'CD・リリース']
    if cd_goods:
        cards = ''.join(_goods_card_html(g, esc) for g in cd_goods)
        sections.append(
            f'<div class="goods-section">'
            f'<p class="section-label">&#127926; CD・リリース'
            f'<span class="cnt">({len(cd_goods)})</span></p>'
            f'<div class="goods-grid">{cards}</div>'
            f'</div>'
        )

    # ── Section 2: FC限定 ─────────────────────────────────────────────────────
    fc_goods = [g for g in goods if g.get('category') == 'FC限定']
    if fc_goods:
        cards = ''.join(_goods_card_html(g, esc) for g in fc_goods)
        sections.append(
            f'<div class="goods-section">'
            f'<p class="section-label">&#11088; FC限定商品'
            f'<span class="cnt">({len(fc_goods)})</span></p>'
            f'<p class="fc-notice">PREMIUM MILK 会員限定。'
            f'<a href="https://store.plusmember.jp/stardustch/products/list.php?category_id=891"'
            f' target="_blank" rel="noopener">FC限定ショップを見る &rarr;</a></p>'
            f'<div class="goods-grid">{cards}</div>'
            f'</div>'
        )
    else:
        # FC 抓不到時顯示連結
        sections.append(
            f'<div class="goods-section">'
            f'<p class="section-label">&#11088; FC限定商品</p>'
            f'<p class="fc-notice">PREMIUM MILK 会員限定。'
            f'<a href="https://store.plusmember.jp/stardustch/products/list.php?category_id=891"'
            f' target="_blank" rel="noopener">FC限定ショップを確認する &rarr;</a></p>'
            f'</div>'
        )

    # ── Section 3: 公式グッズ ─────────────────────────────────────────────────
    official = [g for g in goods
                if g.get('category') not in ('FC限定', 'CD・リリース')]
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
        OFFICIAL_SHOPS = [
            ('sd-milk.com 公式グッズ', 'https://sd-milk.com/contents/goods', '#0277bd'),
            ('UNIVERSAL MUSIC SHOP',   'https://www.universal-music.co.jp/m-lk/', '#0d47a1'),
        ]
        link_cards = ''.join(
            f'<div class="goods-card"><div class="goods-img-none">&#128722;</div>'
            f'<div class="goods-body">'
            f'<span class="cat-badge" style="background:{c}">公式グッズ</span>'
            f'<p class="goods-title-zh">{esc(n)}</p>'
            f'<a class="goods-buy" href="{esc(u)}" target="_blank" rel="noopener">ショップへ &rarr;</a>'
            f'</div></div>'
            for n, u, c in OFFICIAL_SHOPS
        )
        sections.append(
            f'<div class="goods-section">'
            f'<p class="section-label">&#128722; 公式グッズ</p>'
            f'<div class="goods-grid">{link_cards}</div>'
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

CHART_TEMPLATE = '''<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>M!LK Fan Hub — チャート実績</title>
<style>__CSS__
.chart-hero{background:linear-gradient(135deg,var(--blue) 0%,var(--blue2) 100%);
  color:#fff;padding:28px 20px 20px;text-align:center;border-radius:0 0 22px 22px;}
.chart-hero-title{font-size:1.05rem;font-weight:800;letter-spacing:.2em;opacity:.85;margin-bottom:4px;}
.chart-hero-count{font-size:2.8rem;font-weight:900;line-height:1;}
.chart-hero-sub{font-size:.72rem;opacity:.75;margin-top:4px;}
.chart-filters{display:flex;gap:8px;flex-wrap:wrap;margin:18px 0 4px;}
.cf-btn{padding:5px 14px;border-radius:20px;font-size:.72rem;font-weight:700;border:1.5px solid var(--border);
  background:var(--card);color:var(--muted);cursor:pointer;transition:all .15s;}
.cf-btn.active,.cf-btn:hover{background:var(--blue);color:#fff;border-color:var(--blue);}
.chart-week-group{margin-bottom:32px;}
.chart-week-label{font-size:.7rem;font-weight:800;letter-spacing:.25em;text-transform:uppercase;
  color:var(--blue2);padding-left:10px;border-left:3px solid var(--blue3);margin-bottom:12px;}
.chart-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:12px;}
.chart-card{background:var(--card);border:1px solid var(--border);border-radius:14px;
  overflow:hidden;display:flex;flex-direction:column;box-shadow:0 2px 10px rgba(42,91,215,.07);
  transition:transform .18s,box-shadow .18s;text-decoration:none;color:inherit;}
.chart-card:hover{transform:translateY(-3px);box-shadow:0 6px 20px rgba(42,91,215,.15);}
.chart-card-img{width:100%;aspect-ratio:1;object-fit:cover;background:var(--bg);}
.chart-card-img-placeholder{width:100%;aspect-ratio:1;background:linear-gradient(135deg,var(--blue3),var(--bg));
  display:flex;align-items:center;justify-content:center;font-size:2rem;color:var(--blue2);}
.chart-card-body{padding:12px;flex:1;display:flex;flex-direction:column;gap:5px;}
.chart-card-top{display:flex;align-items:center;gap:6px;flex-wrap:wrap;}
.rank-badge{font-size:.8rem;font-weight:900;width:34px;height:34px;border-radius:50%;
  display:flex;align-items:center;justify-content:center;flex-shrink:0;color:#fff;}
.rb-1{background:linear-gradient(135deg,#FFD700,#FFA500);}
.rb-2{background:linear-gradient(135deg,#C0C0C0,#909090);}
.rb-3{background:linear-gradient(135deg,#CD7F32,#A0522D);}
.rb-n{background:var(--blue);font-size:.72rem;}
.chart-type-tag{font-size:.58rem;font-weight:700;color:var(--blue);background:var(--blue3);
  padding:2px 7px;border-radius:6px;}
.chart-new-badge{font-size:.56rem;font-weight:800;color:#fff;background:#e53e3e;
  padding:2px 6px;border-radius:5px;}
.chart-member-tag{font-size:.6rem;font-weight:700;color:var(--muted);background:var(--bg);
  border:1px solid var(--border);padding:1px 6px;border-radius:6px;}
.chart-card-title{font-size:.95rem;font-weight:800;line-height:1.3;color:var(--text);}
.chart-card-artist{font-size:.75rem;color:var(--muted);}
.chart-card-meta{font-size:.65rem;color:var(--muted);margin-top:auto;padding-top:6px;
  border-top:1px solid var(--border);display:flex;justify-content:space-between;flex-wrap:wrap;gap:4px;}
.chart-empty{text-align:center;padding:48px 20px;color:var(--muted);font-size:.9rem;}
/* ── Historical archive ──────────────────────────────── */
.history-wrap{margin-top:48px;}
.history-section-title{font-size:.72rem;letter-spacing:.3em;text-transform:uppercase;
  color:var(--blue);padding-left:10px;border-left:3px solid var(--blue);margin-bottom:18px;font-weight:700;}
.year-group{margin-bottom:12px;border:1px solid var(--border);border-radius:14px;overflow:hidden;}
.year-header{display:flex;align-items:center;gap:10px;padding:13px 16px;
  background:var(--card);cursor:pointer;user-select:none;transition:background .15s;}
.year-header:hover{background:var(--blue3);}
.year-title{font-size:1rem;font-weight:900;color:var(--blue);flex:1;}
.year-count{font-size:.62rem;font-weight:700;color:var(--muted);background:var(--bg);
  border:1px solid var(--border);padding:2px 8px;border-radius:10px;}
.year-toggle{font-size:.75rem;color:var(--muted);transition:transform .2s;}
.year-toggle.open{transform:rotate(90deg);}
.year-body{display:none;border-top:1px solid var(--border);}
.year-body.open{display:block;}
.month-group{padding:10px 16px 6px;}
.month-label{font-size:.65rem;font-weight:800;letter-spacing:.15em;text-transform:uppercase;
  color:var(--blue2);margin-bottom:6px;padding-bottom:4px;border-bottom:1px solid var(--border);}
.hist-row{display:flex;align-items:center;gap:9px;padding:6px 0;border-bottom:1px dashed var(--border);
  text-decoration:none;color:inherit;transition:background .1s;}
.hist-row:last-child{border-bottom:none;}
.hist-row:hover{background:var(--blue3);border-radius:6px;padding-left:6px;}
.hist-rank{font-size:.7rem;font-weight:900;width:26px;height:26px;border-radius:50%;
  display:flex;align-items:center;justify-content:center;flex-shrink:0;color:#fff;font-variant-numeric:tabular-nums;}
.hist-type{font-size:.56rem;font-weight:700;color:var(--blue);background:var(--blue3);
  padding:1px 5px;border-radius:5px;white-space:nowrap;}
.hist-info{flex:1;min-width:0;}
.hist-title{font-size:.82rem;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.hist-artist{font-size:.67rem;color:var(--muted);}
.hist-meta{font-size:.62rem;color:var(--muted);white-space:nowrap;font-variant-numeric:tabular-nums;}
.hist-member{font-size:.57rem;font-weight:700;color:var(--blue);background:var(--blue3);
  padding:1px 5px;border-radius:5px;white-space:nowrap;}
</style>
</head>
<body>
<header class="site-header">
  __STRIPE__
  <div class="wordmark">M<span class="bang">!</span>LK</div>
  <div class="hearts">&#9825; &middot; &#9825; &middot; &#9825;</div>
  <p class="tagline">Fan Hub &middot; チャート実績</p>
  <nav class="page-nav">__NAV__</nav>
  <p class="update-time">最後更新：__UPDATED_AT__ JST</p>
</header>
<div class="sched-wrap" style="max-width:1060px;margin:0 auto;padding:20px 16px 72px;">
  <div class="chart-hero">
    <p class="chart-hero-title">ORICON CHART</p>
    <p class="chart-hero-count">__TOTAL_COUNT__</p>
    <p class="chart-hero-sub">チャートイン記録（累計）</p>
  </div>
  <div class="chart-filters" id="chart-filters">
    <button class="cf-btn active" data-code="all">すべて</button>
    <button class="cf-btn" data-code="cos">合算シングル</button>
    <button class="cf-btn" data-code="coa">合算アルバム</button>
    <button class="cf-btn" data-code="js">シングル</button>
    <button class="cf-btn" data-code="ja">アルバム</button>
    <button class="cf-btn" data-code="dis">デジタルシングル</button>
    <button class="cf-btn" data-code="st">ストリーミング</button>
  </div>

  <!-- 最近12週 cards -->
  <div id="chart-recent">__RECENT_BODY__</div>

  <!-- 過去記録 year/month archive -->
  __HISTORY_SECTION__

  <p style="font-size:.68rem;color:var(--muted);margin-top:28px;">
    データ出典：<a href="https://www.oricon.co.jp/rank/" target="_blank" rel="noopener" style="color:var(--blue)">Oricon</a>
  </p>
</div>
<footer>
  <p>__SOURCES_FOOTER__</p>
</footer>
<script>
// Filter buttons — apply to both recent cards and history rows
document.querySelectorAll('.cf-btn').forEach(function(btn){
  btn.addEventListener('click',function(){
    document.querySelectorAll('.cf-btn').forEach(function(b){b.classList.remove('active');});
    btn.classList.add('active');
    var code=btn.dataset.code;
    // Recent cards
    document.querySelectorAll('.chart-card').forEach(function(card){
      card.style.display=(code==='all'||card.dataset.code===code)?'':'none';
    });
    document.querySelectorAll('.chart-week-group').forEach(function(grp){
      var vis=grp.querySelectorAll('.chart-card:not([style*="none"])').length;
      grp.style.display=vis?'':'none';
    });
    // History rows
    document.querySelectorAll('.hist-row').forEach(function(row){
      row.style.display=(code==='all'||row.dataset.code===code)?'':'none';
    });
    document.querySelectorAll('.month-group').forEach(function(mg){
      var vis=mg.querySelectorAll('.hist-row:not([style*="none"])').length;
      mg.style.display=vis?'':'none';
    });
    document.querySelectorAll('.year-group').forEach(function(yg){
      var vis=yg.querySelectorAll('.hist-row:not([style*="none"])').length;
      yg.style.display=vis?'':'none';
    });
  });
});
// Year accordion toggle
document.querySelectorAll('.year-header').forEach(function(hdr){
  hdr.addEventListener('click',function(){
    var body=hdr.nextElementSibling;
    var tog=hdr.querySelector('.year-toggle');
    var open=body.classList.toggle('open');
    if(tog) tog.classList.toggle('open',open);
  });
});
</script>
</body>
</html>
'''

def generate_chart_html(charts, updated_at):
    from collections import defaultdict
    from datetime import timedelta as _td2

    def esc(s):
        return (str(s).replace('&', '&amp;').replace('<', '&lt;')
                      .replace('>', '&gt;').replace('"', '&quot;'))

    cutoff = (datetime.now(JST) - _td2(days=84)).strftime('%Y-%m-%d')

    recent   = [c for c in charts if c['date'] >= cutoff]
    historic = [c for c in charts if c['date'] <  cutoff]

    def rank_cls(rank):
        return {1:'rb-1',2:'rb-2',3:'rb-3'}.get(rank,'rb-n')

    def rank_badge_span(rank, extra_cls='rank-badge'):
        return f'<span class="{extra_cls} {rank_cls(rank)}">{rank}</span>'

    # ── Recent section (week-by-week cards) ──────────────────────────
    by_date = defaultdict(list)
    for c in sorted(recent, key=lambda c: (c['date'], c['chart_code'], c['rank'])):
        by_date[c['date']].append(c)

    recent_parts = []
    for date in sorted(by_date.keys(), reverse=True):
        entries = by_date[date]
        cards = []
        for e in entries:
            img_html = (f'<img class="chart-card-img" src="{esc(e["image"])}" alt="{esc(e["title"])}" loading="lazy">'
                        if e.get('image')
                        else '<div class="chart-card-img-placeholder">🎵</div>')
            new_badge   = '<span class="chart-new-badge">NEW</span>' if 'NEW' in e.get('status','').upper() else ''
            member_tag  = (f'<span class="chart-member-tag">👤 {esc(e["member"])}</span>'
                           if e['member'] != 'M!LK' else '')
            href = e.get('url','') or f'https://www.oricon.co.jp/rank/{e["chart_code"]}/w/{e["date"]}/'
            cards.append(
                f'<a class="chart-card" href="{esc(href)}" target="_blank" rel="noopener"'
                f' data-code="{esc(e["chart_code"])}">'
                + img_html +
                f'<div class="chart-card-body">'
                f'<div class="chart-card-top">'
                + rank_badge_span(e['rank']) +
                f'<span class="chart-type-tag">{esc(e["chart_name"])}</span>'
                + new_badge + member_tag +
                f'</div>'
                f'<p class="chart-card-title">{esc(e["title"])}</p>'
                f'<p class="chart-card-artist">{esc(e["artist"])}</p>'
                f'<div class="chart-card-meta">'
                f'<span>{esc(e.get("label",""))}</span>'
                f'<span>{esc(e.get("release_date",""))}</span>'
                f'</div></div></a>'
            )
        recent_parts.append(
            f'<div class="chart-week-group">'
            f'<p class="chart-week-label">📅 {date}</p>'
            f'<div class="chart-grid">{"".join(cards)}</div>'
            f'</div>'
        )

    if recent_parts:
        recent_body = ''.join(recent_parts)
    else:
        recent_body = '<div class="chart-empty">直近12週のチャートイン記録はありません。</div>'

    # ── Historical section (year → month accordion) ───────────────────
    history_section = ''
    if historic:
        # group by year then month
        by_ym = defaultdict(lambda: defaultdict(list))
        for c in historic:
            parts = c['date'].split('-')
            if len(parts) == 3:
                by_ym[parts[0]][parts[1]].append(c)

        year_groups = []
        for year in sorted(by_ym.keys(), reverse=True):
            months = by_ym[year]
            month_htmls = []
            year_total = 0
            for month in sorted(months.keys(), reverse=True):
                entries = sorted(months[month], key=lambda c: (c['date'], c['chart_code'], c['rank']))
                year_total += len(entries)
                rows = []
                for e in entries:
                    href = e.get('url','') or f'https://www.oricon.co.jp/rank/{e["chart_code"]}/w/{e["date"]}/'
                    member_chip = (f'<span class="hist-member">👤 {esc(e["member"])}</span> '
                                   if e['member'] != 'M!LK' else '')
                    new_mk = '<span class="chart-new-badge" style="font-size:.5rem;padding:1px 4px">NEW</span> ' if 'NEW' in e.get('status','').upper() else ''
                    rows.append(
                        f'<a class="hist-row" href="{esc(href)}" target="_blank" rel="noopener"'
                        f' data-code="{esc(e["chart_code"])}">'
                        f'<span class="hist-rank {rank_cls(e["rank"])}">{e["rank"]}</span>'
                        f'<span class="hist-type">{esc(e["chart_name"])}</span>'
                        f'<span class="hist-info">'
                        f'<span class="hist-title">{new_mk}{member_chip}{esc(e["title"])}</span>'
                        f'<span class="hist-artist" style="display:block">{esc(e["artist"])}</span>'
                        f'</span>'
                        f'<span class="hist-meta">{e["date"]}</span>'
                        f'</a>'
                    )
                month_htmls.append(
                    f'<div class="month-group">'
                    f'<p class="month-label">{int(month)}月 <span style="font-weight:400;color:var(--muted)">({len(entries)})</span></p>'
                    + ''.join(rows) +
                    f'</div>'
                )
            # First year opens by default
            open_cls = ' open' if not year_groups else ''
            year_groups.append(
                f'<div class="year-group">'
                f'<div class="year-header">'
                f'<span class="year-title">{year}年</span>'
                f'<span class="year-count">{year_total} 件</span>'
                f'<span class="year-toggle{open_cls}">▶</span>'
                f'</div>'
                f'<div class="year-body{open_cls}">{"".join(month_htmls)}</div>'
                f'</div>'
            )

        history_section = (
            f'<div class="history-wrap" id="chart-history">'
            f'<p class="history-section-title">📂 過去記録 — 年別・月別アーカイブ</p>'
            + ''.join(year_groups) +
            f'</div>'
        )

    total_count = f'{len(charts)} 件'

    return (CHART_TEMPLATE
            .replace('__CSS__',            COMMON_CSS)
            .replace('__STRIPE__',         MEMBER_STRIPE_HTML)
            .replace('__NAV__',            NAV_CHART)
            .replace('__SOURCES_FOOTER__', make_sources_footer())
            .replace('__UPDATED_AT__',     updated_at)
            .replace('__TOTAL_COUNT__',    total_count)
            .replace('__RECENT_BODY__',    recent_body)
            .replace('__HISTORY_SECTION__', history_section))


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
:root {
    --ground:       #FAFAF8;
    --surface:      #FFFFFF;
    --text:         #1C1B2E;
    --text-muted:   #6B6A82;
    --accent:       #D4477B;
    --accent-soft:  #FAEEF4;
    --blue:         #7B9EC4;
    --blue-soft:    #EDF3FA;
    --gold:         #B8922A;
    --gold-soft:    #FBF4E6;
    --border:       #E6E4EE;
    --radius:       14px;
    --radius-sm:    8px;
  }

  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    background: var(--ground);
    color: var(--text);
    font-family: -apple-system, 'Hiragino Sans', 'Hiragino Kaku Gothic ProN',
                 'Yu Gothic UI', 'Meiryo', 'Segoe UI', sans-serif;
    font-size: 15px;
    line-height: 1.6;
    padding: 0 0 80px;
  }

  /* ─── HEADER ─── */
  .hero {
    background: var(--text);
    color: #fff;
    padding: 52px 24px 44px;
    text-align: center;
    position: relative;
    overflow: hidden;
  }
  .hero::before {
    content: '';
    position: absolute;
    inset: 0;
    background: repeating-conic-gradient(
      rgba(255,255,255,0.03) 0deg 45deg,
      transparent 45deg 90deg
    );
    background-size: 40px 40px;
    pointer-events: none;
  }
  .hero-group-badge {
    display: inline-block;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: .18em;
    text-transform: uppercase;
    color: var(--accent);
    border: 1.5px solid var(--accent);
    border-radius: 4px;
    padding: 3px 10px;
    margin-bottom: 20px;
  }
  .hero-label {
    font-size: 12px;
    font-weight: 600;
    letter-spacing: .22em;
    text-transform: uppercase;
    color: rgba(255,255,255,0.45);
    margin-bottom: 10px;
  }
  .hero-title {
    font-size: clamp(28px, 7vw, 54px);
    font-weight: 800;
    letter-spacing: -.01em;
    line-height: 1.1;
    margin-bottom: 6px;
  }
  .hero-title em { font-style: normal; color: var(--accent); }
  .hero-subtitle {
    font-size: 13px;
    color: rgba(255,255,255,0.5);
    margin-bottom: 28px;
    letter-spacing: .04em;
  }
  .hero-meta {
    display: inline-flex;
    gap: 24px;
    border-top: 1px solid rgba(255,255,255,0.12);
    padding-top: 20px;
  }
  .hero-meta-item { text-align: center; }
  .hero-meta-item .val {
    display: block;
    font-size: 18px;
    font-weight: 700;
    color: #fff;
    margin-bottom: 2px;
  }
  .hero-meta-item .key {
    font-size: 11px;
    color: rgba(255,255,255,0.4);
    letter-spacing: .08em;
  }

  /* ─── SECTION ─── */
  .section {
    max-width: 960px;
    margin: 0 auto;
    padding: 48px 20px 0;
  }
  .section-head {
    display: flex;
    align-items: baseline;
    gap: 12px;
    margin-bottom: 24px;
    border-bottom: 2px solid var(--text);
    padding-bottom: 10px;
  }
  .section-title { font-size: 17px; font-weight: 800; letter-spacing: -.01em; }
  .section-en {
    font-size: 11px;
    font-weight: 700;
    letter-spacing: .16em;
    text-transform: uppercase;
    color: var(--text-muted);
  }

  /* ─── CHECKER ─── */
  .checker-layout {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 20px;
    align-items: start;
  }
  .wants-panel {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .wants-intro {
    font-size: 13px;
    color: var(--text-muted);
    margin-bottom: 8px;
    line-height: 1.5;
  }
  .want-item {
    display: flex;
    align-items: center;
    gap: 12px;
    background: var(--surface);
    border: 1.5px solid var(--border);
    border-radius: var(--radius-sm);
    padding: 11px 14px;
    cursor: pointer;
    transition: border-color .15s, background .15s;
    user-select: none;
  }
  .want-item:hover { border-color: var(--accent); background: var(--accent-soft); }
  .want-item input[type="checkbox"] { display: none; }
  .want-check {
    width: 20px; height: 20px;
    border: 2px solid var(--border);
    border-radius: 5px;
    flex-shrink: 0;
    position: relative;
    transition: border-color .15s, background .15s;
  }
  .want-item:has(input:checked) .want-check {
    border-color: var(--accent);
    background: var(--accent);
  }
  .want-item:has(input:checked) .want-check::after {
    content: '';
    position: absolute;
    top: 2px; left: 5px;
    width: 6px; height: 9px;
    border: 2px solid #fff;
    border-top: none; border-left: none;
    transform: rotate(45deg);
  }
  .want-item:has(input:checked) {
    border-color: var(--accent);
    background: var(--accent-soft);
  }
  .want-info { display: flex; flex-direction: column; gap: 2px; }
  .want-name { font-size: 13px; font-weight: 700; }
  .want-tag {
    font-size: 11px;
    color: var(--text-muted);
    font-weight: 500;
  }
  .want-tag.fc { color: var(--gold); }

  .sub-opt {
    max-height: 0;
    overflow: hidden;
    transition: max-height .25s ease, margin .2s ease;
    margin-left: 6px;
  }
  .sub-opt.open { max-height: 320px; margin-bottom: 2px; }
  .sub-opt select {
    width: 100%;
    padding: 8px 32px 8px 12px;
    border: 1.5px solid var(--border);
    border-radius: var(--radius-sm);
    background: var(--surface);
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='8' viewBox='0 0 12 8'%3E%3Cpath d='M1 1l5 5 5-5' stroke='%236B6A82' stroke-width='1.5' fill='none' stroke-linecap='round'/%3E%3C/svg%3E");
    background-repeat: no-repeat;
    background-position: right 12px center;
    font-size: 13px;
    color: var(--text);
    font-family: inherit;
    appearance: none;
    -webkit-appearance: none;
  }
  .sub-check-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 5px;
    padding: 6px 4px 4px;
  }
  .sub-check-group-label {
    grid-column: 1 / -1;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: .12em;
    text-transform: uppercase;
    color: var(--text-muted);
    padding: 4px 2px 2px;
  }
  .sub-check-item {
    display: flex;
    align-items: center;
    gap: 7px;
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
    padding: 6px 9px;
    border-radius: 6px;
    border: 1.5px solid var(--border);
    background: var(--surface);
    user-select: none;
    transition: border-color .12s, background .12s;
    line-height: 1.3;
  }
  .sub-check-item:hover { border-color: var(--accent); background: var(--accent-soft); }
  .sub-check-item input[type="checkbox"] { display: none; }
  .sub-check-dot {
    width: 14px; height: 14px;
    border: 2px solid var(--border);
    border-radius: 3px;
    flex-shrink: 0;
    position: relative;
    transition: border-color .12s, background .12s;
  }
  .sub-check-item:has(input:checked) .sub-check-dot {
    border-color: var(--accent);
    background: var(--accent);
  }
  .sub-check-item:has(input:checked) .sub-check-dot::after {
    content: '';
    position: absolute;
    top: 1px; left: 3px;
    width: 5px; height: 7px;
    border: 1.5px solid #fff;
    border-top: none; border-left: none;
    transform: rotate(45deg);
  }
  .sub-check-item:has(input:checked) { border-color: var(--accent); background: var(--accent-soft); }

  /* ─── QTY STEPPER ─── */
  .want-qty, .sub-check-qty {
    display: none;
    align-items: center;
    margin-left: auto;
    background: rgba(0,0,0,0.055);
    border-radius: 6px;
    overflow: hidden;
    flex-shrink: 0;
  }
  .want-item:has(input:checked) .want-qty  { display: flex; }
  .sub-check-item:has(input:checked) .sub-check-qty { display: flex; background: rgba(212,71,123,0.12); }
  .qty-btn {
    width: 26px; height: 26px;
    display: flex; align-items: center; justify-content: center;
    background: none; border: none;
    font-size: 15px; line-height: 1;
    cursor: pointer; color: var(--text-muted);
    font-family: inherit;
    transition: background .1s, color .1s;
    flex-shrink: 0;
  }
  .qty-btn:hover { background: rgba(0,0,0,0.09); color: var(--text); }
  .qty-val {
    min-width: 22px; text-align: center;
    font-size: 13px; font-weight: 800;
    color: var(--text);
    pointer-events: none;
  }
  .reset-btn {
    margin-top: 8px;
    align-self: flex-start;
    padding: 7px 16px;
    background: transparent;
    border: 1.5px solid var(--border);
    border-radius: var(--radius-sm);
    font-size: 12px;
    color: var(--text-muted);
    cursor: pointer;
    font-family: inherit;
    transition: border-color .15s, color .15s;
  }
  .reset-btn:hover { border-color: var(--text); color: var(--text); }

  /* ─── RESULT PANEL ─── */
  .result-panel {
    position: sticky;
    top: 20px;
    background: var(--text);
    color: #fff;
    border-radius: var(--radius);
    padding: 22px;
    min-height: 220px;
    display: flex;
    flex-direction: column;
    gap: 14px;
  }
  .result-empty {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    color: rgba(255,255,255,0.3);
    font-size: 13px;
    text-align: center;
    padding: 20px;
  }
  .res-plan-header {
    background: rgba(212,71,123,0.15);
    border: 1px solid rgba(212,71,123,0.3);
    border-radius: 9px;
    padding: 14px 16px;
    display: flex;
    flex-direction: column;
    gap: 3px;
  }
  .res-plan-label {
    font-size: 10px;
    font-weight: 700;
    letter-spacing: .16em;
    text-transform: uppercase;
    color: rgba(212,71,123,0.85);
  }
  .res-plan-price {
    font-size: 30px;
    font-weight: 800;
    color: var(--accent);
    line-height: 1.1;
    letter-spacing: -.02em;
  }
  .res-plan-price small {
    font-size: 13px;
    font-weight: 500;
    color: rgba(255,255,255,0.4);
    letter-spacing: 0;
    margin-left: 2px;
  }
  .res-plan-note {
    font-size: 12px;
    color: rgba(255,255,255,0.5);
    margin-top: 3px;
    line-height: 1.45;
  }
  .res-sec { display: flex; flex-direction: column; gap: 7px; }
  .res-label {
    font-size: 10px;
    font-weight: 700;
    letter-spacing: .16em;
    text-transform: uppercase;
    color: rgba(255,255,255,0.35);
    margin-bottom: 1px;
  }
  .res-item {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 12px;
    padding: 9px 12px;
    background: rgba(255,255,255,0.07);
    border-radius: 7px;
  }
  .res-item-name {
    font-size: 13px;
    font-weight: 700;
    display: flex;
    align-items: center;
    gap: 6px;
    flex-wrap: wrap;
    line-height: 1.35;
  }
  .res-item-right {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-shrink: 0;
  }
  .res-qty {
    font-size: 12px;
    color: rgba(255,255,255,0.35);
    white-space: nowrap;
  }
  .res-item-price {
    font-size: 15px;
    font-weight: 800;
    color: var(--accent);
    white-space: nowrap;
  }
  .res-badge {
    font-size: 10px;
    padding: 2px 6px;
    border-radius: 4px;
    font-weight: 700;
    background: rgba(212,71,123,0.25);
    color: #FFB0CC;
  }
  .res-badge-warn {
    font-size: 10px;
    padding: 2px 6px;
    border-radius: 4px;
    font-weight: 700;
    background: rgba(184,146,42,0.3);
    color: #F5D47A;
  }
  .res-total {
    font-size: 16px;
    font-weight: 800;
    color: var(--accent);
    text-align: right;
    padding-top: 6px;
    border-top: 1px solid rgba(255,255,255,0.08);
  }
  .res-store {
    background: rgba(255,255,255,0.06);
    border-radius: 7px;
    padding: 11px 13px;
    display: flex;
    flex-direction: column;
    gap: 3px;
  }
  .res-store-label {
    font-size: 10px;
    font-weight: 700;
    letter-spacing: .14em;
    text-transform: uppercase;
    color: rgba(255,255,255,0.3);
  }
  .res-store-val { font-size: 13px; font-weight: 700; }
  .res-store-extra { font-size: 12px; color: rgba(255,255,255,0.5); margin-top: 2px; }

  .res-tok {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: 8px;
    font-size: 12px;
    padding: 5px 0;
    border-bottom: 1px solid rgba(255,255,255,0.06);
  }
  .res-tok:last-child { border-bottom: none; }
  .res-tok-name { font-weight: 600; }
  .res-tok-name.hi { color: var(--accent); }
  .res-tok-desc { color: rgba(255,255,255,0.4); font-size: 11px; text-align: right; }
  .res-tok.bonus { opacity: 0.55; }
  .res-tok.bonus .res-tok-name { font-weight: 500; }
  .res-bonus-sep {
    font-size: 10px; font-weight: 700; letter-spacing: .14em;
    text-transform: uppercase; color: rgba(255,255,255,0.25);
    padding: 6px 0 2px; margin-top: 2px;
    border-top: 1px dashed rgba(255,255,255,0.1);
  }

  .res-warn {
    background: rgba(184,146,42,0.12);
    border: 1px solid rgba(184,146,42,0.25);
    border-radius: 7px;
    padding: 10px 12px;
    font-size: 12px;
    line-height: 1.55;
    color: #F5D47A;
  }
  .res-deadline {
    font-size: 11px;
    color: rgba(255,255,255,0.35);
    text-align: center;
    padding-top: 2px;
  }

  /* ─── HOLO CARDS ─── */
  .editions-top {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
    margin-bottom: 16px;
  }
  .editions-solo {
    display: grid;
    grid-template-columns: repeat(5, 1fr);
    gap: 12px;
  }
  .holo-wrap {
    border-radius: calc(var(--radius) + 2px);
    padding: 2px;
    background: linear-gradient(125deg, #ff6b9d, #c26bfd, #6bb5ff, #6bffcd, #fff36b, #ffa66b, #ff6b9d);
    background-size: 300% 300%;
    animation: holo-flow 6s linear infinite;
    box-shadow: 0 4px 20px rgba(212,71,123,0.15);
  }
  @keyframes holo-flow {
    0%   { background-position: 0% 50%; filter: hue-rotate(0deg); }
    50%  { background-position: 100% 50%; filter: hue-rotate(180deg); }
    100% { background-position: 0% 50%; filter: hue-rotate(360deg); }
  }
  @media (prefers-reduced-motion: reduce) {
    .holo-wrap { animation: none; background: linear-gradient(125deg, #ff6b9d, #6bb5ff, #6bffcd); }
  }
  .plain-wrap {
    border-radius: calc(var(--radius) + 2px);
    padding: 2px;
    background: var(--border);
  }
  .card {
    background: var(--surface);
    border-radius: var(--radius);
    padding: 20px;
    height: 100%;
    display: flex;
    flex-direction: column;
    gap: 12px;
  }
  .card-badge {
    display: inline-flex;
    align-items: center;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: .12em;
    text-transform: uppercase;
    padding: 3px 8px;
    border-radius: 4px;
    align-self: flex-start;
  }
  .badge-standard { background: var(--border); color: var(--text-muted); }
  .badge-limited  { background: var(--gold-soft); color: var(--gold); }
  .badge-solo     { background: var(--accent-soft); color: var(--accent); }
  .card-name { font-size: 15px; font-weight: 800; line-height: 1.25; }
  .card-name-en { font-size: 11px; color: var(--text-muted); font-weight: 500; margin-top: 2px; }
  .card-price { font-size: 22px; font-weight: 800; color: var(--accent); letter-spacing: -.02em; }
  .card-price span { font-size: 13px; font-weight: 500; color: var(--text-muted); margin-left: 2px; }
  .card-divider { border: none; border-top: 1px solid var(--border); }
  .card-row { display: flex; flex-direction: column; gap: 6px; }
  .card-row-label {
    font-size: 10px; font-weight: 700; letter-spacing: .14em;
    text-transform: uppercase; color: var(--text-muted);
  }
  .card-row-val { font-size: 13px; line-height: 1.5; }
  .tokuten-tag {
    display: inline-block;
    font-size: 12px; font-weight: 600;
    background: var(--accent-soft); color: var(--accent);
    border-radius: var(--radius-sm);
    padding: 4px 9px; margin: 2px 2px 2px 0; line-height: 1.4;
  }
  .member-name { font-size: 12px; font-weight: 700; color: var(--accent); }

  /* ─── TOKUTEN BLOCK ─── */
  .tokuten-block { display: flex; flex-direction: column; gap: 16px; }
  .tokuten-row {
    background: var(--surface);
    border: 1.5px solid var(--border);
    border-radius: var(--radius);
    overflow: hidden;
  }
  .tokuten-row-head {
    display: grid;
    grid-template-columns: 200px 1fr;
    align-items: stretch;
  }
  .tokuten-row-type {
    background: var(--text); color: #fff;
    padding: 16px 18px;
    display: flex; flex-direction: column; justify-content: center; gap: 4px;
  }
  .type-label { font-size: 11px; font-weight: 700; letter-spacing: .14em; text-transform: uppercase; color: rgba(255,255,255,0.45); }
  .type-name { font-size: 14px; font-weight: 800; }
  .type-period { font-size: 11px; color: rgba(255,255,255,0.5); margin-top: 4px; }
  .tokuten-row-body { padding: 16px 18px; display: flex; flex-direction: column; gap: 8px; }
  .tokuten-condition { font-size: 12px; color: var(--text-muted); line-height: 1.5; }
  .tokuten-condition strong { color: var(--text); font-weight: 700; }
  .tokuten-item { font-size: 14px; font-weight: 700; }
  .tokuten-stores { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 4px; }
  .store-chip {
    font-size: 11px; background: var(--ground);
    border: 1px solid var(--border); border-radius: 4px;
    padding: 2px 7px; color: var(--text-muted); white-space: nowrap;
  }

  /* ─── CHANNEL GRID ─── */
  .channel-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }
  .channel-card {
    background: var(--surface); border: 1.5px solid var(--border);
    border-radius: var(--radius-sm); padding: 14px 16px;
  }
  .channel-name { font-size: 12px; font-weight: 700; color: var(--text-muted); margin-bottom: 4px; }
  .channel-gift { font-size: 13px; font-weight: 700; line-height: 1.4; }
  .channel-note { font-size: 11px; color: var(--text-muted); margin-top: 3px; }

  /* ─── FC TABLE ─── */
  .fc-note {
    background: var(--gold-soft); border: 1.5px solid #E8D19A;
    border-radius: var(--radius-sm); padding: 12px 16px;
    font-size: 13px; color: var(--gold); font-weight: 600;
    margin-bottom: 16px; line-height: 1.5;
  }
  .fc-table-wrap { border: 1.5px solid var(--border); border-radius: var(--radius); overflow: hidden; }
  .fc-table { width: 100%; border-collapse: collapse; font-size: 13px; }
  .fc-table thead tr { background: var(--text); color: #fff; }
  .fc-table th {
    padding: 10px 14px; text-align: left;
    font-size: 11px; font-weight: 700; letter-spacing: .1em;
    text-transform: uppercase; white-space: nowrap;
  }
  .fc-table td { padding: 10px 14px; border-bottom: 1px solid var(--border); vertical-align: middle; line-height: 1.4; }
  .fc-table tr:last-child td { border-bottom: none; }
  .fc-table tr:nth-child(even) td { background: rgba(0,0,0,0.015); }
  .fc-price { font-weight: 800; color: var(--accent); white-space: nowrap; }

  .deadline-box {
    background: var(--text); color: #fff;
    border-radius: var(--radius); padding: 20px 24px;
    display: flex; flex-wrap: wrap; gap: 16px 32px;
    align-items: flex-start; margin-top: 12px;
  }
  .dl-label { font-size: 10px; font-weight: 700; letter-spacing: .14em; text-transform: uppercase; color: rgba(255,255,255,0.4); margin-bottom: 4px; }
  .dl-val { font-size: 15px; font-weight: 800; }
  .dl-sub { font-size: 11px; color: rgba(255,255,255,0.5); margin-top: 2px; }

  /* ─── RESPONSIVE ─── */
  @media (max-width: 700px) {
    .editions-top       { grid-template-columns: 1fr; }
    .editions-solo      { grid-template-columns: repeat(2, 1fr); }
    .checker-layout     { grid-template-columns: 1fr; }
    .result-panel       { position: static; }
    .tokuten-row-head   { grid-template-columns: 1fr; }
    .tokuten-row-type   { padding: 12px 16px; }
    .channel-grid       { grid-template-columns: 1fr 1fr; }
  }
  @media (max-width: 420px) {
    .editions-solo      { grid-template-columns: 1fr; }
    .channel-grid       { grid-template-columns: 1fr; }
  }
'''

ALBUM_CHECKER_BODY = '''
<!-- ══ HERO ══════════════════════════════════════════════ -->
<header class="hero">
  <div class="hero-group-badge">M!LK</div>
  <p class="hero-label">1st Mini Album</p>
  <h1 class="hero-title">タイトル<em>未定</em></h1>
  <p class="hero-subtitle">Title Undecided</p>
  <div class="hero-meta">
    <div class="hero-meta-item">
      <span class="val">2026.09.16</span>
      <span class="key">発売日</span>
    </div>
    <div class="hero-meta-item">
      <span class="val">7</span>
      <span class="key">形態</span>
    </div>
    <div class="hero-meta-item">
      <span class="val">7/19</span>
      <span class="key">予約締切</span>
    </div>
  </div>
</header>


<!-- ══ CHECKER ════════════════════════════════════════════ -->
<section class="section">
  <div class="section-head">
    <h2 class="section-title">特典チェッカー</h2>
    <span class="section-en">What should I buy?</span>
  </div>

  <div class="checker-layout">
    <!-- Left: wants -->
    <div class="wants-panel">
      <p class="wants-intro">欲しい特典にチェックを入れると、<br>購入すべき形態と通路を表示します。</p>

      <label class="want-item" for="w-pair">
        <input type="checkbox" id="w-pair" onchange="compute()">
        <div class="want-check"></div>
        <div class="want-info">
          <span class="want-name">ペアトレカ（雙人小卡）</span>
          <span class="want-tag">早期予約特典・5種ランダム</span>
        </div>
        <div class="want-qty" onclick="event.stopPropagation()">
          <button class="qty-btn" onclick="changeQty('qty-pair',-1)">－</button>
          <span class="qty-val" id="qty-pair">1</span>
          <button class="qty-btn" onclick="changeQty('qty-pair',1)">＋</button>
        </div>
      </label>

      <label class="want-item" for="w-clear">
        <input type="checkbox" id="w-clear" onchange="compute()">
        <div class="want-check"></div>
        <div class="want-info">
          <span class="want-name">クリアトレカ（透卡）</span>
          <span class="want-tag">早期セット予約特典・ソロ5種ランダム</span>
        </div>
        <div class="want-qty" onclick="event.stopPropagation()">
          <button class="qty-btn" onclick="changeQty('qty-clear',-1)">－</button>
          <span class="qty-val" id="qty-clear">1</span>
          <button class="qty-btn" onclick="changeQty('qty-clear',1)">＋</button>
        </div>
      </label>

      <label class="want-item" for="w-bluray">
        <input type="checkbox" id="w-bluray" onchange="compute()">
        <div class="want-check"></div>
        <div class="want-info">
          <span class="want-name">Blu-ray（イベント映像）</span>
          <span class="want-tag">初回限定盤収録</span>
        </div>
      </label>

      <label class="want-item" for="w-sticker">
        <input type="checkbox" id="w-sticker" onchange="toggleSub('sub-sticker','w-sticker'); compute()">
        <div class="want-check"></div>
        <div class="want-info">
          <span class="want-name">通路別ステッカー</span>
          <span class="want-tag">先着特典・各通路1種</span>
        </div>
      </label>
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

      <label class="want-item" for="w-acrylic">
        <input type="checkbox" id="w-acrylic" onchange="toggleSub('sub-acrylic','w-acrylic'); compute()">
        <div class="want-check"></div>
        <div class="want-info">
          <span class="want-name">アクスタ（複数選択可）</span>
          <span class="want-tag fc">FC限定 · PREMIUM MILK会員</span>
        </div>
      </label>
      <div class="sub-opt" id="sub-acrylic">
        <div class="sub-check-grid">
          <span class="sub-check-group-label">集合アクスタ</span>
          <label class="sub-check-item">
            <input type="checkbox" name="acrylic-type" value="A" onchange="compute()">
            <div class="sub-check-dot"></div>集合A（初回盤）
            <div class="sub-check-qty" onclick="event.stopPropagation()">
              <button class="qty-btn" onclick="changeQty('aq-A',-1)">－</button>
              <span class="qty-val" id="aq-A">1</span>
              <button class="qty-btn" onclick="changeQty('aq-A',1)">＋</button>
            </div>
          </label>
          <label class="sub-check-item">
            <input type="checkbox" name="acrylic-type" value="B" onchange="compute()">
            <div class="sub-check-dot"></div>集合B（通常盤）
            <div class="sub-check-qty" onclick="event.stopPropagation()">
              <button class="qty-btn" onclick="changeQty('aq-B',-1)">－</button>
              <span class="qty-val" id="aq-B">1</span>
              <button class="qty-btn" onclick="changeQty('aq-B',1)">＋</button>
            </div>
          </label>
          <span class="sub-check-group-label">ソロアクスタ</span>
          <label class="sub-check-item">
            <input type="checkbox" name="acrylic-type" value="sano" onchange="compute()">
            <div class="sub-check-dot"></div>佐野勇斗
            <div class="sub-check-qty" onclick="event.stopPropagation()">
              <button class="qty-btn" onclick="changeQty('aq-sano',-1)">－</button>
              <span class="qty-val" id="aq-sano">1</span>
              <button class="qty-btn" onclick="changeQty('aq-sano',1)">＋</button>
            </div>
          </label>
          <label class="sub-check-item">
            <input type="checkbox" name="acrylic-type" value="shiozaki" onchange="compute()">
            <div class="sub-check-dot"></div>塩﨑太智
            <div class="sub-check-qty" onclick="event.stopPropagation()">
              <button class="qty-btn" onclick="changeQty('aq-shiozaki',-1)">－</button>
              <span class="qty-val" id="aq-shiozaki">1</span>
              <button class="qty-btn" onclick="changeQty('aq-shiozaki',1)">＋</button>
            </div>
          </label>
          <label class="sub-check-item">
            <input type="checkbox" name="acrylic-type" value="sono" onchange="compute()">
            <div class="sub-check-dot"></div>曽野舜太
            <div class="sub-check-qty" onclick="event.stopPropagation()">
              <button class="qty-btn" onclick="changeQty('aq-sono',-1)">－</button>
              <span class="qty-val" id="aq-sono">1</span>
              <button class="qty-btn" onclick="changeQty('aq-sono',1)">＋</button>
            </div>
          </label>
          <label class="sub-check-item">
            <input type="checkbox" name="acrylic-type" value="yamanaka" onchange="compute()">
            <div class="sub-check-dot"></div>山中柔太朗
            <div class="sub-check-qty" onclick="event.stopPropagation()">
              <button class="qty-btn" onclick="changeQty('aq-yamanaka',-1)">－</button>
              <span class="qty-val" id="aq-yamanaka">1</span>
              <button class="qty-btn" onclick="changeQty('aq-yamanaka',1)">＋</button>
            </div>
          </label>
          <label class="sub-check-item">
            <input type="checkbox" name="acrylic-type" value="yoshida" onchange="compute()">
            <div class="sub-check-dot"></div>吉田仁人
            <div class="sub-check-qty" onclick="event.stopPropagation()">
              <button class="qty-btn" onclick="changeQty('aq-yoshida',-1)">－</button>
              <span class="qty-val" id="aq-yoshida">1</span>
              <button class="qty-btn" onclick="changeQty('aq-yoshida',1)">＋</button>
            </div>
          </label>
        </div>
      </div>

      <label class="want-item" for="w-solocard">
        <input type="checkbox" id="w-solocard" onchange="toggleSub('sub-solo','w-solocard'); compute()">
        <div class="want-check"></div>
        <div class="want-info">
          <span class="want-name">特定メンバーの封入ソロトレカ（複数選択可）</span>
          <span class="want-tag">ソロ盤封入・3種ランダム</span>
        </div>
      </label>
      <div class="sub-opt" id="sub-solo">
        <div class="sub-check-grid">
          <label class="sub-check-item">
            <input type="checkbox" name="solo-member" value="sano" onchange="compute()">
            <div class="sub-check-dot"></div>佐野勇斗
            <div class="sub-check-qty" onclick="event.stopPropagation()">
              <button class="qty-btn" onclick="changeQty('sq-sano',-1)">－</button>
              <span class="qty-val" id="sq-sano">1</span>
              <button class="qty-btn" onclick="changeQty('sq-sano',1)">＋</button>
            </div>
          </label>
          <label class="sub-check-item">
            <input type="checkbox" name="solo-member" value="shiozaki" onchange="compute()">
            <div class="sub-check-dot"></div>塩﨑太智
            <div class="sub-check-qty" onclick="event.stopPropagation()">
              <button class="qty-btn" onclick="changeQty('sq-shiozaki',-1)">－</button>
              <span class="qty-val" id="sq-shiozaki">1</span>
              <button class="qty-btn" onclick="changeQty('sq-shiozaki',1)">＋</button>
            </div>
          </label>
          <label class="sub-check-item">
            <input type="checkbox" name="solo-member" value="sono" onchange="compute()">
            <div class="sub-check-dot"></div>曽野舜太
            <div class="sub-check-qty" onclick="event.stopPropagation()">
              <button class="qty-btn" onclick="changeQty('sq-sono',-1)">－</button>
              <span class="qty-val" id="sq-sono">1</span>
              <button class="qty-btn" onclick="changeQty('sq-sono',1)">＋</button>
            </div>
          </label>
          <label class="sub-check-item">
            <input type="checkbox" name="solo-member" value="yamanaka" onchange="compute()">
            <div class="sub-check-dot"></div>山中柔太朗
            <div class="sub-check-qty" onclick="event.stopPropagation()">
              <button class="qty-btn" onclick="changeQty('sq-yamanaka',-1)">－</button>
              <span class="qty-val" id="sq-yamanaka">1</span>
              <button class="qty-btn" onclick="changeQty('sq-yamanaka',1)">＋</button>
            </div>
          </label>
          <label class="sub-check-item">
            <input type="checkbox" name="solo-member" value="yoshida" onchange="compute()">
            <div class="sub-check-dot"></div>吉田仁人
            <div class="sub-check-qty" onclick="event.stopPropagation()">
              <button class="qty-btn" onclick="changeQty('sq-yoshida',-1)">－</button>
              <span class="qty-val" id="sq-yoshida">1</span>
              <button class="qty-btn" onclick="changeQty('sq-yoshida',1)">＋</button>
            </div>
          </label>
        </div>
      </div>

      <button class="reset-btn" onclick="resetAll()">選択をリセット</button>
    </div>

    <!-- Right: result -->
    <div class="result-panel" id="result-panel">
      <div class="result-empty">欲しい特典を選んでください</div>
    </div>
  </div>
</section>


<!-- ══ EDITIONS ════════════════════════════════════════════ -->
<section class="section">
  <div class="section-head">
    <h2 class="section-title">版本一覧</h2>
    <span class="section-en">Editions</span>
  </div>

  <div class="editions-top">
    <div class="plain-wrap">
      <div class="card">
        <span class="card-badge badge-standard">通常盤</span>
        <div>
          <div class="card-name">通常盤</div>
          <div class="card-name-en">Regular Edition · CD only</div>
        </div>
        <div class="card-price">¥2,530<span>税込</span></div>
        <hr class="card-divider">
        <div class="card-row">
          <div class="card-row-label">収録</div>
          <div class="card-row-val">CD 全5曲<br>アイドルパワー（既発）＋新曲</div>
        </div>
        <div class="card-row">
          <div class="card-row-label">封入特典</div>
          <div>
            <span class="tokuten-tag">限定トレカ（ソロ5種ランダム1枚）</span>
            <span class="tokuten-tag">応募抽選シリアル ※初回プレスのみ</span>
          </div>
        </div>
      </div>
    </div>

    <div class="holo-wrap">
      <div class="card">
        <span class="card-badge badge-limited">初回限定盤</span>
        <div>
          <div class="card-name">初回限定盤</div>
          <div class="card-name-en">First Limited Edition · CD + Blu-ray</div>
        </div>
        <div class="card-price">¥3,520<span>税込</span></div>
        <hr class="card-divider">
        <div class="card-row">
          <div class="card-row-label">収録</div>
          <div class="card-row-val">CD 全5曲 ＋ Blu-ray<br>「爆裂愛してる / 好きすぎて滅！」<br>発売日記念スペシャルイベント映像</div>
        </div>
        <div class="card-row">
          <div class="card-row-label">封入特典</div>
          <div>
            <span class="tokuten-tag">限定トレカ（ソロ5種ランダム1枚）</span>
            <span class="tokuten-tag">応募抽選シリアル</span>
          </div>
        </div>
      </div>
    </div>
  </div>

  <div class="editions-solo">
    <div class="holo-wrap"><div class="card">
      <span class="card-badge badge-solo">ソロ盤</span>
      <div><div class="member-name">佐野勇斗</div><div class="card-name-en">CD only · 初回生産限定</div></div>
      <div class="card-price">¥2,530<span>税込</span></div>
      <hr class="card-divider">
      <div class="card-row"><div class="card-row-label">収録</div><div class="card-row-val">全6曲（共通5曲＋ユニット曲）</div></div>
      <div class="card-row"><div class="card-row-label">封入特典</div><div>
        <span class="tokuten-tag">佐野勇斗 ソロ3種ランダム1枚</span>
        <span class="tokuten-tag">応募抽選シリアル</span>
      </div></div>
    </div></div>

    <div class="holo-wrap"><div class="card">
      <span class="card-badge badge-solo">ソロ盤</span>
      <div><div class="member-name">塩﨑太智</div><div class="card-name-en">CD only · 初回生産限定</div></div>
      <div class="card-price">¥2,530<span>税込</span></div>
      <hr class="card-divider">
      <div class="card-row"><div class="card-row-label">収録</div><div class="card-row-val">全6曲（共通5曲＋ユニット曲）</div></div>
      <div class="card-row"><div class="card-row-label">封入特典</div><div>
        <span class="tokuten-tag">塩﨑太智 ソロ3種ランダム1枚</span>
        <span class="tokuten-tag">応募抽選シリアル</span>
      </div></div>
    </div></div>

    <div class="holo-wrap"><div class="card">
      <span class="card-badge badge-solo">ソロ盤</span>
      <div><div class="member-name">曽野舜太</div><div class="card-name-en">CD only · 初回生産限定</div></div>
      <div class="card-price">¥2,530<span>税込</span></div>
      <hr class="card-divider">
      <div class="card-row"><div class="card-row-label">収録</div><div class="card-row-val">全6曲（共通5曲＋ユニット曲）</div></div>
      <div class="card-row"><div class="card-row-label">封入特典</div><div>
        <span class="tokuten-tag">曽野舜太 ソロ3種ランダム1枚</span>
        <span class="tokuten-tag">応募抽選シリアル</span>
      </div></div>
    </div></div>

    <div class="holo-wrap"><div class="card">
      <span class="card-badge badge-solo">ソロ盤</span>
      <div><div class="member-name">山中柔太朗</div><div class="card-name-en">CD only · 初回生産限定</div></div>
      <div class="card-price">¥2,530<span>税込</span></div>
      <hr class="card-divider">
      <div class="card-row"><div class="card-row-label">収録</div><div class="card-row-val">全6曲（共通5曲＋ユニット曲）</div></div>
      <div class="card-row"><div class="card-row-label">封入特典</div><div>
        <span class="tokuten-tag">山中柔太朗 ソロ3種ランダム1枚</span>
        <span class="tokuten-tag">応募抽選シリアル</span>
      </div></div>
    </div></div>

    <div class="holo-wrap"><div class="card">
      <span class="card-badge badge-solo">ソロ盤</span>
      <div><div class="member-name">吉田仁人</div><div class="card-name-en">CD only · 初回生産限定</div></div>
      <div class="card-price">¥2,530<span>税込</span></div>
      <hr class="card-divider">
      <div class="card-row"><div class="card-row-label">収録</div><div class="card-row-val">全6曲（共通5曲＋ユニット曲）</div></div>
      <div class="card-row"><div class="card-row-label">封入特典</div><div>
        <span class="tokuten-tag">吉田仁人 ソロ3種ランダム1枚</span>
        <span class="tokuten-tag">応募抽選シリアル</span>
      </div></div>
    </div></div>
  </div>
</section>


<!-- ══ PRE-ORDER TOKUTEN ═══════════════════════════════════ -->
<section class="section">
  <div class="section-head">
    <h2 class="section-title">予約特典</h2>
    <span class="section-en">Pre-order Bonuses</span>
  </div>
  <div class="tokuten-block">
    <div class="tokuten-row">
      <div class="tokuten-row-head">
        <div class="tokuten-row-type">
          <span class="type-label">EC限定</span>
          <span class="type-name">早期セット予約特典</span>
          <span class="type-period">6/19（金）20:00 〜 7/19（日）23:59</span>
        </div>
        <div class="tokuten-row-body">
          <div class="tokuten-condition"><strong>条件：</strong>対象2形態以上のセット購入<br>（初回限定盤＋通常盤）または（初回限定盤＋ソロ盤いずれか1種）</div>
          <div class="tokuten-item">メンバーソロ クリアトレカ（5種ランダム1枚）</div>
          <div class="tokuten-stores">
            <span class="store-chip">VICTOR ONLINE</span><span class="store-chip">楽天ブックス</span>
            <span class="store-chip">セブンネット</span><span class="store-chip">TOWER RECORDS ONLINE</span>
            <span class="store-chip">HMV & BOOKS online</span><span class="store-chip">Amazon.co.jp</span>
          </div>
        </div>
      </div>
    </div>
    <div class="tokuten-row">
      <div class="tokuten-row-head">
        <div class="tokuten-row-type">
          <span class="type-label">EC限定</span>
          <span class="type-name">早期予約特典</span>
          <span class="type-period">6/19（金）20:00 〜 7/19（日）23:59</span>
        </div>
        <div class="tokuten-row-body">
          <div class="tokuten-condition"><strong>条件：</strong>任意の1形態を予約（単品可）</div>
          <div class="tokuten-item">ペアトレカ（5種ランダム1枚）</div>
          <div class="tokuten-condition">5種の組み合わせ：佐野勇斗×曽野舜太、佐野勇斗×山中柔太朗 など</div>
          <div class="tokuten-stores">
            <span class="store-chip">VICTOR ONLINE</span><span class="store-chip">楽天ブックス</span>
            <span class="store-chip">セブンネット</span><span class="store-chip">TOWER RECORDS ONLINE</span>
            <span class="store-chip">HMV & BOOKS online</span><span class="store-chip">Amazon.co.jp</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</section>


<!-- ══ CHANNEL ═════════════════════════════════════════════ -->
<section class="section">
  <div class="section-head">
    <h2 class="section-title">通路別特典</h2>
    <span class="section-en">Store Exclusives · 先着</span>
  </div>
  <div class="channel-grid">
    <div class="channel-card"><div class="channel-name">楽天ブックス</div><div class="channel-gift">佐野勇斗 デザイン ステッカー</div></div>
    <div class="channel-card"><div class="channel-name">TOWER RECORDS</div><div class="channel-gift">塩﨑太智 デザイン ステッカー</div></div>
    <div class="channel-card"><div class="channel-name">HMV & BOOKS</div><div class="channel-gift">山中柔太朗 デザイン ステッカー</div></div>
    <div class="channel-card"><div class="channel-name">VICTOR ONLINE STORE</div><div class="channel-gift">曽野舜太 デザイン ステッカー</div></div>
    <div class="channel-card"><div class="channel-name">応援店（TSUTAYA 等）</div><div class="channel-gift">吉田仁人 デザイン ステッカー</div></div>
    <div class="channel-card"><div class="channel-name">Amazon.co.jp</div><div class="channel-gift">メガジャケ</div><div class="channel-note">ステッカーではなくメガジャケ</div></div>
  </div>
</section>


<!-- ══ FC GOODS ════════════════════════════════════════════ -->
<section class="section">
  <div class="section-head">
    <h2 class="section-title">FC限定商品</h2>
    <span class="section-en">PREMIUM MILK Members Only</span>
  </div>
  <div class="fc-note">
    PREMIUM MILK 会員限定 · VICTOR ONLINE STORE のみ取扱<br>
    FC購入者には早期予約特典（ペアトレカ）＋ <strong>曽野舜太デザインステッカー</strong> が付与される
  </div>
  <div class="fc-table-wrap">
    <table class="fc-table">
      <thead>
        <tr><th>セット内容</th><th>CD形態</th><th>アクスタ</th><th>価格（税込）</th></tr>
      </thead>
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
    </table>
  </div>
  <div class="deadline-box">
    <div class="dl-item"><div class="dl-label">予約締切</div><div class="dl-val">2026年 7月19日（日）23:59</div></div>
    <div class="dl-item"><div class="dl-label">コンビニ払い締切</div><div class="dl-val">7月17日（金）18:00</div></div>
    <div class="dl-item"><div class="dl-label">支払期限</div><div class="dl-val">申込日から3日以内</div><div class="dl-sub">超過するとキャンセル対象</div></div>
  </div>
</section>


<!-- ══ JS ══════════════════════════════════════════════════ -->
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
const EC_STORES = ['VICTOR ONLINE STORE','楽天ブックス','セブンネットショッピング','TOWER RECORDS ONLINE','HMV & BOOKS online','Amazon.co.jp'];

function chk(id) { return document.getElementById(id)?.checked || false; }
function sel(id) { return document.getElementById(id)?.value || ''; }
function getChecked(name) {
  return [...document.querySelectorAll(`input[name="${name}"]:checked`)].map(el => el.value);
}
function getQty(id) { return Math.max(1, parseInt(document.getElementById(id)?.textContent) || 1); }
function changeQty(id, delta) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = Math.max(1, (parseInt(el.textContent) || 1) + delta);
  compute();
}

function toggleSub(subId, checkId) {
  const sub = document.getElementById(subId);
  const checked = document.getElementById(checkId).checked;
  sub.classList.toggle('open', checked);
  if (!checked) {
    sub.querySelectorAll('input[type="checkbox"]').forEach(cb => cb.checked = false);
    sub.querySelectorAll('select').forEach(s => s.value = '');
    sub.querySelectorAll('.qty-val').forEach(el => el.textContent = '1');
  }
}

function resetAll() {
  ['w-pair','w-clear','w-bluray','w-sticker','w-acrylic','w-solocard'].forEach(id => {
    document.getElementById(id).checked = false;
  });
  ['qty-pair','qty-clear'].forEach(id => {
    const el = document.getElementById(id); if (el) el.textContent = '1';
  });
  ['sub-sticker','sub-acrylic','sub-solo'].forEach(id => {
    const el = document.getElementById(id);
    el.classList.remove('open');
    el.querySelectorAll('input[type="checkbox"]').forEach(cb => cb.checked = false);
    el.querySelectorAll('select').forEach(s => s.value = '');
    el.querySelectorAll('.qty-val').forEach(el2 => el2.textContent = '1');
  });
  compute();
}

function compute() {
  const wants = {
    pair:     chk('w-pair'),
    clear:    chk('w-clear'),
    bluray:   chk('w-bluray'),
    sticker:  chk('w-sticker'),
    acrylic:  chk('w-acrylic'),
    solocard: chk('w-solocard'),
  };
  const stickerM         = sel('sel-sticker');
  const selectedAcrylics = wants.acrylic  ? getChecked('acrylic-type') : [];
  const selectedSolos    = wants.solocard ? getChecked('solo-member')  : [];
  const panel            = document.getElementById('result-panel');

  if (!Object.values(wants).some(Boolean)) {
    panel.innerHTML = '<div class="result-empty">欲しい特典を選んでください</div>';
    return;
  }

  // ── Quantities ───────────────────────────────
  const qtyPair  = wants.pair  ? getQty('qty-pair')  : 0;
  const qtyClear = wants.clear ? getQty('qty-clear') : 0;
  const soloQtys = {};
  for (const id of selectedSolos) soloQtys[id] = getQty(`sq-${id}`);

  // アクスタ quantities
  const aqA = selectedAcrylics.includes('A') ? getQty('aq-A') : 0;
  const aqB = selectedAcrylics.includes('B') ? getQty('aq-B') : 0;
  const aqSolo = {};
  for (const id of MEMBER_ORDER) {
    if (selectedAcrylics.includes(id)) aqSolo[id] = getQty(`aq-${id}`);
  }
  const fcRequired     = selectedAcrylics.length > 0;
  const warnings       = [];

  // ── Solo disc counts (merge card qty + acrylic qty) ──
  const soloCounts = {};
  for (const [id, qty] of Object.entries(aqSolo))   soloCounts[id] = Math.max(soloCounts[id]||0, qty);
  for (const [id, qty] of Object.entries(soloQtys)) soloCounts[id] = Math.max(soloCounts[id]||0, qty);
  const totalSoloCount = Object.values(soloCounts).reduce((s,v)=>s+v, 0);

  // ── Required 初回 and 通常 counts ──────────────
  // 初回: max of (bluray need, アクスタA qty, クリアトレカ sets)
  let numInitial = Math.max(wants.bluray ? 1 : 0, aqA, qtyClear);

  // クリアトレカ partner: use solo discs first to avoid buying extra 通常盤
  const soloAsPartner = Math.min(totalSoloCount, qtyClear);
  const clearNeedNormal = qtyClear - soloAsPartner;
  // 通常: max of (アクスタB qty, clear partners needed)
  let numNormal = Math.max(aqB, clearNeedNormal);

  // ペアトレカ: total items must be >= qtyPair
  const existingItems = numInitial + numNormal + totalSoloCount;
  if (qtyPair > existingItems) numNormal += qtyPair - existingItems;

  // ── Build items list ─────────────────────────
  const items = [];

  if (numInitial > 0) {
    const fcSets = aqA;
    if (fcSets > 0) {
      items.push({ name: '初回限定盤＋集合アクスタA セット', price: 5170, badge: 'FC限定', qty: fcSets });
      if (numInitial > fcSets) items.push({ name: '初回限定盤', price: 3520, qty: numInitial - fcSets });
    } else {
      items.push({ name: '初回限定盤', price: 3520, qty: numInitial });
    }
  }
  if (numNormal > 0) {
    const fcSets = aqB;
    if (fcSets > 0) {
      items.push({ name: '通常盤＋集合アクスタB セット', price: 4180, badge: 'FC限定', qty: fcSets });
      if (numNormal > fcSets) items.push({ name: '通常盤', price: 2530, qty: numNormal - fcSets });
    } else {
      items.push({ name: '通常盤', price: 2530, qty: numNormal });
    }
  }
  for (const id of MEMBER_ORDER.filter(id => soloCounts[id])) {
    const mName  = MEMBERS[id].name;
    const count  = soloCounts[id];
    const fcSets = aqSolo[id] || 0;
    if (fcSets > 0) {
      items.push({ name: `${mName} ソロ盤＋ソロアクスタ セット`, price: 4180, badge: 'FC限定', qty: fcSets });
      if (count > fcSets) items.push({ name: `${mName} ソロ盤`, price: 2530, qty: count - fcSets });
    } else {
      items.push({ name: `${mName} ソロ盤`, price: 2530, qty: count });
    }
  }

  // ── Store ────────────────────────────────────
  const needsEC = qtyPair > 0 || qtyClear > 0;
  let primaryStore = null;
  let extraStore   = null;
  let extraPrice   = 0;

  if (fcRequired) primaryStore = 'VICTOR ONLINE STORE（FC会員）';

  if (wants.sticker && stickerM) {
    const sStore = MEMBERS[stickerM]?.sticker_store;
    if (sStore) {
      if (sStore === '応援店（TSUTAYA等）' && needsEC) {
        extraStore = '応援店（TSUTAYA等）'; extraPrice = 2530;
        warnings.push('吉田仁人ステッカーは応援店（TSUTAYA等）の先着特典ですが、応援店は早期予約特典（ペアトレカ・クリアトレカ）の対象外です。ステッカーを取得するには応援店でも別途1形態以上を購入する必要があります。');
      } else if (fcRequired && sStore !== 'VICTOR ONLINE STORE') {
        extraStore = sStore; extraPrice = 2530;
        warnings.push(`${MEMBERS[stickerM].name}のステッカーは${sStore}の先着特典です。FC限定アクスタのためVICTOR ONLINE STOREでも購入しますが、ステッカー取得には${sStore}でも別途1形態以上の購入が必要です。`);
      } else if (!fcRequired) {
        primaryStore = sStore;
      }
    }
  }
  if (!primaryStore && needsEC)  primaryStore = 'EC通路（6店舗いずれか）';
  if (!primaryStore && !needsEC) primaryStore = '取扱店いずれか';

  // ── Achievable early-reservation totals ───────
  // Even if not selected, show what the purchase will yield
  const totalItems  = numInitial + numNormal + totalSoloCount;
  const totalSets   = Math.min(numInitial, numNormal + totalSoloCount); // qualifying 初回+partner sets

  // ── Tokuten list ─────────────────────────────
  const tokuten = [];
  const totalForms = items.reduce((s,i)=>s+i.qty,0);

  // Enclosed cards
  const enclosed = [];
  const initQty  = items.filter(i=>i.name.startsWith('初回限定盤')).reduce((s,i)=>s+i.qty,0);
  const normQty  = items.filter(i=>i.name.startsWith('通常盤')).reduce((s,i)=>s+i.qty,0);
  if (initQty > 0) enclosed.push(`初回限定盤×${initQty}（5種ランダム各1枚）`);
  if (normQty > 0) enclosed.push(`通常盤×${normQty}（5種ランダム各1枚）`);
  for (const id of MEMBER_ORDER.filter(id=>soloCounts[id]))
    enclosed.push(`${MEMBERS[id].name}ソロ盤×${soloCounts[id]}（3種ランダム各1枚）`);
  if (enclosed.length > 0)
    tokuten.push({ name: `封入ソロトレカ ×${totalForms}`, desc: enclosed.join(' / ') });
  tokuten.push({ name: '応募抽選シリアル', desc: `全${totalForms}形態（初回プレス限定）` });

  // ペアトレカ — show actual total, highlighted only if user selected it
  if (totalItems > 0) {
    const isSelected = qtyPair > 0;
    tokuten.push({
      name:  `ペアトレカ ×${totalItems}`,
      desc:  isSelected ? '早期予約特典・各5種ランダム1枚' : 'EC通路で期間内購入の場合に取得',
      hi:    isSelected,
      bonus: !isSelected,
    });
  }

  // クリアトレカ — show qualifying sets count
  if (totalSets > 0) {
    const isSelected = qtyClear > 0;
    tokuten.push({
      name:  `クリアトレカ（透卡）×${totalSets}`,
      desc:  isSelected ? '早期セット予約特典・各ソロ5種ランダム1枚' : 'EC通路で期間内購入の場合に取得',
      hi:    isSelected,
      bonus: !isSelected,
    });
  }

  if (numInitial > 0) tokuten.push({ name: 'Blu-ray イベント映像', desc: '初回限定盤封入・発売日記念スペシャルイベント', hi: wants.bluray, bonus: !wants.bluray });
  if (wants.sticker && stickerM) {
    const info = MEMBERS[stickerM];
    if (info) {
      if (stickerM === 'amazon') tokuten.push({ name: 'メガジャケ', desc: 'Amazon 先着特典', hi: true });
      else tokuten.push({ name: `${info.name} デザインステッカー`, desc: `${info.sticker_store} 先着特典`, hi: true });
    }
  }
  if (aqA > 0) tokuten.push({ name: `集合アクスタA ×${aqA}`, desc: 'FC限定', hi: true });
  if (aqB > 0) tokuten.push({ name: `集合アクスタB ×${aqB}`, desc: 'FC限定', hi: true });
  for (const id of MEMBER_ORDER.filter(id=>aqSolo[id]))
    tokuten.push({ name: `${MEMBERS[id].name} ソロアクスタ ×${aqSolo[id]}`, desc: 'FC限定', hi: true });
  if (fcRequired) tokuten.push({ name: '曽野舜太 デザインステッカー', desc: 'FC購入者全員特典' });

  // ── Render ───────────────────────────────────
  const total = items.reduce((s,i) => s + i.price * i.qty, 0) + extraPrice;

  const shortNames = items.map(i => {
    const qty = i.qty > 1 ? ` ×${i.qty}` : '';
    return i.name.replace('＋集合アクスタA セット','').replace('＋集合アクスタB セット','').replace('＋ソロアクスタ セット','') + qty;
  });
  if (extraStore) shortNames.push(`任意1形態（${extraStore}）`);
  const planNote = shortNames.length <= 2 ? shortNames.join('＋') : `合計${totalForms + (extraStore?1:0)}形態の購入`;

  const itemsHtml = items.map(i => {
    const lineTotal = i.price * i.qty;
    const unitNote  = i.qty > 1 ? `<small style="color:rgba(212,71,123,.6);font-weight:500;margin-left:4px">¥${i.price.toLocaleString()} × ${i.qty}</small>` : '';
    return `
    <div class="res-item">
      <div class="res-item-name">
        ${i.name}${i.badge ? `<span class="res-badge">${i.badge}</span>` : ''}
      </div>
      <div class="res-item-right">
        <span class="res-qty">× ${i.qty}</span>
        <div class="res-item-price">¥${lineTotal.toLocaleString()}${unitNote}</div>
      </div>
    </div>`;
  }).join('');

  const extraHtml = extraStore ? `
    <div class="res-item">
      <div class="res-item-name">任意1形態（${extraStore} ステッカー用）<span class="res-badge-warn">別途</span></div>
      <div class="res-item-right"><span class="res-qty">× 1</span><div class="res-item-price">¥2,530〜</div></div>
    </div>` : '';

  const totalForms2 = totalForms + (extraStore ? 1 : 0);
  const formsSummary = `合計 ${totalForms2}形態 / ¥${total.toLocaleString()}${extraPrice ? '〜' : ''}（税込）`;

  const selectedToks = tokuten.filter(t => !t.bonus);
  const bonusToks    = tokuten.filter(t =>  t.bonus);
  const tokutenHtml =
    selectedToks.map(t => `
    <div class="res-tok">
      <span class="res-tok-name${t.hi ? ' hi' : ''}">${t.name}</span>
      <span class="res-tok-desc">${t.desc}</span>
    </div>`).join('') +
    (bonusToks.length ? `<div class="res-bonus-sep">購入で同時取得</div>` : '') +
    bonusToks.map(t => `
    <div class="res-tok bonus">
      <span class="res-tok-name">${t.name}</span>
      <span class="res-tok-desc">${t.desc}</span>
    </div>`).join('');

  const warningsHtml = warnings.map(w => `<div class="res-warn">⚠️ ${w}</div>`).join('');
  const deadlineHtml = (needsEC || fcRequired)
    ? `<div class="res-deadline">予約締切：2026年7月19日（日）23:59</div>` : '';

  panel.innerHTML = `
    <div class="res-plan-header">
      <div class="res-plan-label">✓ 最安プラン</div>
      <div class="res-plan-price">¥${total.toLocaleString()}<small>${extraPrice ? '〜（税込）' : '（税込）'}</small></div>
      <div class="res-plan-note">${planNote}</div>
    </div>
    <div class="res-sec">
      <div class="res-label">購入リスト</div>
      ${itemsHtml}${extraHtml}
      <div class="res-total">${formsSummary}</div>
    </div>
    <div class="res-store">
      <div class="res-store-label">購入通路</div>
      <div class="res-store-val">${primaryStore}</div>
      ${extraStore ? `<div class="res-store-extra">＋ ${extraStore}（ステッカー用）</div>` : ''}
    </div>
    <div class="res-sec">
      <div class="res-label">取得できる特典</div>
      ${tokutenHtml}
    </div>
    ${warningsHtml}
    ${deadlineHtml}
  `;
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


ARTICLE_MODAL_CSS = '''
#am-backdrop{position:fixed;inset:0;background:rgba(10,10,20,.65);z-index:400;opacity:0;pointer-events:none;transition:opacity .25s;backdrop-filter:blur(3px);}
#am-backdrop.am-open{opacity:1;pointer-events:all;}
#am-modal{position:fixed;bottom:0;left:50%;transform:translateX(-50%) translateY(102%);width:min(860px,100%);max-height:92vh;background:#fff;border-radius:16px 16px 0 0;box-shadow:0 -4px 40px rgba(0,0,0,.18);z-index:500;display:flex;flex-direction:column;transition:transform .32s cubic-bezier(.2,.9,.3,1);overflow:hidden;}
#am-modal.am-open{transform:translateX(-50%) translateY(0);}
.am-handle{width:36px;height:4px;background:#e0e0e8;border-radius:2px;margin:10px auto 0;flex-shrink:0;}
.am-header{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;padding:14px 20px 0;flex-shrink:0;}
.am-meta{display:flex;align-items:center;gap:8px;}
.am-badge{font-size:.68rem;font-weight:700;padding:2px 8px;border-radius:4px;color:#fff;background:var(--c,#888);}
.am-date{font-size:.75rem;color:#888;}
.am-close{width:30px;height:30px;border:none;background:#f0f0f4;border-radius:50%;cursor:pointer;font-size:.9rem;color:#666;display:flex;align-items:center;justify-content:center;flex-shrink:0;}
.am-close:hover{background:#e0e0e8;color:#111;}
.am-scroll{overflow-y:auto;flex:1;padding:0 20px 28px;-webkit-overflow-scrolling:touch;}
.am-title-zh{font-size:1.15rem;font-weight:800;line-height:1.42;margin-top:12px;color:#111118;text-wrap:balance;}
.am-title-ja{font-size:.82rem;color:#888;line-height:1.5;margin-top:5px;}
.am-image-strip{display:flex;gap:10px;overflow-x:auto;padding:16px 0 4px;scrollbar-width:none;}
.am-image-strip::-webkit-scrollbar{display:none;}
.am-img-item{flex-shrink:0;border-radius:8px;overflow:hidden;border:1px solid #e8e8ee;background:#eef3ff;display:flex;align-items:center;justify-content:center;height:200px;}
.am-img-item img{max-width:300px;max-height:200px;width:auto;height:auto;display:block;}
.am-img-dl{display:block;text-decoration:none;position:relative;}
.am-dl-btn{position:absolute;bottom:6px;right:6px;background:rgba(0,0,0,.65);color:#fff;font-size:.63rem;padding:4px 10px;border-radius:20px;backdrop-filter:blur(3px);}
.am-dl-btn:hover{background:rgba(0,0,0,.85);}
.am-divider{border:none;border-top:1px solid #e8e8ee;margin:16px 0;}
.am-section-label{font-size:.68rem;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:#aaa;margin-bottom:10px;}
.am-body-zh{font-size:.95rem;line-height:1.9;color:#111118;max-width:62ch;}.am-body-zh p{margin-bottom:.85em;}.am-body-zh p:last-child{margin-bottom:0;}
.am-ja-toggle{display:flex;align-items:center;gap:6px;background:none;border:1px solid #e0e0e8;border-radius:6px;padding:7px 14px;font-size:.78rem;color:#888;cursor:pointer;margin-top:18px;transition:background .15s;}
.am-ja-toggle:hover{background:#f5f5f8;}
.am-ja-toggle.am-ja-expanded{color:#555;}
.am-body-ja{display:none;margin-top:10px;font-size:.82rem;line-height:1.8;color:#888;max-width:62ch;padding:12px;background:#f8f8fb;border-radius:8px;white-space:pre-wrap;}
.am-body-ja.am-ja-visible{display:block;}
.am-footer{padding:12px 20px;border-top:1px solid #e8e8ee;display:flex;align-items:center;justify-content:space-between;flex-shrink:0;background:#fff;}
.am-source-note{font-size:.72rem;color:#aaa;}
.am-orig-btn{display:inline-flex;align-items:center;gap:5px;background:#111118;color:#fff;font-size:.82rem;font-weight:600;padding:8px 16px;border-radius:7px;text-decoration:none;}
.am-orig-btn:hover{opacity:.8;}
@media(prefers-reduced-motion:reduce){#am-modal,#am-backdrop{transition:none;}}
@media(max-width:600px){.am-scroll{padding:0 14px 24px;}.am-header{padding:14px 14px 0;}.am-footer{padding:10px 14px;}.am-title-zh{font-size:1rem;}}
'''

def generate_news_html(articles, updated_at):
    return build_page(NEWS_TEMPLATE, NAV_NEWS,
                      json.dumps(articles, ensure_ascii=False), updated_at,
                      extra_css=ARTICLE_MODAL_CSS)

_DOW_JA  = ['月','火','水','木','金','土','日']
_MON_ABR = ['Jan','Feb','Mar','Apr','May','Jun',
            'Jul','Aug','Sep','Oct','Nov','Dec']


def _render_today_schedule(events, today_dow, today_str):
    """
    今日行程 HTML: 今日放送の番組 + 今日のイベント（events から）
    today_dow: 0=月 1=火 2=水 3=木 4=金 5=土 6=日
    """
    from datetime import datetime as _dt

    def _esc(s):
        return (str(s).replace('&','&amp;').replace('<','&lt;')
                      .replace('>','&gt;').replace('"','&quot;'))

    DOW_LABEL = ['月', '火', '水', '木', '金', '土', '日']
    parts = []

    # ── ① 今日放送のレギュラー番組（SCHEDULE_PROGRAMS + dow_list）──────────
    airing = []
    for name, badge, station, sched, url, color, status, dow_list, members in SCHEDULE_PROGRAMS:
        if status != 'active':
            continue
        sched_display = _prog_sched_cache.get(name, sched)
        if dow_list is not None and today_dow in dow_list:
            airing.append((name, badge, station, sched_display, url, color))

    if airing:
        parts.append('<p class="today-sub-label">&#128250; 今日放送</p>')
        for name, badge, station, sched_disp, url, color in airing:
            parts.append(
                f'<div class="today-prog-row">'
                f'<span class="prog-badge" style="background:{color}">{_esc(badge)}</span>'
                f'<div class="today-prog-body">'
                f'<strong class="today-prog-name">{_esc(name)}</strong>'
                f'<span class="today-prog-detail">{_esc(station)}'
                f'{"  " + _esc(sched_disp) if sched_disp and sched_disp not in ("定期放送中",) else ""}'
                f'</span></div>'
                f'<a class="today-prog-link" href="{_esc(url)}" target="_blank" rel="noopener">公式 &#8599;</a>'
                f'</div>'
            )
    else:
        parts.append(
            '<p class="today-sub-label">&#128250; 今日放送</p>'
            '<p class="today-empty">本日放送のレギュラー番組はありません</p>'
        )

    # ── ② 今日のイベント（events から date == today_str）─────────────────────
    today_evts = [e for e in (events or []) if e.get('date', '') == today_str]
    if today_evts:
        parts.append('<p class="today-sub-label" style="margin-top:18px">&#127917; 本日のイベント</p>')
        for e in today_evts:
            title = _esc(e.get('title', ''))
            venue = _esc(e.get('venue', '') or e.get('location', ''))
            url   = _esc(e.get('url', '#'))
            parts.append(
                f'<div class="today-evt-row">'
                f'<span class="days-chip dc-soon">今日</span>'
                f'<div class="today-evt-body">'
                f'<strong>{title}</strong>'
                f'{"<span class=today-evt-venue>" + venue + "</span>" if venue else ""}'
                f'</div>'
                f'{"<a class=today-prog-link href=" + chr(34) + url + chr(34) + " target=_blank rel=noopener>詳細 &#8599;</a>" if e.get("url") else ""}'
                f'</div>'
            )

    if not airing and not today_evts:
        parts.append(
            '<p class="today-empty">本日予定されているレギュラー放送・イベントはありません。<br>'
            '<a href="https://sd-milk.com/calendar" target="_blank" rel="noopener" '
            'style="color:var(--blue)">公式カレンダー</a> でご確認ください。</p>'
        )

    return '\n'.join(parts)


def _render_live_section(events, today_str):
    """今日の予定 + ライブ・イベント + ツアー の HTML を生成"""
    from datetime import datetime as _dt

    def _esc(s):
        return (str(s).replace('&','&amp;').replace('<','&lt;')
                      .replace('>','&gt;').replace('"','&quot;'))

    def _brick(date_str):
        try:
            d = _dt.strptime(date_str, '%Y-%m-%d')
            return (f'<div class="datebrick">'
                    f'<span class="ev-month">{_MON_ABR[d.month-1]}</span>'
                    f'<span class="ev-day">{d.day:02d}</span>'
                    f'<span class="ev-dow">{_DOW_JA[d.weekday()]}</span>'
                    f'</div>')
        except Exception:
            return '<div class="datebrick"><span class="ev-day">?</span></div>'

    def _days_chip(date_str):
        try:
            n = (_dt.strptime(date_str,'%Y-%m-%d')
                 - _dt.strptime(today_str,'%Y-%m-%d')).days
        except Exception:
            return ''
        if n == 0:
            return '<span class="days-chip dc-soon">今日</span>'
        if n <= 7:
            return f'<span class="days-chip dc-soon">あと {n} 日</span>'
        if n <= 30:
            return f'<span class="days-chip dc-near">あと {n} 日</span>'
        return ''

    upcoming = sorted(
        [e for e in events if e.get('date','') >= today_str],
        key=lambda x: x['date'])
    if not upcoming:
        return (
            '<div class="sched-section">'
            '<p class="sched-section-label">&#127917; ライブ・イベント</p>'
            '<div class="ev-next-block">'
            '<p class="ev-next-label">現在登録されているライブ・イベントはありません</p>'
            '<p style="font-size:.8rem;color:var(--muted);margin-top:8px;">'
            '公式スケジュールは <a href="https://sd-milk.com/calendar" '
            'target="_blank" rel="noopener" style="color:var(--blue)">こちら</a> でご確認ください。</p>'
            '</div>'
            '</div>\n'
        )

    today_evt = next((e for e in upcoming if e['date'] == today_str), None)
    next_evt  = upcoming[0]

    # ── tour detection: same venue within 14 days → group ──────────────────
    used, groups = set(), []
    for i, e in enumerate(upcoming):
        if i in used:
            continue
        used.add(i)
        venue = e.get('title','').replace('[LIVE]','').strip()
        grp   = [e]
        for j in range(i + 1, len(upcoming)):
            if j in used:
                continue
            e2 = upcoming[j]
            if e2.get('title','').replace('[LIVE]','').strip() != venue:
                continue
            try:
                gap = (_dt.strptime(e2['date'],'%Y-%m-%d')
                       - _dt.strptime(e['date'],'%Y-%m-%d')).days
                if gap <= 14:
                    grp.append(e2)
                    used.add(j)
            except Exception:
                pass
        groups.append(grp)

    singles = [g[0] for g in groups if len(g) == 1]
    tours   = [g    for g in groups if len(g) >  1]
    out     = []

    # ── 今日の予定 ─────────────────────────────────────────────────────────
    if today_evt:
        v   = _esc(today_evt.get('title','').replace('[LIVE]','').strip())
        c   = _esc(today_evt.get('details',''))
        url = _esc(today_evt.get('url','#'))
        try:
            d  = _dt.strptime(today_evt['date'],'%Y-%m-%d')
            ds = f"{d.year}-{d.month:02d}-{d.day:02d} ({_DOW_JA[d.weekday()]})"
        except Exception:
            ds = today_evt['date']
        out.append(
            '<div class="sched-section">'
            '<p class="sched-section-label">今日の予定</p>'
            '<div class="ev-today-block">'
            f'<div class="ev-today-hdr"><span class="ev-today-tag">TODAY LIVE</span>'
            f'<span class="ev-today-date">{ds}</span></div>'
            f'<a class="ev-today-card" href="{url}" target="_blank" rel="noopener">'
            '<div><div style="margin-bottom:6px"><span class="live-badge">LIVE</span></div>'
            f'<p class="ev-today-venue">{v}</p>'
            + (f'<p class="ev-today-city">{c}</p>' if c else '') +
            '</div><span class="ev-today-arrow">&#8594;</span>'
            '</a></div></div>'
        )
    else:
        ne  = next_evt
        v   = _esc(ne.get('title','').replace('[LIVE]','').strip())
        c   = _esc(ne.get('details',''))
        url = _esc(ne.get('url','#'))
        chip = _days_chip(ne['date'])
        try:
            d = _dt.strptime(ne['date'],'%Y-%m-%d')
            mon, day, dow = _MON_ABR[d.month-1], f'{d.day:02d}', _DOW_JA[d.weekday()]
        except Exception:
            mon, day, dow = '?', '??', '?'
        out.append(
            '<div class="sched-section">'
            '<p class="sched-section-label">今日の予定</p>'
            '<div class="ev-next-block">'
            '<p class="ev-next-label">本日のライブ予定はありません &nbsp;&middot;&nbsp; Next up</p>'
            f'<a class="ev-next-link" href="{url}" target="_blank" rel="noopener">'
            '<div class="ev-next-brick">'
            f'<span class="ev-month">{mon}</span>'
            f'<span class="ev-day ev-day-lg">{day}</span>'
            f'<span class="ev-dow">{dow}</span>'
            '</div>'
            '<div class="ev-next-div"></div>'
            '<div class="ev-next-info">'
            + chip +
            f'<p class="ev-next-venue">{v}</p>'
            + (f'<p class="ev-next-city">{c}</p>' if c else '') +
            '</div></a></div></div>'
        )

    # ── ライブ・イベント ────────────────────────────────────────────────────
    if singles:
        rows = []
        for e in singles:
            v   = _esc(e.get('title','').replace('[LIVE]','').strip())
            c   = _esc(e.get('details',''))
            url = _esc(e.get('url','#'))
            rows.append(
                f'<a class="ev-row" href="{url}" target="_blank" rel="noopener">'
                + _brick(e['date']) +
                '<div class="ev-info">'
                '<div class="ev-badges"><span class="live-badge">LIVE</span>'
                '<span class="src-badge">setlist.fm</span></div>'
                f'<p class="ev-venue">{v}</p>'
                + (f'<p class="ev-city">{c}</p>' if c else '') +
                '</div>'
                '<div class="ev-right">' + _days_chip(e['date']) +
                '<span class="ev-ext">詳細</span></div>'
                '</a>'
            )
        out.append(
            '<div class="sched-section">'
            '<p class="sched-section-label">ライブ・イベント</p>'
            '<div class="ev-list">' + ''.join(rows) + '</div>'
            '</div>'
        )

    # ── ツアー ──────────────────────────────────────────────────────────────
    if tours:
        items = []
        for ti, grp in enumerate(tours):
            tid   = f'ev-tour-{ti}'
            first, last = grp[0], grp[-1]
            v = _esc(first.get('title','').replace('[LIVE]','').strip())
            c = _esc(first.get('details',''))
            try:
                df = _dt.strptime(first['date'],'%Y-%m-%d')
                dl = _dt.strptime(last['date'], '%Y-%m-%d')
                yr = f' {dl.year}' if dl.year != df.year else ''
                rng = (f'{_MON_ABR[df.month-1]} {df.day} {_DOW_JA[df.weekday()]}'
                       f' &#8211; {_MON_ABR[dl.month-1]} {dl.day} {_DOW_JA[dl.weekday()]}{yr}')
            except Exception:
                rng = f"{first['date']} &#8211; {last['date']}"
            date_rows = []
            for e in grp:
                eu = _esc(e.get('url','#'))
                ev = _esc(e.get('title','').replace('[LIVE]','').strip())
                ec = _esc(e.get('details',''))
                date_rows.append(
                    f'<a class="tour-dr" href="{eu}" target="_blank" rel="noopener">'
                    + _brick(e['date']) +
                    f'<div><p class="tour-dr-venue">{ev}</p>'
                    + (f'<p class="tour-dr-city">{ec}</p>' if ec else '') +
                    '</div><span class="ev-ext">詳細</span></a>'
                )
            items.append(
                f'<div class="tour-item" id="{tid}">'
                f'<button class="tour-btn" onclick="evTgl(\'{tid}\')" aria-expanded="false">'
                '<div class="tour-icon">'
                '<span class="tour-icon-lbl">TOUR</span>'
                f'<span class="tour-icon-n">{len(grp)}</span>'
                '<span class="tour-icon-u">days</span></div>'
                '<div class="tour-meta">'
                '<div class="ev-badges"><span class="live-badge">LIVE</span>'
                '<span class="src-badge">setlist.fm</span></div>'
                f'<p class="tour-title">{v}</p>'
                f'<p class="tour-range">{rng}'
                + (f' &nbsp;&middot;&nbsp; {c}' if c else '') +
                '</p></div>'
                '<span class="tour-caret">&#9660;</span>'
                '</button>'
                '<div class="tour-dates">' + ''.join(date_rows) + '</div>'
                '</div>'
            )
        out.append(
            '<div class="sched-section">'
            '<p class="sched-section-label">ツアー</p>'
            '<div class="ev-list">' + ''.join(items) + '</div>'
            '</div>'
        )

    return ''.join(out)


def generate_schedule_html(articles, events, updated_at):
    today_jst = datetime.now(JST).strftime('%Y-%m-%d')
    today_dow = datetime.now(JST).weekday()  # 0=月 1=火 2=水 3=木 4=金 5=土 6=日

    # 優先顯示今天的文章，如果沒有則往前找最近 2 天
    today_arts = sorted(
        [a for a in articles if a.get('date', '')[:10] == today_jst],
        key=lambda a: a.get('date', ''), reverse=True
    )
    date_label = today_jst
    if not today_arts:
        for delta in [1, 2]:
            past = (datetime.now(JST) - timedelta(days=delta)).strftime('%Y-%m-%d')
            past_arts = sorted(
                [a for a in articles if a.get('date', '')[:10] == past],
                key=lambda a: a.get('date', ''), reverse=True
            )
            if past_arts:
                today_arts = past_arts
                date_label = f'{past}（直近）'
                break

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
            sum_zh = esc(a.get('summary_zh', ''))
            url = esc(a.get('url', '#'))
            source = esc(a.get('source', ''))
            zh_block = ('<p class="tc-title">' + title_zh + '</p>') if title_zh else ''
            sum_block = (f'<p class="tc-ja" style="font-size:.72rem;color:#374151;margin-top:2px">{sum_zh}</p>') if sum_zh else ''
            cards.append(
                f'<div class="today-card">'
                f'<span class="tc-source" style="background:{color}">{source}</span>'
                + zh_block + sum_block +
                f'<p class="tc-ja">{title_ja}</p>'
                f'<a class="tc-link" href="{url}" target="_blank" rel="noopener">原文を読む &rarr;</a>'
                f'</div>'
            )
        today_html = '<div class="today-grid">' + ''.join(cards) + '</div>'
    else:
        today_html = '<p class="today-empty">直近のニュースが見つかりませんでした。</p>'

    # Build program cards — split active vs upcoming vs release
    def _prog_card(name, badge, station, sched, url, color, dow=None, members=None):
        is_today = dow is not None and today_dow in dow
        today_mk = '<span class="today-badge">本日放送</span>' if is_today else ''
        real_url = url if url and url not in ('#', '') else ''
        link_html = (f'<a class="prog-link" href="{esc(real_url)}" target="_blank" rel="noopener">公式サイト &rarr;</a>'
                     if real_url else '')
        if members:
            member_str = '・'.join(members) if isinstance(members, list) else members
            member_html = f'<span class="prog-member">👤 {esc(member_str)}</span>'
        else:
            member_html = ''
        return (
            f'<div class="prog-card">'
            f'<div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">'
            f'<span class="prog-badge" style="background:{color}">{esc(badge)}</span>'
            + today_mk +
            f'</div>'
            f'<p class="prog-name">{esc(name)}</p>'
            + member_html +
            f'<p class="prog-station">{esc(station)}</p>'
            f'<p class="prog-schedule">{esc(sched)}</p>'
            + link_html +
            f'</div>'
        )

    # ── Build unified program list (merge hardcoded SCHEDULE_PROGRAMS + sd-milk auto-fetch) ──
    TYPE_COLOR = {'TV': '#b71c1c', 'ドラマ': '#b71c1c', 'ラジオ': '#0277bd',
                  '映画': '#37474f', 'イベント': '#00695c', 'リリース': '#6a1b9a'}

    def _norm_prog(name):
        """正規化節目名稱以供去重比對：移除空白、Season/第N季、年份數字。"""
        n = re.sub(r'[\s　]', '', name)
        n = re.sub(r'(?:Season|シーズン|第)\s*\d+', '', n, flags=re.IGNORECASE)
        n = re.sub(r'\d{4}', '', n)
        return n.lower()

    # Index hardcoded programs by normalized name for supplement lookup
    hc_lookup      = {row[0]: row for row in SCHEDULE_PROGRAMS}
    hc_norm_lookup = {_norm_prog(row[0]): row for row in SCHEDULE_PROGRAMS}

    # 出演番組 = TV/radio regular shows; 放送中作品 = dramas/movies
    PROG_TYPES = ('TV', 'ラジオ')
    WORK_TYPES = ('ドラマ', '映画')

    prog_cards_list    = []   # 出演番組（放送中のTV/ラジオ）
    work_cards_list    = []   # 放送中作品（現在放送中のドラマ/映画）
    upcoming_work_list = []   # 公開予定作品（未放送のドラマ/映画）
    seen_norms         = set()

    def _add_card(p_name, p_type, station, sched_d, url_use, color, dow, status='active', members=None):
        card = _prog_card(p_name, p_type, station, sched_d, url_use, color, dow, members)
        if p_type in PROG_TYPES:
            prog_cards_list.append(card)
        elif p_type in WORK_TYPES:
            if status == 'upcoming':
                upcoming_work_list.append(card)
            else:
                work_cards_list.append(card)
        # イベント/リリース: omit (handled by live section)

    if _dynamic_programs:
        for p in _dynamic_programs[:40]:
            p_name  = p.get('name', '')
            p_norm  = _norm_prog(p_name)
            if p_norm in seen_norms:
                continue
            p_type  = p.get('type', 'TV')
            color   = TYPE_COLOR.get(p_type, '#555')
            hc = hc_lookup.get(p_name) or hc_norm_lookup.get(p_norm)
            if hc:
                _, hc_badge, hc_station, hc_sched, hc_url, hc_color, hc_status, hc_dow, hc_members = hc
                sched_display = _prog_sched_cache.get(p_name, hc_sched)
                url_use   = p.get('url') or hc_url
                color     = hc_color
                station   = p.get('station') or hc_station
                dow       = hc_dow
                p_status  = hc_status
                p_members = hc_members
            else:
                sched_display = ''
                url_use   = p.get('url', '#')
                station   = p.get('station', '')
                dow       = None
                p_members = None
                # Infer status from start_date
                sd = p.get('start_date', '')
                p_status = 'upcoming' if sd and sd > today_jst else 'active'
            seen_norms.add(p_norm)
            _add_card(p_name, p_type, station, sched_display, url_use, color, dow, p_status, p_members)

        # Supplement: hardcoded items not covered by sd-milk
        for name, badge, station, sched, url, color, status, dow, members in SCHEDULE_PROGRAMS:
            if _norm_prog(name) in seen_norms:
                continue
            sched_display = _prog_sched_cache.get(name, sched)
            seen_norms.add(_norm_prog(name))
            _add_card(name, badge, station, sched_display, url, color, dow, status, members)
    else:
        # Fallback: hardcoded only
        for name, badge, station, sched, url, color, status, dow, members in SCHEDULE_PROGRAMS:
            sched_display = _prog_sched_cache.get(name, sched)
            _add_card(name, badge, station, sched_display, url, color, dow, status, members)

    prog_broadcast_html = (
        '<div class="prog-grid">' + ''.join(prog_cards_list) + '</div>'
        if prog_cards_list
        else '<p class="today-empty">データ取得中です。しばらくお待ちください。</p>'
    )
    prog_works_html = (
        '<div class="prog-grid">' + ''.join(work_cards_list) + '</div>'
        if work_cards_list
        else '<p class="today-empty">現在放送中の作品はありません。</p>'
    )
    prog_upcoming_html = (
        '<div class="prog-grid">' + ''.join(upcoming_work_list) + '</div>'
        if upcoming_work_list
        else ''
    )

    # ── Past events section ─────────────────────────────────────────────────
    past_events = sorted(
        [e for e in events
         if e.get('date', '') < today_jst and '[LIVE]' in e.get('title', '')],
        key=lambda x: x.get('date', ''), reverse=True
    )
    past_html = ''
    if past_events:
        by_year = {}
        for e in past_events:
            yr = e.get('date', '')[:4]
            by_year.setdefault(yr, []).append(e)
        year_blocks = []
        for yr in sorted(by_year.keys(), reverse=True):
            rows = []
            for e in by_year[yr]:
                mm_dd   = e.get('date', '')[5:]  # MM-DD
                title   = e.get('title', '').replace('[LIVE]', '').strip()
                detail  = e.get('details', '')
                ev_url  = e.get('url', '#')
                rows.append(
                    f'<a class="past-item" href="{esc(ev_url)}" target="_blank" rel="noopener">'
                    f'<span class="past-date-chip">{esc(mm_dd)}</span>'
                    f'<span class="past-title">{esc(title)}</span>'
                    + (f'<span class="past-detail">{esc(detail[:40])}</span>' if detail else '') +
                    f'</a>'
                )
            year_blocks.append(
                f'<div class="past-year-group">'
                f'<p class="past-year-label">{esc(yr)} 年</p>'
                f'<div class="past-list">{"".join(rows)}</div>'
                f'</div>'
            )
        past_html = ''.join(year_blocks)
    else:
        past_html = '<p class="today-empty">過去の出演記録はまだありません。</p>'

    # ── Schedule calendar data (events + dynamic + hardcoded: past & future) ──
    import json as _json
    from datetime import timedelta as _td
    cal_events = list(events)
    # From sd-milk dynamic programs (start_date field)
    for p in _dynamic_programs:
        sd = p.get('start_date', '')
        if sd:
            cal_events.append({
                'date':    sd,
                'title':   p.get('name', ''),
                'details': p.get('station', ''),
                'url':     p.get('url', '#'),
            })
    # From hardcoded SCHEDULE_PROGRAMS
    _DATE_RE_CAL = re.compile(r'(\d{4})年(\d{1,2})月(\d{1,2})日')
    _today_dt    = datetime.now(JST).replace(tzinfo=None).replace(hour=0, minute=0, second=0, microsecond=0)
    for name, badge, station, sched, url, color, status, dow_list, members in SCHEDULE_PROGRAMS:
        _cal_m = ('・'.join(members) if isinstance(members, list) else members) if members else ''
        cal_title = name + (f'（{_cal_m}）' if _cal_m else '')
        if status in ('upcoming', 'release'):
            # Explicit date in station/sched text
            for text_field in (station, sched):
                m = _DATE_RE_CAL.search(text_field)
                if m:
                    ev_date = f'{m.group(1)}-{m.group(2).zfill(2)}-{m.group(3).zfill(2)}'
                    already = any(e.get('date') == ev_date and e.get('title') == cal_title for e in cal_events)
                    if not already:
                        cal_events.append({'date': ev_date, 'title': cal_title, 'details': station, 'url': url})
                    break
        elif status == 'active' and dow_list:
            # Generate weekly recurring dates: 4 weeks back → 8 weeks forward
            for dw in dow_list:
                days_back = (_today_dt.weekday() - dw) % 7
                base = _today_dt - _td(days=days_back)  # most recent occurrence
                for w in range(-4, 9):
                    candidate = base + _td(weeks=w)
                    date_str = candidate.strftime('%Y-%m-%d')
                    already = any(e.get('date') == date_str and e.get('title') == cal_title for e in cal_events)
                    if not already:
                        cal_events.append({'date': date_str, 'title': cal_title, 'details': station, 'url': url})

    sched_data_js = _json.dumps(
        [{'date': e.get('date',''), 'title': e.get('title',''),
          'details': e.get('details',''), 'url': e.get('url','')}
         for e in cal_events if e.get('date')],
        ensure_ascii=False
    )

    live_section  = _render_live_section(events, today_jst)
    today_sched   = _render_today_schedule(events, today_dow, today_jst)

    # Build 公開予定 section only if there are upcoming works
    if prog_upcoming_html:
        upcoming_section = (
            '<div class="sched-section">'
            '<p class="sched-section-title">&#127915; 公開予定・出演予定</p>'
            + prog_upcoming_html +
            '</div>'
        )
    else:
        upcoming_section = ''

    return (SCHEDULE_TEMPLATE
            .replace('__CSS__', COMMON_CSS)
            .replace('__STRIPE__', MEMBER_STRIPE_HTML)
            .replace('__NAV__', NAV_SCHEDULE)
            .replace('__SOURCES_FOOTER__', make_sources_footer())
            .replace('__UPDATED_AT__', updated_at)
            .replace('__TODAY__', date_label)
            .replace('__TODAY_SCHED__', today_sched)
            .replace('__PROG_BROADCAST__', prog_broadcast_html)
            .replace('__PROG_WORKS__', prog_works_html)
            .replace('__PROG_UPCOMING_SECTION__', upcoming_section)
            .replace('__PAST_EVENTS__', past_html)
            .replace('__SCHED_DATA__', sched_data_js)
            .replace('__LIVE_SECTION__', live_section))

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    import os as _os, sys as _sys
    NEWS_ONLY = _os.environ.get('NEWS_ONLY') == '1' or '--news-only' in _sys.argv
    print(f'=== M!LK Fan Hub {"(NEWS ONLY)" if NEWS_ONLY else "(FULL UPDATE)"} ===')

    existing = load_json(ARTICLES_FILE)
    print(f'Loaded {len(existing)} existing articles')

    # Google News URL の記事に誤って保存された画像を除去
    # 判定: 記事 URL が news.google.com OR 画像 URL が Google logo CDN
    _GOOGLE_IMG_HOSTS = ('googleusercontent.com', 'news.google.com', 'google.com/s2/')
    _fixed = 0
    for _a in existing:
        img = _a.get('image', '')
        is_gnews_url = 'news.google.com' in _a.get('url', '')
        is_google_img = img and any(h in img for h in _GOOGLE_IMG_HOSTS)
        if img and (is_gnews_url or is_google_img):
            _a['image'] = ''
            _fixed += 1
        # Also clean images list
        old_imgs = _a.get('images', [])
        if old_imgs:
            clean_imgs = [i for i in old_imgs if i and not any(h in i for h in _GOOGLE_IMG_HOSTS)]
            if len(clean_imgs) < len(old_imgs):
                _a['images'] = clean_imgs
                _fixed += len(old_imgs) - len(clean_imgs)
    if _fixed:
        print(f'  Cleared {_fixed} Google News logo images from existing articles')

    # Google News RSS タイトル・サマリーに付いた "- SOURCE NAME" を過去記事から除去
    _fixed_titles = 0
    _gnews_suffix_re = re.compile(r'\s+-\s+\S+\s*$')
    for _a in existing:
        if 'news.google.com' not in _a.get('url', ''):
            continue
        tj = _a.get('title_ja', '')
        cleaned = _gnews_suffix_re.sub('', tj).strip()
        if cleaned != tj:
            _a['title_ja'] = cleaned
            _a['title_zh'] = ''  # re-translate with corrected title
            _fixed_titles += 1
        sj = _a.get('summary_ja', '')
        cleaned_s = _gnews_suffix_re.sub('', sj).strip()
        if cleaned_s != sj:
            _a['summary_ja'] = cleaned_s
            _a['summary_zh'] = ''  # re-translate
    if _fixed_titles:
        print(f'  Stripped Google News title suffix from {_fixed_titles} articles (will re-translate)')

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

    # Dedup existing archive by URL then by title (removes cross-source duplicates)
    existing = dedup_by_url(existing)
    existing = dedup_by_title(existing)

    # Back-fill missing translations (budget=300 covers large gaps after quota reset)
    if translator or _google_translate_ok:
        _tx_budget = 300
        _tx_count  = 0
        # Pass 1: missing title translations
        for _a in existing:
            if _tx_count >= _tx_budget:
                break
            if _a.get('title_ja') and not _a.get('title_zh'):
                result = translate(_a['title_ja'])
                if result:           # only count when translation actually succeeded
                    _a['title_zh'] = result
                    _tx_count += 1
        # Pass 2: missing summary translations (independent of title)
        for _a in existing:
            if _tx_count >= _tx_budget:
                break
            if _a.get('summary_ja') and not _a.get('summary_zh'):
                result = translate(_a['summary_ja'])
                if result:
                    _a['summary_zh'] = result
                    _tx_count += 1
        # Pass 3: missing body translations (full-text for modal display)
        for _a in existing:
            if _tx_count >= _tx_budget:
                break
            if _a.get('body_ja') and not _a.get('body_zh'):
                result = translate(_a['body_ja'])
                if result:
                    _a['body_zh'] = result
                    _tx_count += 1
        if _tx_count:
            print(f'Back-filled {_tx_count} missing translations')

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

    # 抓每篇文章的圖片 + 全文本文
    print(f'Fetching article data (image + body) for {len(new_raw)} articles...')
    for a in new_raw:
        if a.get('url'):
            img, imgs, body, pub_date = fetch_article_data(a['url'])
            a['image']  = img
            a['images'] = imgs
            if pub_date and not a.get('date'):
                a['date'] = pub_date
            # summary_ja：RSS / scrape 來的摘要，保留不蓋掉（供卡片顯示）
            if body and not a.get('summary_ja'):
                a['summary_ja'] = body[:300]   # 卡片摘要限 300 字
            # body_ja：永遠存最完整的全文（供 Modal 顯示）
            if body:
                a['body_ja'] = body
            time.sleep(0.3)

    if translator or _google_translate_ok:
        print(f'Translating {len(new_raw)} articles...')
        for a in new_raw:
            a['title_zh']   = translate(a.get('title_ja', ''))
            a['summary_zh'] = translate(a['summary_ja']) if a.get('summary_ja') else ''
            # 全文翻譯（body_ja → body_zh）
            if a.get('body_ja'):
                a['body_zh'] = translate(a['body_ja'])
    else:
        print('WARNING: No translation service available')

    merged, added = merge_by_url(existing, new_raw)
    # Remove cross-source duplicates (same article via Oricon RSS + Google News RSS)
    before_td = len(merged)
    merged = dedup_by_title(merged)
    if len(merged) < before_td:
        print(f'  Title dedup removed {before_td - len(merged)} cross-source duplicates')
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
    if translator or _google_translate_ok:
        for g in new_goods:
            if g.get('title') and not g.get('title_zh'):
                g['title_zh'] = translate(g['title'])
    # Fetch images for goods that don't have one
    for g in new_goods:
        if g.get('url') and not g.get('image'):
            img, _, _, _ = fetch_article_data(g['url'])
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

    fetch_prog_schedules()           # populate _prog_sched_cache before rendering
    global _dynamic_programs
    _dynamic_programs = fetch_sd_milk_programs_pw()   # auto-fetch program list
    with open('schedule.html', 'w', encoding='utf-8') as f:
        f.write(generate_schedule_html(merged, merged_sched, now_jst))

    with open('goods.html', 'w', encoding='utf-8') as f:
        f.write(generate_goods_html(merged_goods, merged, now_jst))

    with open('album.html', 'w', encoding='utf-8') as f:
        f.write(generate_album_html(now_jst))

    # ── Oricon Charts ─────────────────────────────────────────────────────────
    ex_charts   = load_json(CHARTS_FILE)
    new_charts  = fetch_oricon_charts()
    ex_chart_ids = {c['id'] for c in ex_charts}
    added_charts = [c for c in new_charts if c['id'] not in ex_chart_ids]
    merged_charts = new_charts + [c for c in ex_charts if c['id'] not in {e['id'] for e in new_charts}]
    # Keep only last 52 weeks worth of entries (cap to avoid unbounded growth)
    merged_charts = sorted(merged_charts, key=lambda c: c['date'], reverse=True)
    save_json(CHARTS_FILE, merged_charts)
    print(f'Charts: +{len(added_charts)} new (total {len(merged_charts)})')
    with open('chart.html', 'w', encoding='utf-8') as f:
        f.write(generate_chart_html(merged_charts, now_jst))

    with open('sources.html', 'w', encoding='utf-8') as f:
        f.write(generate_sources_html(now_jst))

    print('Done!')


if __name__ == '__main__':
    main()

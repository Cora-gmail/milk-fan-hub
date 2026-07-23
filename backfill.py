"""
backfill.py — M!LK Fan Hub 歷史文章回補腳本

用法（本機）：
    python backfill.py
    python backfill.py --since 2026-01-01   # 只補指定日期之後
    python backfill.py --dry-run             # 只列出找到的文章，不寫入

GitHub Actions：workflow_dispatch 時自動觸發（見 daily-fetch.yml）。
"""

import argparse
import json
import os
import re
import sys
import time

import requests
from bs4 import BeautifulSoup

# ── 從 fetch_news.py 借用共用工具 ─────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(__file__))
from fetch_news import (
    HEADERS, _milk_check, translate,
    fetch_article_data, load_json, save_json,
    merge_by_url, _make_article,
    ARTICLES_FILE,
    _parse_oricon_chart_page, _parse_oricon_daily_chart_page,
    CHARTS_FILE, CHART_SCANNED_FILE, CHART_DAILY_SCANNED_FILE,
    make_id,
)

# ── 設定 ──────────────────────────────────────────────────────────────────────
MAX_PAGES   = 12       # 每個來源最多抓幾頁
SLEEP_SEC   = 0.8      # 每次請求間隔（秒）
MIN_SINCE   = '2026-01-01'  # 預設最早回補到這個日期

# ── 來源定義 ──────────────────────────────────────────────────────────────────
def _get_page(url):
    r = requests.get(url, headers=HEADERS, timeout=14)
    r.encoding = r.apparent_encoding or 'utf-8'
    return BeautifulSoup(r.text, 'html.parser')


# ─── The First Times ──────────────────────────────────────────────────────────
# URL pattern: https://www.thefirsttimes.jp/?s=M%21LK&paged=N
# 每頁有 20 筆左右；連結文字格式：NEWS2026.06.29M!LK...
_FT_DATE_RE = re.compile(r'(20\d\d)\.(\d{2})\.(\d{2})')
_FT_STRIP   = re.compile(r'^(NEWS|COLUMN|INTERVIEW|LIVE|SPECIAL|REVIEW)\s*', re.I)

def _scrape_firsttimes(since: str, max_pages: int):
    print('\n[The First Times] 開始抓取（paged=1~' + str(max_pages) + '）...')
    articles = []
    for page in range(1, max_pages + 1):
        url = ('https://www.thefirsttimes.jp/?s=M%21LK'
               + (f'&paged={page}' if page > 1 else ''))
        try:
            soup = _get_page(url)
        except Exception as e:
            print(f'  p{page}: 連線失敗 {e}')
            break

        found_this_page = 0
        for a in soup.find_all('a', href=True):
            href = a['href']
            if 'thefirsttimes.jp/news/' not in href and 'thefirsttimes.jp/column/' not in href:
                continue
            raw_text = a.get_text(' ', strip=True)
            if not _milk_check(raw_text):
                continue

            # 解析日期
            m = _FT_DATE_RE.search(raw_text)
            if not m:
                continue
            date_str = f'{m.group(1)}-{m.group(2)}-{m.group(3)}'
            if date_str < since:
                print(f'  p{page}: 到達 {date_str}，早於 {since}，停止')
                return articles

            # 清理標題（去掉前綴 NEWS / COLUMN 和日期）
            title = _FT_STRIP.sub('', raw_text)
            title = _FT_DATE_RE.sub('', title).strip()
            if not title or len(title) < 5:
                continue

            art = _make_article('The First Times', title, href, date_str, '')
            articles.append(art)
            found_this_page += 1

        print(f'  p{page}: {found_this_page} 筆')
        if found_this_page == 0:
            break
        time.sleep(SLEEP_SEC)
    return articles


# ─── Musicvoice ───────────────────────────────────────────────────────────────
def _scrape_musicvoice(since: str, max_pages: int):
    print('\n[Musicvoice] 開始抓取（paged=1~' + str(max_pages) + '）...')
    articles = []
    for page in range(1, max_pages + 1):
        url = ('https://www.musicvoice.jp/?s=m%21lk'
               + (f'&paged={page}' if page > 1 else ''))
        try:
            soup = _get_page(url)
        except Exception as e:
            print(f'  p{page}: 連線失敗 {e}')
            break

        found = 0
        seen_hrefs = {a['url'] for a in articles}
        for a in soup.find_all('a', href=True):
            href = a['href']
            if 'musicvoice.jp/news/' not in href:
                continue
            if href in seen_hrefs:
                continue
            title = a.get_text(strip=True)
            if not title or len(title) < 8 or not _milk_check(title):
                continue
            seen_hrefs.add(href)
            art = _make_article('Musicvoice', title, href, '', '')
            articles.append(art)
            found += 1

        print(f'  p{page}: {found} 筆')
        if found == 0:
            break
        time.sleep(SLEEP_SEC)
    return articles


# ─── BARKS（多試幾種翻頁格式）────────────────────────────────────────────────
def _scrape_barks(since: str, max_pages: int):
    print('\n[BARKS] 嘗試翻頁...')
    articles = []
    formats = [
        lambda p: f'https://www.barks.jp/search/?q=m%21lk&type=news&page={p}',
        lambda p: f'https://www.barks.jp/search/?q=m%21lk&type=news&p={p}',
        lambda p: f'https://www.barks.jp/search/?q=m%21lk&type=news&offset={p*20}',
    ]
    for fmt in formats:
        for page in range(2, 6):
            url = fmt(page)
            try:
                r = requests.get(url, headers=HEADERS, timeout=10)
                if r.status_code != 200:
                    break
                r.encoding = 'utf-8'
                if 'M!LK' not in r.text and 'm!lk' not in r.text.lower():
                    break
                soup = BeautifulSoup(r.text, 'html.parser')
                for a in soup.find_all('a', href=True):
                    if 'barks.jp/news' not in a['href']:
                        continue
                    title = a.get_text(strip=True)
                    if not _milk_check(title):
                        continue
                    art = _make_article('BARKS', title, a['href'], '', '')
                    articles.append(art)
                if articles:
                    print(f'  format OK, page {page}: {len(articles)} 筆')
                    break
            except Exception:
                break
        if articles:
            break
    if not articles:
        print('  BARKS 翻頁格式不支援（跳過）')
    return articles


# ─── Natalie Music（嘗試 M!LK 藝人頁）─────────────────────────────────────────
def _scrape_natalie(since: str):
    print('\n[Natalie Music] 嘗試藝人頁...')
    candidate_ids = [11419]  # M!LK の Natalie artist ID
    for aid in candidate_ids:
        url = f'https://natalie.mu/music/artist/{aid}'
        try:
            r = requests.get(url, headers=HEADERS, timeout=10)
            if r.status_code != 200:
                continue
            r.encoding = 'utf-8'
            if 'M!LK' not in r.text:
                continue
            print(f'  找到 artist/{aid}，解析中...')
            soup = BeautifulSoup(r.text, 'html.parser')
            articles = []
            for a in soup.find_all('a', href=True):
                if 'natalie.mu/music/news' not in a['href']:
                    continue
                title = a.get_text(strip=True)
                if not _milk_check(title) or len(title) < 5:
                    continue
                art = _make_article('Natalie Music', title, a['href'], '', '')
                articles.append(art)
            print(f'  {len(articles)} 筆')
            return articles
        except Exception:
            continue
    print('  Natalie 藝人頁未找到（跳過）')
    return []


# ─── Oricon（Playwright で CF bypass、検索ページ分頁）────────────────────────
def _scrape_oricon_pw(since: str, max_pages: int = 10):
    """Playwright で Oricon の M!LK 検索結果を取得（Cloudflare 対策）"""
    print('\n[Oricon] Playwright でスクレイプ中...')
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        print('  Playwright not installed, skip')
        return []

    articles = []
    seen_urls = set()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            ctx = browser.new_context(
                user_agent=HEADERS['User-Agent'],
                locale='ja-JP',
            )
            pg = ctx.new_page()

            for page_num in range(1, max_pages + 1):
                url = 'https://www.oricon.co.jp/search/?q=m%21lk&cat=news'
                if page_num > 1:
                    url += f'&page={page_num}'
                try:
                    pg.goto(url, timeout=25000, wait_until='domcontentloaded')
                    pg.wait_for_timeout(2500)  # CF チェック待ち
                except PWTimeout:
                    print(f'  p{page_num}: timeout')
                    break
                except Exception as e:
                    print(f'  p{page_num}: nav error {e}')
                    break

                content = pg.content()
                if 'Just a moment' in content:
                    print(f'  p{page_num}: CF challenge, 追加待機...')
                    pg.wait_for_timeout(5000)
                    content = pg.content()

                soup = BeautifulSoup(content, 'html.parser')

                # Oricon 検索結果の記事リンクを収集
                found, stop = 0, False
                for a in soup.find_all('a', href=re.compile(r'/news/\d+')):
                    href = a['href']
                    if not href.startswith('http'):
                        href = 'https://www.oricon.co.jp' + href
                    href = href.split('?')[0]  # クエリ除去
                    if href in seen_urls:
                        continue
                    seen_urls.add(href)

                    title = a.get_text(strip=True)
                    par = a.find_parent(['li', 'article', 'div'])
                    if not title or len(title) < 4:
                        title = par.get_text(' ', strip=True)[:120] if par else ''

                    # 日付チェック
                    date_str = ''
                    if par:
                        t = par.find('time')
                        if t:
                            date_str = t.get('datetime', t.get_text(strip=True))[:10]
                    if date_str and date_str < since:
                        stop = True
                        break

                    if not _milk_check(title):
                        continue
                    articles.append(_make_article('Oricon', title, href, date_str, ''))
                    found += 1

                print(f'  p{page_num}: {found} 筆')
                if stop or found == 0:
                    break
                time.sleep(1.5)
        finally:
            browser.close()

    print(f'  Oricon 合計: {len(articles)} 筆')
    return articles


# ─── BARKS 検索分頁（改良版）───────────────────────────────────────────────
def _scrape_barks_search(since: str, max_pages: int = 8):
    print('\n[BARKS] 検索ページスクレイプ中...')
    articles, seen = [], set()

    for page_num in range(1, max_pages + 1):
        url = f'https://www.barks.jp/search/?q=m%21lk&type=news&page={page_num}'
        try:
            r = requests.get(url, headers=HEADERS, timeout=12)
            if r.status_code != 200:
                print(f'  p{page_num}: HTTP {r.status_code}')
                break
            r.encoding = 'utf-8'
            soup = BeautifulSoup(r.text, 'html.parser')
        except Exception as e:
            print(f'  p{page_num}: {e}')
            break

        # 相対 URL 対応：/news/... も barks.jp/news/... も両方マッチ
        links = soup.find_all('a', href=re.compile(r'(?:barks\.jp)?/news/'))
        if not links:
            print(f'  p{page_num}: no links')
            break

        found, stop = 0, False
        for a in links:
            href = a['href']
            if not href.startswith('http'):
                href = 'https://www.barks.jp' + href
            href = href.split('?')[0]  # query string 除去
            if href in seen:
                continue
            title = a.get_text(strip=True) or ''
            if len(title) < 4:
                par = a.find_parent(['li', 'article', 'div'])
                title = par.get_text(' ', strip=True)[:120] if par else ''
            # 日付チェック（ISO 形式 YYYY-MM-DD のみ信頼）
            par = a.find_parent(['li', 'article'])
            date_str = ''
            if par:
                t = par.find('time') or par.find(class_=re.compile('date', re.I))
                if t:
                    raw = t.get('datetime', '') or t.get_text(strip=True)
                    if re.match(r'\d{4}-\d{2}-\d{2}', raw):
                        date_str = raw[:10]
            if date_str and date_str < since:
                stop = True
                break
            if not _milk_check(title):
                continue
            seen.add(href)
            articles.append(_make_article('BARKS', title, href, date_str, ''))
            found += 1

        print(f'  p{page_num}: {found} 筆')
        if stop:
            break
        time.sleep(SLEEP_SEC)

    print(f'  BARKS 合計: {len(articles)} 筆')
    return articles


# ─── SANSPO 検索分頁 ─────────────────────────────────────────────────────────
def _scrape_sanspo_search(since: str, max_pages: int = 6):
    print('\n[SANSPO] 検索ページスクレイプ中...')
    articles, seen = [], set()

    for page_num in range(1, max_pages + 1):
        url = f'https://www.sanspo.com/search/?q=m%21lk&page={page_num}'
        try:
            r = requests.get(url, headers=HEADERS, timeout=12)
            if r.status_code != 200:
                print(f'  p{page_num}: HTTP {r.status_code}')
                break
            r.encoding = 'utf-8'
            soup = BeautifulSoup(r.text, 'html.parser')
        except Exception as e:
            print(f'  p{page_num}: {e}')
            break

        # /article/ を含む URL を対象（相対・絶対両対応）
        links = soup.find_all('a', href=re.compile(r'/article/'))
        if not links:
            print(f'  p{page_num}: no links')
            break

        found, stop = 0, False
        for a in links:
            href = a['href']
            if not href.startswith('http'):
                href = 'https://www.sanspo.com' + href
            href = href.split('?')[0]
            if href in seen:
                continue
            title = a.get_text(strip=True)
            if not title or len(title) < 4:
                continue
            par = a.find_parent(['li', 'article', 'div'])
            date_str = ''
            if par:
                t = par.find('time') or par.find(class_=re.compile('date', re.I))
                if t:
                    raw = t.get('datetime', '') or t.get_text(strip=True)
                    if re.match(r'\d{4}-\d{2}-\d{2}', raw):
                        date_str = raw[:10]
            if date_str and date_str < since:
                stop = True
                break
            if not _milk_check(title):
                continue
            seen.add(href)
            articles.append(_make_article('SANSPO', title, href, date_str, ''))
            found += 1

        print(f'  p{page_num}: {found} 筆')
        if stop:
            break
        time.sleep(SLEEP_SEC)

    print(f'  SANSPO 合計: {len(articles)} 筆')
    return articles


# ─── Modelpress 検索分頁 ──────────────────────────────────────────────────────
def _scrape_modelpress_search(since: str, max_pages: int = 6):
    print('\n[Modelpress] 検索ページスクレイプ中...')
    articles, seen = [], set()

    for page_num in range(1, max_pages + 1):
        url = f'https://mdpr.jp/search?q=m%21lk&p={page_num}'
        try:
            r = requests.get(url, headers=HEADERS, timeout=12)
            if r.status_code != 200:
                print(f'  p{page_num}: HTTP {r.status_code}')
                break
            r.encoding = 'utf-8'
            soup = BeautifulSoup(r.text, 'html.parser')
        except Exception as e:
            print(f'  p{page_num}: {e}')
            break

        links = soup.find_all('a', href=re.compile(r'(?:mdpr\.jp)?/(news|article|music)/'))
        if not links:
            print(f'  p{page_num}: no links')
            break

        found, stop = 0, False
        for a in links:
            href = a['href']
            if not href.startswith('http'):
                href = 'https://mdpr.jp' + href
            href = href.split('?')[0]
            if href in seen:
                continue
            title = a.get_text(strip=True)
            if not title or len(title) < 4:
                continue
            par = a.find_parent(['li', 'article', 'div'])
            date_str = ''
            if par:
                t = par.find('time') or par.find(class_=re.compile('date', re.I))
                if t:
                    raw = t.get('datetime', '') or t.get_text(strip=True)
                    if re.match(r'\d{4}-\d{2}-\d{2}', raw):
                        date_str = raw[:10]
            if date_str and date_str < since:
                stop = True
                break
            if not _milk_check(title):
                continue
            seen.add(href)
            articles.append(_make_article('Modelpress', title, href, date_str, ''))
            found += 1

        print(f'  p{page_num}: {found} 筆')
        if stop:
            break
        time.sleep(SLEEP_SEC)

    print(f'  Modelpress 合計: {len(articles)} 筆')
    return articles


# ── 主流程 ────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description='M!LK Fan Hub 歷史文章回補')
    parser.add_argument('--since',        default=MIN_SINCE, help='最早回補日期 (YYYY-MM-DD)')
    parser.add_argument('--dry-run',      action='store_true', help='只顯示找到的文章，不寫入')
    parser.add_argument('--no-translate', action='store_true', help='跳過翻譯（測試用）')
    parser.add_argument('--repair-dates',        action='store_true', help='修復現有文章中日期錯誤的條目')
    parser.add_argument('--repair-translations', action='store_true', help='補翻譯：掃描 title_zh / summary_zh 空白的文章並重新翻譯')
    parser.add_argument('--backfill-charts',     action='store_true', help='回補 Oricon 週別+日別排行榜（配合 --since 指定起始日期）')
    parser.add_argument('--weekly-only',         action='store_true', help='--backfill-charts 時只補週別（跳過日別，速度更快）')
    parser.add_argument('--repair-body',         action='store_true', help='重抓最近 90 天 body_ja 空白或內容不完整的文章內文')
    args = parser.parse_args()

    since = args.since
    print(f'=== M!LK Backfill ===')

    # ── 排行榜回補模式 ────────────────────────────────────────────────────────────
    if args.backfill_charts:
        print(f'排行榜回補模式：since={since}, weekly_only={args.weekly_only}')
        _backfill_charts(
            since=since,
            weekly=True,
            daily=not args.weekly_only,
        )
        return

    # ── 補翻譯模式：title_zh / summary_zh が空の記事を再翻訳 ─────────────────────
    if args.repair_translations:
        deepl_key = os.environ.get('DEEPL_API_KEY', '')
        if not deepl_key:
            print('ERROR: DEEPL_API_KEY が設定されていません。')
            return
        print('補翻譯模式：掃描 title_zh / summary_zh / body 空白的文章...\n')
        existing = load_json(ARTICLES_FILE)

        # Google News URL → Playwright で JS 転送先の実 URL を解決
        def _resolve_gnews(url):
            if 'news.google.com' not in url:
                return url
            try:
                from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
                with sync_playwright() as pw:
                    browser = pw.chromium.launch(headless=True)
                    try:
                        page = browser.new_page()
                        page.goto(url, timeout=20000, wait_until='domcontentloaded')
                        # JS 転送後の最終 URL を待つ
                        for _ in range(20):
                            cur = page.url
                            if 'news.google.com' not in cur:
                                return cur
                            page.wait_for_timeout(500)
                        return page.url
                    finally:
                        browser.close()
            except Exception as e:
                print(f'    resolve error: {e}')
                return url

        # ① Google News 記事：実 URL を取得して内文・画像を補完
        gnews_arts = [a for a in existing
                      if 'news.google.com' in a.get('url', '')
                      and not a.get('body_ja')]
        if gnews_arts:
            print(f'Google News 記事 {len(gnews_arts)} 筆の実 URL を解決中...')
        for i, a in enumerate(gnews_arts):
            real_url = _resolve_gnews(a['url'])
            if real_url == a['url']:
                print(f'  [{i+1}/{len(gnews_arts)}] 転送先取得失敗: {a["title_ja"][:40]}')
                continue
            print(f'  [{i+1}/{len(gnews_arts)}] {real_url[:60]}')
            try:
                img, imgs, body, pub_date, _ = fetch_article_data(real_url)
                if img:
                    a['image'] = img
                if imgs:
                    a['images'] = imgs
                if body:
                    a['body_ja'] = body
                    a['summary_ja'] = a.get('summary_ja') or body[:300]
                if pub_date and not a.get('date'):
                    a['date'] = pub_date
                # 実 URL に更新（以降は直接抓取できるようになる）
                a['url'] = real_url
            except Exception as e:
                print(f'    fetch error: {e}')
            time.sleep(0.5)

        # ② title_zh / summary_zh / body_zh が空の記事を翻訳
        to_fix = [
            a for a in existing
            if a.get('title_ja') and (
                not a.get('title_zh') or
                (a.get('summary_ja') and not a.get('summary_zh')) or
                (a.get('body_ja') and not a.get('body_zh'))
            )
        ]
        print(f'\n翻訳が必要な記事: {len(to_fix)} 筆')
        changed = 0
        for i, a in enumerate(to_fix):
            try:
                if not a.get('title_zh') and a.get('title_ja'):
                    a['title_zh'] = translate(a['title_ja'])
                if a.get('summary_ja') and not a.get('summary_zh'):
                    a['summary_zh'] = translate(a['summary_ja'])
                if a.get('body_ja') and not a.get('body_zh'):
                    a['body_zh'] = translate(a['body_ja'])
                changed += 1
                print(f'  [{i+1}/{len(to_fix)}] ✓ {a.get("title_ja","")[:50]}')
            except Exception as e:
                print(f'  [{i+1}/{len(to_fix)}] 錯誤: {e}')
            time.sleep(0.3)
        save_json(ARTICLES_FILE, existing)
        print(f'\n已處理 {changed} 筆，寫入 {ARTICLES_FILE}。')
        print('請重新執行 fetch_news.py 以重新產生 HTML。')
        return

    # ── 修復模式：重抓 body_ja 空白或品質不佳的文章 ─────────────────────────────
    if args.repair_body:
        print('修復 body 模式：掃描最近 90 天 body_ja 空白或不完整的文章...\n')
        existing = load_json(ARTICLES_FILE)
        cutoff = (datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d')

        def _body_needs_repair_bf(art):
            bj = art.get('body_ja') or ''
            if not bj:
                return True
            if len(bj) < 120:
                return True
            if len(re.findall(r'https?://', bj)) >= 3:
                return True
            return False

        candidates = [
            a for a in existing
            if a.get('url')
            and (a.get('date', '') or '')[:10] >= cutoff
            and _body_needs_repair_bf(a)
        ]
        print(f'需要修復 body 的文章: {len(candidates)} 筆\n')
        repaired = 0
        for i, a in enumerate(candidates):
            try:
                img, imgs, body, pub_date, _ = fetch_article_data(a['url'])
                if body:
                    a['body_ja'] = body
                    a['body_zh'] = ''   # clear stale translation
                    repaired += 1
                    print(f'  [{i+1}/{len(candidates)}] ✓ {a.get("title_ja","")[:50]}')
                else:
                    print(f'  [{i+1}/{len(candidates)}] 未取到 body: {a.get("title_ja","")[:50]}')
                if img and not a.get('image'):
                    a['image'] = img
            except Exception as e:
                print(f'  [{i+1}/{len(candidates)}] 錯誤: {e}')
            time.sleep(0.4)
        save_json(ARTICLES_FILE, existing)
        print(f'\n已修復 body_ja: {repaired} 筆。請執行 backfill.py --repair-translations 補翻譯，再重跑 fetch_news.py。')
        return

    # ── 修復模式：重新抓日期錯誤的現有文章 ──────────────────────────────────────
    if args.repair_dates:
        print('修復模式：重新抓取日期為空或有問題的文章日期...\n')
        existing = load_json(ARTICLES_FILE)
        # Musicvoice / BARKS / Natalie 在 backfill 時沒有真實日期，全部重新抓
        NEEDS_REPAIR = {'Musicvoice', 'BARKS', 'Natalie Music'}
        to_repair = [
            a for a in existing
            if a.get('url') and (
                not a.get('date')
                or a.get('source') in NEEDS_REPAIR
            )
        ]
        print(f'找到 {len(to_repair)} 筆需要修復')
        changed = 0
        for i, a in enumerate(to_repair):
            try:
                _, _, _, pub_date, _ = fetch_article_data(a['url'])
                if pub_date:
                    a['date'] = pub_date
                    changed += 1
                    print(f'  [{i+1}/{len(to_repair)}] ✓ {pub_date}  {a["title_ja"][:45]}')
                else:
                    print(f'  [{i+1}/{len(to_repair)}] – 無法取得日期  {a["title_ja"][:45]}')
            except Exception as e:
                print(f'  [{i+1}/{len(to_repair)}] 錯誤: {e}')
            time.sleep(0.5)
        save_json(ARTICLES_FILE, existing)
        print(f'\n已更新 {changed} 筆日期，寫入 {ARTICLES_FILE}。')
        print('請重新執行 fetch_news.py 以重新產生 HTML。')
        return

    print(f'回補範圍：{since} 以後\n')

    # 抓取各來源
    raw = []
    raw += _scrape_firsttimes(since, MAX_PAGES)
    raw += _scrape_musicvoice(since, max_pages=6)
    raw += _scrape_barks(since, max_pages=5)        # 既存（一般 RSS fallback）
    raw += _scrape_barks_search(since, max_pages=8) # 検索ページ分頁（新）
    raw += _scrape_natalie(since)
    raw += _scrape_oricon_pw(since, max_pages=10)   # Playwright CF bypass（新）
    raw += _scrape_sanspo_search(since, max_pages=6)
    raw += _scrape_modelpress_search(since, max_pages=6)

    print(f'\n合計找到 {len(raw)} 筆（去重前）')
    if not raw:
        print('沒有新文章，結束。')
        return

    if args.dry_run:
        for a in sorted(raw, key=lambda x: x.get('date', ''), reverse=True):
            print(f"  {a.get('date','?'):10s}  [{a['source']}]  {a['title_ja'][:60]}")
        print('\n--dry-run 模式，不寫入。')
        return

    # 載入現有資料，找出真正新增的文章
    existing = load_json(ARTICLES_FILE)
    seen_urls = {a['url'] for a in existing if a.get('url')}
    new_articles = [a for a in raw if a.get('url') and a['url'] not in seen_urls]
    dup_count = len(raw) - len(new_articles)
    print(f'新增 {len(new_articles)} 筆（重複 {dup_count} 筆跳過）')

    if not new_articles:
        print('沒有新增文章，結束。')
        return

    # 抓每篇文章的圖片 + 內文
    total = len(new_articles)
    print(f'\n抓取文章內容（圖片 + 全文）共 {total} 篇...')
    for i, a in enumerate(new_articles):
        if a.get('url'):
            try:
                img, imgs, body, pub_date, _ = fetch_article_data(a['url'])
                a['image']  = img
                a['images'] = imgs
                if pub_date:
                    a['date'] = pub_date
                if body:
                    a['body_ja'] = body
                if body and not a.get('summary_ja'):
                    a['summary_ja'] = body[:300]
            except Exception as e:
                print(f'  [{i+1}/{total}] 抓取失敗: {e}')
            print(f'  [{i+1}/{total}] {a.get("date","?"):10s} {a["title_ja"][:45]}')
            time.sleep(0.4)

    # 翻譯
    if not args.no_translate:
        deepl_key = os.environ.get('DEEPL_API_KEY', '')
        if deepl_key:
            print(f'\n翻譯 {total} 篇...')
            for i, a in enumerate(new_articles):
                a['title_zh']   = translate(a.get('title_ja', ''))
                a['summary_zh'] = translate(a['summary_ja']) if a.get('summary_ja') else ''
                if a.get('body_ja'):
                    a['body_zh'] = translate(a['body_ja'])
                print(f'  [{i+1}/{total}] 完成')
        else:
            print('\nWARNING: 未設定 DEEPL_API_KEY，跳過翻譯')
    else:
        print('\n--no-translate 模式，跳過翻譯')

    # 合併並寫回
    merged, _ = merge_by_url(existing, new_articles)
    save_json(ARTICLES_FILE, merged)
    print(f'\n已寫入 {ARTICLES_FILE}，共 {len(merged)} 篇。')
    print('完成！請重新執行 fetch_news.py 以重新產生 HTML。')


def _backfill_charts(since: str, weekly: bool = True, daily: bool = True):
    """Back-fill Oricon weekly + daily charts from `since` date to today."""
    from datetime import date as _date, timedelta as _td
    import time as _time

    since_dt = _date.fromisoformat(since)
    today_dt = _date.today()

    WEEKLY_TARGETS = [
        ('cos', '合算シングル'),
        ('coa', '合算アルバム'),
        ('js',  'シングル'),
        ('ja',  'アルバム'),
    ]
    DAILY_TARGETS = [
        ('cos', '合算シングル'),
        ('coa', '合算アルバム'),
        ('js',  'シングル'),
        ('ja',  'アルバム'),
    ]

    existing_charts = load_json(CHARTS_FILE)
    ex_ids = {c['id'] for c in existing_charts}
    new_entries = []

    if weekly:
        print('\n=== 週別チャート 回補 ===')
        ex_scanned = load_json(CHART_SCANNED_FILE)
        scanned_weekly = {(e['chart_code'], e['date']) for e in ex_scanned
                          if e.get('chart_code') and e.get('date')}
        # Also treat existing weekly entries as scanned
        for c in existing_charts:
            if c.get('period_type', 'weekly') == 'weekly' and c.get('chart_code') and c.get('date'):
                scanned_weekly.add((c['chart_code'], c['date']))

        # Generate all Sundays from since_dt to today
        # Oricon weekly chart date = Sunday of that week
        cur = since_dt
        while cur.weekday() != 6:  # advance to first Sunday
            cur += _td(days=1)
        sundays = []
        while cur <= today_dt:
            sundays.append(cur.strftime('%Y-%m-%d'))
            cur += _td(weeks=1)

        newly_scanned_weekly = []
        for chart_code, chart_name in WEEKLY_TARGETS:
            found_code = 0
            for date_str in sundays:
                if (chart_code, date_str) in scanned_weekly:
                    continue
                entries = _parse_oricon_chart_page(chart_code, chart_name, date_str)
                for e in entries:
                    if e['id'] not in ex_ids:
                        ex_ids.add(e['id'])
                        new_entries.append(e)
                        found_code += 1
                newly_scanned_weekly.append({'chart_code': chart_code, 'date': date_str})
                _time.sleep(0.4)
            print(f'  [週別 {chart_name}] {found_code} 件追加')

        if newly_scanned_weekly:
            combined_scanned = ex_scanned + [s for s in newly_scanned_weekly
                                              if (s['chart_code'], s['date']) not in scanned_weekly]
            save_json(CHART_SCANNED_FILE, combined_scanned)

    if daily:
        print('\n=== 日別チャート 回補 ===')
        ex_daily_scanned = load_json(CHART_DAILY_SCANNED_FILE)
        scanned_daily = {(e['chart_code'], e['date']) for e in ex_daily_scanned
                         if e.get('chart_code') and e.get('date')}
        for c in existing_charts:
            if c.get('period_type') == 'daily' and c.get('chart_code') and c.get('date'):
                scanned_daily.add((c['chart_code'], c['date']))

        # Generate all dates from since_dt to today
        dates = []
        cur = since_dt
        while cur <= today_dt:
            dates.append(cur.strftime('%Y-%m-%d'))
            cur += _td(days=1)

        newly_scanned_daily = []
        for chart_code, chart_name in DAILY_TARGETS:
            found_code = 0
            for date_str in dates:
                if (chart_code, date_str) in scanned_daily:
                    continue
                entries = _parse_oricon_daily_chart_page(chart_code, chart_name, date_str)
                for e in entries:
                    if e['id'] not in ex_ids:
                        ex_ids.add(e['id'])
                        new_entries.append(e)
                        found_code += 1
                newly_scanned_daily.append({'chart_code': chart_code, 'date': date_str})
                _time.sleep(0.3)
            print(f'  [日別 {chart_name}] {found_code} 件追加')

        if newly_scanned_daily:
            combined_daily = ex_daily_scanned + [s for s in newly_scanned_daily
                                                  if (s['chart_code'], s['date']) not in scanned_daily]
            save_json(CHART_DAILY_SCANNED_FILE, combined_daily)

    if new_entries:
        merged_charts = new_entries + existing_charts
        save_json(CHARTS_FILE, merged_charts)
        print(f'\n週別+日別チャート 回補完了: {len(new_entries)} 件追加 (合計 {len(merged_charts)} 件)')
    else:
        print('\n追加するチャートエントリはありませんでした。')


if __name__ == '__main__':
    main()

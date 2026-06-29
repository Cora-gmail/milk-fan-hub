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
    # 搜尋 M!LK 在 Natalie 的藝人 ID（嘗試已知候選）
    candidate_ids = [4441, 12345, 23456, 34567, 45678]
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


# ── 主流程 ────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description='M!LK Fan Hub 歷史文章回補')
    parser.add_argument('--since',        default=MIN_SINCE, help='最早回補日期 (YYYY-MM-DD)')
    parser.add_argument('--dry-run',      action='store_true', help='只顯示找到的文章，不寫入')
    parser.add_argument('--no-translate', action='store_true', help='跳過翻譯（測試用）')
    parser.add_argument('--repair-dates', action='store_true', help='修復現有文章中日期錯誤的條目')
    args = parser.parse_args()

    since = args.since
    print(f'=== M!LK Backfill ===')

    # ── 修復模式：重新抓日期錯誤的現有文章 ──────────────────────────────────────
    if args.repair_dates:
        print('修復模式：重新抓取日期為空或有問題的文章日期...\n')
        existing = load_json(ARTICLES_FILE)
        today_str = time.strftime('%Y-%m-%d')
        to_repair = [
            a for a in existing
            if a.get('url') and (not a.get('date') or a.get('date') == today_str)
        ]
        print(f'找到 {len(to_repair)} 筆需要修復')
        changed = 0
        for i, a in enumerate(to_repair):
            try:
                _, _, _, pub_date = fetch_article_data(a['url'])
                if pub_date and pub_date != today_str:
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
    raw += _scrape_barks(since, max_pages=5)
    raw += _scrape_natalie(since)

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
                img, imgs, body, pub_date = fetch_article_data(a['url'])
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


if __name__ == '__main__':
    main()

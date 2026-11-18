import json
import os
import re
import time
import urllib3
from datetime import datetime
import feedparser
import requests
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

RSS_FEEDS = [
    {"url": "https://feeds.bbci.co.uk/news/health/rss.xml", "default_cat": "medical"},
    {"url": "https://www.medicalnewstoday.com/feed", "default_cat": "medical"},
    {"url": "https://www.webmd.com/rss/rss.aspx?rss=news", "default_cat": "nutrition"},
    {"url": "https://www.sciencedaily.com/rss/health_medicine.xml", "default_cat": "fitness"},
    {"url": "https://health.clevelandclinic.org/feed/", "default_cat": "mental"}
]

DATA_FILE = "news.json"
MAX_NEWS_TOTAL = 80
MAX_NEW_PER_SOURCE = 6

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
}

DEFAULT_IMAGE = "https://images.unsplash.com/photo-1505751172876-fa1923c5c528?auto=format&fit=crop&w=800&q=80"

def is_persian(text):
    return bool(re.search(r'[\u0600-\u06FF]', text or ""))

def translate_text(text, sl="en", tl="fa"):
    if not text or not text.strip():
        return ""
    text = text.strip()[:2500]

    url1 = "https://translate.googleapis.com/translate_a/single"
    params1 = {"client": "gtx", "sl": sl, "tl": tl, "dt": "t", "q": text}
    try:
        res = requests.get(url1, params=params1, headers=HEADERS, timeout=12)
        if res.status_code == 200:
            data = res.json()
            return "".join([part[0] for part in data[0] if part[0]]).strip()
    except Exception:
        pass

    url2 = "https://clients5.google.com/translate_a/t"
    params2 = {"client": "dict-chrome-ex", "sl": sl, "tl": tl, "q": text}
    try:
        res2 = requests.get(url2, params=params2, headers=HEADERS, timeout=12)
        if res2.status_code == 200:
            data2 = res2.json()
            if isinstance(data2, list) and len(data2) > 0:
                if isinstance(data2[0], list):
                    return "".join([part[0] for part in data2 if part and part[0]]).strip()
                return str(data2[0]).strip()
    except Exception as e:
        print(f"Backup translate error: {e}")
    return ""

def clean_html(raw_html):
    if not raw_html:
        return ""
    soup = BeautifulSoup(raw_html, "html.parser")
    return re.sub(r'\s+', ' ', soup.get_text(separator=" ", strip=True))

def detect_category(title_fa, summary_fa, default_cat):
    text = f"{title_fa} {summary_fa}"
    if any(k in text for k in ["رژیم", "تغذیه", "غذا", "ویتامین", "قند", "کلسترول", "پروتئین", "رژیمی"]):
        return "nutrition"
    if any(k in text for k in ["روان", "استرس", "اضطراب", "افسردگی", "خواب", "مدیتیشن", "ذهن‌آگاهی"]):
        return "mental"
    if any(k in text for k in ["ورزش", "بدنسازی", "تحرک", "عضله", "تمرین", "دویدن", "یوگا"]):
        return "fitness"
    if any(k in text for k in ["پوست", "مو", "زیبایی", "جوان‌سازی", "چین و چروک", "کلاژن"]):
        return "beauty"
    if any(k in text for k in ["بیماری", "درمان", "دارو", "پزشک", "جراحی", "قلب", "سرطان", "فشار خون"]):
        return "medical"
    return default_cat

def scrape_full_article(url, fallback_summary, fallback_img):
    paragraphs = []
    hd_image = fallback_img
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10, verify=False)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            og_img = soup.find("meta", property="og:image")
            if og_img and og_img.get("content"):
                hd_image = og_img["content"]

            for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
                tag.decompose()

            article_body = soup.find("article") or soup.find("main") or soup
            for p in article_body.find_all("p"):
                txt = re.sub(r'\s+', ' ', p.get_text(strip=True))
                if len(txt) > 55 and not any(skip in txt.lower() for skip in ["cookie", "subscribe", "newsletter", "all rights reserved", "terms of use"]):
                    if txt not in paragraphs:
                        paragraphs.append(txt)
                if len(paragraphs) >= 8:
                    break
    except Exception as e:
        print(f"Scrape error: {e}")

    if not paragraphs and fallback_summary:
        paragraphs = [fallback_summary]
    return paragraphs, hd_image

def extract_rss_image(entry):
    if 'media_thumbnail' in entry and len(entry.media_thumbnail) > 0:
        return entry.media_thumbnail[0]['url']
    if 'media_content' in entry and len(entry.media_content) > 0:
        return entry.media_content[0]['url']
    return DEFAULT_IMAGE

def main():
    existing_news = []
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                existing_news = [item for item in loaded if is_persian(item.get("title", "")) and item.get("content")]
        except Exception:
            existing_news = []

    existing_links = {item["link"] for item in existing_news}
    max_news_id = max([item.get("news_id", 1000) for item in existing_news], default=1000)
    new_items = []

    for feed_cfg in RSS_FEEDS:
        feed_url = feed_cfg["url"]
        default_cat = feed_cfg["default_cat"]
        try:
            resp = requests.get(feed_url, headers=HEADERS, timeout=12, verify=False)
            feed = feedparser.parse(resp.content)
            count = 0
            for entry in feed.entries:
                if count >= MAX_NEW_PER_SOURCE:
                    break
                link = entry.get("link", "")
                if not link or link in existing_links:
                    continue

                orig_title = clean_html(entry.get("title", ""))
                orig_summary = clean_html(entry.get("summary", ""))
                if not orig_title:
                    continue

                rss_img = extract_rss_image(entry)
                raw_paragraphs, best_image = scrape_full_article(link, orig_summary, rss_img)

                en_title = orig_title
                en_paragraphs = [p.strip() for p in raw_paragraphs if len(p.strip()) > 15]
                if not en_paragraphs and orig_summary:
                    en_paragraphs = [orig_summary]
                if not en_paragraphs:
                    continue

                fa_title = translate_text(orig_title, sl="en", tl="fa")
                if not is_persian(fa_title):
                    continue

                full_en_text = "\n\n".join(en_paragraphs)
                fa_full_text = translate_text(full_en_text, sl="en", tl="fa")
                fa_paragraphs = [p.strip() for p in fa_full_text.split("\n") if len(p.strip()) > 20]
                if not fa_paragraphs:
                    fb = translate_text(orig_summary, sl="en", tl="fa")
                    if is_persian(fb):
                        fa_paragraphs = [fb]

                if not fa_paragraphs:
                    continue

                fa_summary = fa_paragraphs[0][:200] + "..." if len(fa_paragraphs[0]) > 200 else fa_paragraphs[0]
                en_summary = en_paragraphs[0][:200] + "..." if len(en_paragraphs[0]) > 200 else en_paragraphs[0]
                category = detect_category(fa_title, fa_summary, default_cat)

                new_items.append({
                    "title": fa_title,
                    "summary": fa_summary,
                    "content": fa_paragraphs,
                    "title_en": en_title,
                    "summary_en": en_summary,
                    "content_en": en_paragraphs,
                    "category": category,
                    "link": link,
                    "image": best_image,
                    "timestamp": datetime.utcnow().isoformat() + "Z"
                })
                existing_links.add(link)
                count += 1
                time.sleep(0.3)
        except Exception as e:
            print(f"Error: {e}")

    new_items.reverse()
    for item in new_items:
        max_news_id += 1
        item["news_id"] = max_news_id
    new_items.reverse()

    for idx, item in enumerate(reversed(existing_news)):
        if "news_id" not in item:
            item["news_id"] = 1001 + idx

    updated_news = (new_items + existing_news)[:MAX_NEWS_TOTAL]

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(updated_news, f, ensure_ascii=False, indent=2)

    print(f"Saved {len(updated_news)} Health articles.")

if __name__ == "__main__":
    main()

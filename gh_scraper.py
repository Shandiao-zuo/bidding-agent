#!/usr/bin/env python3
"""
GitHub Actions 招标信息爬虫 v2
使用 Playwright 模拟真实浏览器，更智能地提取搜索结果
"""

import json, os, re, sys, time
from datetime import datetime
from urllib.parse import quote

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
except ImportError:
    print("[ERROR] pip install playwright && playwright install chromium")
    sys.exit(1)

KW = os.environ.get("KEYWORDS", "咨询评估机构").strip()
# Support multiple keywords separated by comma
KEYWORDS = [k.strip() for k in KW.split(",") if k.strip()]
print(f"Keywords: {KEYWORDS}")

PLATFORMS = [
    {
        "id": "ccgp",
        "name": "中国政府采购网",
        "region": "全国",
        "search_url": "https://search.ccgp.gov.cn/bxsearch?searchtype=1&bidSort=0&pinMu=0&bidType=0&dbselect=bidx&kw={kw}&timeType=6&page=1",
        "base_url": "https://search.ccgp.gov.cn",
    },
    {
        "id": "bidcenter",
        "name": "招标投标公共服务平台",
        "region": "全国",
        "search_url": "https://bulletin.cebpubservice.com/search?kw={kw}",
        "base_url": "https://bulletin.cebpubservice.com",
    },
    {
        "id": "chinabidding",
        "name": "中国招标投标网",
        "region": "全国",
        "search_url": "https://www.cebpubservice.com/search?kw={kw}",
        "base_url": "https://www.cebpubservice.com",
    },
    {
        "id": "soubiao",
        "name": "搜标网",
        "region": "全国",
        "search_url": "https://www.soubiao.net/search/?keyword={kw}",
        "base_url": "https://www.soubiao.net",
    },
]

# Pattern to identify likely bidding/announcement titles
# Expanded to include consulting/assessment keywords
ANNOUNCE_PATTERN = re.compile(
    r"(招标|采购|询价|竞价|中标|公告|意向|需求|磋商|"
    r"选聘|委托|遴选|比选|竞争|成交|结果|"
    r"项目|服务|工程|货物|预算|评审|监督)"
)


def scrape_platform(page, platform, keyword):
    """Scrape a single platform for a given keyword."""
    name = platform["name"]
    url = platform["search_url"].format(kw=quote(keyword))

    print(f"\n[{name}] {url[:80]}...")
    results = []

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
    except Exception as e:
        print(f"  x Load failed: {str(e)[:60]}")
        return results

    # Scroll to trigger lazy loading
    for _ in range(2):
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(1)

    # Extract ALL links from the page
    all_anchors = page.query_selector_all("a[href]")
    print(f"  Found {len(all_anchors)} links total")

    for a in all_anchors:
        try:
            title = a.inner_text().strip()
            href = (a.get_attribute("href") or "").strip()

            # Skip navigation/footer links
            if not title or len(title) < 6:
                continue
            if not href or href.startswith("javascript:") or href == "#":
                continue
            # Skip obviously non-bidding links
            skip_words = ["首页", "上一页", "下一页", "末页", "登录", "注册",
                          "关于我们", "联系我们", "网站地图", "首页", "返回"]
            if any(title.startswith(w) or title == w for w in skip_words):
                continue

            # Must contain announce keywords OR the search keyword itself
            kw_match = keyword in title
            announce_match = ANNOUNCE_PATTERN.search(title)
            if not (kw_match or announce_match):
                continue

            # Complete URL
            if not href.startswith("http"):
                base = platform["base_url"].rstrip("/")
                href = base + "/" + href.lstrip("/")

            # Try to extract date from the anchor's parent element
            try:
                parent = a.evaluate("el => el.closest('li,tr,div,p')?.innerText || ''")
                date_m = re.search(r"(\d{4}[-/]\d{1,2}[-/]\d{1,2})", parent)
                pub_date = date_m.group(1) if date_m else datetime.now().strftime("%Y-%m-%d")
            except:
                pub_date = datetime.now().strftime("%Y-%m-%d")

            results.append({
                "title": title,
                "url": href,
                "date": pub_date,
            })
        except:
            continue

    # Deduplicate by title
    seen = set()
    unique = []
    for r in results:
        key = r["title"]
        if key not in seen:
            seen.add(key)
            unique.append(r)

    # Sort by date descending
    unique.sort(key=lambda x: x.get("date", ""), reverse=True)

    print(f"  => {len(unique)} valid results")
    if unique and len(unique) <= 3:
        for r in unique:
            print(f"     {r['title'][:50]} | {r['date']}")

    return unique


def push_wechat(records):
    """Push results to WeChat via Server酱."""
    sckey = os.environ.get("SERVER_CHAN_KEY", "")
    if not sckey:
        print("[WARN] No SERVER_CHAN_KEY configured")
        return False

    import requests as req
    from collections import defaultdict

    today = datetime.now().strftime("%Y-%m-%d %H:%M")

    # Group by source
    by_src = defaultdict(list)
    for r in records:
        by_src[r["src_name"]].append(r)

    lines = [
        f"[招标信息日报]",
        f"时间: {today}",
        f"关键词: {', '.join(KEYWORDS)}",
        f"结果: {len(records)} 条",
        f"",
        f"{'='*30}",
    ]

    for src_name, items in by_src.items():
        lines.append(f"\n【{src_name}】{len(items)}条:")
        for i, item in enumerate(items[:5]):
            t = item["title"][:50]
            lines.append(f"  {i+1}. {t}")
            lines.append(f"     {item['url']}")
        if len(items) > 5:
            lines.append(f"  ...还有{len(items)-5}条")

    body = "\n".join(lines)
    title = f"招标日报 {datetime.now().strftime('%m/%d')} | {len(records)}条"

    try:
        resp = req.post(
            f"https://sctapi.ftqq.com/{sckey}.send",
            data={"title": title, "desp": body},
            timeout=15,
        )
        result = resp.json()
        ok = result.get("code") in [0, 200]
        print(f"[WeChat] {'OK' if ok else 'FAIL'}: {result.get('message','')}")
        return ok
    except Exception as e:
        print(f"[WeChat] Error: {e}")
        return False


def main():
    print("=" * 50)
    print("  Bidding Agent v2 (GitHub Actions)")
    print("=" * 50)

    all_records = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
            locale="zh-CN",
            ignore_https_errors=True,  # Ignore SSL cert errors
        )
        page = ctx.new_page()

        try:
            for kw in KEYWORDS:
                print(f"\n>>> Searching: {kw}")
                for plat in PLATFORMS:
                    results = scrape_platform(page, plat, kw)
                    for r in results:
                        r["keyword"] = kw
                        r["src_name"] = plat["name"]
                        r["region"] = plat["region"]
                    all_records.extend(results)
                    time.sleep(1.5)
        finally:
            ctx.close()
            browser.close()

    # Summary
    print(f"\n{'='*50}")
    print(f"TOTAL: {len(all_records)} results from {len(PLATFORMS)} platforms")
    if all_records:
        # Per-platform summary
        from collections import Counter
        counts = Counter(r["src_name"] for r in all_records)
        for name, cnt in counts.most_common():
            print(f"  {name}: {cnt}")

    # Save
    data = {
        "records": all_records,
        "last_push": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "keywords": KEYWORDS,
        "total": len(all_records),
    }
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    # Push
    push_wechat(all_records)

    print("\nDone!")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

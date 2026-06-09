#!/usr/bin/env python3
"""
GitHub Actions 招标信息爬虫
使用 Playwright 模拟真实浏览器访问各平台搜索
"""

import json
import os
import re
import sys
import time
from datetime import datetime
from urllib.parse import quote

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
except ImportError:
    print("[ERROR] 请先安装 playwright: pip install playwright && playwright install chromium")
    sys.exit(1)

# ── 关键词：从环境变量读取 ──
KEYWORDS = os.environ.get("KEYWORDS", "咨询评估机构").split(",")
KEYWORDS = [k.strip() for k in KEYWORDS if k.strip()]

# ── 平台搜索配置 ──
PLATFORMS = [
    {
        "id": "ccgp",
        "name": "中国政府采购网",
        "region": "全国",
        "search_url": "https://search.ccgp.gov.cn/bxsearch?searchtype=1&bidSort=0&pinMu=0&bidType=0&dbselect=bidx&kw={kw}&timeType=6&page=1",
        "wait_selector": "ul.vT-srch-result-list-bid",
        "result_selector": "ul.vT-srch-result-list-bid li",
        "title_selector": "a",
        "link_selector": "a",
        "base_url": "https://search.ccgp.gov.cn",
    },
    {
        "id": "bidcenter",
        "name": "招标网",
        "region": "全国",
        "search_url": "https://www.bidcenter.com.cn/search/?q={kw}",
        "wait_selector": "body",
        "result_selector": "li",
        "title_selector": "a",
        "link_selector": "a",
        "base_url": "https://www.bidcenter.com.cn",
    },
    {
        "id": "caizhao",
        "name": "采招网",
        "region": "全国",
        "search_url": "https://www.caizhaowang.com/search?q={kw}",
        "wait_selector": "body",
        "result_selector": "li",
        "title_selector": "a",
        "link_selector": "a",
        "base_url": "https://www.caizhaowang.com",
    },
]

# ── 招标关键词正则（用于过滤无关链接） ──
BIDDING_PATTERN = re.compile(r"(招标|采购|询价|竞价|中标|公告|意向|需求|磋商)")


def scrape_page(page, platform, keyword):
    """在已打开的浏览器页面上搜索并提取结果"""
    name = platform["name"]
    url = platform["search_url"].format(kw=quote(keyword))
    
    print(f"[{name}] 访问搜索页...")
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
    except Exception as e:
        print(f"  x 页面加载超时: {e}")
        return []
    
    # 等待搜索结果区域出现
    try:
        page.wait_for_selector(platform["wait_selector"], timeout=15000)
        print(f"  v 搜索结果已加载")
    except PlaywrightTimeout:
        print(f"  - 等待超时，尝试解析已有内容")
    except Exception:
        pass
    
    # 滚动页面以加载懒加载内容
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    time.sleep(1.5)
    
    # 提取结果
    results = []
    try:
        items = page.query_selector_all(platform["result_selector"])
        print(f"  找到 {len(items)} 个元素节点")
        
        for item in items:
            try:
                # 提取标题链接
                link_el = item.query_selector(platform["link_selector"])
                title_el = item.query_selector(platform["title_selector"])
                
                if not link_el and not title_el:
                    continue
                
                el = link_el or title_el
                title = el.inner_text().strip()
                href = el.get_attribute("href") or ""
                
                if not title or len(title) < 8:
                    continue
                if not BIDDING_PATTERN.search(title):
                    continue
                
                # 补全链接
                if href and not href.startswith("http"):
                    href = platform["base_url"].rstrip("/") + "/" + href.lstrip("/")
                
                # 提取日期
                parent_text = item.inner_text()
                date_match = re.search(r"(\d{4}[-/]\d{1,2}[-/]\d{1,2})", parent_text)
                pub_date = date_match.group(1) if date_match else datetime.now().strftime("%Y-%m-%d")
                
                results.append({
                    "title": title,
                    "url": href,
                    "date": pub_date,
                })
            except Exception:
                continue
    except Exception as e:
        print(f"  x 解析异常: {e}")
    
    # 去重
    seen = set()
    unique = []
    for r in results:
        key = r["title"] + r["url"]
        if key not in seen:
            seen.add(key)
            unique.append(r)
    
    print(f"  = 有效招标信息: {len(unique)} 条")
    return unique


def push_wechat(records):
    """通过 Server 酱推送到微信"""
    sckey = os.environ.get("SERVER_CHAN_KEY", "")
    if not sckey:
        print("[WARN] 未配置 SERVER_CHAN_KEY，跳过微信推送")
        return False
    
    import requests as req
    
    today = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    # 按来源分组
    from collections import defaultdict
    by_src = defaultdict(list)
    for r in records:
        by_src[r["src_name"]].append(r)
    
    lines = [
        f"[招标信息日报]",
        f"检索时间: {today}",
        f"关键词: {', '.join(KEYWORDS)}",
        f"共找到: {len(records)} 条招标信息",
        "",
        "=" * 30,
    ]
    
    for src_name, items in by_src.items():
        lines.append(f"")
        lines.append(f"【{src_name}】  {len(items)} 条:")
        for i, item in enumerate(items[:5]):
            title_short = item["title"][:40]
            lines.append(f"  {i+1}. {title_short}")
            lines.append(f"     {item['url']}")
        if len(items) > 5:
            lines.append(f"  ... 还有 {len(items)-5} 条")
    
    body = "\n".join(lines)
    title = f"招标日报 {datetime.now().strftime('%m/%d')} | {len(records)}条"
    
    try:
        resp = req.post(
            f"https://sctapi.ftqq.com/{sckey}.send",
            data={"title": title, "desp": body},
            timeout=15
        )
        result = resp.json()
        if result.get("code") in [0, 200]:
            print(f"[OK] 微信推送成功 ({len(records)}条)")
            return True
        else:
            print(f"[FAIL] 推送失败: {result}")
            return False
    except Exception as e:
        print(f"[FAIL] 推送异常: {e}")
        return False


def main():
    print("=" * 50)
    print("  招标信息自动检索系统 (GitHub Actions)")
    print("=" * 50)
    print(f"关键词: {KEYWORDS}")
    print(f"平台数: {len(PLATFORMS)}")
    print()
    
    all_records = []
    
    with sync_playwright() as pw:
        # 启动无头浏览器
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
            locale="zh-CN",
        )
        page = context.new_page()
        
        try:
            for kw in KEYWORDS:
                for platform in PLATFORMS:
                    results = scrape_page(page, platform, kw)
                    for r in results:
                        r["keyword"] = kw
                        r["src_name"] = platform["name"]
                        r["region"] = platform["region"]
                    all_records.extend(results)
                    time.sleep(2)  # 避免请求过快
        finally:
            browser.close()
    
    print(f"\n{'=' * 30}")
    print(f"总计: {len(all_records)} 条招标信息")
    
    # 保存结果
    data = {
        "records": all_records,
        "last_push": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "keywords": KEYWORDS,
        "total": len(all_records),
    }
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    # 推送到微信
    push_wechat(all_records)
    
    print("\nDone!")


if __name__ == "__main__":
    main()

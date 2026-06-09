#!/usr/bin/env python3
"""
招标信息自动检索 + 微信推送系统 v3.0
真正爬取各平台搜索结果，提取真实招标公告标题和链接
"""

import json
import os
import sys
import io
import re
import time
from datetime import datetime, timedelta
from collections import defaultdict
from urllib.parse import quote, urljoin

# Windows 中文输出修复
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
DATA_FILE = os.path.join(BASE_DIR, "data.json")
WEB_URL = "https://8c879c6d69ed48668f25b74d54d4fa2b.app.codebuddy.work"

# ──────────────────────────────────────────
# HTTP 请求配置
# ──────────────────────────────────────────
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
}

import requests
SESSION = requests.Session()
SESSION.headers.update(HEADERS)

# ──────────────────────────────────────────
# 平台配置
# ──────────────────────────────────────────
PLATFORMS = [
    {
        "id": "ccgp",
        "name": "中国政府采购网",
        "region": "全国",
        "search_url": "https://search.ccgp.gov.cn/bxsearch?searchtype=1&bidSort=0&pinMu=0&bidType=0&dbselect=bidx&kw={kw}&timeType=6&page=1",
        "enabled": True,
    },
    {
        "id": "bidcenter",
        "name": "招标网",
        "region": "全国",
        "search_url": "https://www.bidcenter.com.cn/search/?q={kw}",
        "enabled": True,
    },
    {
        "id": "ggzy",
        "name": "全国公共资源交易平台",
        "region": "全国",
        "search_url": "https://deal.ggzy.gov.cn/ds/deal/dealList_gczb.jsp?DEAL_TIME=9999&DEAL_NAME={kw}",
        "enabled": True,
    },
]


# ──────────────────────────────────────────
# 通用解析函数（适配多平台）
# ──────────────────────────────────────────

def fetch_html(url, timeout=20):
    """发送 GET 请求，返回 HTML 文本"""
    try:
        resp = SESSION.get(url, timeout=timeout, allow_redirects=True, verify=False)
        # 自动识别编码
        encoding = resp.apparent_encoding
        if encoding and encoding.lower() != "utf-8":
            resp.encoding = encoding
        else:
            resp.encoding = "utf-8"
        if resp.status_code == 200:
            return resp.text
        else:
            print(f"  [HTTP {resp.status_code}] {url[:80]}")
            return ""
    except Exception as e:
        print(f"  [请求失败] {str(e)[:80]}")
        return ""


def parse_results_generic(html, base_url=""):
    """
    通用解析：从搜索结果页提取招标公告链接
    策略：找到所有 <a> 标签，过滤出标题足够长且包含招标关键词的
    """
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    results = []
    seen = set()

    # 关键词过滤：标题里含有这些词才认为是招标公告
    kw_pattern = re.compile(r"(招标|采购|询价|竞价|中标|流标|变更|公告|意向|需求)")

    all_links = soup.find_all("a", href=True)
    for a in all_links:
        try:
            title = a.get_text(strip=True)
            href = a["href"].strip()

            if not title or len(title) < 8:
                continue
            if not kw_pattern.search(title):
                continue
            if href.startswith("javascript") or href == "#" or href.startswith("#"):
                continue

            # 拼接完整 URL
            if not href.startswith("http"):
                href = urljoin(base_url, href)

            if href in seen:
                continue
            seen.add(href)

            # 尝试找日期
            parent = a.parent
            date_text = ""
            for _ in range(3):
                if parent is None:
                    break
                # 在父节点中找日期文本
                txt = parent.get_text()
                dm = re.search(r"(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}[日]?)", txt)
                if dm:
                    date_text = dm.group(1).replace("年", "-").replace("月", "-").replace("日", "")
                    break
                parent = parent.parent

            results.append({
                "title": title,
                "url": href,
                "date": date_text,
            })
        except Exception:
            continue

    return results


def crawl_platform(platform, keyword):
    """爬取单个平台，返回结果列表"""
    pid = platform["id"]
    name = platform["name"]
    url = platform["search_url"].format(kw=quote(keyword))

    print(f"  -> {name} | 关键词: {keyword}")

    html = fetch_html(url)
    if not html:
        print(f"     x 无法获取页面")
        return []

    results = parse_results_generic(html, url)
    print(f"     ✓ 找到 {len(results)} 条")
    return results


def is_recent(date_str, days=5):
    """判断日期是否在最近 N 天内"""
    if not date_str:
        return True
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y年%m月%d日"):
        try:
            d = datetime.strptime(date_str, fmt)
            return (datetime.now() - d).days <= days
        except ValueError:
            continue
    return True


# ──────────────────────────────────────────
# 微信推送（Server酱）
# ──────────────────────────────────────────

def send_wechat(cfg, records):
    """推送真实招标结果到微信"""
    sckey = cfg.get("server_chan_key", "").strip()
    if not sckey:
        print("[!] 未配置 SendKey，跳过微信推送")
        print("    请访问 https://sct.ftqq.com/ 获取 SendKey")
        return False

    today = datetime.now().strftime("%Y-%m-%d %H:%M")

    # 按日期分组
    by_date = defaultdict(list)
    for r in records:
        d = r.get("date", today[:10])
        if not d:
            d = today[:10]
        by_date[d].append(r)

    sorted_dates = sorted(by_date.keys(), reverse=True)

    lines = [
        "[招标信息日报]",
        f"检索时间：{today}",
        f"关键词：{'、'.join(cfg['keywords'])}",
        f"共找到：{len(records)} 条招标信息",
        "",
        "----------------------------------------",
    ]

    for d in sorted_dates:
        items = by_date[d]
        lines.append("")
        lines.append(f"【{d}】 共 {len(items)} 条：")
        for i, item in enumerate(items[:8]):  # 每天最多8条，避免微信消息过长
            title_short = item["title"][:35]
            lines.append(f"  {i+1}. {title_short}")
            lines.append(f"     来源：{item['src_name']}")
            lines.append(f"     链接：{item['url']}")
        if len(items) > 8:
            lines.append(f"  ...还有 {len(items)-8} 条，请在线查看")

    lines.extend([
        "",
        "----------------------------------------",
        f"查看全部 & 导出CSV：{WEB_URL}",
    ])

    body = "\n".join(lines)
    title = f"招标日报 {datetime.now().strftime('%m/%d')} | {len(records)}条"

    try:
        resp = SESSION.post(
            f"https://sctapi.ftqq.com/{sckey}.send",
            data={"title": title, "desp": body},
            timeout=15
        )
        result = resp.json()
        if result.get("code") in [0, 200]:
            print(f"[OK] 微信推送成功！({len(records)}条)")
            return True
        else:
            print(f"[FAIL] 推送失败: {result.get('message', str(result))}")
            return False
    except Exception as e:
        print(f"[FAIL] 推送异常: {e}")
        return False


# ──────────────────────────────────────────
# 主流程
# ──────────────────────────────────────────

def main():
    # 关闭 SSL 警告（部分政府网站证书过期）
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    if not os.path.exists(CONFIG_FILE):
        print("[!] 找不到 config.json，请先在网页上配置关键词")
        sys.exit(1)

    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    keywords = cfg.get("keywords", [])
    enabled_ids = set(cfg.get("enabled_sources", [p["id"] for p in PLATFORMS]))

    if not keywords:
        print("[!] 没有配置关键词，请在网页左侧添加关键词")
        sys.exit(1)

    platforms = [p for p in PLATFORMS if p["id"] in enabled_ids and p.get("enabled")]

    print("=" * 50)
    print("  招标信息自动检索系统 v3.0（真实爬取）")
    print("=" * 50)
    print(f"关键词：{keywords}")
    print(f"启用平台：{len(platforms)} 个")
    print()

    all_records = []
    seen_urls = set()

    for kw in keywords:
        for platform in platforms:
            results = crawl_platform(platform, kw)
            time.sleep(2)  # 礼貌延迟，避免被封

            for r in results:
                if r["url"] in seen_urls:
                    continue
                seen_urls.add(r["url"])
                all_records.append({
                    "title": r["title"],
                    "url": r["url"],
                    "date": r.get("date", datetime.now().strftime("%Y-%m-%d")),
                    "keyword": kw,
                    "src_name": platform["name"],
                    "region": platform["region"],
                })

    # 过滤最近 N 天的
    days_range = cfg.get("days_range", 5)
    recent_records = [r for r in all_records if is_recent(r.get("date", ""), days_range)]

    print()
    print(f"总计找到：{len(all_records)} 条，最近 {days_range} 天：{len(recent_records)} 条")

    # 保存 data.json
    data = {
        "records": recent_records,
        "last_push": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "keywords": keywords,
        "total": len(recent_records),
    }
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"已保存 data.json（{len(recent_records)} 条）")

    # 推送到微信
    if recent_records:
        send_wechat(cfg, recent_records)
    else:
        print("[!] 没有找到任何招标信息")
        print("    可能原因：网站结构变化 / 网络访问受限 / 该关键词暂无招标")

    print(f"\n在线查看：{WEB_URL}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "set-key":
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        if len(sys.argv) > 2:
            cfg["server_chan_key"] = sys.argv[2]
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            print("[OK] SendKey 已保存")
        else:
            print("用法: python scraper.py set-key <你的SendKey>")
    else:
        main()

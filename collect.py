#!/usr/bin/env python3
"""Collect fresh public offer leads and build the static daily page.

Only RSS metadata is used. The output deliberately never calls a lead a
verified, currently redeemable coupon without checking the merchant app.
"""

import argparse
import concurrent.futures
import datetime as dt
import difflib
import email.utils
import html
import json
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path


CST = dt.timezone(dt.timedelta(hours=8))
PLATFORMS = {
    "腾讯": ("会员订阅与支付", "腾讯"),
    "阿里巴巴": ("电商购物", "淘宝 天猫"),
    "字节跳动": ("电商购物", "抖音"),
    "美团": ("外卖餐饮与本地生活", "美团"),
    "拼多多": ("电商购物", "拼多多"),
    "京东": ("电商购物", "京东"),
    "网易": ("电商购物", "网易严选"),
    "百度": ("会员订阅与支付", "百度"),
    "小米": ("电商购物", "小米"),
    "快手": ("电商购物", "快手"),
    "携程": ("出行旅游与酒店", "携程"),
    "滴滴": ("出行旅游与酒店", "滴滴"),
    "蚂蚁集团": ("会员订阅与支付", "支付宝"),
    "哔哩哔哩": ("会员订阅与支付", "哔哩哔哩 B站"),
    "微博": ("会员订阅与支付", "微博"),
    "360": ("会员订阅与支付", "360会员"),
    "唯品会": ("电商购物", "唯品会"),
    "小红书": ("电商购物", "小红书"),
    "贝壳": ("本地生活", "贝壳找房"),
    "东方财富": ("会员订阅与支付", "东方财富"),
}
ALIASES = {
    "阿里巴巴": ("淘宝", "天猫", "阿里巴巴"),
    "字节跳动": ("抖音", "字节跳动"),
    "网易": ("网易严选", "网易"),
    "蚂蚁集团": ("支付宝", "蚂蚁"),
    "哔哩哔哩": ("哔哩哔哩", "B站", "bilibili"),
    "贝壳": ("贝壳找房", "贝壳"),
}
PROMO = re.compile(r"优惠|消费券|补贴|红包|折扣|满减|立减|买一送一|免单|特价|直降|领券|返现|折上折|免费领")
AMOUNT = re.compile(r"(?:\d+(?:\.\d+)?\s*(?:元|折|%))|(?:满\s*\d+\s*减\s*\d+)")
SPAM = re.compile(r"怎么领|领取入口|口令|攻略|保姆级|省钱技巧|一文(看懂|讲透)|教程|指南|怎么(买|抢|领|选)|避坑|合集|汇总|大全|最划算|FAQ|[?？]|哪个.*靠谱|神价实测")
FUTURE_EVENT = re.compile(r"双\s*11|双十一")
CURRENT_ACTION = re.compile(r"已开启|已上线|今日|今天|现在|正在|现领|开抢|发放|启动")
OFFICIAL_DOMAINS = (".gov.cn", "jd.com", "meituan.com", "taobao.com", "tmall.com", "pinduoduo.com", "mi.com", "ctrip.com", "alipay.com", "vip.com", "bilibili.com", "tencent.com", "douyin.com")
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; YouhuiDaily/1.0; public RSS reader)"}


def rss_url(query):
    return "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": query + " when:7d", "hl": "zh-CN", "gl": "CN", "ceid": "CN:zh-Hans"}
    )


def fetch(platform):
    category, terms = PLATFORMS[platform]
    query = "(" + " OR ".join(terms.split()) + ") (优惠券 OR 消费券 OR 补贴 OR 红包 OR 折扣)"
    request = urllib.request.Request(rss_url(query), headers=HEADERS)
    with urllib.request.urlopen(request, timeout=20) as response:
        data = response.read(2_000_000)
    root = ET.fromstring(data)
    if root.tag != "rss":
        raise ValueError("RSS 响应格式异常")
    return platform, category, root.findall("./channel/item")


def parse_item(item, platform, category, today):
    raw_title = html.unescape(item.findtext("title") or "").strip()
    # Google News appends the publisher to each title.
    title = raw_title.rsplit(" - ", 1)[0].split("|")[0].strip()
    aliases = ALIASES.get(platform, (platform,))
    if not title or not PROMO.search(title) or not any(alias.casefold() in title.casefold() for alias in aliases):
        return None
    if SPAM.search(title) or (FUTURE_EVENT.search(title) and not CURRENT_ACTION.search(title)):
        return None
    link = (item.findtext("link") or "").strip()
    if not link.startswith("https://news.google.com/"):
        return None
    try:
        published = email.utils.parsedate_to_datetime(item.findtext("pubDate"))
        if published.tzinfo is None:
            return None
        local_date = published.astimezone(CST).date()
    except (TypeError, ValueError):
        return None
    age = (today - local_date).days
    if not 0 <= age <= 7:
        return None
    source = item.find("source")
    publisher = (source.text or "未知来源").strip() if source is not None else "未知来源"
    source_url = (source.attrib.get("url") or "") if source is not None else ""
    domain = urllib.parse.urlparse(source_url).hostname or ""
    official = any(domain == d.lstrip(".") or domain.endswith(d) for d in OFFICIAL_DOMAINS)
    return {
        "platform": platform,
        "category": category,
        "title": title,
        "published": local_date.isoformat(),
        "publisher": publisher,
        "source_type": "官方来源线索" if official else "公开报道线索",
        "link": link,
        "discount_in_title": bool(AMOUNT.search(title)),
    }


def collect(today):
    offers, errors = [], []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(fetch, platform): platform for platform in PLATFORMS}
        for future in concurrent.futures.as_completed(futures):
            platform = futures[future]
            try:
                name, category, items = future.result()
                offers.extend(filter(None, (parse_item(item, name, category, today) for item in items)))
            except (OSError, ValueError, ET.ParseError) as exc:
                errors.append(f"{platform}: {type(exc).__name__}: {exc}")
    if len(errors) == len(PLATFORMS):
        raise RuntimeError("全部 20 个平台采集失败，保留上一次页面。")
    seen, unique, per_platform = set(), [], {}
    for item in sorted(offers, key=lambda x: (x["published"], x["discount_in_title"], x["source_type"] == "官方来源线索"), reverse=True):
        key = re.sub(r"\s+", "", item["title"]).casefold()
        similar = any(
            old["platform"] == item["platform"]
            and difflib.SequenceMatcher(None, old["title"], item["title"]).ratio() > 0.72
            for old in unique
        )
        if key not in seen and not similar and per_platform.get(item["platform"], 0) < 5:
            seen.add(key)
            unique.append(item)
            per_platform[item["platform"]] = per_platform.get(item["platform"], 0) + 1
    return unique[:100], errors


def render(payload):
    esc = html.escape
    today = payload["date"]
    items = payload["offers"]
    count = len(items)
    cards = []
    for offer in items:
        badge = "official" if offer["source_type"] == "官方来源线索" else "media"
        cards.append(
            '<article class="card">'
            f'<div class="meta"><span>{esc(offer["platform"])}</span><span>{esc(offer["category"])}</span>'
            f'<span class="{badge}">{esc(offer["source_type"])}</span></div>'
            f'<h2>{esc(offer["title"])}</h2>'
            f'<p>发布于 {esc(offer["published"])} · {esc(offer["publisher"])}</p>'
            f'<a href="{esc(offer["link"], quote=True)}" target="_blank" rel="noopener noreferrer">查看原始报道 ↗</a>'
            '</article>'
        )
    body = "\n".join(cards) if cards else '<div class="empty">近 7 天没有检索到符合规则的公开优惠线索。可明天再看，或直接查看各平台 App 的领券中心。</div>'
    failures = f'；{len(payload["errors"])} 个平台采集失败' if payload["errors"] else ""
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{today} 每日优惠线索</title>
<style>
:root{{font-family:system-ui,"Microsoft YaHei",sans-serif;color:#222;background:#f7f5f1}}*{{box-sizing:border-box}}
body{{margin:0}}.wrap{{max-width:1080px;margin:auto;padding:24px}}header{{background:linear-gradient(120deg,#b92e21,#ef8938);color:white;padding:32px;border-radius:20px}}
h1{{font-size:clamp(26px,5vw,42px);margin:0 0 8px}}header p{{margin:0;line-height:1.6}}.notice{{background:#fff5dc;border:1px solid #edd49a;border-radius:12px;padding:15px 18px;margin:18px 0;line-height:1.7}}
.toolbar{{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:20px 0}}input,select{{font:inherit;border:1px solid #d7d3cc;border-radius:9px;padding:10px 12px;background:white}}input{{flex:1;min-width:220px}}.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(290px,1fr));gap:14px}}
.card,.empty{{background:white;border:1px solid #ebe6dc;border-radius:13px;padding:18px;box-shadow:0 2px 10px #00000009}}.card h2{{font-size:17px;line-height:1.5;margin:12px 0}}.card p{{color:#666;font-size:13px}}.card a{{color:#a42e1a;font-weight:600;text-decoration:none}}.meta{{display:flex;gap:6px;flex-wrap:wrap}}.meta span{{font-size:12px;background:#f2eee8;padding:3px 7px;border-radius:5px}}.meta .official{{background:#e6f4e9;color:#216537}}.meta .media{{background:#e8eef7;color:#345d91}}.empty{{grid-column:1/-1}}footer{{margin-top:24px;color:#67615b;font-size:13px;line-height:1.6}}
</style></head><body><main class="wrap"><header><h1>每日优惠线索</h1><p>{today}（北京时间） · 近 7 天公开信息 · {count} 条线索{failures}</p></header>
<div class="notice"><strong>使用前请核验：</strong>这里自动整理的是报道标题线索，不代表优惠仍可领取。金额、门槛、地区、有效期和领取步骤，请点击来源后再到官方 App 或活动规则页确认。无公开可核验信息时不会编造优惠。</div>
<div class="toolbar"><input id="search" type="search" placeholder="搜索平台或优惠"><select id="category"><option value="">全部场景</option><option>外卖餐饮与本地生活</option><option>电商购物</option><option>出行旅游与酒店</option><option>会员订阅与支付</option><option>本地生活</option></select></div>
<div class="grid" id="offers">{body}</div><footer>覆盖 20 家平台。每天 10:30（北京时间）自动采集公开新闻 RSS；更新结果与故障状态保存在仓库 data/latest.json。仅收录正规优惠信息。</footer></main>
<script>const s=document.querySelector('#search'),c=document.querySelector('#category');function f(){{document.querySelectorAll('.card').forEach(x=>{{x.hidden=!(x.textContent.toLowerCase().includes(s.value.trim().toLowerCase())&&(!c.value||x.textContent.includes(c.value)))}})}}s.addEventListener('input',f);c.addEventListener('change',f);</script>
</body></html>'''


def main():
    parser = argparse.ArgumentParser(description="采集 20 家平台近期公开优惠线索")
    parser.add_argument("--output", type=Path, default=Path("index.html"))
    parser.add_argument("--json-output", type=Path, default=Path("data/latest.json"))
    args = parser.parse_args()
    today = dt.datetime.now(CST).date()
    offers, errors = collect(today)
    payload = {"date": today.isoformat(), "timezone": "Asia/Shanghai", "source": "Google News RSS", "offers": offers, "errors": errors}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(payload), encoding="utf-8")
    args.json_output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{today}: {len(offers)} 条线索，{len(errors)} 个平台采集失败")
    for error in errors:
        print(error, file=sys.stderr)


if __name__ == "__main__":
    main()

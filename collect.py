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
    "腾讯": ("会员订阅与综合补贴", "腾讯"),
    "阿里巴巴": ("电商购物", "淘宝 天猫"),
    "字节跳动": ("电商购物", "抖音"),
    "美团": ("外卖、本地生活与餐饮", "美团"),
    "拼多多": ("电商购物", "拼多多"),
    "京东": ("电商购物", "京东"),
    "网易": ("电商购物", "网易严选"),
    "百度": ("会员订阅与综合补贴", "百度"),
    "小米": ("电商购物", "小米"),
    "快手": ("电商购物", "快手"),
    "携程": ("出行、旅游与酒店", "携程"),
    "滴滴": ("出行、旅游与酒店", "滴滴"),
    "蚂蚁集团": ("会员订阅与综合补贴", "支付宝"),
    "哔哩哔哩": ("会员订阅与综合补贴", "哔哩哔哩 B站"),
    "微博": ("会员订阅与综合补贴", "微博"),
    "360": ("会员订阅与综合补贴", "360会员"),
    "唯品会": ("电商购物", "唯品会"),
    "小红书": ("电商购物", "小红书"),
    "贝壳": ("外卖、本地生活与餐饮", "贝壳找房"),
    "东方财富": ("会员订阅与综合补贴", "东方财富"),
    "招商银行": ("信用卡优惠", "招商银行 招行"),
    "建设银行": ("信用卡优惠", "建设银行 建行"),
    "工商银行": ("信用卡优惠", "工商银行 工行"),
    "中国银行": ("信用卡优惠", "中国银行 中行"),
    "农业银行": ("信用卡优惠", "农业银行 农行"),
    "交通银行": ("信用卡优惠", "交通银行 交行"),
    "邮储银行": ("信用卡优惠", "邮储银行 邮政储蓄"),
    "平安银行": ("信用卡优惠", "平安银行"),
    "中信银行": ("信用卡优惠", "中信银行"),
    "浦发银行": ("信用卡优惠", "浦发银行 浦发"),
    "中国银联": ("信用卡优惠", "中国银联 云闪付"),
    "民生银行": ("信用卡优惠", "民生银行"),
    "光大银行": ("信用卡优惠", "光大银行"),
}
ALIASES = {
    "阿里巴巴": ("淘宝", "天猫", "阿里巴巴"),
    "字节跳动": ("抖音", "字节跳动"),
    "网易": ("网易严选", "网易"),
    "蚂蚁集团": ("支付宝", "蚂蚁"),
    "哔哩哔哩": ("哔哩哔哩", "B站", "bilibili"),
    "贝壳": ("贝壳找房", "贝壳"),
    "招商银行": ("招商银行", "招行"),
    "建设银行": ("建设银行", "建行"),
    "工商银行": ("工商银行", "工行"),
    "中国银行": ("中国银行", "中行"),
    "农业银行": ("农业银行", "农行"),
    "交通银行": ("交通银行", "交行"),
    "邮储银行": ("邮储银行", "邮政储蓄"),
    "中国银联": ("中国银联", "云闪付"),
    "浦发银行": ("浦发银行", "浦发"),
}
PROMO = re.compile(r"优惠|消费券|补贴|红包|折扣|满减|立减|买一送一|免单|特价|直降|领券|返现|折上折|免费领|信用卡|积分抵现|免年费")
AMOUNT = re.compile(r"(?:\d+(?:\.\d+)?\s*(?:元|折|%))|(?:满\s*\d+\s*减\s*\d+)")
SPAM = re.compile(r"怎么领|领取入口|口令|攻略|保姆级|省钱技巧|一文(看懂|讲透)|教程|指南|怎么(买|抢|领|选)|避坑|合集|汇总|大全|最划算|FAQ|[?？]|哪个.*靠谱|神价实测|博彩|赌博|套现|刷单|澳门威斯尼斯人|官方网站下载|虚假折扣|欧盟被罚|假网站")
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
    if category == "信用卡优惠":
        keywords = "信用卡优惠 OR 消费立减 OR 满减 OR 积分抵现 OR 免年费 OR 免息 OR 免密支付 OR 消费券"
    else:
        keywords = "优惠券 OR 消费券 OR 补贴 OR 红包 OR 折扣 OR 满减 OR 立减 OR 会员折扣 OR 以旧换新"
    query = "(" + " OR ".join(terms.split()) + ") (" + keywords + ")"
    request = urllib.request.Request(rss_url(query), headers=HEADERS)
    with urllib.request.urlopen(request, timeout=20) as response:
        data = response.read(2_000_000)
    root = ET.fromstring(data)
    if root.tag != "rss":
        raise ValueError("RSS 响应格式异常")
    return platform, category, root.findall("./channel/item")


def clean_summary(raw):
    raw = html.unescape(raw or "")
    raw = re.sub(r"<[^>]*>", " ", raw)
    raw = html.unescape(raw)
    raw = re.sub(r"https?://\S+", " ", raw)
    return re.sub(r"\s+", " ", raw).strip()[:360]


def extract_deal_amount(text):
    patterns = (
        r"满\s*\d+(?:\.\d+)?\s*元?\s*减\s*\d+(?:\.\d+)?\s*元?",
        r"(?:至高|最高)?\s*(?:补贴|立减|优惠)\s*(?:至高|最高)?\s*\d+(?:\.\d+)?\s*(?:元|%)",
        r"\d+(?:\.\d+)?\s*%\s*补贴",
        r"\d+(?:\.\d+)?\s*折",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return re.sub(r"\s+", "", match.group(0))
    return None


def extract_threshold(text):
    match = re.search(r"满\s*\d+(?:\.\d+)?\s*元?(?:\s*减\s*\d+(?:\.\d+)?\s*元?)?", text)
    if match:
        return re.sub(r"\s+", "", match.group(0))
    for phrase in ("限新客", "新用户专享", "限指定商品", "每人限领", "每人限用"):
        if phrase in text:
            return phrase
    return None


def extract_valid_until(text):
    match = re.search(r"(?:截至|截止(?:日期)?(?:到|至)?|有效期(?:至|到)?|活动(?:至|截至|截止)|至)\s*(20\d{2})[年./-](\d{1,2})[月./-](\d{1,2})日?", text)
    if not match:
        return None
    try:
        return dt.date(int(match.group(1)), int(match.group(2)), int(match.group(3))).isoformat()
    except ValueError:
        return None


def extract_region(text):
    for region in ("成都", "四川", "重庆", "全国"):
        if re.search(r"(?:限|适用|定位|仅在|可在|面向|覆盖)(?:于|在)?[^。；，,]{0,12}" + re.escape(region), text):
            return region
    return None


def parse_item(item, platform, category, today):
    raw_title = html.unescape(item.findtext("title") or "").strip()
    title = raw_title.rsplit(" - ", 1)[0].split("|")[0].strip()
    aliases = ALIASES.get(platform, (platform,))
    raw_description = item.findtext("description") or ""
    summary = clean_summary(raw_description)
    evidence = title + " " + summary
    if not title or not PROMO.search(evidence) or not any(alias.casefold() in title.casefold() for alias in aliases):
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
        "platform": platform, "category": category, "title": title,
        "published": local_date.isoformat(), "publisher": publisher,
        "source_type": "官方来源线索" if official else "公开报道线索",
        "link": link, "discount_in_title": bool(AMOUNT.search(title)),
        "summary": summary or None,
        "date": today.isoformat(), "company": platform, "scene": category,
        "source_url": link, "source_name": publisher,
        "published_at": local_date.isoformat(),
        "deal_amount": extract_deal_amount(evidence),
        "threshold": extract_threshold(summary) if summary else None,
        "valid_until": extract_valid_until(evidence),
        "region": extract_region(evidence),
        "entry": None, "how_to_use": None, "verify_status": "一方称",
    }




SCENES = {
    "外卖、本地生活与餐饮", "外卖餐饮与本地生活", "本地生活",
    "电商购物", "出行、旅游与酒店", "出行旅游与酒店",
    "会员订阅与综合补贴", "会员订阅与支付", "信用卡优惠",
}


def normalize_valid_until(value, today):
    """Return an ISO end date when one is explicit, while preserving the source wording."""
    if not isinstance(value, str) or not value.strip():
        return None, None
    value = value.strip()
    try:
        return dt.date.fromisoformat(value).isoformat(), None
    except ValueError:
        pass
    full_dates = list(re.finditer(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})日?", value))
    if full_dates:
        last = full_dates[-1]
        year, month, day = map(int, last.groups())
        tail = value[last.end():]
        short_end = re.search(r"(?:至|到|—|-)\s*(\d{1,2})[-/.月](\d{1,2})日?", tail)
        if short_end:
            end_month, end_day = map(int, short_end.groups())
            try:
                end_date = dt.date(year, end_month, end_day)
                start_date = dt.date(year, month, day)
                if end_date < start_date:
                    end_date = dt.date(year + 1, end_month, end_day)
                return end_date.isoformat(), value
            except ValueError:
                pass
        try:
            return dt.date(year, month, day).isoformat(), value
        except ValueError:
            pass
    return None, value


def load_doubao_offers(today, incoming_dir=Path("incoming")):
    """Load a manually reviewed, date-matched Doubao JSON handoff file."""
    path = incoming_dir / f"{today.isoformat()}.json"
    if not path.exists():
        return [], []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [], [f"豆包交接文件无法读取: {exc}"]
    if payload.get("date") != today.isoformat() or not isinstance(payload.get("offers"), list):
        return [], ["豆包交接文件日期或 offers 格式不正确"]
    accepted, errors = [], []
    for index, raw in enumerate(payload["offers"], 1):
        if not isinstance(raw, dict):
            errors.append(f"豆包第 {index} 条不是对象")
            continue
        scene = raw.get("scene")
        source_url = raw.get("source_url") or raw.get("link")
        if not isinstance(raw.get("company"), str) or not raw["company"].strip() or not isinstance(raw.get("title"), str) or not raw["title"].strip():
            errors.append(f"豆包第 {index} 条缺少 company/title")
            continue
        if scene not in SCENES:
            errors.append(f"豆包第 {index} 条场景不在允许列表: {scene}")
            continue
        parsed_url = urllib.parse.urlparse(source_url or "")
        if parsed_url.scheme != "https" or not parsed_url.netloc:
            errors.append(f"豆包第 {index} 条缺少有效 HTTPS 来源链接")
            continue
        valid_until, valid_until_note = normalize_valid_until(raw.get("valid_until"), today)
        if valid_until and dt.date.fromisoformat(valid_until) < today:
            errors.append(f"豆包第 {index} 条活动已过截止日期")
            continue
        published_at = raw.get("published_at")
        try:
            if published_at:
                published_at = dt.date.fromisoformat(published_at).isoformat()
        except (TypeError, ValueError):
            published_at = None
        source_name = raw.get("source_name")
        if not isinstance(source_name, str) or not source_name.strip():
            source_name = parsed_url.hostname or "来源未注明"
        item = {
            "platform": raw["company"].strip(), "category": scene,
            "title": raw["title"].strip(), "published": published_at or "",
            "publisher": source_name.strip(),
            "source_type": "豆包整理（待核对）",
            "link": source_url, "discount_in_title": bool(AMOUNT.search(raw["title"])),
            "summary": raw.get("summary"), "date": today.isoformat(),
            "company": raw["company"].strip(), "scene": scene,
            "source_url": source_url, "source_name": source_name.strip(),
            "published_at": published_at, "deal_amount": raw.get("deal_amount"),
            "threshold": raw.get("threshold"), "valid_until": valid_until,
            "valid_until_note": valid_until_note,
            "region": raw.get("region"), "entry": raw.get("entry"),
            "how_to_use": raw.get("how_to_use"),
            "verify_status": "豆包标注已查证，待复核" if raw.get("verify_status") == "已查证" else "待核对",
        }
        accepted.append(item)
    return accepted, errors


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
    doubao_offers, doubao_errors = load_doubao_offers(today)
    errors.extend(doubao_errors)
    # When a dated Doubao handoff exists, it is the primary editorial source.
    # Keep RSS as a fallback only for days without an accepted handoff.
    if doubao_offers:
        offers = doubao_offers
    if not offers:
        raise RuntimeError("自动采集与豆包交接均无有效内容，保留上一次页面。")
    seen, unique, per_platform = set(), [], {}
    for item in sorted(offers, key=lambda x: (x["source_type"].startswith("豆包"), x["published"], x["discount_in_title"], x["source_type"] == "官方来源线索"), reverse=True):
        key = re.sub(r"\s+", "", item["title"]).casefold()
        similar = any(
            old["platform"] == item["platform"]
            and difflib.SequenceMatcher(None, old["title"], item["title"]).ratio() > 0.72
            for old in unique
        )
        under_limit = item["source_type"].startswith("豆包") or per_platform.get(item["platform"], 0) < 5
        if key not in seen and not similar and under_limit:
            seen.add(key)
            unique.append(item)
            per_platform[item["platform"]] = per_platform.get(item["platform"], 0) + 1
    return unique, errors


def build_catalog(archive_dir):
    """Rebuild the search index from immutable, dated snapshots."""
    days, offers = [], []
    for path in sorted(archive_dir.glob("????-??-??.json"), reverse=True):
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        date = snapshot["date"]
        if path.stem != date:
            raise ValueError(f"归档日期与文件名不符: {path}")
        days.append({"date": date, "count": len(snapshot["offers"]), "errors": len(snapshot["errors"])})
        offers.extend({**offer, "snapshot_date": date} for offer in snapshot["offers"])
    return {"days": days, "offers": offers}


PAGE_TEMPLATE = "<!doctype html>\n<html lang=\"zh-CN\">\n<head>\n<meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">\n<meta name=\"theme-color\" content=\"#f1f5f5\">\n<title>每日优惠 · 生活补贴速查</title>\n<style>\n:root{color-scheme:light;--ink:#20343b;--muted:#667b80;--line:#d8e2e3;--paper:#f1f5f5;--card:#fff;--teal:#176b68;--teal-pale:#e5f1ef;--amber:#9a5c19;--amber-pale:#fff3df;--red:#a44242;--red-pale:#fff0ef;--green:#306b4e;--green-pale:#eaf4eb;font-family:Inter,\"PingFang SC\",\"Microsoft YaHei\",system-ui,sans-serif;background:var(--paper);color:var(--ink)}\n*{box-sizing:border-box}body{margin:0;line-height:1.55}a{color:inherit}.shell{max-width:1320px;margin:auto;padding:24px 32px 48px}\n.top{display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid var(--line);padding-bottom:14px}.brand{font-weight:750}.brand small{font-size:13px;font-weight:500;color:var(--muted);margin-left:10px}.nav a{padding:8px 12px;text-decoration:none;color:var(--muted);font-size:14px;border-radius:6px}.nav a[aria-current]{background:#dcebea;color:var(--teal);font-weight:650}\n.intro{display:flex;justify-content:space-between;align-items:end;gap:20px;padding:28px 0 18px}h1{font-size:clamp(30px,4vw,43px);line-height:1.15;letter-spacing:-.035em;margin:0 0 9px}.intro p{margin:0;color:var(--muted)}.stamp{text-align:right;color:var(--muted);font-size:13px;white-space:nowrap}\n.note{border-left:3px solid #78a8a4;background:#e6efef;padding:11px 15px;color:#435c60;font-size:13px;margin-bottom:18px}\n.filters{display:grid;grid-template-columns:auto minmax(180px,.65fr) 1fr minmax(180px,.5fr);gap:9px;margin-bottom:19px}.step{display:flex;gap:7px}.control,button{font:inherit;color:var(--ink);background:var(--card);border:1px solid var(--line);border-radius:6px;padding:9px 11px;min-height:41px}button{cursor:pointer}.control:focus-visible,button:focus-visible,a:focus-visible{outline:3px solid #61a9a4;outline-offset:2px}.filters input{width:100%}\n.section{margin:18px 0 24px}.section-head{display:flex;align-items:baseline;justify-content:space-between;gap:16px;margin-bottom:10px}.section-head h2{font-size:21px;letter-spacing:-.02em;margin:0}.section-head p{font-size:12px;color:var(--muted);margin:0}\n.top-layout{display:grid;grid-template-columns:minmax(0,1.7fr) minmax(250px,.8fr);gap:16px}.top-box,.deadline-box{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:16px}.top-grid{display:grid;grid-template-columns:1fr 1fr;gap:0 18px}.top-item{display:grid;grid-template-columns:30px minmax(0,1fr);gap:9px;padding:11px 0;border-bottom:1px solid #edf1f1}.rank{font-weight:750;color:var(--teal);font-size:16px}.top-item a{display:block;text-decoration:none}.top-item strong{display:block;font-size:14px;color:var(--teal)}.top-item span{display:block;color:var(--muted);font-size:12px;margin-top:3px;line-height:1.45}\n.deadline-box{border-top:3px solid var(--red)}.deadline-item{padding:9px 0;border-bottom:1px solid #f0e7e6}.deadline-item strong{display:block;color:var(--red);font-size:14px}.deadline-item span{font-size:12px;color:var(--muted)}\n.scene-nav{display:flex;flex-wrap:wrap;gap:8px;margin:20px 0 8px}.scene-nav a{border:1px solid var(--line);border-radius:5px;padding:7px 10px;background:#fff;text-decoration:none;color:#4b6268;font-size:13px}.scene-nav a:hover{border-color:#79aaa6;color:var(--teal)}\n.summary{font-size:14px;color:var(--muted);margin:13px 0}.group{margin:22px 0 26px;scroll-margin-top:15px}.group h2{font-size:19px;margin:0;padding:0 0 8px;border-bottom:1px solid var(--line)}.group h2 small{font-size:13px;color:var(--muted);font-weight:500;margin-left:8px}\n.card{display:grid;grid-template-columns:minmax(0,1fr) 225px;gap:18px;background:var(--card);border:1px solid var(--line);border-radius:7px;padding:16px 18px;margin-top:9px}.badges{display:flex;flex-wrap:wrap;gap:6px}.badge{font-size:12px;color:#53696e;background:#edf2f2;padding:3px 7px;border-radius:4px}.badge.verify{background:var(--amber-pale);color:var(--amber)}.badge.official{background:var(--green-pale);color:var(--green)}\nh3{font-size:16px;line-height:1.55;margin:9px 0 5px}.source{font-size:13px;color:var(--muted)}.source a{color:var(--teal);text-decoration:none;font-weight:650}.source a:hover{text-decoration:underline}.blurb{font-size:13px;color:#475f64;margin:8px 0 0;line-height:1.65}\n.details{display:flex;flex-direction:column;gap:5px;border-left:1px solid var(--line);padding-left:16px;font-size:13px;color:#53696e}.amount{font-size:17px;font-weight:750;color:var(--teal)}.unknown{font-size:13px;font-weight:600;color:var(--muted)}.expiry{width:max-content;padding:2px 6px;border-radius:4px;color:var(--muted);background:#eff2f2}.soon{color:var(--red);background:var(--red-pale)}.long{color:var(--green);background:var(--green-pale)}.expired{color:#758185;background:#e8ecec}\n.empty{background:var(--card);border:1px dashed #c8d6d8;border-radius:7px;padding:18px;color:var(--muted);font-size:13px}.scene-empty{margin-top:9px}#more{display:block;margin:17px auto}[hidden]{display:none!important}footer{border-top:1px solid var(--line);margin-top:26px;padding-top:14px;color:var(--muted);font-size:12px}\n@media(max-width:930px){.shell{padding:20px}.filters{grid-template-columns:1fr 1fr}.top-layout{grid-template-columns:1fr}.deadline-box{grid-row:1}.filters .step{grid-column:1/-1}.step button{flex:1}}\n@media(max-width:600px){.shell{padding:13px}.brand small{display:block;margin:2px 0}.intro{display:block;padding:22px 0 14px}.stamp{text-align:left;margin-top:8px}.filters{grid-template-columns:1fr 1fr}.filters input{grid-column:1/-1}.top-grid{grid-template-columns:1fr}.top-box,.deadline-box{padding:13px}.card{grid-template-columns:1fr;gap:11px;padding:14px}.details{border-left:0;border-top:1px solid var(--line);padding:10px 0 0}.section-head{display:block}.section-head p{margin-top:3px}}\n@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;transition:none!important}}\n</style>\n</head><body><main class=\"shell\">\n<div class=\"top\"><div class=\"brand\">生活优惠速查 <small>补贴、消费券与会员权益</small></div><nav class=\"nav\"><a href=\"https://dawanglin.github.io/news-daily/\">新闻</a><a href=\"https://dawanglin.github.io/youhui-pages/\" aria-current=\"page\">优惠</a></nav></div>\n<section class=\"intro\"><div><h1>今天有哪些值得留意的优惠</h1><p>先看活动力度、适用范围和截止时间，再决定要不要领取。</p></div><div class=\"stamp\">采集日期 __DATE__（北京时间）<br>当日 __COUNT__ 条公开线索</div></section>\n<div class=\"note\"><strong>使用提醒：</strong>页面汇集自动 RSS 线索与豆包整理后导入的优惠。RSS 摘要有时只是标题复述；豆包标注“已查证”也仅表示其自报，未经本站独立核验。条件、名额和叠加规则以官方活动页及支付结算页为准；来源未写明的字段会标注。</div>\n<div class=\"filters\"><div class=\"step\"><button id=\"older\" type=\"button\">← 较早</button><button id=\"newer\" type=\"button\">较新 →</button></div><select class=\"control\" id=\"date\" aria-label=\"选择采集日期\"></select><input class=\"control\" id=\"search\" type=\"search\" placeholder=\"搜索平台、优惠或来源\"><select class=\"control\" id=\"category\" aria-label=\"筛选生活场景\"><option value=\"\">全部场景</option><option>外卖、本地生活与餐饮</option><option>电商购物</option><option>出行、旅游与酒店</option><option>会员订阅与综合补贴</option><option>信用卡优惠</option></select></div>\n<section class=\"section\"><div class=\"section-head\"><h2>今日 TOP10</h2><p>只从标题有明确优惠数字的线索中选取；按标题数字排序，不代表实际省钱价值排名。</p></div><div class=\"top-layout\"><div class=\"top-box\"><div class=\"top-grid\" id=\"top10\"></div></div><div class=\"deadline-box\"><div class=\"section-head\"><h2>即将截止</h2><p>仅列来源明确写有截止日期的活动</p></div><div id=\"deadlines\"></div></div></div></section>\n<nav class=\"scene-nav\" aria-label=\"优惠场景\"><a href=\"#scene-life\">外卖 / 本地生活 / 餐饮</a><a href=\"#scene-shopping\">电商购物</a><a href=\"#scene-travel\">出行 / 旅游 / 酒店</a><a href=\"#scene-membership\">会员订阅与综合补贴</a><a href=\"#scene-credit\">信用卡优惠</a></nav>\n<div class=\"summary\" id=\"count\" aria-live=\"polite\"></div><div id=\"offers\"></div><button id=\"more\" type=\"button\" hidden>显示更多</button>\n<footer>仅供个人查找优惠线索，不保证活动仍有名额、适用于所有用户或可与其他优惠叠加。来源和活动规则以商家、银行、政府及支付平台官方页面为准。历史归档按采集日期保存；过往记录不回填或伪装成当前优惠。每天 10:30（北京时间）自动采集，运行时间可能延迟。</footer>\n<script id=\"catalog\" type=\"application/json\">__CATALOG__</script>\n<script>\nconst SCENES=[\n {id:'scene-life',name:'外卖、本地生活与餐饮',aliases:['外卖、本地生活与餐饮','外卖餐饮与本地生活','本地生活']},\n {id:'scene-shopping',name:'电商购物',aliases:['电商购物']},\n {id:'scene-travel',name:'出行、旅游与酒店',aliases:['出行、旅游与酒店','出行旅游与酒店']},\n {id:'scene-membership',name:'会员订阅与综合补贴',aliases:['会员订阅与综合补贴','会员订阅与支付']},\n {id:'scene-credit',name:'信用卡优惠',aliases:['信用卡优惠']}\n];\nconst data=JSON.parse(document.getElementById('catalog').textContent),days=data.days.map(x=>x.date);\nconst date=document.getElementById('date'),search=document.getElementById('search'),category=document.getElementById('category'),root=document.getElementById('offers'),count=document.getElementById('count'),more=document.getElementById('more');\nconst topRoot=document.getElementById('top10'),deadlineRoot=document.getElementById('deadlines');\ndate.add(new Option('全部历史','all'));for(const day of data.days)date.add(new Option(day.date+' · '+day.count+' 条',day.date));\nconst params=new URLSearchParams(location.search),requested=params.get('date');date.value=requested==='all'||days.includes(requested)?requested:(days[0]||'all');search.value=params.get('q')||'';\nlet matches=[],shown=0;const pageSize=60;\nfunction text(p,t,v,c){const n=document.createElement(t);n.textContent=v;if(c)n.className=c;p.append(n);return n}\nfunction sceneName(o){const s=o.scene||o.category||'';return SCENES.find(x=>x.aliases.includes(s))?.name||s||'其他线索'}\nfunction amount(o){return o.deal_amount||null}\nfunction amountScore(o){const m=(amount(o)||'').match(/\\d+(?:\\.\\d+)?/g);return m?Math.max(...m.map(Number)):0}\nfunction deadline(o){const v=o.valid_until;if(!v)return null;const n=new Date(v+'T23:59:59+08:00').getTime();return Number.isFinite(n)?{date:v,time:n}:null}\nfunction expiryClass(o){const d=deadline(o);if(!d)return [o.valid_until_note||'有效期未注明','expiry'];const left=Math.ceil((d.time-Date.now())/86400000);return left<0?['已截止','expiry expired']:left<=3?['即将截止','expiry soon']:['长期有效','expiry long']}\nfunction sourceLink(o){return o.source_url||o.link||'#'}\nfunction renderTop(items){topRoot.replaceChildren();if(!items.length){text(topRoot,'div','暂无标题明确写出优惠数字的线索。','empty');return}items.slice(0,10).forEach((o,i)=>{const row=document.createElement('div');row.className='top-item';text(row,'div',String(i+1).padStart(2,'0'),'rank');const a=document.createElement('a');a.href=sourceLink(o);a.target='_blank';a.rel='noopener noreferrer';text(a,'strong',amount(o)||'力度见原文');text(a,'span',(o.company||o.platform||'平台未注明')+' · '+o.title);row.append(a);topRoot.append(row)})}\nfunction renderDeadlines(items){deadlineRoot.replaceChildren();const now=Date.now();const soon=items.filter(o=>{const d=deadline(o);return d&&d.time>=now&&d.time-now<=3*86400000}).sort((a,b)=>deadline(a).time-deadline(b).time).slice(0,10);if(!soon.length){text(deadlineRoot,'div','当前归档没有来源明确写出、且 3 天内截止的活动。','empty');return}for(const o of soon){const row=document.createElement('div');row.className='deadline-item';const a=document.createElement('a');a.href=sourceLink(o);a.target='_blank';a.rel='noopener noreferrer';text(a,'strong',(o.valid_until||'即将截止')+' · '+(amount(o)||'优惠线索'));text(a,'span',(o.company||o.platform)+' · '+o.title);row.append(a);deadlineRoot.append(row)}}\nfunction offerCard(o){const card=document.createElement('article');card.className='card';const main=document.createElement('div');const badges=text(main,'div','','badges');text(badges,'span',o.snapshot_date||o.date||'历史线索','badge');text(badges,'span',o.company||o.platform||'平台未注明','badge');text(badges,'span',o.verify_status||'公开报道线索','badge verify');text(main,'h3',o.title);const src=text(main,'div','','source');src.append(document.createTextNode((o.source_name||o.publisher||'来源未注明')+' · '+(o.published_at||o.published||'发布日期未注明')+'　'));const a=document.createElement('a');a.href=sourceLink(o);a.target='_blank';a.rel='noopener noreferrer';a.textContent='查看来源';src.append(a);if(o.summary)text(main,'p',o.summary,'blurb');const details=document.createElement('div');details.className='details';text(details,'div',amount(o)||'优惠力度未注明',amount(o)?'amount':'amount unknown');text(details,'div','门槛：'+(o.threshold||'报道未说明'));const [exp,cls]=expiryClass(o);text(details,'div',exp,cls);text(details,'div','地区：'+(o.region||'报道未说明'));text(details,'div','领取：'+(o.entry||'报道未说明'));text(details,'div','使用：'+(o.how_to_use||'报道未说明'));card.append(main,details);return card}\nfunction draw(){const part=matches.slice(shown,shown+pageSize),groups=new Map();for(const s of SCENES){const rows=part.filter(o=>s.aliases.includes(o.scene||o.category));groups.set(s.name,rows)}const known=new Set(SCENES.flatMap(s=>s.aliases));for(const o of part){const n=sceneName(o);if(!known.has(o.scene||o.category)&&n!=='其他线索'){if(!groups.has(n))groups.set(n,[]);groups.get(n).push(o)}}for(const [name,items] of groups){const spec=SCENES.find(x=>x.name===name);const section=document.createElement('section');section.className='group';section.id=spec?spec.id:'';const heading=document.createElement('h2');heading.append(document.createTextNode(name));text(heading,'small',items.length+' 条');section.append(heading);if(items.length){for(const o of items)section.append(offerCard(o))}else text(section,'div','这一天暂未采到符合条件的线索；不以过期活动填充。','empty scene-empty');root.append(section)}shown+=part.length;more.hidden=shown>=matches.length}\nfunction update(){const selected=date.value,q=search.value.trim().toLocaleLowerCase();matches=data.offers.filter(x=>(selected==='all'||(x.snapshot_date||x.date)===selected)&&(!category.value||sceneName(x)===category.value)&&(!q||[x.company,x.platform,x.title,x.publisher,x.source_name,x.scene,x.category,x.summary].filter(Boolean).join(' ').toLocaleLowerCase().includes(q)));root.replaceChildren();shown=0;count.textContent=(selected==='all'?'全部 '+days.length+' 天':'采集于 '+selected)+' · 找到 '+matches.length+' 条线索';if(matches.length)draw();else text(root,'div','当前没有符合条件的线索。试试切换日期或场景。','empty');renderTop(matches.filter(o=>amount(o)&&expiryClass(o)[0]!=='已截止').sort((a,b)=>amountScore(b)-amountScore(a)));renderDeadlines(matches);const i=days.indexOf(selected);document.getElementById('older').disabled=i<0||i>=days.length-1;document.getElementById('newer').disabled=i<=0;const url=new URL(location.href);url.searchParams.set('date',selected);q?url.searchParams.set('q',search.value.trim()):url.searchParams.delete('q');history.replaceState(null,'',url)}\ndate.addEventListener('change',update);search.addEventListener('input',update);category.addEventListener('change',update);more.addEventListener('click',draw);document.getElementById('older').addEventListener('click',()=>{date.value=days[days.indexOf(date.value)+1];update()});document.getElementById('newer').addEventListener('click',()=>{date.value=days[days.indexOf(date.value)-1];update()});update();\n</script></body></html>"

def render(payload, catalog):
    """Render the shared page template for both the live page and daily runs."""
    embedded = json.dumps(catalog, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    return PAGE_TEMPLATE.replace("__DATE__", html.escape(payload["date"])).replace(
        "__COUNT__", str(len(payload["offers"]))
    ).replace("__CATALOG__", embedded)



def main():
    parser = argparse.ArgumentParser(description="采集 20 家平台近期公开优惠线索")
    parser.add_argument("--output", type=Path, default=Path("index.html"))
    parser.add_argument("--json-output", type=Path, default=Path("data/latest.json"))
    args = parser.parse_args()
    today = dt.datetime.now(CST).date()
    offers, errors = collect(today)
    payload = {"date": today.isoformat(), "timezone": "Asia/Shanghai", "source": "Google News RSS", "offers": offers, "errors": errors}
    archive_dir = args.json_output.parent / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    (archive_dir / f"{today.isoformat()}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    catalog = build_catalog(archive_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(payload, catalog), encoding="utf-8")
    args.json_output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.json_output.parent / "catalog.json").write_text(json.dumps(catalog, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"{today}: {len(offers)} 条线索，{len(errors)} 个平台采集失败")
    for error in errors:
        print(error, file=sys.stderr)


if __name__ == "__main__":
    main()

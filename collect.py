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
        key = (item["platform"].casefold(), re.sub(r"\s+", "", item["title"]).casefold())
        similar = not item["source_type"].startswith("豆包") and any(
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


PAGE_TEMPLATE = Path(__file__).with_name("page_template.html").read_text(encoding="utf-8")

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

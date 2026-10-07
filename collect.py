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


def extract_deal_amount(title):
    """Return only an explicit discount phrase; never infer missing offer details."""
    patterns = (
        r"满\s*\d+(?:\.\d+)?\s*元?\s*减\s*\d+(?:\.\d+)?\s*元?",
        r"(?:至高|最高)?\s*(?:补贴|立减|优惠)\s*(?:至高|最高)?\s*\d+(?:\.\d+)?\s*(?:元|%)",
        r"\d+(?:\.\d+)?\s*%\s*补贴",
        r"\d+(?:\.\d+)?\s*折",
    )
    for pattern in patterns:
        match = re.search(pattern, title)
        if match:
            return re.sub(r"\s+", "", match.group(0))
    return None


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
        # Preserve headline/source fields while exposing unknown details honestly.
        "date": today.isoformat(),
        "company": platform,
        "scene": category,
        "source_url": link,
        "source_name": publisher,
        "published_at": local_date.isoformat(),
        "deal_amount": extract_deal_amount(title),
        "threshold": None,
        "valid_until": None,
        "region": None,
        "entry": None,
        "how_to_use": None,
        "verify_status": "一方称",
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


PAGE_TEMPLATE = "<!doctype html>\n<html lang=\"zh-CN\">\n<head>\n<meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">\n<meta name=\"theme-color\" content=\"#edf2f4\">\n<title>每日优惠情报</title>\n<style>\n:root{color-scheme:light;--ink:#20343b;--muted:#667b80;--line:#d8e2e3;--paper:#f1f5f5;--card:#fff;--teal:#176b68;--teal-soft:#e5f1ef;--amber:#9a5c19;--amber-soft:#fff3df;--red:#a44242;--red-soft:#fff0ef;--green:#306b4e;--green-soft:#eaf4eb;font-family:Inter,\"PingFang SC\",\"Microsoft YaHei\",system-ui,sans-serif;background:var(--paper);color:var(--ink)}\n*{box-sizing:border-box}body{margin:0;line-height:1.55}a{color:inherit}.shell{max-width:1280px;margin:auto;padding:24px 32px 44px}\n.top{display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid var(--line);padding-bottom:14px}.brand{font-weight:750}.brand small{font-size:13px;font-weight:500;color:var(--muted);margin-left:10px}.nav a{padding:8px 12px;text-decoration:none;color:var(--muted);font-size:14px;border-radius:6px}.nav a[aria-current]{background:#dcebea;color:var(--teal);font-weight:650}\n.intro{display:flex;justify-content:space-between;align-items:end;gap:20px;padding:30px 0 20px}h1{font-size:clamp(30px,4vw,43px);line-height:1.15;letter-spacing:-.035em;margin:0 0 9px}.intro p{margin:0;color:var(--muted)}.stamp{text-align:right;color:var(--muted);font-size:13px;white-space:nowrap}\n.note{border-left:3px solid #78a8a4;background:#e6efef;padding:11px 15px;color:#435c60;font-size:13px;margin-bottom:20px}\n.filters{display:grid;grid-template-columns:auto 1fr minmax(180px,.5fr);gap:9px;margin-bottom:22px}.step{display:flex;gap:7px}.control,button{font:inherit;color:var(--ink);background:var(--card);border:1px solid var(--line);border-radius:6px;padding:9px 11px;min-height:41px}button{cursor:pointer}.control:focus-visible,button:focus-visible,a:focus-visible{outline:3px solid #61a9a4;outline-offset:2px}.filters input{width:100%}\n.columns{display:grid;grid-template-columns:minmax(0,1fr) 285px;gap:22px;align-items:start}.summary{font-size:14px;color:var(--muted);margin-bottom:12px}.group{margin-bottom:20px}.group h2{font-size:17px;margin:0;padding:0 0 7px;border-bottom:1px solid var(--line)}.card{display:grid;grid-template-columns:minmax(0,1fr) 200px;gap:17px;background:var(--card);border:1px solid var(--line);border-radius:8px;padding:16px 18px;margin-top:9px}.badges{display:flex;flex-wrap:wrap;gap:6px}.badge{font-size:12px;color:#53696e;background:#edf2f2;padding:3px 7px;border-radius:4px}.badge.verify{background:var(--amber-soft);color:var(--amber)}h3{font-size:16px;line-height:1.55;margin:9px 0 5px}.source{font-size:13px;color:var(--muted)}.source a{color:var(--teal);text-decoration:none;font-weight:650}.source a:hover{text-decoration:underline}\n.details{display:flex;flex-direction:column;gap:5px;border-left:1px solid var(--line);padding-left:16px;font-size:13px;color:#53696e}.amount{font-size:17px;font-weight:750;color:var(--teal)}.unknown{font-size:13px;font-weight:600;color:var(--muted)}.expiry{width:max-content;padding:2px 6px;border-radius:4px}.soon{color:var(--red);background:var(--red-soft)}.long{color:var(--green);background:var(--green-soft)}\n.aside{position:sticky;top:14px;background:#e8f0f0;border:1px solid var(--line);border-radius:8px;padding:15px}.aside h2{font-size:16px;margin:0 0 4px}.aside p{font-size:12px;color:var(--muted);margin:0 0 10px}.pick{display:block;border-top:1px solid #d2dede;padding:10px 0;text-decoration:none}.pick strong{display:block;color:var(--teal);font-size:14px}.pick span{font-size:12px;color:var(--muted)}\n.empty{background:var(--card);border:1px dashed #c8d6d8;border-radius:8px;padding:24px;color:var(--muted)}#more{display:block;margin:17px auto}[hidden]{display:none!important}footer{border-top:1px solid var(--line);margin-top:25px;padding-top:14px;color:var(--muted);font-size:12px}\n@media(max-width:820px){.shell{padding:19px}.columns{grid-template-columns:1fr}.aside{position:static;grid-row:1}.filters{grid-template-columns:1fr 1fr}.step{grid-column:1/-1}.step button{flex:1}}\n@media(max-width:560px){.shell{padding:13px}.brand small{display:block;margin:2px 0}.intro{display:block;padding:23px 0 16px}.stamp{text-align:left;margin-top:8px}.filters{grid-template-columns:1fr 1fr}.filters input{grid-column:1/-1}.card{grid-template-columns:1fr;gap:11px;padding:14px}.details{border-left:0;border-top:1px solid var(--line);padding:10px 0 0}}\n</style>\n</head><body><main class=\"shell\">\n<div class=\"top\"><div class=\"brand\">每日情报 <small>线索清楚，规则不猜</small></div><nav class=\"nav\"><a href=\"https://dawanglin.github.io/news-daily/\">新闻</a><a href=\"https://dawanglin.github.io/youhui-pages/\" aria-current=\"page\">优惠</a></nav></div>\n<section class=\"intro\"><div><h1>优惠情报</h1><p>先看力度与适用条件，再决定要不要打开活动页。</p></div><div class=\"stamp\">最新采集 __DATE__（北京时间）<br>当日 __COUNT__ 条线索</div></section>\n<div class=\"note\"><strong>请先核对：</strong>当前采集来自公开报道 RSS，并未逐条打开商家 App 核验。只展示标题明确写出的优惠力度；门槛、地区、有效期和领取步骤未说明时会留空提示。实际规则以平台官方页面为准。</div>\n<div class=\"filters\"><div class=\"step\"><button id=\"older\" type=\"button\">← 较早</button><button id=\"newer\" type=\"button\">较新 →</button></div><select class=\"control\" id=\"date\" aria-label=\"选择采集日期\"></select><input class=\"control\" id=\"search\" type=\"search\" placeholder=\"搜索平台、内容或来源\"><select class=\"control\" id=\"category\"><option value=\"\">全部场景</option><option>外卖餐饮与本地生活</option><option>电商购物</option><option>出行旅游与酒店</option><option>会员订阅与支付</option><option>本地生活</option></select></div>\n<div class=\"columns\"><section><div class=\"summary\" id=\"count\" aria-live=\"polite\"></div><div id=\"offers\"></div><button id=\"more\" type=\"button\" hidden>显示更多</button></section><aside class=\"aside\"><h2>标题中明确写出力度</h2><p>仅作线索展示，不是推荐排名，也不代表仍可领取。</p><div id=\"picks\"></div></aside></div>\n<footer>公开报道线索不等于官方领取入口。打开来源和平台规则核实活动期限、资格及适用范围。历史归档按采集日保存，不回填或改写旧记录。每天 10:30（北京时间）采集。</footer>\n<script id=\"catalog\" type=\"application/json\">__CATALOG__</script>\n<script>\nconst data=JSON.parse(document.getElementById('catalog').textContent),days=data.days.map(x=>x.date);\nconst date=document.getElementById('date'),search=document.getElementById('search'),category=document.getElementById('category'),root=document.getElementById('offers'),count=document.getElementById('count'),more=document.getElementById('more'),picks=document.getElementById('picks');\ndate.add(new Option('全部历史','all'));for(const day of data.days)date.add(new Option(day.date+' · '+day.count+' 条',day.date));\nconst params=new URLSearchParams(location.search),requested=params.get('date');date.value=requested==='all'||days.includes(requested)?requested:(days[0]||'all');search.value=params.get('q')||'';\nlet matches=[],shown=0;const pageSize=60;\nfunction text(p,t,v,c){const n=document.createElement(t);n.textContent=v;if(c)n.className=c;p.append(n);return n}\nfunction missing(o,k){return o[k]||'报道未说明'}\nfunction offerCard(o){const card=document.createElement('article');card.className='card';const main=document.createElement('div');const badges=text(main,'div','','badges');text(badges,'span',o.snapshot_date||o.date||'历史线索','badge');text(badges,'span',o.company||o.platform||'平台未注明','badge');text(badges,'span',o.verify_status||'公开报道线索','badge verify');text(main,'h3',o.title);const src=text(main,'div','','source');src.append(document.createTextNode((o.source_name||o.publisher||'来源未注明')+' · '+(o.published_at||o.published||'发布日期未注明')+'　'));const a=document.createElement('a');a.href=o.source_url||o.link||'#';a.target='_blank';a.rel='noopener noreferrer';a.textContent='查看来源';src.append(a);const details=document.createElement('div');details.className='details';const amount=o.deal_amount;text(details,'div',amount||'优惠力度未注明',amount?'amount':'amount unknown');text(details,'div','门槛：'+missing(o,'threshold'));const exp=o.valid_until;if(!exp)text(details,'div','有效期未注明','expiry');else{const remaining=Math.ceil((new Date(exp+'T23:59:59+08:00')-new Date())/86400000);text(details,'div',remaining<=3?'即将截止':'长期有效','expiry '+(remaining<=3?'soon':'long'))}text(details,'div','地区：'+missing(o,'region'));text(details,'div','领取：'+missing(o,'entry'));text(details,'div','使用：'+missing(o,'how_to_use'));card.append(main,details);return card}\nfunction draw(){const part=matches.slice(shown,shown+pageSize),groups=new Map();for(const o of part){const k=o.scene||o.category||'其他线索';if(!groups.has(k))groups.set(k,[]);groups.get(k).push(o)}for(const [name,items] of groups){const section=document.createElement('section');section.className='group';text(section,'h2',name+'　'+items.length+' 条');for(const o of items)section.append(offerCard(o));root.append(section)}shown+=part.length;more.hidden=shown>=matches.length}\nfunction update(){const selected=date.value,q=search.value.trim().toLocaleLowerCase();matches=data.offers.filter(x=>(selected==='all'||(x.snapshot_date||x.date)===selected)&&(!category.value||(x.scene||x.category)===category.value)&&(!q||[x.company,x.platform,x.title,x.publisher,x.source_name,x.scene,x.category].filter(Boolean).join(' ').toLocaleLowerCase().includes(q)));root.replaceChildren();shown=0;count.textContent=(selected==='all'?'全部 '+days.length+' 天':'采集于 '+selected)+' · 找到 '+matches.length+' 条线索';if(matches.length)draw();else text(root,'div','当前没有符合条件的线索。试试切换日期或场景。','empty');const noted=matches.filter(x=>x.deal_amount||/(满\\s*\\d+\\s*减\\s*\\d+|(?:至高|最高)?\\s*(?:补贴|立减|优惠)\\s*\\d+(?:\\.\\d+)?\\s*(?:元|%)|\\d+(?:\\.\\d+)?\\s*折)/.test(x.title||'')).slice(0,5);picks.replaceChildren();if(noted.length){for(const o of noted){const a=document.createElement('a');a.className='pick';a.href=o.source_url||o.link||'#';a.target='_blank';a.rel='noopener noreferrer';text(a,'strong',o.deal_amount||'查看报道中的力度');text(a,'span',(o.company||o.platform)+' · '+o.title);picks.append(a)}}else text(picks,'div','这一天没有标题明确写出优惠数字的线索。','source');const i=days.indexOf(selected);document.getElementById('older').disabled=i<0||i>=days.length-1;document.getElementById('newer').disabled=i<=0;const url=new URL(location.href);url.searchParams.set('date',selected);q?url.searchParams.set('q',search.value.trim()):url.searchParams.delete('q');history.replaceState(null,'',url)}\ndate.addEventListener('change',update);search.addEventListener('input',update);category.addEventListener('change',update);more.addEventListener('click',draw);document.getElementById('older').addEventListener('click',()=>{date.value=days[days.indexOf(date.value)+1];update()});document.getElementById('newer').addEventListener('click',()=>{date.value=days[days.indexOf(date.value)-1];update()});update();\n</script></body></html>"

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

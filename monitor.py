"""
에이블씨엔씨 뉴스 모니터링 시스템 v2
수집(네이버 API + 구글 RSS + 다음 RSS) → 분류(Claude) → 발송(이메일)
GitHub Actions에서 평일 아침 자동 실행

카테고리:
  - 자사 배포: 에이블씨엔씨/미샤가 주요 소재인 기사
  - 자사 언급: 에이블씨엔씨/미샤가 짧게 언급된 기사
  - 경쟁사: 달바글로벌, 스킨푸드, 아모레퍼시픽, LG생활건강 등
  - 업계: K뷰티, 화장품 업계 전반
"""

import os
import json
import hashlib
import re
import datetime
import smtplib
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from html import unescape

# ─── 환경 변수 ───
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
NAVER_CLIENT_ID = os.environ.get("NAVER_CLIENT_ID", "")
NAVER_CLIENT_SECRET = os.environ.get("NAVER_CLIENT_SECRET", "")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD", "")
EMAIL_FROM = os.environ.get("EMAIL_FROM", "")
EMAIL_TO = os.environ.get("EMAIL_TO", "")

# ─── 키워드 설정 ───
COMPANY_KEYWORDS = ["에이블씨엔씨", "미샤", "MISSHA", "어퓨", "초공진"]
COMPETITOR_KEYWORDS = ["달바글로벌", "스킨푸드", "아모레퍼시픽", "LG생활건강", "에이피알"]
INDUSTRY_KEYWORDS = ["K뷰티", "K-뷰티", "화장품 수출", "뷰티 업계"]

HISTORY_FILE = "sent_history.json"


# ═══════════════════════════════════════════
# 1단계: 수집
# ═══════════════════════════════════════════

def collect_naver_api(keyword: str, display: int = 30) -> list[dict]:
    """네이버 뉴스 검색 API로 기사를 수집한다."""
    if not NAVER_CLIENT_ID:
        return []

    encoded = urllib.parse.quote(keyword)
    url = f"https://openapi.naver.com/v1/search/news.json?query={encoded}&display={display}&sort=date"
    req = urllib.request.Request(url)
    req.add_header("X-Naver-Client-Id", NAVER_CLIENT_ID)
    req.add_header("X-Naver-Client-Secret", NAVER_CLIENT_SECRET)

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        articles = []
        for item in data.get("items", []):
            title = _strip_html(item.get("title", ""))
            link = item.get("originallink", "") or item.get("link", "")
            desc = _strip_html(item.get("description", ""))
            pub_date = item.get("pubDate", "")
            articles.append({
                "title": title,
                "link": link,
                "description": desc,
                "pub_date": pub_date,
                "source": "naver",
                "search_keyword": keyword,
            })
        return articles
    except Exception as e:
        print(f"[수집] 네이버 API 오류 ({keyword}): {e}")
        return []


def collect_google_rss(keyword: str) -> list[dict]:
    """구글 뉴스 RSS로 기사를 수집한다."""
    encoded = urllib.parse.quote(keyword)
    url = f"https://news.google.com/rss/search?q={encoded}+when:1d&hl=ko&gl=KR&ceid=KR:ko"
    return _fetch_rss(url, keyword, "google")


def collect_daum_rss(keyword: str) -> list[dict]:
    """다음 뉴스를 구글 RSS 경유로 수집한다."""
    encoded = urllib.parse.quote(keyword)
    url = f"https://news.google.com/rss/search?q={encoded}+site:v.daum.net+when:1d&hl=ko&gl=KR&ceid=KR:ko"
    return _fetch_rss(url, keyword, "daum")


def _fetch_rss(url: str, keyword: str, source: str) -> list[dict]:
    """RSS URL에서 기사를 가져온다."""
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "Mozilla/5.0")

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            root = ET.fromstring(resp.read())
        articles = []
        for item in root.findall(".//item"):
            title = item.findtext("title", "")
            link = item.findtext("link", "")
            pub_date = item.findtext("pubDate", "")
            src = item.findtext("source", "") or source
            articles.append({
                "title": title,
                "link": link,
                "description": "",
                "pub_date": pub_date,
                "source": src,
                "search_keyword": keyword,
            })
        return articles
    except Exception as e:
        print(f"[수집] {source} RSS 오류 ({keyword}): {e}")
        return []


def collect_all() -> list[dict]:
    """모든 소스에서 기사를 수집하고 중복을 제거한다."""
    all_articles = []
    all_keywords = COMPANY_KEYWORDS + COMPETITOR_KEYWORDS + INDUSTRY_KEYWORDS

    for kw in all_keywords:
        naver = collect_naver_api(kw)
        all_articles.extend(naver)
        google = collect_google_rss(kw)
        all_articles.extend(google)
        daum = collect_daum_rss(kw)
        all_articles.extend(daum)
        total_kw = len(naver) + len(google) + len(daum)
        print(f"[수집] '{kw}': 네이버 {len(naver)} + 구글 {len(google)} + 다음 {len(daum)} = {total_kw}건")

    # 중복 제거 (제목 기준 해시)
    seen = set()
    unique = []
    for a in all_articles:
        key = _article_hash(a["title"])
        if key not in seen:
            seen.add(key)
            unique.append(a)

    print(f"[수집] 총 {len(all_articles)}건 → 중복 제거 후 {len(unique)}건")
    return unique


# ═══════════════════════════════════════════
# 2단계: 분류 (Claude)
# ═══════════════════════════════════════════

def classify_articles(articles: list[dict]) -> dict:
    """Claude에게 기사를 보내 4개 카테고리로 분류한다. 30건씩 배치 처리."""
    categorized = {"자사배포": [], "자사언급": [], "경쟁사": [], "업계": []}
    if not articles:
        return categorized

    # 이미 보낸 기사 제외
    history = _load_history()
    new_articles = [a for a in articles if _article_hash(a["title"]) not in history]

    if not new_articles:
        print("[분류] 새 기사 없음")
        return categorized

    new_articles = new_articles[:120]
    print(f"[분류] {len(new_articles)}건 분석 시작")

    # 30건씩 배치 처리
    BATCH = 30
    for batch_start in range(0, len(new_articles), BATCH):
        batch = new_articles[batch_start:batch_start + BATCH]
        batch_num = batch_start // BATCH + 1
        print(f"[분류] 배치 {batch_num}: {len(batch)}건")

        result = _classify_batch(batch)
        for cat in categorized:
            categorized[cat].extend(result.get(cat, []))

    for cat, arts in categorized.items():
        print(f"[분류] {cat}: {len(arts)}건")
    return categorized


def _classify_batch(batch: list[dict]) -> dict:
    """기사 배치 하나를 Claude로 분류한다."""
    article_text = ""
    for i, a in enumerate(batch, 1):
        desc = a.get("description", "")[:80]
        article_text += f"[{i}] {a['title']} | 출처: {a['source']} | 검색어: {a['search_keyword']}"
        if desc:
            article_text += f" | {desc}"
        article_text += "\n"

    prompt = f"""다음은 화장품·뷰티 업계 뉴스 기사 목록입니다.

{article_text}

각 기사를 아래 5개 카테고리 중 하나로 분류하세요:
- 자사배포: 에이블씨엔씨·미샤(MISSHA)·어퓨·초공진이 기사의 주요 소재
- 자사언급: 에이블씨엔씨·미샤가 여러 브랜드 중 하나로 짧게 언급
- 경쟁사: 달바글로벌·스킨푸드·아모레퍼시픽·LG생활건강·에이피알 등이 주요 소재
- 업계: K뷰티·화장품 수출·뷰티 트렌드 등 업계 전반
- 제외: 화장품과 무관, 동명이인(첼리스트 미샤 마이스키 등), 단순 광고

감성(긍정/부정/중립)과 한 줄 요약을 포함해 JSON 배열로만 응답하세요. 다른 텍스트 없이 JSON만:
[{{"index":1,"category":"자사배포","sentiment":"긍정","summary":"요약"}}]"""

    try:
        result = _call_claude(prompt)
        start = result.find("[")
        end = result.rfind("]") + 1
        if start < 0 or end <= start:
            print(f"[분류] 배치 JSON 파싱 실패")
            return {}

        analyses = json.loads(result[start:end])
        cat_result = {"자사배포": [], "자사언급": [], "경쟁사": [], "업계": []}

        for item in analyses:
            idx = item.get("index", 0) - 1
            cat = item.get("category", "제외")
            if 0 <= idx < len(batch) and cat in cat_result:
                article = batch[idx].copy()
                article["sentiment"] = item.get("sentiment", "중립")
                article["summary"] = item.get("summary", "")
                cat_result[cat].append(article)

        return cat_result

    except Exception as e:
        print(f"[분류] 배치 오류: {e}")
        return {}


# ═══════════════════════════════════════════
# 3단계: 발송
# ═══════════════════════════════════════════

def send_report(categorized: dict) -> bool:
    """분류된 기사를 HTML 이메일로 발송한다."""
    today = datetime.date.today().strftime("%Y-%m-%d")
    weekday = ["월", "화", "수", "목", "금", "토", "일"][datetime.date.today().weekday()]
    total = sum(len(v) for v in categorized.values())

    summary_text = _generate_summary(categorized) if total > 0 else ""

    html = _build_html(today, weekday, total, summary_text, categorized)

    parts = []
    for k, v in categorized.items():
        if v:
            parts.append(f"{k} {len(v)}")
    detail = ", ".join(parts) if parts else "기사 없음"
    subject = f"[에이블씨엔씨] 뉴스 모니터링 — {today} ({weekday}) | {detail}"

    success = _send_email(html, subject)
    if success:
        all_arts = [a for v in categorized.values() for a in v]
        _save_history(all_arts)
    return success


def _build_html(today, weekday, total, summary_text, categorized):
    """이메일 HTML 본문을 생성한다."""
    html = f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8">
<title>ABLE C&amp;C DAILY MONITORING {today}</title></head>
<body style="margin:0;padding:0;background-color:#FAF6F1;">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#FAF6F1;">
<tr><td align="center" style="padding:16px;">
<table width="640" cellpadding="0" cellspacing="0" border="0" style="width:640px;max-width:640px;font-family:'Malgun Gothic','Apple SD Gothic Neo',sans-serif;color:#2B2620;">

<tr><td style="padding:2px 4px 12px 4px;border-bottom:3px solid #E8730C;">
<div style="font-size:27px;font-weight:bold;color:#2B2620;letter-spacing:.3px;">ABLE C&amp;C · DAILY MONITORING</div>
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:9px;">
<tr><td style="vertical-align:bottom;">
<span style="font-size:20px;font-weight:bold;color:#C25A00;">{today.replace('-', '. ')} ({weekday})</span>
</td><td align="right" style="vertical-align:bottom;font-size:12.5px;color:#857B6F;font-weight:bold;">
GitHub Actions 자동 수집 · AI 분류
</td></tr></table>
</td></tr>"""

    if total == 0:
        html += """<tr><td style="padding:20px 0;">
<div style="font-size:14px;color:#666;text-align:center;">오늘 유의미한 뉴스가 없습니다.</div>
</td></tr>"""
    else:
        if summary_text:
            html += f"""<tr><td style="padding:14px 0 0 0;">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#FFF7EC;border:1px solid #F3C79B;">
<tr><td style="padding:16px 18px;">
<div style="font-size:16px;font-weight:bold;color:#2B2620;padding-bottom:8px;">Today Summary</div>
<div style="font-size:13.5px;color:#4a4036;line-height:1.7;">{summary_text}</div>
</td></tr></table></td></tr>"""

        sections = [
            ("자사배포", "1", "자사 배포"),
            ("자사언급", "2", "자사 언급"),
            ("경쟁사", "3", "경쟁사"),
            ("업계", "4", "업계·트렌드"),
        ]

        for cat_key, num, label in sections:
            arts = categorized[cat_key]
            if not arts:
                continue
            html += _build_section(num, f"{label} ({len(arts)}건)", arts)

    html += f"""<tr><td style="padding:16px 0 0 0;">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-top:1px solid #E5DACB;">
<tr><td align="center" style="padding:12px 15px;font-size:11.5px;color:#857B6F;">
Monitoring by <b style="color:#2B2620;">프라우드컴(주)</b> · Copyright 2026 Proudcomm<br>
<span style="font-size:10px;color:#aaa;">네이버 API + 구글 RSS + 다음 | Claude AI 분류</span>
</td></tr></table></td></tr>

</table></td></tr></table></body></html>"""
    return html


def _build_section(num: str, label: str, articles: list[dict]) -> str:
    """하나의 카테고리 섹션 HTML을 만든다."""
    s = f"""<tr><td style="padding:14px 0 0 0;">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#FFFFFF;border:1px solid #ECE3D8;">
<tr><td style="padding:16px 18px;">
<div style="font-size:16px;font-weight:bold;color:#2B2620;padding-bottom:6px;">
<span style="color:#E8730C;">{num}</span> &nbsp;&nbsp;{label}</div>"""

    sentiment_colors = {"긍정": "#28a745", "부정": "#dc3545", "중립": "#6c757d"}

    for a in articles:
        sentiment = a.get("sentiment", "중립")
        color = sentiment_colors.get(sentiment, "#6c757d")
        title = a["title"]
        link = a["link"]
        source = a["source"]
        summary = a.get("summary", "")

        s += f"""<div style="font-size:14px;padding:7px 0;border-top:1px solid #F2ECE3;">
<span style="display:inline-block;background:{color};color:white;padding:1px 6px;border-radius:3px;font-size:11px;">{sentiment}</span>&nbsp;
<a href="{link}" style="color:#2B2620;text-decoration:none;font-weight:bold;">{title}</a>
<span style="color:#C25A00;font-size:11px;font-weight:bold;"> {source} ›</span>
<br><span style="color:#857B6F;font-size:12px;">{summary}</span></div>"""

    s += "</td></tr></table></td></tr>"
    return s


# ═══════════════════════════════════════════
# 보조 함수
# ═══════════════════════════════════════════

def _strip_html(text: str) -> str:
    clean = re.sub(r"<[^>]+>", "", text)
    return unescape(clean).strip()


def _article_hash(title: str) -> str:
    clean = re.sub(r"^\[.*?\]\s*", "", title)
    clean = re.sub(r"\s+", "", clean)[:50]
    return hashlib.md5(clean.encode("utf-8")).hexdigest()[:12]


def _load_history() -> set:
    if not os.path.exists(HISTORY_FILE):
        return set()
    try:
        with open(HISTORY_FILE, "r") as f:
            data = json.load(f)
        return set(data.get("hashes", []))
    except Exception:
        return set()


def _save_history(articles: list[dict]):
    history = _load_history()
    for a in articles:
        history.add(_article_hash(a["title"]))
    hashes = list(history)[-1000:]
    with open(HISTORY_FILE, "w") as f:
        json.dump({"hashes": hashes, "updated": str(datetime.datetime.now())}, f)


def _call_claude(prompt: str) -> str:
    url = "https://api.anthropic.com/v1/messages"
    payload = json.dumps({
        "model": "claude-haiku-5-5",
        "max_tokens": 8192,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")

    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("x-api-key", ANTHROPIC_API_KEY)
    req.add_header("anthropic-version", "2023-06-01")

    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    for block in data["content"]:
        if block.get("type") == "text":
            return block["text"]
    return data["content"][0].get("text", "")


def _generate_summary(categorized: dict) -> str:
    lines = []
    for cat, articles in categorized.items():
        for a in articles:
            lines.append(f"[{cat}][{a.get('sentiment', '중립')}] {a['title']}: {a.get('summary', '')}")
    if not lines:
        return ""

    prompt = f"""아래는 오늘 에이블씨엔씨 관련 뉴스 모니터링 결과입니다.

{chr(10).join(lines[:50])}

PR 대행사 대표의 관점에서 CEO에게 보고하는 핵심 인사이트를 작성하세요.
- 자사 관련 기사가 있으면 먼저 다루세요
- 경쟁사와 업계 동향을 간략히 짚어주세요
- 3~5줄 분량으로, 흐름과 의미를 짚어주세요
- HTML 형식, <b> 태그로 핵심 강조"""

    try:
        return _call_claude(prompt)
    except Exception as e:
        print(f"[요약] 오류: {e}")
        return ""


def _send_email(html: str, subject: str) -> bool:
    if not EMAIL_FROM or not EMAIL_PASSWORD:
        print("[발송] 이메일 설정 없음")
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = EMAIL_FROM
        msg["To"] = EMAIL_TO
        msg.attach(MIMEText(html, "html", "utf-8"))

        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(EMAIL_FROM, EMAIL_PASSWORD)
            server.send_message(msg)

        print(f"[발송] 완료: {subject}")
        return True
    except Exception as e:
        print(f"[발송] 오류: {e}")
        return False


# ═══════════════════════════════════════════
if __name__ == "__main__":
    print("=" * 60)
    print(f"에이블씨엔씨 뉴스 모니터링 v2 시작: {datetime.datetime.now()}")
    print("=" * 60)

    articles = collect_all()
    categorized = classify_articles(articles)
    send_report(categorized)

    print("=" * 60)
    print(f"완료: {datetime.datetime.now()}")
    print("=" * 60)

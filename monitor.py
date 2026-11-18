"""
에이블씨엔씨 뉴스 모니터링 시스템 v3
가이드 기준: 100+ 키워드, 매체 티어, 대표 1건+외 N건, Google Drive 업로드
GitHub Actions 평일 오전 자동 실행

카테고리:
  - 자사배포: 에이블씨엔씨/미샤 등이 주요 소재인 기사
  - 자사언급: 에이블씨엔씨/미샤 등이 짧게 언급된 기사
  - 경쟁사메이저: 메이저 경쟁사 단일 브랜드 중심 기사
  - 경쟁사인디: 인디 경쟁사 단일 브랜드 중심 기사
  - 업계: K뷰티/화장품 업계 전반, 복수 브랜드 나열 기사
  - 제외: 무관 기사
"""

import os
import sys
import json
import hashlib
import re
import time
import datetime
import smtplib
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from html import unescape

# GitHub Actions에서 print 즉시 출력 (버퍼링 해제)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)

# ─── 환경 변수 ───
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
NAVER_CLIENT_ID = os.environ.get("NAVER_CLIENT_ID", "")
NAVER_CLIENT_SECRET = os.environ.get("NAVER_CLIENT_SECRET", "")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD", "")
EMAIL_FROM = os.environ.get("EMAIL_FROM", "")
EMAIL_TO = os.environ.get("EMAIL_TO", "")
GDRIVE_SERVICE_ACCOUNT = os.environ.get("GDRIVE_SERVICE_ACCOUNT", "")
GDRIVE_FOLDER_ID = os.environ.get("GDRIVE_FOLDER_ID", "")

HISTORY_FILE = "sent_history.json"

# ═══════════════════════════════════════════
# 키워드 설정 (가이드 2장 기준)
# ═══════════════════════════════════════════

COMPANY_KEYWORDS = [
    "에이블씨엔씨", "미샤", "MISSHA", "어퓨", "초공진", "셀라피", "apieu",
]

FASHION_EXCLUDE = [
    "착장", "코트", "하객룩", "하객", "패션", "스타일링", "룩북", "코디",
    "아우터", "원피스", "자켓", "니트", "패딩", "미샤 마이스키",
    "첼리스트", "첼리스트 미샤",
    # 시선인터내셔널 MICHAA (패션 브랜드) — 에이블씨엔씨 MISSHA와 완전 별개
    "시선인터내셔널", "michaa", "미샤 컬렉션", "미샤 fw", "미샤 ss",
    "미샤 패션", "미샤 의류", "미샤 매장",
]

COMPETITOR_MAJOR_KEYWORDS = [
    "아모레퍼시픽", "설화수", "라네즈", "헤라", "프리메라", "아이오페",
    "마몽드", "한율", "비레디", "일리윤", "에스쁘아", "에스트라",
    "코스알엑스", "미쟝센",
    "LG생활건강", "더후", "CNP", "빌리프", "닥터그루트", "VDL",
    "피지오겔", "프라엘",
    "애경산업", "루나", "에이지투웨니스",
    "이니스프리", "더페이스샵", "에뛰드", "네이처리퍼블릭",
    "토니모리",
    "클리오", "트윙클팝", "구달", "페리페라",
    "잇츠한불", "AHC",
]

COMPETITOR_INDIE_KEYWORDS = [
    "에이피알", "메디큐브", "에이프릴스킨",
    "구다이글로벌", "조선미녀", "티르티르", "스킨푸드", "라운드랩", "스킨1004",
    "더파운더즈", "아누아",
    "브이티",
    "달바글로벌",
    "닥터지",
    "아이패밀리에스씨", "롬앤", "누즈",
    "비나우", "넘버즈인", "퓌",
    "아임프롬",
    "엘앤피", "메디힐",
    "올리브인터내셔널", "밀크터치",
    "파켓", "믹순",
    "포컴퍼니", "아비브",
    "토리든",
    "정샘물뷰티",
    "모먼츠", "비플레인",
    "스타일난다", "3CE",
    "브랜드501", "닥터멜락신",
    "바이오던스",
    "바이오힐보",
    "닥터엘시아",
    "아로마티카",
    "톤28",
]

INDUSTRY_KEYWORDS = [
    "화장품", "뷰티", "올리브영", "다이소 뷰티", "무신사 뷰티",
    "레디영약국", "홍익약국", "오프뷰티",
    "코스맥스", "한국콜마", "코스메카",
    "실리콘투",
    "식약처 화장품", "소비자원 화장품",
]

# ═══════════════════════════════════════════
# 매체 티어 (가이드 5장 기준, T1 = 31개)
# ═══════════════════════════════════════════

T1_MEDIA = {
    "연합뉴스", "뉴시스", "뉴스1",
    "조선일보", "중앙일보", "동아일보", "한국일보", "경향신문", "한겨레",
    "매일경제", "한국경제", "서울경제", "이데일리", "헤럴드경제",
    "파이낸셜뉴스", "아시아경제", "머니투데이", "더벨",
    "조선비즈", "중앙일보 조인스", "한경비즈니스",
    "KBS", "MBC", "SBS", "YTN", "JTBC", "MBN", "채널A", "TV조선",
    "연합뉴스TV", "KBS NEWS",
}

def get_media_tier(source_name: str) -> int:
    if not source_name:
        return 1
    for t1 in T1_MEDIA:
        if t1 in source_name or source_name in t1:
            return 5
    return 2


# ═══════════════════════════════════════════
# 1단계: 수집
# ═══════════════════════════════════════════

def collect_naver_api(keyword: str, display: int = 50) -> list[dict]:
    if not NAVER_CLIENT_ID:
        return []
    encoded = urllib.parse.quote(keyword)
    url = f"https://naverapihub.apigw.ntruss.com/search/v1/news?query={encoded}&display={display}&sort=date"
    req = urllib.request.Request(url)
    req.add_header("X-NCP-APIGW-API-KEY-ID", NAVER_CLIENT_ID)
    req.add_header("X-NCP-APIGW-API-KEY", NAVER_CLIENT_SECRET)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        articles = []
        for item in data.get("items", []):
            title = _strip_html(item.get("title", ""))
            link = item.get("originallink", "") or item.get("link", "")
            desc = _strip_html(item.get("description", ""))
            pub_date = item.get("pubDate", "")
            articles.append({
                "title": title, "link": link, "description": desc,
                "pub_date": pub_date, "source": _extract_media_name(link),
                "search_keyword": keyword,
            })
        return articles
    except Exception as e:
        print(f"[수집] 네이버 API 오류 ({keyword}): {e}")
        return []


def collect_google_rss(keyword: str) -> list[dict]:
    encoded = urllib.parse.quote(keyword)
    days = os.environ.get("SEARCH_DAYS", "1")
    url = f"https://news.google.com/rss/search?q={encoded}+when:{days}d&hl=ko&gl=KR&ceid=KR:ko"
    return _fetch_rss(url, keyword, "google")


def collect_daum_rss(keyword: str) -> list[dict]:
    encoded = urllib.parse.quote(keyword)
    days = os.environ.get("SEARCH_DAYS", "1")
    url = f"https://news.google.com/rss/search?q={encoded}+site:v.daum.net+when:{days}d&hl=ko&gl=KR&ceid=KR:ko"
    return _fetch_rss(url, keyword, "daum")


def _fetch_rss(url: str, keyword: str, source: str) -> list[dict]:
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "Mozilla/5.0 (compatible; AbleCNCMonitor/3.0)")
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            root = ET.fromstring(resp.read())
        articles = []
        for item in root.findall(".//item"):
            title = item.findtext("title", "")
            link = item.findtext("link", "")
            pub_date = item.findtext("pubDate", "")
            src = item.findtext("source", "") or source
            articles.append({
                "title": title, "link": link, "description": "",
                "pub_date": pub_date, "source": src,
                "search_keyword": keyword,
            })
        return articles
    except Exception as e:
        print(f"[수집] {source} RSS 오류 ({keyword}): {e}")
        return []


def _extract_media_name(url: str) -> str:
    try:
        from urllib.parse import urlparse
        domain = urlparse(url).netloc
        domain_map = {
            "www.yna.co.kr": "연합뉴스", "www.newsis.com": "뉴시스",
            "www.news1.kr": "뉴스1", "www.chosun.com": "조선일보",
            "www.joongang.co.kr": "중앙일보", "www.donga.com": "동아일보",
            "www.hankookilbo.com": "한국일보", "www.khan.co.kr": "경향신문",
            "www.hani.co.kr": "한겨레", "www.mk.co.kr": "매일경제",
            "www.hankyung.com": "한국경제", "www.sedaily.com": "서울경제",
            "www.edaily.co.kr": "이데일리", "biz.heraldcorp.com": "헤럴드경제",
            "www.fnnews.com": "파이낸셜뉴스", "www.asiae.co.kr": "아시아경제",
            "news.mt.co.kr": "머니투데이", "www.thebell.co.kr": "더벨",
        }
        return domain_map.get(domain, domain.replace("www.", "").split(".")[0])
    except Exception:
        return "unknown"


def collect_all() -> list[dict]:
    all_articles = []
    keyword_groups = [
        ("자사", COMPANY_KEYWORDS),
        ("메이저경쟁사", COMPETITOR_MAJOR_KEYWORDS),
        ("인디경쟁사", COMPETITOR_INDIE_KEYWORDS),
        ("업계", INDUSTRY_KEYWORDS),
    ]
    total_keywords = sum(len(kws) for _, kws in keyword_groups)
    print(f"[수집] 총 {total_keywords}개 키워드 검색 시작")

    # 1차: 네이버 API로 전체 키워드 수집 (메인)
    collect_start = time.time()

    for group_name, keywords in keyword_groups:
        group_count = 0
        group_start = time.time()
        for idx, kw in enumerate(keywords):
            naver = collect_naver_api(kw)
            all_articles.extend(naver)
            group_count += len(naver)
            if (idx + 1) % 10 == 0:
                elapsed = time.time() - group_start
                print(f"[수집] {group_name}: {idx+1}/{len(keywords)} 완료 ({group_count}건, {elapsed:.0f}초)")
            time.sleep(0.15)
        elapsed = time.time() - group_start
        print(f"[수집] {group_name}: {len(keywords)}개 키워드 → {group_count}건 ({elapsed:.0f}초)")

    naver_elapsed = time.time() - collect_start
    naver_count = len(all_articles)
    print(f"[수집] 네이버 완료: {naver_count}건 ({naver_elapsed:.0f}초)")

    # 2차: 자사 키워드만 구글·다음 RSS 추가 (네이버에 없는 기사 보충)
    # RSS 차단 시 자동 스킵 (8초 타임아웃)
    rss_count = 0
    for kw in COMPANY_KEYWORDS:
        google = collect_google_rss(kw)
        daum = collect_daum_rss(kw)
        all_articles.extend(google + daum)
        rss_count += len(google) + len(daum)
        time.sleep(0.2)
    if rss_count > 0:
        print(f"[수집] 구글·다음 RSS (자사 7개): {rss_count}건 추가")
    else:
        print(f"[수집] 구글·다음 RSS: 0건 (차단 또는 결과 없음 — 네이버 단독 진행)")

    total_elapsed = time.time() - collect_start
    print(f"[수집] 전체 수집 완료: {len(all_articles)}건 ({total_elapsed:.0f}초)")

    seen = set()
    unique = []
    for a in all_articles:
        key = _article_hash(a["title"])
        if key not in seen:
            seen.add(key)
            unique.append(a)

    filtered = _filter_fashion_missha(unique)
    print(f"[수집] 총 {len(all_articles)}건 → 중복제거 {len(unique)}건 → 패션필터 후 {len(filtered)}건")
    return filtered


def _filter_fashion_missha(articles: list[dict]) -> list[dict]:
    result = []
    for a in articles:
        if a["search_keyword"] in ("미샤", "MISSHA", "missha"):
            text = (a["title"] + " " + a.get("description", "")).lower()
            if any(excl in text for excl in FASHION_EXCLUDE):
                continue
        result.append(a)
    return result


# ═══════════════════════════════════════════
# 2단계: 분류 (Claude)
# ═══════════════════════════════════════════

def classify_articles(articles: list[dict]) -> dict:
    categorized = {
        "자사배포": [], "자사언급": [],
        "경쟁사메이저": [], "경쟁사인디": [],
        "업계": [],
    }
    if not articles:
        return categorized

    history = _load_history()
    new_articles = [a for a in articles if _article_hash(a["title"]) not in history]
    if not new_articles:
        print("[분류] 새 기사 없음")
        return categorized

    # 키워드 그룹별 균등 배분 (최대 300건)
    MAX_TOTAL = 300
    company_kws = set(COMPANY_KEYWORDS)
    major_kws = set(COMPETITOR_MAJOR_KEYWORDS)
    indie_kws = set(COMPETITOR_INDIE_KEYWORDS)
    industry_kws = set(INDUSTRY_KEYWORDS)

    buckets = {"자사": [], "메이저": [], "인디": [], "업계": []}
    for a in new_articles:
        kw = a["search_keyword"]
        if kw in company_kws:
            buckets["자사"].append(a)
        elif kw in major_kws:
            buckets["메이저"].append(a)
        elif kw in indie_kws:
            buckets["인디"].append(a)
        else:
            buckets["업계"].append(a)

    # 자사 전부 + 나머지 균등
    selected = buckets["자사"][:]
    remaining = MAX_TOTAL - len(selected)
    per_group = remaining // 3
    for group in ["메이저", "인디", "업계"]:
        selected.extend(buckets[group][:per_group])
    new_articles = selected

    for grp, arts in buckets.items():
        print(f"[분류] 버킷 {grp}: {len(arts)}건")
    print(f"[분류] {len(new_articles)}건 분석 시작 (균등 배분)")

    BATCH = 30
    total_batches = (len(new_articles) + BATCH - 1) // BATCH
    for batch_start in range(0, len(new_articles), BATCH):
        batch = new_articles[batch_start:batch_start + BATCH]
        batch_num = batch_start // BATCH + 1
        print(f"[분류] 배치 {batch_num}/{total_batches}: {len(batch)}건")
        result = _classify_batch(batch)
        for cat in categorized:
            categorized[cat].extend(result.get(cat, []))
        time.sleep(0.5)

    for cat, arts in categorized.items():
        print(f"[분류] {cat}: {len(arts)}건")
    return categorized


def _classify_batch(batch: list[dict]) -> dict:
    major_str = ", ".join(COMPETITOR_MAJOR_KEYWORDS[:20]) + " 등"
    indie_str = ", ".join(COMPETITOR_INDIE_KEYWORDS[:20]) + " 등"

    article_text = ""
    for i, a in enumerate(batch, 1):
        desc = a.get("description", "")[:80]
        article_text += f"[{i}] {a['title']} | 매체: {a['source']} | 검색어: {a['search_keyword']}"
        if desc:
            article_text += f" | {desc}"
        article_text += "\n"

    prompt = f"""다음은 화장품·뷰티 업계 뉴스 기사 목록입니다.

{article_text}

각 기사를 아래 6개 카테고리 중 하나로 분류하세요:

- **자사배포**: 에이블씨엔씨·미샤(MISSHA)·어퓨·초공진·셀라피가 기사의 주요 소재
- **자사언급**: 에이블씨엔씨·미샤 등이 본문에서 짧게 거론 (뷰티 맥락일 때만)
- **경쟁사메이저**: 다음 브랜드가 단일 주요 소재인 기사: {major_str}
- **경쟁사인디**: 다음 브랜드가 단일 주요 소재인 기사: {indie_str}
- **업계**: 복수 브랜드 나열 기사, K뷰티·화장품·유통·플랫폼 기사
- **제외**: 화장품과 무관, 패션 맥락의 미샤, 동명이인, 단순 광고

**규칙:**
- 단일 브랜드 중심 기사만 경쟁사로 분류
- 복수 브랜드 나열 → 무조건 업계
- 곁다리 언급 → 제외

JSON 배열로만 응답:
[{{"index":1,"category":"자사배포","sentiment":"긍정","summary":"한줄요약"}}]"""

    try:
        result = _call_claude(prompt)
        start = result.find("[")
        end = result.rfind("]") + 1
        if start < 0 or end <= start:
            return {}
        analyses = json.loads(result[start:end])
        cat_result = {
            "자사배포": [], "자사언급": [],
            "경쟁사메이저": [], "경쟁사인디": [],
            "업계": [],
        }
        for item in analyses:
            idx = item.get("index", 0) - 1
            cat = item.get("category", "제외")
            if 0 <= idx < len(batch) and cat in cat_result:
                article = batch[idx].copy()
                article["sentiment"] = item.get("sentiment", "중립")
                article["summary"] = item.get("summary", "")
                article["tier_score"] = get_media_tier(article["source"])
                cat_result[cat].append(article)
        return cat_result
    except Exception as e:
        print(f"[분류] 배치 오류: {e}")
        return {}


# ═══════════════════════════════════════════
# 2.5단계: 그루핑 (대표 1건 + 외 N건)
# ═══════════════════════════════════════════

def group_similar_articles(categorized: dict) -> dict:
    grouped = {}
    for cat, articles in categorized.items():
        if not articles:
            grouped[cat] = []
            continue
        groups = []
        used = set()
        for i, a in enumerate(articles):
            if i in used:
                continue
            group = [a]
            used.add(i)
            for j, b in enumerate(articles):
                if j in used:
                    continue
                if _is_similar(a["title"], b["title"]):
                    group.append(b)
                    used.add(j)
            group.sort(key=lambda x: x.get("tier_score", 1), reverse=True)
            representative = group[0]
            representative["others_count"] = len(group) - 1
            groups.append(representative)
        grouped[cat] = groups
    return grouped


def _is_similar(title_a: str, title_b: str) -> bool:
    words_a = set(re.findall(r"[가-힣]+", title_a))
    words_b = set(re.findall(r"[가-힣]+", title_b))
    if not words_a or not words_b:
        return False
    overlap = len(words_a & words_b)
    min_len = min(len(words_a), len(words_b))
    if min_len == 0:
        return False
    return overlap / min_len >= 0.6


# ═══════════════════════════════════════════
# 3단계: 발송
# ═══════════════════════════════════════════

def send_report(categorized: dict, grouped: dict) -> bool:
    today = datetime.date.today().strftime("%Y-%m-%d")
    weekday = ["월", "화", "수", "목", "금", "토", "일"][datetime.date.today().weekday()]
    total = sum(len(v) for v in categorized.values())

    summary_text = _generate_summary(categorized) if total > 0 else ""
    html = _build_html(today, weekday, total, summary_text, grouped, categorized)

    parts = []
    label_map = {"자사배포": "배포", "자사언급": "언급", "경쟁사메이저": "메이저",
                 "경쟁사인디": "인디", "업계": "업계"}
    for k, v in categorized.items():
        if v:
            parts.append(f"{label_map.get(k, k)} {len(v)}")
    detail = ", ".join(parts) if parts else "기사 없음"
    subject = f"[에이블씨엔씨] 뉴스 모니터링 — {today} ({weekday}) | {detail}"

    success = _send_email(html, subject)

    if GDRIVE_SERVICE_ACCOUNT and GDRIVE_FOLDER_ID:
        _upload_to_drive(html, f"데일리모니터링_{today}_통합.html")

    # 발송 성패 무관하게 이력 저장 (안 그러면 다음 실행 때 중복 분류)
    all_arts = [a for v in categorized.values() for a in v]
    _save_history(all_arts)
    return success


def _build_html(today, weekday, total, summary_text, grouped, categorized):
    label_map = {"자사배포": "배포", "자사언급": "언급", "경쟁사메이저": "메이저",
                 "경쟁사인디": "인디", "업계": "업계"}

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
GitHub Actions · AI 분류 · v3
</td></tr></table>
</td></tr>"""

    stats_parts = []
    for cat, arts in categorized.items():
        if arts:
            stats_parts.append(f"{label_map.get(cat, cat)} {len(arts)}")
    stats_str = " · ".join(stats_parts) if stats_parts else "수집 기사 없음"

    html += f"""<tr><td style="padding:10px 0 0 0;">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#F5EFE6;border:1px solid #E5DACB;">
<tr><td style="padding:8px 14px;font-size:12.5px;color:#6B5F52;">
총 <b>{total}</b>건 | {stats_str}
</td></tr></table></td></tr>"""

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
            ("자사배포", "1", "자사 배포", "#E8730C"),
            ("자사언급", "2", "자사 언급", "#C25A00"),
            ("경쟁사메이저", "3", "경쟁사 — 메이저", "#2E86AB"),
            ("경쟁사인디", "4", "경쟁사 — 인디", "#5BA4CF"),
            ("업계", "5", "화장품 업계 · 트렌드", "#6B5F52"),
        ]

        for cat_key, num, label, color in sections:
            arts = grouped.get(cat_key, [])
            orig_count = len(categorized.get(cat_key, []))
            if not arts:
                continue
            html += _build_section(num, f"{label} ({orig_count}건)", arts, color)

    html += f"""<tr><td style="padding:16px 0 0 0;">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-top:1px solid #E5DACB;">
<tr><td align="center" style="padding:12px 15px;font-size:11.5px;color:#857B6F;">
Monitoring by <b style="color:#2B2620;">프라우드컴(주)</b> · Copyright 2026 Proudcomm<br>
<span style="font-size:10px;color:#aaa;">네이버 API + 구글 RSS + 다음 | Claude AI 분류 | v3</span>
</td></tr></table></td></tr>

</table></td></tr></table></body></html>"""
    return html


def _build_section(num, label, articles, accent_color):
    s = f"""<tr><td style="padding:14px 0 0 0;">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#FFFFFF;border:1px solid #ECE3D8;">
<tr><td style="padding:16px 18px;">
<div style="font-size:16px;font-weight:bold;color:#2B2620;padding-bottom:6px;">
<span style="color:{accent_color};">{num}</span> &nbsp;&nbsp;{label}</div>"""

    sentiment_colors = {"긍정": "#28a745", "부정": "#dc3545", "중립": "#6c757d"}
    tier_badges = {5: ("T1", "#E8730C"), 3: ("T2", "#6B5F52"), 2: ("T3", "#999"), 1: ("T4", "#bbb")}

    for a in articles:
        sentiment = a.get("sentiment", "중립")
        s_color = sentiment_colors.get(sentiment, "#6c757d")
        title = a["title"]
        link = a["link"]
        source = a["source"]
        summary = a.get("summary", "")
        tier_score = a.get("tier_score", 1)
        others = a.get("others_count", 0)
        tier_label, tier_color = tier_badges.get(tier_score, ("T4", "#bbb"))
        others_html = f' <span style="color:#C25A00;font-size:11px;">외 {others}건</span>' if others > 0 else ""

        s += f"""<div style="font-size:14px;padding:7px 0;border-top:1px solid #F2ECE3;">
<span style="display:inline-block;background:{s_color};color:white;padding:1px 6px;border-radius:3px;font-size:10px;">{sentiment}</span>
<span style="display:inline-block;background:{tier_color};color:white;padding:1px 5px;border-radius:3px;font-size:10px;margin-left:2px;">{tier_label}</span>&nbsp;
<a href="{link}" style="color:#2B2620;text-decoration:none;font-weight:bold;">{title}</a>{others_html}
<span style="color:#C25A00;font-size:11px;font-weight:bold;"> {source} ›</span>
<br><span style="color:#857B6F;font-size:12px;">{summary}</span></div>"""

    s += "</td></tr></table></td></tr>"
    return s


# ═══════════════════════════════════════════
# Google Drive 업로드
# ═══════════════════════════════════════════

def _upload_to_drive(html_content: str, filename: str):
    try:
        import base64
        sa_info = json.loads(base64.b64decode(GDRIVE_SERVICE_ACCOUNT))
        token = _get_drive_token(sa_info)
        if not token:
            print("[Drive] 토큰 발급 실패")
            return

        boundary = "----DriveUploadBoundary"
        metadata = json.dumps({
            "name": filename,
            "parents": [GDRIVE_FOLDER_ID],
            "mimeType": "text/html",
        })
        body = (
            f"--{boundary}\r\n"
            f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
            f"{metadata}\r\n"
            f"--{boundary}\r\n"
            f"Content-Type: text/html; charset=UTF-8\r\n\r\n"
            f"{html_content}\r\n"
            f"--{boundary}--"
        ).encode("utf-8")

        req = urllib.request.Request(
            "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart",
            data=body, method="POST",
        )
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Content-Type", f"multipart/related; boundary={boundary}")

        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            print(f"[Drive] 업로드 완료: {result.get('name')} (id: {result.get('id')})")
    except Exception as e:
        print(f"[Drive] 업로드 오류: {e}")


def _get_drive_token(sa_info: dict) -> str:
    try:
        import base64
        import subprocess
        import tempfile

        header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256", "typ": "JWT"}).encode()).rstrip(b"=")
        now = int(time.time())
        claim = base64.urlsafe_b64encode(json.dumps({
            "iss": sa_info["client_email"],
            "scope": "https://www.googleapis.com/auth/drive.file",
            "aud": "https://oauth2.googleapis.com/token",
            "iat": now, "exp": now + 3600,
        }).encode()).rstrip(b"=")

        sign_input = header + b"." + claim
        with tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False) as f:
            f.write(sa_info["private_key"])
            key_path = f.name
        try:
            result = subprocess.run(
                ["openssl", "dgst", "-sha256", "-sign", key_path],
                input=sign_input, capture_output=True,
            )
            signature = base64.urlsafe_b64encode(result.stdout).rstrip(b"=")
        finally:
            os.unlink(key_path)

        jwt_token = sign_input + b"." + signature
        token_data = urllib.parse.urlencode({
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": jwt_token.decode(),
        }).encode()

        req = urllib.request.Request(
            "https://oauth2.googleapis.com/token",
            data=token_data, method="POST",
        )
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("access_token", "")
    except Exception as e:
        print(f"[Drive] 토큰 오류: {e}")
        return ""


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
    hashes = list(history)[-2000:]
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
            tier = a.get("tier_score", 1)
            tier_label = {5: "T1", 3: "T2", 2: "T3"}.get(tier, "T4")
            lines.append(f"[{cat}][{a.get('sentiment', '중립')}][{tier_label}] {a['title']}: {a.get('summary', '')}")
    if not lines:
        return ""
    prompt = f"""아래는 오늘 에이블씨엔씨 관련 뉴스 모니터링 결과입니다.

{chr(10).join(lines[:80])}

PR 대행사 대표가 CEO에게 아침 보고하는 Today Summary를 아래 형식으로 작성하세요.

**형식 규칙:**
- 번호를 매긴 항목별 서술 (1. 2. 3. ...)
- 명사형 종결체 사용 ("~임", "~됨", "~필요", "~예정" 등)
- HTML 형식, <b> 태그로 핵심 강조

**내용 규칙:**
- [자사]: 배포나 언급이 있을 때만 작성. 없으면 아예 언급하지 않음. "배포 없었다", "언급 없었다" 같은 말 절대 금지.
  - 배포가 있으면: 주제 + 대표 매체(티어) + "외 총 N건 기사화" 형태
- [경쟁사]: 카테고리 구분 없이, 주목할 경쟁사 움직임을 각각 1~2문장으로 서술
- [업계 동향]: IPO, 유통 변화, 규제 등 업계 주요 이슈
- [시사점]: 미샤/에이블씨엔씨 관점에서의 시사점과 대응 방향. 2~3문장.

**참고 예시:**
1. <b>[자사]</b> 미샤의 송혜교 2026 윈터 캠페인 화보와 겨울룩 기사가 4건 나왔고 모두 긍정적. 다만 전부 자사배포(T3)라 외부 매체 확산력은 제한적이므로, T1 매체 2차 보도 확보가 다음 과제.
2. <b>[경쟁사]</b> 아누아는 수지 겨울 캠페인 기사를 7건 쏟아내며 PDRN 세럼을 전면에 내세우고 있어, 같은 시즌 모델 마케팅 경쟁이 치열함. 티르티르도 미국 엘르 뷰티 어워즈 수상 등 해외 성과를 연달아 알리는 중.
3. <b>[업계 동향]</b> 구다이글로벌의 코스피 상장 예비심사 청구(기업가치 최대 10조 원 관측)가 IPO 시장의 주목을 받고 있음. 이마트·노브랜드의 초저가 더마 라인 확대와 ODM 생산 양극화 이슈도 부각 중.
4. <b>[시사점]</b> 뷰티 시장은 초저가 확대, 기술 기반 차별화, 해외 어워즈 경쟁으로 재편 중. 미샤는 가격 포지셔닝과 기능성 스토리를 점검하고, 캠페인 성과를 외부 매체로 확산시키는 전략 필요.

위 예시의 구조와 톤을 따르되, 오늘 실제 기사 내용으로 작성하세요. 해당 없는 섹션은 생략."""
    try:
        return _call_claude(prompt)
    except Exception as e:
        print(f"[요약] 오류: {e}")
        return ""

def _send_email(html: str, subject: str) -> bool:
    """Gmail API(REST)로 이메일을 발송한다. SMTP는 GitHub Actions에서 차단됨."""
    import base64

    gmail_token = os.environ.get("GMAIL_REFRESH_TOKEN", "")
    gmail_client_id = os.environ.get("GMAIL_CLIENT_ID", "")
    gmail_client_secret = os.environ.get("GMAIL_CLIENT_SECRET", "")

    if not EMAIL_FROM:
        print("[발송] EMAIL_FROM 없음")
        return False

    print(f"[발송] HTML 크기: {len(html.encode('utf-8')):,} bytes")

    # 복수 수신자 지원 (쉼표 구분)
    recipients = [addr.strip() for addr in EMAIL_TO.split(",") if addr.strip()]

    # 방법 1: Gmail API (OAuth refresh token)
    if gmail_token and gmail_client_id and gmail_client_secret:
        return _send_via_gmail_api(html, subject, recipients, gmail_token,
                                    gmail_client_id, gmail_client_secret)

    # 방법 2: SMTP 폴백 (로컬 환경용)
    if EMAIL_PASSWORD:
        return _send_via_smtp(html, subject, recipients)

    print("[발송] 발송 수단 없음 (GMAIL_REFRESH_TOKEN 또는 EMAIL_PASSWORD 필요)")
    return False


def _send_via_gmail_api(html, subject, recipients, refresh_token, client_id, client_secret):
    """Gmail API REST로 이메일을 발송한다."""
    import base64

    # 1. refresh token으로 access token 획득
    token_data = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
    }).encode()

    try:
        req = urllib.request.Request("https://oauth2.googleapis.com/token",
                                     data=token_data, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        with urllib.request.urlopen(req, timeout=15) as resp:
            token_resp = json.loads(resp.read().decode("utf-8"))
        access_token = token_resp["access_token"]
    except Exception as e:
        print(f"[발송] Gmail API 토큰 오류: {e}")
        return False

    # 2. MIME 메시지 생성
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(html, "html", "utf-8"))

    raw_msg = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")

    # 3. Gmail API로 발송
    try:
        send_data = json.dumps({"raw": raw_msg}).encode("utf-8")
        req = urllib.request.Request(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
            data=send_data, method="POST",
        )
        req.add_header("Authorization", f"Bearer {access_token}")
        req.add_header("Content-Type", "application/json")

        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            print(f"[발송] Gmail API 완료: {result.get('id')} | {subject}")
            return True
    except Exception as e:
        print(f"[발송] Gmail API 발송 오류: {e}")
        return False


def _send_via_smtp(html, subject, recipients):
    """SMTP로 이메일을 발송한다 (로컬 환경 폴백)."""
    for attempt in range(3):
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = EMAIL_FROM
            msg["To"] = ", ".join(recipients)
            msg.attach(MIMEText(html, "html", "utf-8"))
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
                server.login(EMAIL_FROM, EMAIL_PASSWORD)
                server.send_message(msg)
            print(f"[발송] SMTP 완료: {subject}")
            return True
        except Exception as e:
            print(f"[발송] SMTP 시도 {attempt+1}/3 실패: {e}")
            if attempt < 2:
                time.sleep(3)
    print("[발송] SMTP 3회 모두 실패")
    return False


# ═══════════════════════════════════════════
if __name__ == "__main__":
    print("=" * 60)
    print(f"에이블씨엔씨 뉴스 모니터링 v3 시작: {datetime.datetime.now()}")
    kw_total = len(COMPANY_KEYWORDS) + len(COMPETITOR_MAJOR_KEYWORDS) + len(COMPETITOR_INDIE_KEYWORDS) + len(INDUSTRY_KEYWORDS)
    print(f"키워드: 자사 {len(COMPANY_KEYWORDS)} + 메이저 {len(COMPETITOR_MAJOR_KEYWORDS)} + 인디 {len(COMPETITOR_INDIE_KEYWORDS)} + 업계 {len(INDUSTRY_KEYWORDS)} = {kw_total}개")
    print("=" * 60)

    articles = collect_all()
    categorized = classify_articles(articles)
    grouped = group_similar_articles(categorized)
    send_report(categorized, grouped)

    print("=" * 60)
    print(f"완료: {datetime.datetime.now()}")
    print("=" * 60)

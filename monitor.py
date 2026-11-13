"""
에이블씨엔씨 뉴스 모니터링 시스템
수집(RSS) → 검사(Claude) → 발송(이메일) 3단계 구조
GitHub Actions에서 평일 아침 자동 실행
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

# ─── 환경 변수 ───
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD", "")
EMAIL_FROM = os.environ.get("EMAIL_FROM", "funnyjun@gmail.com")
EMAIL_TO = os.environ.get("EMAIL_TO", "funnyjun@gmail.com")

KEYWORDS = ["에이블씨엔씨", "미샤", "MISSHA", "어퓨", "초공진"]
HISTORY_FILE = "sent_history.json"


# ═══════════════════════════════════════════
# 1단계: 수집 직원 (Collector)
# ═══════════════════════════════════════════

def collect_google_news(keyword: str) -> list[dict]:
    """구글 뉴스 RSS로 기사를 수집한다."""
    encoded = urllib.parse.quote(keyword)
    url = f"https://news.google.com/rss/search?q={encoded}+when:1d&hl=ko&gl=KR&ceid=KR:ko"
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
            source = item.findtext("source", "")
            articles.append({
                "title": title,
                "link": link,
                "description": "",
                "pub_date": pub_date,
                "source": source if source else "google",
            })
        return articles
    except Exception as e:
        print(f"[수집] 구글 뉴스 오류 ({keyword}): {e}")
        return []


def collect_naver_rss(keyword: str) -> list[dict]:
    """네이버 뉴스 RSS로 기사를 수집한다 (API 키 불필요)."""
    encoded = urllib.parse.quote(keyword)
    url = f"https://news.google.com/rss/search?q={encoded}+site:news.naver.com+when:1d&hl=ko&gl=KR&ceid=KR:ko"
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
            source = item.findtext("source", "")
            articles.append({
                "title": title,
                "link": link,
                "description": "",
                "pub_date": pub_date,
                "source": source if source else "naver",
            })
        return articles
    except Exception as e:
        print(f"[수집] 네이버 RSS 오류 ({keyword}): {e}")
        return []


def collect_all() -> list[dict]:
    """모든 소스에서 기사를 수집하고 중복을 제거한다."""
    all_articles = []
    for kw in KEYWORDS:
        all_articles.extend(collect_google_news(kw))
        all_articles.extend(collect_naver_rss(kw))
        print(f"[수집] '{kw}' 완료: 누적 {len(all_articles)}건")

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
# 2단계: 검사 직원 (Reviewer)
# ═══════════════════════════════════════════

def review_articles(articles: list[dict]) -> list[dict]:
    """Claude에게 기사를 보내 유의미한 것만 골라내고 분석한다."""
    if not articles:
        return []

    # 이미 보낸 기사 제외
    history = _load_history()
    new_articles = []
    for a in articles:
        h = _article_hash(a["title"])
        if h not in history:
            new_articles.append(a)

    if not new_articles:
        print("[검사] 새 기사 없음")
        return []

    # 최대 60건만 분석 (비용 관리)
    new_articles = new_articles[:60]
    print(f"[검사] {len(new_articles)}건 분석 시작")

    article_text = ""
    for i, a in enumerate(new_articles, 1):
        article_text += f"\n[{i}] {a['title']}\n출처: {a['source']} | {a['link']}\n"

    prompt = f"""다음은 '에이블씨엔씨'와 관련될 수 있는 뉴스 기사 목록입니다.

{article_text}

각 기사에 대해 다음을 판단해 주세요:
1. 에이블씨엔씨(미샤, 어퓨, 초공진 포함)에 실질적으로 유의미한 기사인지 (YES/NO)
2. YES인 경우: 감성(긍정/부정/중립), 한 줄 요약

JSON 배열로 응답해 주세요. 형식:
[
  {{"index": 1, "relevant": true, "sentiment": "긍정", "summary": "한 줄 요약"}},
  {{"index": 2, "relevant": false}}
]

유의미하지 않은 기사(단순 광고, 에이블씨엔씨와 무관한 기사)는 relevant: false로 표시하세요."""

    try:
        result = _call_claude(prompt)
        start = result.find("[")
        end = result.rfind("]") + 1
        if start >= 0 and end > start:
            analyses = json.loads(result[start:end])
        else:
            print("[검사] Claude 응답에서 JSON을 찾을 수 없음")
            return []

        reviewed = []
        for item in analyses:
            idx = item.get("index", 0) - 1
            if 0 <= idx < len(new_articles) and item.get("relevant"):
                article = new_articles[idx].copy()
                article["sentiment"] = item.get("sentiment", "중립")
                article["summary"] = item.get("summary", "")
                reviewed.append(article)

        print(f"[검사] 유의미한 기사: {len(reviewed)}건")
        return reviewed

    except Exception as e:
        print(f"[검사] Claude 분석 오류: {e}")
        return []


# ═══════════════════════════════════════════
# 3단계: 발송 직원 (Sender)
# ═══════════════════════════════════════════

def send_report(articles: list[dict]) -> bool:
    """분석된 기사를 HTML 이메일로 발송한다."""
    today = datetime.date.today().strftime("%Y-%m-%d")
    weekday = ["월", "화", "수", "목", "금", "토", "일"][datetime.date.today().weekday()]

    if not articles:
        # 기사가 없어도 빈 보고서를 보냄
        html = f"""
        <html>
        <head><meta charset="utf-8"></head>
        <body style="font-family: 'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif; max-width: 700px; margin: 0 auto; padding: 20px;">
            <h2 style="color: #333; border-bottom: 2px solid #0066cc; padding-bottom: 10px;">
                📰 에이블씨엔씨 뉴스 모니터링 — {today} ({weekday})
            </h2>
            <p style="color: #666;">오늘 유의미한 뉴스가 없습니다.</p>
            <hr style="margin-top: 30px;">
            <p style="color: #aaa; font-size: 11px;">이 이메일은 GitHub Actions에서 자동 생성되었습니다.</p>
        </body>
        </html>
        """
        return _send_email(html, f"[에이블씨엔씨] 뉴스 모니터링 — {today} ({weekday}) | 기사 없음")

    # CEO 요약 생성
    ceo_summary = _generate_ceo_summary(articles)

    # HTML 이메일 본문
    html = f"""
    <html>
    <head><meta charset="utf-8"></head>
    <body style="font-family: 'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif; max-width: 700px; margin: 0 auto; padding: 20px;">
        <h2 style="color: #333; border-bottom: 2px solid #0066cc; padding-bottom: 10px;">
            📰 에이블씨엔씨 뉴스 모니터링 — {today} ({weekday})
        </h2>

        <div style="background: #f0f7ff; padding: 15px; border-radius: 8px; margin: 15px 0;">
            <h3 style="margin-top: 0; color: #0066cc;">💡 CEO 인사이트</h3>
            <p style="line-height: 1.6;">{ceo_summary}</p>
        </div>

        <h3>📋 주요 기사 ({len(articles)}건)</h3>
        <table style="width: 100%; border-collapse: collapse;">
    """

    sentiment_colors = {"긍정": "#28a745", "부정": "#dc3545", "중립": "#6c757d"}

    for a in articles:
        color = sentiment_colors.get(a.get("sentiment", "중립"), "#6c757d")
        html += f"""
            <tr style="border-bottom: 1px solid #eee;">
                <td style="padding: 12px 8px;">
                    <span style="display: inline-block; background: {color}; color: white; padding: 2px 8px; border-radius: 4px; font-size: 12px; margin-bottom: 4px;">
                        {a.get('sentiment', '중립')}
                    </span>
                    <br>
                    <a href="{a['link']}" style="color: #333; text-decoration: none; font-weight: bold; font-size: 14px;">
                        {a['title']}
                    </a>
                    <br>
                    <span style="color: #888; font-size: 12px;">{a.get('summary', '')}</span>
                    <br>
                    <span style="color: #aaa; font-size: 11px;">출처: {a['source']}</span>
                </td>
            </tr>
        """

    html += """
        </table>
        <hr style="margin-top: 30px;">
        <p style="color: #aaa; font-size: 11px;">이 이메일은 GitHub Actions에서 자동 생성되었습니다.</p>
    </body>
    </html>
    """

    subject = f"[에이블씨엔씨] 뉴스 모니터링 — {today} ({weekday}) | {len(articles)}건"
    success = _send_email(html, subject)

    if success:
        _save_history(articles)

    return success


# ═══════════════════════════════════════════
# 보조 함수
# ═══════════════════════════════════════════

def _article_hash(title: str) -> str:
    """기사 제목으로 해시를 만든다 (중복 판별용)."""
    clean = re.sub(r"^\[.*?\]\s*", "", title)
    clean = re.sub(r"\s+", "", clean)[:50]
    return hashlib.md5(clean.encode("utf-8")).hexdigest()[:12]


def _load_history() -> set:
    """이미 발송한 기사 해시 목록을 불러온다."""
    if not os.path.exists(HISTORY_FILE):
        return set()
    try:
        with open(HISTORY_FILE, "r") as f:
            data = json.load(f)
        return set(data.get("hashes", []))
    except Exception:
        return set()


def _save_history(articles: list[dict]):
    """발송한 기사 해시를 저장한다 (최근 500건만 유지)."""
    history = _load_history()
    for a in articles:
        history.add(_article_hash(a["title"]))
    hashes = list(history)[-500:]
    with open(HISTORY_FILE, "w") as f:
        json.dump({"hashes": hashes, "updated": str(datetime.datetime.now())}, f)


def _call_claude(prompt: str) -> str:
    """Anthropic Messages API를 호출한다."""
    url = "https://api.anthropic.com/v1/messages"
    payload = json.dumps({
        "model": "claude-haiku-3-5-20241022",
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")

    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("x-api-key", ANTHROPIC_API_KEY)
    req.add_header("anthropic-version", "2023-06-01")

    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    return data["content"][0]["text"]


def _generate_ceo_summary(articles: list[dict]) -> str:
    """CEO용 인사이트를 생성한다."""
    article_list = "\n".join(
        f"- [{a.get('sentiment', '중립')}] {a['title']}: {a.get('summary', '')}"
        for a in articles
    )

    prompt = f"""아래는 오늘 에이블씨엔씨 관련 주요 뉴스입니다.

{article_list}

PR 대행사 대표의 관점에서 CEO에게 보고하는 3~5줄 분량의 핵심 인사이트를 작성해 주세요.
단순 나열이 아니라, 오늘 뉴스의 흐름과 의미를 짚어 주세요."""

    try:
        return _call_claude(prompt)
    except Exception as e:
        print(f"[요약] CEO 요약 생성 오류: {e}")
        return "CEO 인사이트를 생성하지 못했습니다."


def _send_email(html: str, subject: str) -> bool:
    """HTML 이메일을 발송한다."""
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = EMAIL_FROM
        msg["To"] = EMAIL_TO
        msg.attach(MIMEText(html, "html", "utf-8"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_FROM, EMAIL_PASSWORD)
            server.send_message(msg)

        print(f"[발송] 이메일 발송 완료")
        return True
    except Exception as e:
        print(f"[발송] 이메일 발송 오류: {e}")
        return False


# ═══════════════════════════════════════════
# 실행
# ═══════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 50)
    print(f"에이블씨엔씨 뉴스 모니터링 시작: {datetime.datetime.now()}")
    print("=" * 50)

    # 1단계: 수집
    articles = collect_all()

    # 2단계: 검사
    reviewed = review_articles(articles)

    # 3단계: 발송
    send_report(reviewed)

    print("=" * 50)
    print(f"완료: {datetime.datetime.now()}")
    print("=" * 50)

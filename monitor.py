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
    "에이블씨엔씨", "미샤", "MISSHA", "missha", "able c&c", "어퓨", "초공진", "셀라피", "apieu",
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

def collect_naver_api(keyword: str, display: int = 100) -> list[dict]:
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


_unmapped_domains = set()  # 미등록 도메인 추적용

def _extract_media_name(url: str) -> str:
    """URL에서 한글 매체명을 추출한다."""
    try:
        from urllib.parse import urlparse
        domain = urlparse(url).netloc.lower()
        if not domain:
            return "알수없음"
        # 1. 정확히 일치
        if domain in DOMAIN_TO_KOREAN:
            return DOMAIN_TO_KOREAN[domain]
        # 2. www 제거 후 매칭
        d = domain[4:] if domain.startswith("www.") else domain
        for k, v in DOMAIN_TO_KOREAN.items():
            k2 = k[4:] if k.startswith("www.") else k
            if d == k2:
                return v
        # 3. 부분 도메인 포함 매칭 (sports.khan.co.kr → khan 포함)
        parts = d.split(".")
        for k, v in DOMAIN_TO_KOREAN.items():
            k2 = k[4:] if k.startswith("www.") else k
            k_parts = k2.split(".")
            if set(parts) & set(k_parts) - {"co", "kr", "com", "net", "org", "or"}:
                return v
        # 미등록 도메인 로그 (실행 종료 시 한번에 출력)
        fallback = d.split(".")[0]
        if domain not in _unmapped_domains:
            _unmapped_domains.add(domain)
        return fallback
    except Exception:
        return "알수없음"


# 도메인→한글 매체명 매핑
DOMAIN_TO_KOREAN = {
    # === T1 주요 매체 ===
    "www.yna.co.kr": "연합뉴스", "www.newsis.com": "뉴시스",
    "www.news1.kr": "뉴스1", "www.chosun.com": "조선일보",
    "biz.chosun.com": "조선비즈", "www.joongang.co.kr": "중앙일보",
    "www.donga.com": "동아일보", "www.hankookilbo.com": "한국일보",
    "www.khan.co.kr": "경향신문", "www.hani.co.kr": "한겨레",
    "www.mk.co.kr": "매일경제", "www.hankyung.com": "한국경제",
    "www.sedaily.com": "서울경제", "www.edaily.co.kr": "이데일리",
    "biz.heraldcorp.com": "헤럴드경제", "news.heraldcorp.com": "헤럴드경제",
    "www.fnnews.com": "파이낸셜뉴스", "www.asiae.co.kr": "아시아경제",
    "news.mt.co.kr": "머니투데이", "www.thebell.co.kr": "더벨",
    "news.sbs.co.kr": "SBS", "www.sbs.co.kr": "SBS",
    "news.kbs.co.kr": "KBS", "www.kbs.co.kr": "KBS",
    "imnews.imbc.com": "MBC", "www.mbc.co.kr": "MBC",
    "www.ytn.co.kr": "YTN", "news.jtbc.co.kr": "JTBC",
    "www.mbn.co.kr": "MBN",
    # === 경제/산업 ===
    "www.ajunews.com": "아주경제", "www.dt.co.kr": "디지털타임스",
    "www.etnews.com": "전자신문", "www.zdnet.co.kr": "지디넷코리아",
    "www.bloter.net": "블로터", "www.inews24.com": "아이뉴스24",
    "www.newspim.com": "뉴스핌", "www.businesspost.co.kr": "비즈니스포스트",
    "www.etoday.co.kr": "이투데이", "www.newdaily.co.kr": "뉴데일리",
    "www.segye.com": "세계일보", "www.kukinews.com": "쿠키뉴스",
    "www.nocutnews.co.kr": "노컷뉴스", "www.dailian.co.kr": "데일리안",
    "www.sisajournal.com": "시사저널", "www.shinailbo.co.kr": "신아일보",
    "www.seoul.co.kr": "서울신문", "www.metroseoul.co.kr": "메트로서울",
    "www.seoulfn.com": "서울파이낸스",
    # === 스포츠/엔터 ===
    "www.sportsseoul.com": "스포츠서울", "sports.khan.co.kr": "스포츠경향",
    "www.sportsworldi.com": "스포츠월드", "www.sportsw.kr": "스포츠W",
    "sports.chosun.com": "스포츠조선", "isplus.com": "일간스포츠",
    "www.starnewskorea.com": "스타뉴스", "www.topstarnews.net": "톱스타뉴스",
    "www.xportsnews.com": "엑스포츠뉴스", "www.osen.co.kr": "OSEN",
    "www.spotvnews.co.kr": "스포티비뉴스", "www.newsen.com": "뉴센",
    # === 뷰티/패션/소비 ===
    "www.bntnews.co.kr": "비엔티뉴스", "www.beautynury.com": "뷰티누리",
    "www.cmn.co.kr": "화장품신문", "www.cosinkorea.com": "코신코리아",
    "www.cstimes.com": "컨슈머타임스", "www.theviewers.co.kr": "더뷰어스",
    "www.beautyhankook.com": "뷰티한국", "www.fashionbiz.co.kr": "패션비즈",
    "www.fashionseoul.com": "패션서울", "www.apparelnews.co.kr": "어패럴뉴스",
    "www.fashionjournal.co.kr": "패션저널", "www.singlist.com": "싱글리스트",
    # === IT/테크 ===
    "www.ddaily.co.kr": "디지털데일리", "www.itbiznews.com": "아이티비즈",
    "www.g-enews.com": "글로벌이코노믹", "www.econovill.com": "이코노믹리뷰",
    # === 종합/지역 ===
    "www.enewstoday.co.kr": "이뉴스투데이", "www.nextdaily.co.kr": "넥스트데일리",
    "www.newsian.co.kr": "뉴시안", "www.pointe.co.kr": "포인트데일리",
    "www.wsobi.com": "여성소비자신문", "www.thefairnews.co.kr": "더페어뉴스",
    "www.insight.co.kr": "인사이트", "www.thebk.co.kr": "더비케이",
    "www.webeconomy.co.kr": "웹이코노미",
    "www.pinpointnews.co.kr": "핀포인트뉴스", "www.popcornnews.net": "팝콘뉴스",
    "www.dhnews.co.kr": "대학저널", "www.dailysmart.co.kr": "데일리스마트",
    "www.newsfreezone.co.kr": "뉴스프리존", "www.gosiweek.com": "고시위크",
    "www.beyondpost.co.kr": "비욘드포스트", "dream.kotra.or.kr": "코트라",
    "www.e-science.co.kr": "이사이언스", "www.view.co.kr": "뷰어스",
    "www.koreadaily.com": "코리아데일리", "www.vegannews.co.kr": "비건뉴스",
    "www.newsdream.net": "뉴스드림", "www.catchnews.co.kr": "캐치뉴스",
    "www.lcnews.co.kr": "르씨엘뉴스", "www.dnews.co.kr": "디노미뉴스",
    "www.iminju.net": "민주신문", "www.news2day.co.kr": "뉴스투데이",
    "www.kbsm.net": "KBS미디어", "www.businessplus.kr": "비즈니스플러스",
    "www.newsroad.co.kr": "뉴스로드", "www.venturesquare.net": "벤처스퀘어",
    "www.mdtoday.co.kr": "메디컬투데이", "www.asiaa.co.kr": "아시아A",
    "www.medisobizanews.com": "메디소비즈",
    "www.digitaltoday.co.kr": "디지털투데이", "www.newsprime.co.kr": "뉴스프라임",
    "www.sisain.co.kr": "시사인", "www.upinews.kr": "유피아이뉴스",
    "www.theceluv.com": "더셀럽", "www.dementianews.co.kr": "디멘시아뉴스",
    "www.moneys.mt.co.kr": "머니S", "moneys.mt.co.kr": "머니S",
    "www.biztribune.co.kr": "비즈트리뷴", "www.newsworker.co.kr": "뉴스워커",
    "www.dailypharm.com": "데일리팜", "www.yakup.com": "약업신문",
    "www.segye.com": "세계일보", "www.opinionnews.co.kr": "오피니언뉴스",
    # === 추가 매체 ===
    "www.fntoday.co.kr": "파이낸셜투데이", "fntoday.co.kr": "파이낸셜투데이",
    "www.theguru.co.kr": "더구루", "theguru.co.kr": "더구루",
    "www.wowtv.co.kr": "한국경제TV", "www.sentv.co.kr": "서울경제TV",
    "www.getnews.co.kr": "겟뉴스", "www.newstomato.com": "뉴스토마토",
    "www.newstof.com": "뉴스토프", "www.tfmedia.co.kr": "TF미디어",
    "www.news-paper.co.kr": "뉴스페이퍼", "www.paxnetnews.com": "팍스넷뉴스",
    "www.straightnews.co.kr": "스트레이트뉴스", "www.daejonilbo.com": "대전일보",
    "www.busan.com": "부산일보", "www.kwnews.co.kr": "강원일보",
    "www.jjan.kr": "전북일보", "www.hg-times.com": "화장품경제",
    "www.beautybiz.co.kr": "뷰티비즈", "www.cosmetic-news.co.kr": "코스메틱뉴스",
    "www.c-news.kr": "씨뉴스", "www.newsinside.kr": "뉴스인사이드",
    "www.dailysecu.com": "데일리시큐", "www.sisanewsn.co.kr": "시사뉴스앤",
    "www.kihoilbo.co.kr": "기호일보", "www.gnnews.co.kr": "경남일보",
    "www.gjdream.com": "광주드림", "www.joongdo.co.kr": "중도일보",
    "www.ksilbo.co.kr": "경상일보", "www.dtoday.co.kr": "디투데이",
    "www.tvreport.co.kr": "TV리포트", "www.heraldpop.com": "헤럴드팝",
    "www.mhns.co.kr": "문화뉴스", "www.kmib.co.kr": "국민일보",
    "www.ilyoseoul.co.kr": "일요서울", "www.newscj.com": "뉴스천지",
    "www.wikitree.co.kr": "위키트리", "www.bizwatch.co.kr": "비즈워치",
    "www.bloter.net": "블로터", "www.moneytoday.co.kr": "머니투데이",
    "www.medigate.net": "메디게이트뉴스", "www.pharmnews.com": "팜뉴스",
    "www.hitnews.co.kr": "히트뉴스", "www.ktnews.com": "KT뉴스",
    "www.wolyo.co.kr": "월요신문", "www.it-b.co.kr": "잇비뉴스",
    # === 추가 매체 (11/27) ===
    "www.kpenews.com": "한국정경뉴스", "kpenews.com": "한국정경뉴스",
    "www.dailyimpact.co.kr": "데일리임팩트", "www.fpn119.co.kr": "소방방재신문",
    "www.newscape.co.kr": "뉴스케이프", "www.efnews.co.kr": "이에프뉴스",
    "www.sisamagazine.co.kr": "시사매거진", "www.newswatch.kr": "뉴스워치",
    "www.sisafocus.co.kr": "시사포커스", "www.cnbnews.com": "CNB뉴스",
    "www.brandbrief.co.kr": "브랜드브리프", "www.sisaon.co.kr": "시사ON",
    "www.dailymedi.com": "데일리메디", "www.mdsobiz.com": "엠디소비즈",
    "www.ohmynews.com": "오마이뉴스", "www.pressian.com": "프레시안",
    "www.mediatoday.co.kr": "미디어오늘", "www.journalist.or.kr": "한국기자협회",
    "www.newstap.co.kr": "뉴스탭", "www.consumernews.co.kr": "소비자뉴스",
    "www.hansbiz.co.kr": "한스비즈", "www.meconomynews.com": "엠이코노미뉴스",
    "www.etnews.com": "전자신문", "etnews.com": "전자신문",
    "www.techm.kr": "테크M", "www.zdnet.co.kr": "지디넷코리아",
    "www.ceoscoredaily.com": "CEO스코어데일리", "www.thestock.co.kr": "더스탁",
    "www.thewordnews.co.kr": "더워드뉴스", "www.medigatenews.com": "메디게이트뉴스",
    "www.thepharma.co.kr": "더파마", "www.pharmnews.com": "팜뉴스",
    "www.hitnews.co.kr": "히트뉴스", "www.viva100.com": "비바100",
    "www.shindongnews.co.kr": "신동뉴스", "www.onbao.com": "온바오",
    "www.cosinkorea.com": "코스인코리아", "cosinkorea.com": "코스인코리아",
    "www.hg-times.com": "화장품경제", "www.beautybiz.co.kr": "뷰티비즈",
    "www.cosmetic-news.co.kr": "코스메틱뉴스", "www.cncnews.co.kr": "CNC뉴스",
    "www.c-news.kr": "씨뉴스", "www.newsinside.kr": "뉴스인사이드",
    "www.thevaluenews.co.kr": "더밸류뉴스", "www.fetv.co.kr": "FE금융경제",
    "www.newsim.co.kr": "뉴심", "www.apnews.kr": "AP뉴스",
    "www.m-i.kr": "매일산업뉴스", "www.asiatime.co.kr": "아시아타임즈",
    "www.lcnews.co.kr": "르씨엘뉴스", "www.lecturernews.com": "한국강사신문",
    "www.wikileaks-kr.org": "위키리크스한국", "www.newstown.co.kr": "뉴스타운",
    "www.joseilbo.com": "조세일보", "www.taxtimes.co.kr": "세정일보",
    "www.startuptoday.kr": "스타트업투데이", "www.startupn.kr": "스타트업엔",
    "www.jobsn.com": "잡스엔", "www.mdilbo.com": "무등일보",
    "www.headlinejeju.co.kr": "헤드라인제주", "www.jejunews.com": "제주뉴스",
    "www.kyeongin.com": "경인일보", "www.imaeil.com": "매일신문",
    "www.kookje.co.kr": "국제신문", "www.jnilbo.com": "전남일보",
    # === 미등록 도메인 일괄 추가 (11/27, 379개) ===
    "bizhankook.com": "비즈한국", "byline.network": "바이라인네트워크",
    "ch.yes24.com": "예스24채널", "coinreaders.com": "코인리더스",
    "cooknchefnews.com": "쿡앤셰프뉴스", "daily.hankooki.com": "데일리한국",
    "dealsite.co.kr": "딜사이트", "digitalchosun.dizzo.com": "디지털조선",
    "ditoday.com": "디투데이", "economist.co.kr": "이코노미스트",
    "economychosun.com": "이코노미조선", "econotimes.com": "에코노타임스",
    "futurechosun.com": "퓨처조선", "game.dailyesports.com": "데일리e스포츠",
    "golfhankook.hankooki.com": "골프한국", "hbnpress.com": "HBN프레스",
    "idsn.co.kr": "산업일보", "ilyo.co.kr": "일요신문",
    "kookbang.dema.mil.kr": "국방일보", "kor.theasian.asia": "더아시안",
    "koreajoongangdaily.joins.com": "코리아중앙데일리", "kormedi.com": "코메디닷컴",
    "kr.aving.net": "에이빙뉴스", "m.skyedaily.com": "스카이데일리",
    "medicalworldnews.co.kr": "메디컬월드뉴스", "mhnse.com": "MHN스포츠",
    "okfashion.co.kr": "오케이패션", "platum.kr": "플래텀",
    "sateconomy.co.kr": "SAT경제", "slownews.kr": "슬로우뉴스",
    "swtvnews.com": "SW TV뉴스", "thetracker.co.kr": "더트래커",
    "weekly.hankooki.com": "위클리한국", "weeklytrade.co.kr": "위클리무역",
    "wemakenews.co.kr": "위메이크뉴스",
    "www.100ssd.co.kr": "백세시대", "www.1conomynews.co.kr": "일코노미뉴스",
    "www.4th.kr": "더포스", "www.abcn.kr": "ABC뉴스",
    "www.ablenews.co.kr": "에이블뉴스", "www.aitimes.com": "AI타임스",
    "www.aitimes.kr": "AI타임스", "www.akomnews.com": "아시아경제TV",
    "www.allurekorea.com": "얼루어코리아", "www.apsk.co.kr": "아시아경제신문",
    "www.artinsight.co.kr": "아트인사이트", "www.asiatoday.co.kr": "아시아투데이",
    "www.atstar1.com": "앳스타일", "www.autodaily.co.kr": "오토데일리",
    "www.babytimes.co.kr": "베이비타임즈", "www.banronbodo.com": "반론보도",
    "www.betanews.net": "베타뉴스", "www.bigtanews.co.kr": "빅타뉴스",
    "www.biospectator.com": "바이오스펙테이터", "www.biotimes.co.kr": "바이오타임즈",
    "www.bizwnews.com": "비즈W뉴스", "www.bizwork.co.kr": "비즈워크",
    "www.bokuennews.com": "보건뉴스", "www.bosa.co.kr": "보사닷컴",
    "www.breaknews.com": "브레이크뉴스", "www.businesskorea.co.kr": "비즈니스코리아",
    "www.bzeronews.com": "B제로뉴스", "www.cbci.co.kr": "충북인뉴스",
    "www.ccdailynews.com": "시시데일리뉴스", "www.ccdn.co.kr": "충청뉴스",
    "www.ccreview.co.kr": "충청리뷰", "www.cctimes.kr": "충청타임즈",
    "www.celuvmedia.com": "셀럽미디어", "www.chiefexe.com": "치프이그제큐티브",
    "www.choicenews.co.kr": "초이스뉴스", "www.christiantoday.co.kr": "크리스천투데이",
    "www.chungnamilbo.co.kr": "충남일보", "www.cnbizm.com": "CNB저널",
    "www.consumuch.com": "컨슈머치", "www.cwn.kr": "CW뉴스",
    "www.dailycar.co.kr": "데일리카", "www.dailylog.co.kr": "데일리로그",
    "www.dailynk.com": "데일리NK", "www.dailypop.kr": "데일리팝",
    "www.dailysportshankook.co.kr": "데일리스포츠한국", "www.dailyt.co.kr": "데일리T",
    "www.dandinews.com": "단디뉴스", "www.datanet.co.kr": "데이터넷",
    "www.datanews.co.kr": "데이터뉴스", "www.datasom.co.kr": "데이터솜",
    "www.ddanzi.com": "딴지일보", "www.delighti.co.kr": "딜라이트",
    "www.discoverynews.kr": "디스커버리뉴스", "www.dispatch.co.kr": "디스패치",
    "www.dkilbo.com": "대경일보", "www.docdocdoc.co.kr": "닥닥닥",
    "www.doctorsnews.co.kr": "의협신문", "www.doctorstimes.com": "닥터스타임즈",
    "www.dongascience.com": "동아사이언스", "www.dongponews.net": "동포뉴스",
    "www.e2news.com": "이투뉴스", "www.ebn.co.kr": "EBN",
    "www.economytalk.kr": "이코노미톡", "www.econonews.co.kr": "에코노뉴스",
    "www.ekn.kr": "에너지경제", "www.ekoreanews.co.kr": "이코리아뉴스",
    "www.elle.co.kr": "엘르코리아", "www.energy-news.co.kr": "에너지뉴스",
    "www.enetnews.co.kr": "이넷뉴스", "www.epnc.co.kr": "테크월드",
    "www.eroun.net": "이로운넷", "www.esgeconomy.com": "ESG경제",
    "www.esquirekorea.co.kr": "에스콰이어코리아", "www.ezyeconomy.com": "이지경제",
    "www.factin.co.kr": "팩트인뉴스", "www.farmnmarket.com": "팜앤마켓매거진",
    "www.fashionn.com": "패션엔", "www.fieldnews.kr": "필드뉴스",
    "www.financialpost.co.kr": "파이낸셜포스트", "www.financialreview.co.kr": "파이낸셜리뷰",
    "www.finomy.com": "피노미", "www.fntimes.com": "한국금융신문",
    "www.fomos.kr": "포모스", "www.foodnews.co.kr": "식품외식경제",
    "www.forbeskorea.co.kr": "포브스코리아", "www.fortunekorea.co.kr": "포춘코리아",
    "www.ftimes.kr": "F타임스", "www.ftoday.co.kr": "파이낸스투데이",
    "www.gamechosun.co.kr": "게임조선", "www.gamemeca.com": "게임메카",
    "www.gametoc.co.kr": "게임톡", "www.gamevu.co.kr": "게임뷰",
    "www.gasnews.com": "가스신문", "www.geconomy.co.kr": "G이코노미",
    "www.ggilbo.com": "경기일보", "www.ggmedinews.com": "경기메디뉴스",
    "www.globale.co.kr": "글로벌이", "www.globalepic.co.kr": "글로벌에픽",
    "www.gnmaeil.com": "경남매일", "www.goal.com": "골닷컴",
    "www.gocj.net": "공감신문", "www.gokorea.kr": "고코리아",
    "www.golfjournal.co.kr": "골프저널", "www.goodkyung.com": "굿경제",
    "www.goodmorningcc.com": "굿모닝충청", "www.goodnews1.com": "굿뉴스1",
    "www.gpkorea.com": "GP코리아", "www.gqkorea.co.kr": "GQ코리아",
    "www.greened.kr": "그린포스트코리아", "www.greenpostkorea.co.kr": "그린포스트코리아",
    "www.gukjenews.com": "국제뉴스", "www.handmk.com": "핸드MK",
    "www.harpersbazaar.co.kr": "하퍼스바자코리아", "www.hellodd.com": "헬로디디",
    "www.hellot.net": "헬로티", "www.hemophilia.co.kr": "헤모필리아라이프",
    "www.heraldmuse.com": "헤럴드뮤즈", "www.hidomin.com": "히도민",
    "www.hinews.co.kr": "하이뉴스", "www.huffingtonpost.kr": "허핑턴포스트코리아",
    "www.hyunbulnews.com": "현대불교", "www.ibabynews.com": "베이비뉴스",
    "www.ibulgyo.com": "불교신문", "www.ichannela.com": "채널A",
    "www.idaegu.co.kr": "아이대구", "www.idomin.com": "경남도민일보",
    "www.ifs.or.kr": "국제금융센터", "www.ikbc.co.kr": "KBC광주방송",
    "www.ikld.kr": "강원도민일보", "www.ikoreanspirit.com": "코리안스피릿",
    "www.ikunkang.com": "건강뉴스", "www.ilemonde.com": "르몽드디플로마티크",
    "www.ilovepc.co.kr": "아이러브PC", "www.ilyosisa.co.kr": "일요시사",
    "www.impacton.net": "임팩트온", "www.imwood.co.kr": "임우드",
    "www.incheonilbo.com": "인천일보", "www.incheontoday.com": "인천투데이",
    "www.industrynews.co.kr": "산업뉴스", "www.inews365.com": "아이뉴스365",
    "www.insidevina.com": "인사이드비나", "www.insightkorea.co.kr": "인사이트코리아",
    "www.inthenews.co.kr": "인더뉴스", "www.intn.co.kr": "인터넷신문",
    "www.inven.co.kr": "인벤", "www.investchosun.com": "인베스트조선",
    "www.issuenbiz.com": "이슈앤비즈", "www.itooza.com": "아이투자",
    "www.iusm.co.kr": "IUSM", "www.ize.co.kr": "아이즈",
    "www.jasm.co.kr": "자스미디어", "www.jbnews.com": "전북뉴스",
    "www.jejudomin.co.kr": "제주도민일보", "www.jejumaeil.net": "제주매일",
    "www.jejusori.net": "제주의소리", "www.jemin.com": "제민일보",
    "www.jeonmae.co.kr": "전매일보", "www.jeonmin.co.kr": "전민일보",
    "www.jibs.co.kr": "JIBS", "www.jmbc.co.kr": "JTV전주방송",
    "www.jndn.com": "전남도민신문", "www.job-post.co.kr": "잡포스트",
    "www.joongangenews.com": "중앙e뉴스", "www.joongboo.com": "중부일보",
    "www.joynews24.com": "조이뉴스24", "www.junggi.co.kr": "중기이코노미",
    "www.k-health.com": "케이헬스", "www.kado.net": "강원도민일보",
    "www.kbmaeil.com": "경북매일", "www.kdfnews.com": "한국면세뉴스",
    "www.kdpress.co.kr": "대한전문건설신문", "www.kfenews.co.kr": "한국섬유신문",
    "www.kgnews.co.kr": "경기뉴스", "www.khgames.co.kr": "경향게임스",
    "www.kizmom.com": "키즈맘", "www.kjdaily.com": "광주일보",
    "www.klnews.co.kr": "법률뉴스", "www.kmaeil.com": "경북매일",
    "www.kmedinfo.co.kr": "의학정보", "www.knpnews.com": "국립공원신문",
    "www.korea.kr": "대한민국정부", "www.koreabiomed.com": "코리아바이오메드",
    "www.koreahealthlog.com": "코리아헬스로그", "www.koreaherald.com": "코리아헤럴드",
    "www.koreaittimes.com": "코리아IT타임스", "www.koreajoongangdaily.com": "코리아중앙데일리",
    "www.koreareport.co.kr": "코리아리포트", "www.koreastocknews.com": "코리아스탁뉴스",
    "www.koreatimes.co.kr": "코리아타임스", "www.koreatimes.com": "코리아타임스",
    "www.kpanews.co.kr": "한국약사신문", "www.kpinews.kr": "KPI뉴스",
    "www.ksg.co.kr": "해양한국", "www.ktv.go.kr": "KTV",
    "www.kyeonggi.com": "경기신문", "www.kyongbuk.co.kr": "경북일보",
    "www.kyosu.net": "교수신문", "www.laborplus.co.kr": "노동플러스",
    "www.lawissue.co.kr": "로이슈", "www.legaltimes.co.kr": "리걸타임즈",
    "www.livebiz.today": "라이브비즈", "www.livesnews.com": "라이브뉴스",
    "www.m-economynews.com": "M이코노미뉴스", "www.m-joongang.com": "M중앙",
    "www.maisonkorea.com": "메종코리아", "www.marieclairekorea.com": "마리끌레르코리아",
    "www.maxmovie.com": "맥스무비", "www.mediafine.co.kr": "미디어파인",
    "www.mediagunpo.co.kr": "미디어군포", "www.mediajeju.com": "미디어제주",
    "www.mediapen.com": "미디어펜", "www.mediawatch.kr": "미디어워치",
    "www.medicaltimes.com": "메디칼타임즈", "www.medipana.com": "메디파나",
    "www.megaeconomy.co.kr": "메가경제", "www.mhj21.com": "문화저널21",
    "www.mkhealth.co.kr": "매일건강", "www.moneystorm.kr": "머니스톰",
    "www.movist.com": "무비스트", "www.mstoday.co.kr": "MS투데이",
    "www.mtnews.net": "MT뉴스", "www.munhwa.com": "문화일보",
    "www.mydaily.co.kr": "마이데일리", "www.mygoyang.com": "마이고양",
    "www.naeil.com": "내일신문", "www.namdonews.com": "남도뉴스",
    "www.nbntv.co.kr": "NBN뉴스", "www.nc.press": "NC프레스",
    "www.newsam.co.kr": "뉴스앰", "www.newsbrite.net": "뉴스브라이트",
    "www.newsclaim.co.kr": "뉴스클레임", "www.newsfarm.co.kr": "뉴스팜",
    "www.newsfc.co.kr": "뉴스FC", "www.newskr.kr": "뉴스코리아",
    "www.newslock.co.kr": "뉴스락", "www.newsmaker.or.kr": "뉴스메이커",
    "www.newsmap.co.kr": "뉴스맵", "www.newsmp.com": "뉴스MP",
    "www.newspost.kr": "뉴스포스트", "www.newsq.co.kr": "뉴스큐",
    "www.newsquest.co.kr": "뉴스퀘스트", "www.newstnt.com": "뉴스TNT",
    "www.newstopkorea.com": "뉴스탑코리아", "www.newstree.kr": "뉴스트리",
    "www.newsverse.kr": "뉴스버스", "www.newsway.co.kr": "뉴스웨이",
    "www.newswell.co.kr": "뉴스웰", "www.newsworks.co.kr": "뉴스웍스",
    "www.ngetnews.com": "N겟뉴스", "www.ngonews.kr": "NGO뉴스",
    "www.niceeconomy.co.kr": "나이스경제", "www.nongaek.com": "농객닷컴",
    "www.nongmin.com": "농민신문", "www.nongup.net": "농업인신문",
    "www.nspna.com": "NSP통신", "www.ntoday.co.kr": "N투데이",
    "www.onews.tv": "O뉴스", "www.paxetv.com": "팍스TV",
    "www.pennmike.com": "펜앤드마이크", "www.peoplewatch.co.kr": "피플워치",
    "www.pointdaily.co.kr": "포인트데일리", "www.polinews.co.kr": "폴리뉴스",
    "www.ppss.kr": "ㅍㅍㅅㅅ", "www.press9.kr": "프레스9",
    "www.pressman.kr": "프레스맨", "www.public25.com": "퍼블릭25",
    "www.queen.co.kr": "퀸", "www.raonnews.com": "라온뉴스",
    "www.rapportian.com": "라포르시안", "www.rcast.co.kr": "알캐스트",
    "www.rookie.co.kr": "루키닷컴", "www.s-journal.co.kr": "S저널",
    "www.safetimes.co.kr": "세이프타임즈", "www.sciencetimes.co.kr": "사이언스타임즈",
    "www.segyebiz.com": "세계비즈", "www.sejungilbo.com": "세정일보",
    "www.seouland.com": "서울앤", "www.seouleconews.com": "서울경제뉴스",
    "www.seoulwire.com": "서울와이어", "www.sidae.com": "시대일보",
    "www.siminilbo.co.kr": "시민일보", "www.sisacast.kr": "시사캐스트",
    "www.sisajournal-e.com": "시사저널e", "www.sisaweek.com": "시사위크",
    "www.sisunnews.co.kr": "시선뉴스", "www.slist.kr": "S리스트",
    "www.smartbizn.com": "스마트비즈", "www.smartfn.co.kr": "스마트에프앤",
    "www.smarttimes.co.kr": "스마트타임즈", "www.smarttoday.co.kr": "스마트투데이",
    "www.smedaily.co.kr": "중소기업신문", "www.socialvalue.kr": "소셜밸류",
    "www.sommeliertimes.com": "소믈리에타임즈", "www.sportalkorea.com": "스포탈코리아",
    "www.sportschosun.com": "스포츠조선", "www.sportsq.co.kr": "스포츠Q",
    "www.srtimes.kr": "SR타임즈", "www.stardailynews.co.kr": "스타데일리뉴스",
    "www.stnsports.co.kr": "STN스포츠", "www.stoo.com": "스투닷컴",
    "www.swtvnews.com": "SW TV뉴스", "www.techholic.co.kr": "테크홀릭",
    "www.tenasia.co.kr": "텐아시아", "www.tennis.co.kr": "테니스코리아",
    "www.tgdaily.co.kr": "TG데일리", "www.the-biz.co.kr": "더비즈",
    "www.the-pr.co.kr": "더피알", "www.the-stock.kr": "더스탁",
    "www.the-today.com": "더투데이", "www.thebigdata.co.kr": "더빅데이터",
    "www.thedailypost.kr": "더데일리포스트", "www.thefirstmedia.net": "더퍼스트미디어",
    "www.thelec.kr": "더일렉", "www.thepowernews.co.kr": "더파워뉴스",
    "www.thepublic.kr": "더퍼블릭", "www.thereport.co.kr": "더리포트",
    "www.thesportstimes.co.kr": "더스포츠타임즈", "www.thisisgame.com": "디스이즈게임",
    "www.tjb.co.kr": "TJB", "www.todaykorea.co.kr": "투데이코리아",
    "www.tokenpost.kr": "토큰포스트", "www.topdaily.kr": "탑데일리",
    "www.tournews21.com": "투어뉴스21", "www.ttlnews.com": "TTL뉴스",
    "www.tvdaily.co.kr": "TV데일리", "www.ujeil.com": "우제일보",
    "www.ulsanpress.net": "울산프레스", "www.updownnews.co.kr": "업다운뉴스",
    "www.upkoreanews.kr": "업코리아", "www.veritas-a.com": "베리타스알파",
    "www.vogue.co.kr": "보그코리아", "www.weeklytoday.com": "위클리투데이",
    "www.whitepaper.co.kr": "화이트페이퍼", "www.whosaeng.com": "후생신보",
    "www.widedaily.com": "와이드데일리", "www.womaneconomy.co.kr": "여성경제",
    "www.womennews.co.kr": "여성뉴스", "www.womentimes.co.kr": "여성타임즈",
    "www.woodkorea.co.kr": "우드코리아", "www.worklaw.co.kr": "노동법률",
    "www.worktoday.co.kr": "워크투데이", "www.yonhapnewstv.co.kr": "연합뉴스TV",
    "www.youthdaily.co.kr": "청년일보", "www.ziksir.com": "직썰",
}


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

    # 네이버 뉴스 API 단독 수집 (구글/다음 RSS 미사용)

    total_elapsed = time.time() - collect_start
    print(f"[수집] 전체 수집 완료: {len(all_articles)}건 ({total_elapsed:.0f}초)")

    seen = set()
    unique = []
    for a in all_articles:
        key = _article_hash(a["title"])
        if key not in seen:
            seen.add(key)
            unique.append(a)

    # 날짜 필터: 수집 윈도우 내 기사만 (기간 외 무조건 제외)
    date_filtered = _filter_by_date(unique)
    filtered = _filter_fashion_missha(date_filtered)
    print(f"[수집] 총 {len(all_articles)}건 → 중복제거 {len(unique)}건 → 날짜필터 {len(date_filtered)}건 → 패션필터 후 {len(filtered)}건")
    return filtered


def _filter_by_date(articles: list[dict]) -> list[dict]:
    """수집 윈도우 내 기사만 남긴다. 가이드 기준: 전일 09:00 KST ~ 현재."""
    from email.utils import parsedate_to_datetime

    # 수집 윈도우 시작: 전일 09:00 KST (월요일이면 금요일 09:00)
    now_kst = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
    search_days = int(os.environ.get("SEARCH_DAYS", "1"))

    if search_days == 1:
        # 전일 09:00 KST
        window_start = (now_kst - datetime.timedelta(days=1)).replace(
            hour=9, minute=0, second=0, microsecond=0)
    else:
        # N일 전 09:00 KST
        window_start = (now_kst - datetime.timedelta(days=search_days)).replace(
            hour=9, minute=0, second=0, microsecond=0)

    print(f"[수집] 날짜 필터: {window_start.strftime('%Y-%m-%d %H:%M')} KST ~ 현재")

    result = []
    for a in articles:
        pub_date = a.get("pub_date", "")
        if not pub_date:
            result.append(a)  # 날짜 없으면 포함
            continue
        try:
            dt = parsedate_to_datetime(pub_date)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone(datetime.timedelta(hours=9)))
            if dt >= window_start:
                result.append(a)
        except Exception:
            result.append(a)  # 파싱 실패면 포함
    return result


def _filter_fashion_missha(articles: list[dict]) -> list[dict]:
    result = []
    for a in articles:
        text = (a["title"] + " " + a.get("description", "")).lower()
        # 패션 미샤(MICHAA/시선인터내셔널) 제외
        if a["search_keyword"] in ("미샤", "MISSHA", "missha"):
            if any(excl in text for excl in FASHION_EXCLUDE):
                continue
        # 셀라피바이오 제외 (셀라피와 다른 회사)
        if a["search_keyword"] == "셀라피" and "셀라피바이오" in text:
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
    major_str = ", ".join(COMPETITOR_MAJOR_KEYWORDS)
    indie_str = ", ".join(COMPETITOR_INDIE_KEYWORDS)

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
- 애경산업·루나·에이지투웨니스는 반드시 경쟁사메이저로 분류 (인디 아님)

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
    # 1. 기존 60% 한글 단어 겹침
    if overlap / min_len >= 0.6:
        return True
    # 2. 핵심 고유명사(2글자 이상) 겹침이 3개 이상이면 유사
    long_a = {w for w in words_a if len(w) >= 2}
    long_b = {w for w in words_b if len(w) >= 2}
    long_overlap = long_a & long_b
    if len(long_overlap) >= 3:
        return True
    return False


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
    comp_count = len(categorized.get("경쟁사메이저", [])) + len(categorized.get("경쟁사인디", []))
    if categorized.get("자사배포"):
        parts.append(f"배포 {len(categorized['자사배포'])}")
    if categorized.get("자사언급"):
        parts.append(f"언급 {len(categorized['자사언급'])}")
    if comp_count:
        parts.append(f"경쟁사 {comp_count}")
    if categorized.get("업계"):
        parts.append(f"업계 {len(categorized['업계'])}")
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
    FONT = "'Malgun Gothic','맑은 고딕','Apple SD Gothic Neo',sans-serif"

    # 모니터링 기간 계산
    now_kst = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
    search_days = int(os.environ.get("SEARCH_DAYS", "1"))
    window_start = (now_kst - datetime.timedelta(days=search_days)).replace(
        hour=9, minute=0, second=0, microsecond=0)
    start_wd = ["월", "화", "수", "목", "금", "토", "일"][window_start.weekday()]
    end_wd = weekday
    period_str = (f"{window_start.month}/{window_start.day}({start_wd}) "
                  f"{window_start.strftime('%H:%M')} ~ "
                  f"{now_kst.month}/{now_kst.day}({end_wd}) "
                  f"{now_kst.strftime('%H:%M')}")

    html = f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8">
<title>ABLE C&amp;C DAILY MONITORING {today}</title>
</head>
<body style="margin:0;padding:0;background-color:#FAF6F1;font-family:{FONT};font-size:11pt;">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#FAF6F1;">
<tr><td align="center" style="padding:16px;">
<table width="640" cellpadding="0" cellspacing="0" border="0" style="width:640px;max-width:640px;font-family:{FONT};font-size:11pt;color:#2B2620;">

<tr><td style="padding:2px 4px 12px 4px;border-bottom:3px solid #E8730C;">
<div style="font-size:16pt;font-weight:bold;color:#2B2620;letter-spacing:.5px;font-family:{FONT};">ABLE C&amp;C · DAILY MONITORING</div>
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:9px;">
<tr><td style="vertical-align:bottom;">
<span style="font-size:12pt;font-weight:bold;color:#C25A00;font-family:{FONT};">{today.replace('-', '. ')} ({weekday})</span>
</td><td align="right" style="vertical-align:bottom;font-size:8pt;color:#857B6F;font-family:{FONT};">
모니터링 기간 &nbsp;{period_str}
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
<div style="font-size:11pt;font-weight:bold;color:#2B2620;padding-bottom:8px;font-family:'Malgun Gothic','맑은 고딕',sans-serif;">Today Summary</div>
<div style="font-size:11pt;color:#4a4036;line-height:1.8;font-family:'Malgun Gothic','맑은 고딕',sans-serif;">{summary_text}</div>
</td></tr></table></td></tr>"""

        # 경쟁사 메이저+인디 통합
        competitor_arts = grouped.get("경쟁사메이저", []) + grouped.get("경쟁사인디", [])
        industry_arts = grouped.get("업계", [])

        # 7. 업계에서 경쟁사 키워드가 헤드라인에 있는 기사 → 경쟁사로 이동
        all_comp_keywords = set(COMPETITOR_MAJOR_KEYWORDS + COMPETITOR_INDIE_KEYWORDS)
        moved_to_comp = []
        kept_industry = []
        for a in industry_arts:
            title = a["title"]
            if any(kw in title for kw in all_comp_keywords):
                moved_to_comp.append(a)
            else:
                kept_industry.append(a)
        competitor_arts.extend(moved_to_comp)
        industry_arts = kept_industry

        # 8. 업계 기사 중 키워드에 없는 기업명만 있는 기사 제외
        all_known_keywords = set(
            COMPANY_KEYWORDS + COMPETITOR_MAJOR_KEYWORDS +
            COMPETITOR_INDIE_KEYWORDS + INDUSTRY_KEYWORDS
        )
        filtered_industry = []
        for a in industry_arts:
            title = a["title"]
            desc = a.get("description", "")
            text = title + " " + desc
            # 업계 키워드(화장품, 뷰티, 올리브영 등) 또는 알려진 키워드가 하나라도 있으면 포함
            if any(kw in text for kw in all_known_keywords):
                filtered_industry.append(a)
        industry_arts = filtered_industry

        # 6. 업계 30건 제한 (티어 높은 순 정렬)
        industry_arts.sort(key=lambda x: x.get("tier_score", 1), reverse=True)
        if len(industry_arts) > 30:
            industry_arts = industry_arts[:30]

        # 경쟁사 메이저/인디 분리 유지 (서브섹션용)
        comp_major = grouped.get("경쟁사메이저", [])
        comp_indie = grouped.get("경쟁사인디", [])
        # 업계에서 이동한 기사는 메이저/인디 판별
        major_kw_set = set(COMPETITOR_MAJOR_KEYWORDS)
        for a in moved_to_comp:
            if any(kw in a["title"] for kw in major_kw_set):
                comp_major.append(a)
            else:
                comp_indie.append(a)

        jasa_baepo = grouped.get("자사배포", [])
        jasa_news = grouped.get("자사언급", [])
        # 1. 자사 섹션 (배포 + 뉴스 서브)
        if jasa_baepo or jasa_news:
            html += _build_section_with_subs("1", "자사", "#E8730C", [
                ("배포", jasa_baepo),
                ("뉴스", jasa_news),
            ])

        # 3. 경쟁사 섹션 (메이저 + 인디 서브)
        if comp_major or comp_indie:
            html += _build_section_with_subs("3", "경쟁사", "#E8730C", [
                ("메이저", comp_major),
                ("인디", comp_indie),
            ])

        # 4. 업계·트렌드
        if industry_arts:
            html += _build_section_flat("4", "업계·트렌드", "#E8730C", industry_arts)

    html += f"""<tr><td style="padding:16px 0 0 0;">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-top:1px solid #E5DACB;">
<tr><td align="center" style="padding:12px 15px;font-size:8pt;color:#857B6F;font-family:'Malgun Gothic','맑은 고딕',sans-serif;">
Monitoring by <b style="color:#2B2620;">프라우드컴(주)</b> · Copyright 2026 Proudcomm<br>
<span style="font-size:8pt;color:#aaa;">네이버 뉴스 API | Claude AI 분류 | v3</span>
</td></tr></table></td></tr>

</table></td></tr></table></body></html>"""
    return html


def _article_date_prefix(article: dict) -> str:
    """기사 날짜에서 M/D 형식 추출."""
    pub_date = article.get("pub_date", "")
    if not pub_date:
        return ""
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(pub_date)
        return f"{dt.month}/{dt.day}"
    except Exception:
        return ""


def _render_article_line(a: dict) -> str:
    """날짜 | 헤드라인 | 매체명 (3요소만)."""
    FONT = "'Malgun Gothic','맑은 고딕',sans-serif"
    date_str = _article_date_prefix(a)
    title = a["title"]
    link = a["link"]
    source = a["source"]
    date_html = f'<span style="color:#C25A00;font-weight:bold;">{date_str}</span> &nbsp;' if date_str else ""

    return f"""<div style="font-size:11pt;padding:7px 0 7px 12px;line-height:1.6;font-family:{FONT};">
{date_html}<a href="{link}" style="color:#2B2620;text-decoration:none;font-weight:bold;">{title}</a>
<span style="color:#C25A00;font-size:9pt;"> {source} ›</span></div>"""


def _build_section_with_subs(num, label, accent_color, sub_sections):
    """자사(배포/뉴스), 경쟁사(메이저/인디) 등 서브섹션이 있는 섹션."""
    FONT = "'Malgun Gothic','맑은 고딕',sans-serif"
    s = f"""<tr><td style="padding:14px 0 0 0;">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#FFFFFF;border:1px solid #ECE3D8;">
<tr><td style="padding:16px 18px;">
<div style="font-size:12pt;font-weight:bold;color:#2B2620;padding-bottom:10px;font-family:{FONT};">
<span style="color:{accent_color};">{num}</span> &nbsp;&nbsp;{label}</div>"""

    for sub_name, articles in sub_sections:
        if not articles:
            continue
        s += f"""<div style="font-size:11pt;font-weight:bold;color:#E8730C;padding:8px 0 2px 0;border-top:2px solid #F2ECE3;font-family:{FONT};">▌{sub_name}</div>"""
        for a in articles:
            s += _render_article_line(a)

    s += "</td></tr></table></td></tr>"
    return s


def _build_section_flat(num, label, accent_color, articles):
    """서브섹션 없는 단일 섹션 (업계·트렌드)."""
    FONT = "'Malgun Gothic','맑은 고딕',sans-serif"
    s = f"""<tr><td style="padding:14px 0 0 0;">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#FFFFFF;border:1px solid #ECE3D8;">
<tr><td style="padding:16px 18px;">
<div style="font-size:12pt;font-weight:bold;color:#2B2620;padding-bottom:6px;font-family:{FONT};">
<span style="color:{accent_color};">{num}</span> &nbsp;&nbsp;{label}</div>"""

    for a in articles:
        s += _render_article_line(a)

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
- [카테고리] 블록 형식으로 구성 (자사, 경쟁사, 업계, 시사점)
- 한 블록은 3~5문장. 콤마와 마침표로 연결하며 구체적 사실 나열.
- 명사형 종결 가능 ("~중임", "~선점 중임", "~제시함")
- 각 블록 사이에 <br><br>로 줄바꿈
- HTML 형식, <b> 태그로 카테고리 강조

**내용 규칙 (이 순서대로):**
1. <b>[자사]</b> 자사배포+자사언급을 묶어서 구체적 기사 내용 나열 (매체명, 건수, 핵심 팩트). 배포/언급 없으면 이 블록 자체를 생략. "배포 없었다" 같은 말 절대 금지.
2. <b>[경쟁사]</b> 메이저+인디 경쟁사 주요 움직임. 기업명+구체적 행위(신제품 출시, 실적 발표, 제휴 등) 중심. 3~5문장.
3. <b>[업계]</b> 업계 전반 이슈. 유통·플랫폼·수출·규제·트렌드 핵심만. 수치 포함. 3~5문장.
4. <b>[시사점]</b> 미샤/에이블씨엔씨 관점에서 대응 방향. 위 3개 블록과 별도로 작성. 2~3문장.

**참고 예시 (이 형식을 정확히 따를 것):**
<b>[경쟁사]</b> 아모레퍼시픽재단은 '2026 장원특강'(11월 27일~12월 11일)을 앞세워 AI 시대 사고력과 질문의 가치를 집중 조명하는 중임. 아모레퍼시픽은 일본 뷰티테크 행사에서 AI·뷰티 디바이스·바이오를 미래 성장축으로 제시함. LG생활건강은 직장인 대상 두피·모발 '헤어 론젝비티' 클리닉을 반복 진행하며 두피 안티에이징 시장을 선점 중임. 인디에서는 에이피알 메디큐브가 콜라겐 스킨케어 글로벌 누적 1억 7개(해외 비중 80% 이상)를 발표하며 존재감이 커지는 중임.<br><br>
<b>[업계]</b> 무신사 뷰티는 신규 입점 145개와 럭셔리 거래액 110% 증가를 바탕으로 설화수·에 등 신제품 선론칭 채널로 부상함. 11월 화장품 수출은 62% 급증했으나 미국 시장에서 K뷰티가 중국보다 잘 팔리는 흐름이 뚜렷해짐. 한중 FTA 후속타결로 중국 시장 기대감이 살아났으나 면세 특수는 불확실함. 규제 측면에서는 제조정지 조 졸한 OEM 기업 43개 제품의 허가취소 통지 사례가 나와 품질 관리 리스크가 부각됨.<br><br>
<b>[시사점]</b> 메이저는 인문학·AI·두피 등 제품 외 콘텐츠로 브랜드 신뢰를 넓히고, 인디는 글로벌 누적 수치로 성장 서사를 만드는 중임. 미샤/에이블씨엔씨는 온라인 신제품 선론칭 채널과 미국 수출 흐름에 맞춘 대응을 강화하고, 해외 성과를 수치화한 콘텐츠 준비와 OEM 생산 품질 점검이 필요함.

위 예시처럼 블록별로 묶어서 작성. 해당 없는 블록은 생략."""
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

    # 미등록 도메인 출력 (매체명 한글 매핑 추가 필요)
    if _unmapped_domains:
        print(f"[매체] 미등록 도메인 {len(_unmapped_domains)}개:")
        for d in sorted(_unmapped_domains):
            print(f"  - {d}")

    print("=" * 60)
    print(f"완료: {datetime.datetime.now()}")
    print("=" * 60)

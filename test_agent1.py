"""
에이전트1(탐색) + 에이전트2(정리/DB화) 테스트 스크립트

이 스크립트가 하는 일:
1. OpenAlex + Nature/Science RSS에서 최근 논문을 찾는다
   (제목, 저자, 초록, 저널명, 게재일, 출처, 링크까지 구조화해서 수집)
2. 각 논문을 Claude한테 보여주고 "이 연구실과 관련 있는지" 판단시킨다
3. 관련 있는 것만 화면에 출력하고, Supabase DB에 저장한다
   (이미 저장된 링크는 중복 저장하지 않음)

실행 방법: python test_agent1.py
"""

import os
import requests
import feedparser
from datetime import datetime, timedelta
from anthropic import Anthropic
from supabase import create_client

# ============================================
# 1. 설정값 (여기를 연구실 상황에 맞게 바꾸세요)
# ============================================

# 연구실 키워드 - 영어로, OpenAlex 검색에 쓰일 거예요
LAB_KEYWORDS = [
    "self-powered sensor",
    "triboelectric nanogenerator",
    "gas sensor nanomaterial",
    "wearable biosignal sensor",
]

# 연구실 프로필 설명 - Claude가 관련성을 판단할 때 참고할 문맥
LAB_PROFILE = """
성균관대학교 장지수 교수 연구실은 나노소재·멤브레인 기술을 기반으로
환경 에너지를 전기로 변환하고, 유해가스와 생체신호를 감지·분석하는
자가발전형 지능형 센서 및 바이오전자소자를 개발하는 연구실입니다.
주요 관심사: 나노소재, 멤브레인, 에너지 하베스팅, 가스센서, 생체신호센서, 바이오전자소자
"""

# 최근 며칠치 논문을 볼지
DAYS_BACK = 7

# Nature/Science RSS 피드
# 종합지 대신(또는 같이), 이 연구실 분야에 맞는 세부 전문지를 넣었어요.
# 논문 수가 너무 많다 싶으면 아래 목록에서 몇 개 빼셔도 됩니다.
RSS_FEEDS = {
    "Nature Nanotechnology": "https://www.nature.com/nnano.rss",
    "Nature Energy": "https://www.nature.com/nenergy.rss",
    "Nature Electronics": "https://www.nature.com/natelectron.rss",
    "Nature Materials": "https://www.nature.com/nmat.rss",
    "Nature Biomedical Engineering": "https://www.nature.com/natbiomedeng.rss",
    "Science Advances": "https://www.science.org/action/showFeed?type=etoc&feed=rss&jc=sciadv",
}

# 참고: 종합 Nature/Science 전체를 다시 보고 싶으시면 아래 두 줄을 위 딕셔너리에 추가하세요.
# "Nature": "https://www.nature.com/nature.rss",
# "Science": "https://www.science.org/action/showFeed?type=etoc&feed=rss&jc=science",

# OpenAlex API 키 (2026년 2월부터 필수로 바뀌었어요)
# 무료 가입 후 즉시 발급: https://openalex.org/settings/api
#   PowerShell: $env:OPENALEX_API_KEY="여기에_받은_키"
OPENALEX_API_KEY = os.environ.get("OPENALEX_API_KEY")

# Supabase 접속 정보
#   PowerShell: $env:SUPABASE_URL="..."  /  $env:SUPABASE_KEY="..."
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

# RSS/API 요청 시 "저는 사람이 쓰는 브라우저입니다"라고 알려주는 정보.
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# Science RSS 같은 일부 피드는 개별 논문이 아니라
# "이번 호 요약", "이번 주 뉴스" 같은 섹션 안내 항목도 같이 들어있어요.
NON_ARTICLE_TITLE_KEYWORDS = [
    "in science journals",
    "this week in science",
    "editors' choice",
    "news at a glance",
    "in other journals",
    "table of contents",
]

# ============================================
# 2. OpenAlex에서 논문 검색하기
# ============================================

def _reconstruct_abstract(inverted_index):
    """OpenAlex의 초록(단어별 위치 목록)을 읽을 수 있는 문장으로 재조립합니다."""
    if not inverted_index:
        return "(초록 없음)"
    position_word = {}
    for word, positions in inverted_index.items():
        for pos in positions:
            position_word[pos] = word
    return " ".join(position_word[i] for i in sorted(position_word))


def search_openalex(keyword, days_back=7):
    """
    OpenAlex API로 키워드 검색해서 최근 논문 목록을 '구조화된 딕셔너리'로 가져옵니다.
    각 논문은 title/authors/abstract/journal/publication_date/link/source 필드를 가져요.
    """
    end_date = datetime.now().date()
    start_date = end_date - timedelta(days=days_back)

    url = "https://api.openalex.org/works"
    params = {
        "search": keyword,
        "filter": f"from_publication_date:{start_date},to_publication_date:{end_date}",
        "per_page": 10,
    }
    if OPENALEX_API_KEY:
        params["api_key"] = OPENALEX_API_KEY

    try:
        response = requests.get(url, params=params, headers=BROWSER_HEADERS, timeout=15)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"  [경고] OpenAlex 요청 실패 (건너뜁니다): {e}")
        return []

    data = response.json()
    papers = []
    for item in data.get("results", []):
        title = item.get("title") or "제목 없음"
        abstract = _reconstruct_abstract(item.get("abstract_inverted_index"))

        # 저자 목록 추출 - authorships 안에 저자별 정보가 들어있음
        authorships = item.get("authorships") or []
        author_names = [
            (a.get("author") or {}).get("display_name", "")
            for a in authorships
        ]
        authors = ", ".join(n for n in author_names if n) or "(저자 정보 없음)"

        primary_location = item.get("primary_location") or {}
        source_info = primary_location.get("source") or {}
        journal = source_info.get("display_name") or "(학술지 정보 없음)"

        publication_date = item.get("publication_date") or "(날짜 정보 없음)"
        link = item.get("doi") or item.get("id") or ""

        papers.append({
            "title": title,
            "authors": authors,
            "abstract": abstract,
            "journal": journal,
            "publication_date": publication_date,
            "link": link,
            "source": "OpenAlex",
        })

    return papers


# ============================================
# 2-2. RSS 피드에서 논문(글) 목록 가져오기
# ============================================

def fetch_rss(feed_name, feed_url):
    """
    RSS 주소 하나를 읽어서, 최근 게시물들을 다른 소스와 같은
    구조화된 딕셔너리 형태로 변환합니다.
    """
    try:
        response = requests.get(feed_url, headers=BROWSER_HEADERS, timeout=10)
        response.raise_for_status()
        parsed = feedparser.parse(response.content)
    except requests.exceptions.RequestException as e:
        print(f"  [경고] {feed_name} RSS 접속 실패 (건너뜁니다): {e}")
        return []

    if parsed.bozo and not parsed.entries:
        print(f"  [경고] {feed_name} RSS를 읽는 데 문제가 있었어요: {parsed.get('bozo_exception')}")
        return []

    articles = []
    for entry in parsed.entries:
        title = entry.get("title", "제목 없음")
        summary = entry.get("summary", entry.get("description", ""))
        link = entry.get("link", "")

        # 품질 필터1: 요약이 아예 없는 항목은 실제 논문이 아닐 가능성이 높음
        if not summary or len(summary.strip()) < 20:
            continue

        # 품질 필터2: "이번 호 요약" 같은 섹션 안내성 제목은 논문이 아니므로 제외
        if any(kw in title.lower() for kw in NON_ARTICLE_TITLE_KEYWORDS):
            continue

        # 저자 - RSS마다 필드명이 달라서 여러 후보를 시도 (없으면 빈 값)
        authors = entry.get("author", "") or "(저자 정보 없음)"

        # 게재일 - RSS 표준 필드는 published, 없으면 updated
        publication_date = entry.get("published", entry.get("updated", "(날짜 정보 없음)"))

        articles.append({
            "title": title,
            "authors": authors,
            "abstract": summary,
            "journal": feed_name,
            "publication_date": publication_date,
            "link": link,
            "source": f"{feed_name} RSS",
        })

    return articles


# ============================================
# 3. Claude한테 관련성 판단시키기
# ============================================

def judge_relevance(client, paper, lab_profile):
    """
    논문 딕셔너리 하나를 Claude에게 보여주고
    이 연구실과 관련 있는지, 몇 점인지, 왜 그런지 판단하게 합니다.
    """
    paper_text = (
        f"제목: {paper['title']}\n"
        f"저자: {paper['authors']}\n"
        f"저널: {paper['journal']}\n"
        f"게재일: {paper['publication_date']}\n"
        f"초록: {paper['abstract'][:1200]}"
    )

    prompt = f"""당신은 연구실 논문 큐레이터입니다. 아래 연구실 프로필과 논문 내용을 보고 판단하세요.

[연구실 프로필]
{lab_profile}

[논문 내용]
{paper_text}

다음 형식으로만 답하세요 (다른 말 붙이지 마세요):
점수: (0~100 사이 숫자)
이유: (관련성 판단 이유, 한 문장, 한국어로, '~습니다'체로 작성)
요약: (이 논문이 무엇을 했고 어떤 결과를 얻었는지, 연구실 담당자가 읽을 수 있게 2~3문장으로 한국어 요약. 원문을 그대로 번역하지 말고 핵심만 쉽게 풀어서 설명. 모든 문장은 반드시 '~습니다'로 끝나는 존댓말체로 통일해서 작성하세요.
"정보가 부족합니다", "정확한 요약이 어렵습니다", "전체 내용을 제공해주세요" 같은 사과성/요청성 문구는 절대 스스로 만들어내지 마세요.
단, 초록이 "(초록 없음)"으로 표시되어 있거나 내용이 너무 짧아(40자 미만) 실질적으로 판단이 어려운 경우에는, 제목만 보고 어떤 연구를 했을지 조심스럽게 추론한 한 문장을 만든 다음 다음 형식 그대로 작성하세요: 해당 논문의 초록을 불러올 수 없습니다. 자세한 내용은 링크를 참고해주세요. (제목 정보만으로는 "추론한 문장"을 추론할 수 있습니다.))
"""

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",  # 테스트 단계라 가장 저렴한 모델 사용
        max_tokens=500,  # 요약이 추가되어 응답이 길어져서 늘림
        messages=[{"role": "user", "content": prompt}]
    )

    return message.content[0].text


def parse_score(claude_result_text):
    """Claude 응답 텍스트에서 '점수: 85' 같은 줄을 찾아 숫자만 뽑아냅니다."""
    try:
        score_line = [l for l in claude_result_text.split("\n") if "점수" in l][0]
        return int("".join(filter(str.isdigit, score_line)))
    except (ValueError, IndexError):
        return None


def parse_summary(claude_result_text):
    """Claude 응답 텍스트에서 '요약:' 뒷부분을 뽑아냅니다 (여러 줄일 수 있음)."""
    marker = "요약:"
    idx = claude_result_text.find(marker)
    if idx == -1:
        return ""
    return claude_result_text[idx + len(marker):].strip()


def parse_reason(claude_result_text):
    """Claude 응답 텍스트에서 '이유:' 줄만 뽑아냅니다 ('요약:' 시작 전까지)."""
    reason_idx = claude_result_text.find("이유:")
    summary_idx = claude_result_text.find("요약:")
    if reason_idx == -1:
        return ""
    end = summary_idx if summary_idx != -1 else len(claude_result_text)
    return claude_result_text[reason_idx + len("이유:"):end].strip()


# ============================================
# 3-2. Supabase에 저장하기 (에이전트2: 정리/DB화)
# ============================================

def save_to_supabase(supabase_client, paper, lab_keyword=""):
    """
    관련성 있다고 판단된 논문 하나를 papers 테이블에 저장합니다.
    link에 unique 제약을 걸어뒀기 때문에, 이미 저장된 논문(같은 링크)이면
    에러 대신 조용히 건너뜁니다 (중복 저장 방지).
    """
    row = {
        "title": paper["title"],
        "authors": paper["authors"],
        "abstract": paper["abstract"],
        "journal": paper["journal"],
        "publication_date": paper["publication_date"],
        "link": paper["link"],
        "source": paper["source"],
        "relevance_score": paper["score"],
        "relevance_reason": paper["reason"],
        "summary_ko": paper.get("summary_ko", ""),
        "lab_keyword": lab_keyword,
    }

    try:
        # upsert: link가 이미 있으면 무시(update 안 함), 없으면 새로 삽입
        supabase_client.table("papers").upsert(
            row, on_conflict="link", ignore_duplicates=True
        ).execute()
        return True
    except Exception as e:
        print(f"  [경고] DB 저장 실패: {e}")
        return False


def process_papers(client, supabase_client, papers, all_relevant_papers, lab_keyword=""):
    """공통 처리 로직: 논문 목록을 Claude로 판단하고, 70점 넘으면 결과에 추가 + DB저장."""
    for paper in papers:
        print(f"\n판단 중: {paper['title'][:60]}...")

        result = judge_relevance(client, paper, LAB_PROFILE)
        print(result)

        score = parse_score(result)
        if score is not None and score >= 70:
            paper_with_judgement = dict(paper)
            paper_with_judgement["score"] = score
            paper_with_judgement["reason"] = result
            paper_with_judgement["summary_ko"] = parse_summary(result)
            all_relevant_papers.append(paper_with_judgement)

            if supabase_client:
                save_to_supabase(supabase_client, paper_with_judgement, lab_keyword)


def main():
    # API 키는 환경변수에서 자동으로 읽어옵니다 (코드에 직접 안 씀)
    client = Anthropic()  # ANTHROPIC_API_KEY 환경변수를 자동으로 사용

    if not OPENALEX_API_KEY:
        print("[안내] OPENALEX_API_KEY 환경변수가 설정되어 있지 않아요. "
              "openalex.org/settings/api 에서 무료로 키를 받아 등록해주세요.\n")

    supabase_client = None
    if SUPABASE_URL and SUPABASE_KEY:
        supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)
        print("[안내] Supabase에 연결되어, 관련 논문은 DB에도 저장됩니다.\n")
    else:
        print("[안내] SUPABASE_URL / SUPABASE_KEY가 설정되어 있지 않아 "
              "이번 실행은 DB 저장 없이 화면 출력만 됩니다.\n")

    all_relevant_papers = []

    # ---- 소스 1: OpenAlex (키워드 검색 기반, 재료공학/센서 커버리지 넓음) ----
    for keyword in LAB_KEYWORDS:
        print(f"\n=== [OpenAlex] '{keyword}' 검색 중... ===")
        papers = search_openalex(keyword, DAYS_BACK)
        print(f"검색된 논문 수: {len(papers)}")
        process_papers(client, supabase_client, papers, all_relevant_papers, lab_keyword=keyword)

    # ---- 소스 2: Nature/Science RSS (최고 권위 저널 최신호 체크) ----
    for feed_name, feed_url in RSS_FEEDS.items():
        print(f"\n=== [{feed_name} RSS] 가져오는 중... ===")
        articles = fetch_rss(feed_name, feed_url)
        print(f"가져온 게시물 수: {len(articles)}")
        process_papers(client, supabase_client, articles, all_relevant_papers, lab_keyword=f"{feed_name} RSS")

    # 관련도 점수 높은 순으로 정렬
    all_relevant_papers.sort(key=lambda p: p["score"], reverse=True)

    # 최종 결과 요약 - 이제 제목뿐 아니라 저자/저널/날짜/출처/링크까지 다 보여줍니다
    print("\n\n" + "=" * 60)
    print(f"최종 관련 논문 수: {len(all_relevant_papers)}")
    print("=" * 60)
    for p in all_relevant_papers:
        print(f"""
[{p['score']}점] {p['title']}
  저자: {p['authors']}
  저널: {p['journal']} | 게재일: {p['publication_date']} | 출처: {p['source']}
  링크: {p['link']}
  판단 이유: {parse_reason(p['reason'])}
  한글 요약: {p['summary_ko']}
""")


if __name__ == "__main__":
    main()
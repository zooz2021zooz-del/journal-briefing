"""
논문 검색 + 관련성 판단 로직 (test_agent1.py의 핵심 로직을 웹 요청용으로 재사용)
검색 결과는 화면에 보여줌과 동시에, 설정돼 있으면 Supabase의 web_papers 테이블에도 누적 저장합니다.
(기존 자동발송 파이프라인이 쓰는 papers 테이블과는 별도)
"""

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

import feedparser
import requests
from anthropic import Anthropic
from supabase import create_client

OPENALEX_API_KEY = os.environ.get("OPENALEX_API_KEY")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

_supabase_client = None
if SUPABASE_URL and SUPABASE_KEY:
    _supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# 한 번 검색에 너무 많은 API 호출(비용/시간)이 발생하지 않도록 상한을 둡니다.
# (Render 등 배포 환경의 요청 타임아웃 안에 끝나야 하므로 총 판단 건수도 별도로 제한)
# 판단을 병렬로 처리하므로(JUDGE_CONCURRENCY), 총 건수를 늘려도 소요 시간은 크게 늘지 않습니다.
# OpenAlex와 RSS는 서로 다른 상한을 따로 가져서, 키워드가 많다고 RSS(Nature/Science)가
# 밀려나 아예 검색되지 않는 일이 없게 합니다.
MAX_KEYWORDS = 5
MAX_PAPERS_PER_KEYWORD = 8
MAX_OPENALEX_TOTAL = 25
MAX_RSS_TOTAL = 25
MAX_PER_RSS_FEED = 8
JUDGE_CONCURRENCY = 8

# test_agent1.py와 동일한 Nature/Science 계열 세부 저널 RSS 피드
RSS_FEEDS = {
    "Nature Nanotechnology": "https://www.nature.com/nnano.rss",
    "Nature Energy": "https://www.nature.com/nenergy.rss",
    "Nature Electronics": "https://www.nature.com/natelectron.rss",
    "Nature Materials": "https://www.nature.com/nmat.rss",
    "Nature Biomedical Engineering": "https://www.nature.com/natbiomedeng.rss",
    "Science Advances": "https://www.science.org/action/showFeed?type=etoc&feed=rss&jc=sciadv",
}

NON_ARTICLE_TITLE_KEYWORDS = [
    "in science journals",
    "this week in science",
    "editors' choice",
    "news at a glance",
    "in other journals",
    "table of contents",
]


def _reconstruct_abstract(inverted_index):
    if not inverted_index:
        return "(초록 없음)"
    position_word = {}
    for word, positions in inverted_index.items():
        for pos in positions:
            position_word[pos] = word
    return " ".join(position_word[i] for i in sorted(position_word))


def search_openalex(keyword, days_back=7, per_page=MAX_PAPERS_PER_KEYWORD):
    end_date = datetime.now().date()
    start_date = end_date - timedelta(days=days_back)

    url = "https://api.openalex.org/works"
    params = {
        "search": keyword,
        "filter": f"from_publication_date:{start_date},to_publication_date:{end_date}",
        "per_page": per_page,
    }
    if OPENALEX_API_KEY:
        params["api_key"] = OPENALEX_API_KEY

    try:
        response = requests.get(url, params=params, headers=BROWSER_HEADERS, timeout=15)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        return [], f"OpenAlex 요청 실패: {e}"

    data = response.json()
    papers = []
    for item in data.get("results", []):
        title = item.get("title") or "제목 없음"
        abstract = _reconstruct_abstract(item.get("abstract_inverted_index"))

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

    return papers, None


def fetch_rss(feed_name, feed_url, limit=MAX_PER_RSS_FEED):
    """RSS 피드 하나에서 최근 게시물을 구조화된 딕셔너리 목록으로 가져옵니다."""
    try:
        response = requests.get(feed_url, headers=BROWSER_HEADERS, timeout=10)
        response.raise_for_status()
        parsed = feedparser.parse(response.content)
    except requests.exceptions.RequestException as e:
        return [], f"{feed_name} RSS 접속 실패: {e}"

    if parsed.bozo and not parsed.entries:
        return [], f"{feed_name} RSS를 읽는 데 문제가 있었어요: {parsed.get('bozo_exception')}"

    articles = []
    for entry in parsed.entries:
        if len(articles) >= limit:
            break

        title = entry.get("title", "제목 없음")
        summary = entry.get("summary", entry.get("description", ""))
        link = entry.get("link", "")

        if not summary or len(summary.strip()) < 20:
            continue
        if any(kw in title.lower() for kw in NON_ARTICLE_TITLE_KEYWORDS):
            continue

        authors = entry.get("author", "") or "(저자 정보 없음)"
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

    return articles, None


def judge_relevance(client, paper, lab_profile):
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
요약: (이 논문이 무엇을 했고 어떤 결과를 얻었는지, 연구실 담당자가 읽을 수 있게 2~3문장으로 한국어 요약. 모든 문장은 반드시 '~습니다'로 끝나는 존댓말체로 작성하세요.)
"""

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}]
    )

    return message.content[0].text


def parse_score(text):
    try:
        score_line = [l for l in text.split("\n") if "점수" in l][0]
        return int("".join(filter(str.isdigit, score_line)))
    except (ValueError, IndexError):
        return None


def parse_summary(text):
    marker = "요약:"
    idx = text.find(marker)
    if idx == -1:
        return ""
    return text[idx + len(marker):].strip()


def parse_reason(text):
    reason_idx = text.find("이유:")
    summary_idx = text.find("요약:")
    if reason_idx == -1:
        return ""
    end = summary_idx if summary_idx != -1 else len(text)
    return text[reason_idx + len("이유:"):end].strip()


def save_web_results(papers, lab_name):
    """검색된 논문들을 web_papers 테이블에 누적 저장합니다 (link 기준 중복 무시)."""
    if not _supabase_client or not papers:
        return

    rows = [
        {
            "title": p["title"],
            "authors": p["authors"],
            "abstract": p["abstract"],
            "journal": p["journal"],
            "publication_date": p["publication_date"],
            "link": p["link"],
            "source": p["source"],
            "relevance_score": p["score"],
            "relevance_reason": p["reason"],
            "summary_ko": p["summary_ko"],
            "lab_keyword": p.get("matched_keyword", ""),
            "lab_name": lab_name,
        }
        for p in papers
        if p.get("link")
    ]
    if not rows:
        return

    try:
        _supabase_client.table("web_papers").upsert(
            rows, on_conflict="link", ignore_duplicates=True
        ).execute()
    except Exception as e:
        print(f"[경고] web_papers 저장 실패: {e}")


def is_top_journal(journal):
    """저널명이 Nature/Science 계열(세부 저널 포함)인지 판단합니다.
    'Materials Science in ...'처럼 이름 중간에 science가 들어간 일반 저널과
    구분하기 위해, 이름이 Nature/Science로 시작하는 경우만 인정합니다.
    """
    j = (journal or "").strip().lower()
    return j.startswith("nature") or j.startswith("science")


def reset_web_history():
    """web_papers 테이블의 누적 기록을 전부 삭제합니다."""
    if not _supabase_client:
        return False
    try:
        _supabase_client.table("web_papers").delete().neq("id", 0).execute()
        return True
    except Exception as e:
        print(f"[경고] web_papers 초기화 실패: {e}")
        return False


def fetch_web_history(limit=200):
    """마이페이지에 보여줄 누적 검색 기록을 최신순으로 가져옵니다."""
    if not _supabase_client:
        return []
    try:
        response = (
            _supabase_client.table("web_papers")
            .select("*")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return response.data
    except Exception as e:
        print(f"[경고] web_papers 조회 실패: {e}")
        return []


def run_search(keywords, lab_profile, days_back=7, min_score=70, include_rss=True):
    """
    키워드 목록 + 연구실 프로필을 받아 실시간으로 OpenAlex(+선택 시 Nature/Science RSS)
    검색과 Claude 판단을 수행하고, 관련도 min_score 이상인 논문 목록(점수 높은 순)과
    경고 메시지 목록을 반환합니다.
    Claude 판단은 여러 건을 동시에 호출해서(JUDGE_CONCURRENCY) 전체 요청이
    배포 환경의 요청 타임아웃 안에 끝나도록 합니다.
    """
    client = Anthropic()  # ANTHROPIC_API_KEY 환경변수 사용

    keywords = [k.strip() for k in keywords if k.strip()][:MAX_KEYWORDS]

    warnings = []
    candidates = []
    seen_links = set()
    openalex_count = 0
    rss_count = 0

    for keyword in keywords:
        if openalex_count >= MAX_OPENALEX_TOTAL:
            break

        papers, err = search_openalex(keyword, days_back)
        if err:
            warnings.append(f"[{keyword}] {err}")
            continue

        for paper in papers:
            if paper["link"] and paper["link"] in seen_links:
                continue
            if openalex_count >= MAX_OPENALEX_TOTAL:
                break
            candidates.append((paper, keyword))
            openalex_count += 1
            if paper["link"]:
                seen_links.add(paper["link"])

    if include_rss:
        for feed_name, feed_url in RSS_FEEDS.items():
            if rss_count >= MAX_RSS_TOTAL:
                break

            articles, err = fetch_rss(feed_name, feed_url)
            if err:
                warnings.append(err)
                continue

            for article in articles:
                if article["link"] and article["link"] in seen_links:
                    continue
                if rss_count >= MAX_RSS_TOTAL:
                    break
                candidates.append((article, feed_name))
                rss_count += 1
                if article["link"]:
                    seen_links.add(article["link"])

    relevant_papers = []

    def _judge(paper, keyword):
        return paper, keyword, judge_relevance(client, paper, lab_profile)

    with ThreadPoolExecutor(max_workers=JUDGE_CONCURRENCY) as executor:
        futures = [executor.submit(_judge, paper, keyword) for paper, keyword in candidates]
        for future in as_completed(futures):
            try:
                paper, keyword, result_text = future.result()
            except Exception as e:
                warnings.append(f"판단 실패: {e}")
                continue

            score = parse_score(result_text)
            if score is not None and score >= min_score:
                paper_with_judgement = dict(paper)
                paper_with_judgement["score"] = score
                paper_with_judgement["reason"] = parse_reason(result_text)
                paper_with_judgement["summary_ko"] = parse_summary(result_text)
                paper_with_judgement["matched_keyword"] = keyword
                relevant_papers.append(paper_with_judgement)

    relevant_papers.sort(key=lambda p: p["score"], reverse=True)
    return relevant_papers, warnings

"""
논문 검색 + 관련성 판단 로직 (test_agent1.py의 핵심 로직을 웹 요청용으로 재사용)
DB 저장 없이, 요청마다 실시간으로 검색/판단해서 결과를 반환합니다.
"""

import os
from datetime import datetime, timedelta

import requests
from anthropic import Anthropic

OPENALEX_API_KEY = os.environ.get("OPENALEX_API_KEY")

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# 한 번 검색에 너무 많은 API 호출(비용)이 발생하지 않도록 상한을 둡니다.
MAX_KEYWORDS = 5
MAX_PAPERS_PER_KEYWORD = 5


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


def run_search(keywords, lab_profile, days_back=7, min_score=70):
    """
    키워드 목록 + 연구실 프로필을 받아 실시간으로 OpenAlex 검색 + Claude 판단을 수행하고
    관련도 min_score 이상인 논문 목록(점수 높은 순)과 경고 메시지 목록을 반환합니다.
    """
    client = Anthropic()  # ANTHROPIC_API_KEY 환경변수 사용

    keywords = [k.strip() for k in keywords if k.strip()][:MAX_KEYWORDS]

    warnings = []
    relevant_papers = []
    seen_links = set()

    for keyword in keywords:
        papers, err = search_openalex(keyword, days_back)
        if err:
            warnings.append(f"[{keyword}] {err}")
            continue

        for paper in papers:
            if paper["link"] and paper["link"] in seen_links:
                continue

            try:
                result_text = judge_relevance(client, paper, lab_profile)
            except Exception as e:
                warnings.append(f"[{paper['title'][:40]}...] 판단 실패: {e}")
                continue

            score = parse_score(result_text)
            if score is not None and score >= min_score:
                paper_with_judgement = dict(paper)
                paper_with_judgement["score"] = score
                paper_with_judgement["reason"] = parse_reason(result_text)
                paper_with_judgement["summary_ko"] = parse_summary(result_text)
                paper_with_judgement["matched_keyword"] = keyword
                relevant_papers.append(paper_with_judgement)
                if paper["link"]:
                    seen_links.add(paper["link"])

    relevant_papers.sort(key=lambda p: p["score"], reverse=True)
    return relevant_papers, warnings

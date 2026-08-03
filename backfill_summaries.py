"""
한국어 요약 보충(backfill) 스크립트

이 스크립트가 하는 일:
1. Supabase의 papers 테이블에서 summary_ko가 비어있는 논문들을 찾는다
   (한국어 요약 기능을 추가하기 전에 저장됐던 옛날 데이터들)
2. 각 논문의 제목/초록을 Claude에게 보여주고 한국어 요약을 만들게 한다
3. 그 요약을 DB에 업데이트한다

실행 방법: python backfill_summaries.py
"""

import os
from supabase import create_client
from anthropic import Anthropic

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")


def fetch_papers_missing_summary(supabase_client):
    """summary_ko가 비어있거나(null) 빈 문자열인 논문들을 가져옵니다."""
    response = (
        supabase_client.table("papers")
        .select("*")
        .or_("summary_ko.is.null,summary_ko.eq.")
        .execute()
    )
    return response.data


def generate_summary(client, paper):
    """제목/초록을 보고 한국어 요약을 생성합니다. 초록이 없으면 제목만으로 추론한
    한 문장을 지정된 안내 템플릿 안에 넣어서 반환합니다."""
    abstract = (paper.get("abstract") or "").strip()
    title = paper.get("title", "")

    # 초록이 아예 없거나, 있어도 너무 짧아서 (40자 미만) 내용 판단이 어려운 경우
    is_insufficient = abstract in EMPTY_ABSTRACT_MARKERS or len(abstract) < 40

    if is_insufficient:
        prompt = f"""다음 논문 제목만 보고, 어떤 연구를 했을지 한 문장으로 추론해주세요.
'~했을 가능성' 같은 추측 표현을 써서 조심스럽게 작성하고, 반드시 한국어로 작성하세요.
문장만 답하고 다른 말은 붙이지 마세요 (따옴표도 붙이지 마세요).

제목: {title}
"""
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}]
        )
        inferred = message.content[0].text.strip()
        return (
            f'해당 논문의 초록을 불러올 수 없습니다. 자세한 내용은 링크를 참고해주세요. '
            f'(제목 정보만으로는 "{inferred}"을 추론할 수 있습니다.)'
        )

    prompt = f"""다음 논문의 내용을 연구실 담당자가 읽기 편하게 한국어로 2~3문장 요약해주세요.
원문을 그대로 번역하지 말고 핵심(무엇을 했고 어떤 결과를 얻었는지)만 쉽게 풀어서 설명하세요.
모든 문장은 반드시 '~습니다'로 끝나는 존댓말체로 통일해서 작성하세요.
"정보가 부족합니다", "정확한 요약이 어렵습니다", "전체 내용을 제공해주세요" 같은
사과성/요청성 문구는 절대 스스로 만들어내지 마세요. 주어진 정보만으로 최선의 요약을 작성하세요.
요약 내용만 답하고 다른 말은 붙이지 마세요.

제목: {title}
초록: {abstract[:1200]}
"""

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}]
    )
    return message.content[0].text.strip()


# 초록이 비어있다고 간주할 값들
EMPTY_ABSTRACT_MARKERS = ("", "(초록 없음)", "(초록 정보 없음)", "None")


def main():
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("[오류] SUPABASE_URL / SUPABASE_KEY 환경변수를 먼저 등록해주세요.")
        return

    supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)
    client = Anthropic()  # ANTHROPIC_API_KEY 환경변수 자동 사용

    print("한국어 요약이 없는 논문을 찾는 중...")
    papers = fetch_papers_missing_summary(supabase_client)
    print(f"채워야 할 논문 수: {len(papers)}")

    if not papers:
        print("이미 다 채워져 있어요. 할 일이 없어요!")
        return

    for i, paper in enumerate(papers, 1):
        title = paper.get("title", "")[:50]
        print(f"\n[{i}/{len(papers)}] 요약 생성 중: {title}...")

        try:
            summary = generate_summary(client, paper)
            print(f"  → {summary[:80]}...")

            supabase_client.table("papers").update(
                {"summary_ko": summary}
            ).eq("id", paper["id"]).execute()
            print("  ✅ DB 업데이트 완료")
        except Exception as e:
            print(f"  [경고] 실패해서 건너뜁니다: {e}")

    print(f"\n\n완료! 총 {len(papers)}건 처리했어요.")


if __name__ == "__main__":
    main()
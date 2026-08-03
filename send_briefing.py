"""
에이전트3(발송) 스크립트

이 스크립트가 하는 일:
1. Supabase의 papers 테이블에서 "아직 발송 안 된"(sent_at이 비어있는) 논문들을 가져온다
2. 이메일로 예쁘게 정리해서 보낸다
3. 발송 성공하면 sent_at을 현재 시각으로 업데이트한다 (다음번에 중복 발송 방지)

실행 방법: python send_briefing.py
"""

import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone
from supabase import create_client

# ============================================
# 설정값
# ============================================

# Supabase 접속 정보 (test_agent1.py와 동일한 환경변수 사용)
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

# 발송용 Gmail 계정 정보
#   PowerShell: $env:GMAIL_ADDRESS="보내는사람@gmail.com"
#               $env:GMAIL_APP_PASSWORD="16자리 앱 비밀번호 (공백 없이)"
GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD")

# 받는 사람 이메일 (연구실 담당자)
RECIPIENT_EMAIL = "daesung8680@gmail.com"  # ← 실제 주소로 바꿔주세요

# 연구실 이름 (이메일 제목/본문에 쓰임)
LAB_NAME = "test_journals"


# ============================================
# 1. DB에서 미발송 논문 가져오기
# ============================================

def fetch_unsent_papers(supabase_client):
    """sent_at이 비어있는(=아직 안 보낸) 논문들을 가져옵니다."""
    response = (
        supabase_client.table("papers")
        .select("*")
        .is_("sent_at", "null")
        .order("relevance_score", desc=True)  # 점수 높은 순으로 정렬
        .execute()
    )
    return response.data


# ============================================
# 2. 이메일 내용 만들기
# ============================================

def build_email_body(papers):
    """논문 목록을 HTML 이메일 본문으로 변환합니다."""
    today = datetime.now().strftime("%Y년 %m월 %d일")

    html = f"""
    <html>
    <body style="font-family: 'Malgun Gothic', sans-serif; line-height: 1.6;">
        <h2>📚 {LAB_NAME} 논문 브리핑 ({today})</h2>
        <p>이번 브리핑에서 {len(papers)}건의 관련 논문을 찾았습니다.</p>
        <hr>
    """

    for p in papers:
        summary_html = (p.get('summary_ko') or (p.get('abstract') or '')[:200] + '...').replace('\n', '<br>')

        # source에 "RSS"가 들어있으면 Nature/Science 계열 세부 저널에서 온 논문
        # (예: "Nature Nanotechnology RSS", "Science Advances RSS")
        is_top_journal = "RSS" in (p.get("source") or "")

        if is_top_journal:
            # 하이라이트 스타일: 금색 테두리 + 배지
            border_color = "#D4A017"
            background = "#FFF9EC"
            badge = (
                '<span style="background:#D4A017; color:white; font-size:11px; '
                'padding:2px 8px; border-radius:10px; margin-right:6px;">⭐ 주요저널</span>'
            )
        else:
            border_color = "#4A90D9"
            background = "#FFFFFF"
            badge = ""

        html += f"""
        <div style="margin-bottom: 24px; padding: 12px; border-left: 4px solid {border_color}; background: {background};">
            <p style="font-size: 12px; color: #888; margin: 0;">
                {badge}관련도 {p.get('relevance_score', '?')}점 · {p.get('journal', '')} · {p.get('publication_date', '')}
            </p>
            <h3 style="margin: 4px 0;">
                <a href="{p.get('link', '#')}" style="color: #1a1a1a; text-decoration: none;">
                    {p.get('title', '제목 없음')}
                </a>
            </h3>
            <p style="font-size: 13px; color: #555; margin: 4px 0;">
                저자: {p.get('authors', '정보 없음')}
            </p>
            <p style="font-size: 14px; color: #333; background: #f7f9fb; padding: 8px; border-radius: 4px;">
                {summary_html}
            </p>
        </div>
        """

    html += """
        <hr>
        <p style="font-size: 12px; color: #999;">
            이 브리핑은 자동으로 생성되었습니다.
        </p>
    </body>
    </html>
    """
    return html


# ============================================
# 3. 이메일 발송
# ============================================

def send_email(papers):
    """만든 이메일을 실제로 발송합니다."""
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        print("[오류] GMAIL_ADDRESS / GMAIL_APP_PASSWORD 환경변수가 설정되어 있지 않아요.")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[{LAB_NAME}] 논문 브리핑 - {datetime.now().strftime('%m/%d')}"
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = RECIPIENT_EMAIL

    html_body = build_email_body(papers)
    msg.attach(MIMEText(html_body, "html"))

    try:
        # Gmail SMTP 서버에 접속해서 발송
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            server.send_message(msg)
        return True
    except Exception as e:
        print(f"[오류] 이메일 발송 실패: {e}")
        return False


# ============================================
# 4. 발송 완료 표시 (DB 업데이트)
# ============================================

def mark_as_sent(supabase_client, paper_ids):
    """발송된 논문들의 sent_at을 현재 시각으로 업데이트합니다."""
    now = datetime.now(timezone.utc).isoformat()
    for paper_id in paper_ids:
        supabase_client.table("papers").update({"sent_at": now}).eq("id", paper_id).execute()


# ============================================
# 5. 메인 실행 흐름
# ============================================

def main():
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("[오류] SUPABASE_URL / SUPABASE_KEY 환경변수를 먼저 등록해주세요.")
        return

    supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)

    print("미발송 논문을 확인하는 중...")
    papers = fetch_unsent_papers(supabase_client)
    print(f"발송 대기 중인 논문: {len(papers)}건")

    if not papers:
        print("보낼 논문이 없어요. (이전 실행에서 이미 다 보냈거나, 새로 찾은 게 없어요)")
        return

    print("이메일 발송 중...")
    success = send_email(papers)

    if success:
        paper_ids = [p["id"] for p in papers]
        mark_as_sent(supabase_client, paper_ids)
        print(f"✅ 발송 완료! {len(papers)}건을 {RECIPIENT_EMAIL}로 보냈고, DB에도 발송 표시했어요.")
    else:
        print("❌ 발송 실패. DB는 업데이트하지 않았어요 (다음에 다시 시도 가능).")


if __name__ == "__main__":
    main()
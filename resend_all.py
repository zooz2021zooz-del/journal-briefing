"""
전체 재발송 스크립트

이 스크립트가 하는 일:
1. Supabase의 papers 테이블에서 "지금까지 쌓인 전체" 논문을 가져온다
   (send_briefing.py와 달리 sent_at 여부 상관없이 다 가져옴)
2. 지정한 다른 수신자에게 한꺼번에 보낸다
3. sent_at은 건드리지 않는다 (원래 발송 이력에 영향 없음)

실행 방법: python resend_all.py
"""

import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from supabase import create_client

# ============================================
# 설정값
# ============================================

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD")

# 이번에 새로 보낼 대상 (원래 받는 사람과 다른 사람) - 여러 명이면 콤마로 추가
RECIPIENT_EMAILS = [
    "wkdwltn92@skku.edu",  # ← 실제 주소로 바꿔주세요
    "xhdmb@naver.com",  # ← 실제 주소로 바꿔주세요
]

LAB_NAME = "journals-briefing-test"


# ============================================
# 1. DB에서 전체 논문 가져오기 (sent_at 상관없이)
# ============================================

def fetch_all_papers(supabase_client):
    """지금까지 DB에 쌓인 모든 논문을 관련도 순으로 가져옵니다."""
    response = (
        supabase_client.table("papers")
        .select("*")
        .order("relevance_score", desc=True)
        .execute()
    )
    return response.data


# ============================================
# 2. 이메일 내용 만들기 (send_briefing.py와 동일한 스타일)
# ============================================

def build_email_body(papers):
    today = datetime.now().strftime("%Y년 %m월 %d일")

    html = f"""
    <html>
    <body style="font-family: 'Malgun Gothic', sans-serif; line-height: 1.6;">
        <h2>📚 {LAB_NAME} 논문 브리핑 모아보기 ({today} 기준 전체 {len(papers)}건)</h2>
        <hr>
    """

    for p in papers:
        summary_html = (p.get('summary_ko') or (p.get('abstract') or '')[:200] + '...').replace('\n', '<br>')
        is_top_journal = "RSS" in (p.get("source") or "")

        if is_top_journal:
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
            이 메일은 지금까지 쌓인 전체 브리핑 내용을 모아 재발송한 것입니다.
        </p>
    </body>
    </html>
    """
    return html


# ============================================
# 3. 이메일 발송 (sent_at 업데이트 없음)
# ============================================

def send_email(papers):
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        print("[오류] GMAIL_ADDRESS / GMAIL_APP_PASSWORD 환경변수가 설정되어 있지 않아요.")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[{LAB_NAME}] 논문 브리핑 모아보기 - {datetime.now().strftime('%m/%d')}"
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = ", ".join(RECIPIENT_EMAILS)

    html_body = build_email_body(papers)
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            server.send_message(msg)
        return True
    except Exception as e:
        print(f"[오류] 이메일 발송 실패: {e}")
        return False


# ============================================
# 4. 메인 실행 흐름
# ============================================

def main():
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("[오류] SUPABASE_URL / SUPABASE_KEY 환경변수를 먼저 등록해주세요.")
        return

    supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)

    print("전체 논문을 가져오는 중...")
    papers = fetch_all_papers(supabase_client)
    print(f"전체 논문 수: {len(papers)}")

    if not papers:
        print("DB에 저장된 논문이 없어요.")
        return

    print(f"{', '.join(RECIPIENT_EMAILS)}로 발송 중...")
    success = send_email(papers)

    if success:
        print(f"✅ 발송 완료! {len(papers)}건을 {', '.join(RECIPIENT_EMAILS)}로 보냈어요. "
              "(sent_at은 건드리지 않아서 원래 발송 이력엔 영향 없어요)")
    else:
        print("❌ 발송 실패.")


if __name__ == "__main__":
    main()
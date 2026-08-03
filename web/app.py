import os
import secrets

from flask import Flask, render_template, request, redirect, url_for, session

from paper_search import (
    run_search,
    save_web_results,
    fetch_web_history,
    reset_web_history,
    is_top_journal,
    MAX_KEYWORDS,
    _supabase_client,
)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)
app.jinja_env.globals["is_top_journal"] = is_top_journal

SITE_PASSWORD = os.environ.get("SITE_PASSWORD")


@app.before_request
def require_login():
    if request.endpoint in ("login", "static"):
        return
    if not session.get("authed"):
        return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        submitted = request.form.get("password", "")
        if SITE_PASSWORD and submitted == SITE_PASSWORD:
            session["authed"] = True
            return redirect(url_for("index"))
        error = "비밀번호가 올바르지 않아요."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html", max_keywords=MAX_KEYWORDS)


@app.route("/search", methods=["POST"])
def search():
    lab_name = request.form.get("lab_name", "").strip() or "우리 연구실"
    lab_profile = request.form.get("lab_profile", "").strip()
    keywords_raw = request.form.get("keywords", "")
    keywords = [k.strip() for k in keywords_raw.splitlines() if k.strip()]
    days_back = int(request.form.get("days_back", 7))
    min_score = int(request.form.get("min_score", 70))
    include_rss = request.form.get("include_rss") == "on"

    error = None
    papers, warnings = [], []

    if not keywords:
        error = "키워드를 한 줄에 하나씩 최소 1개 이상 입력해주세요."
    elif not lab_profile:
        error = "연구실 소개를 입력해주세요."
    else:
        papers, warnings = run_search(keywords, lab_profile, days_back, min_score, include_rss)
        save_web_results(papers, lab_name)

    return render_template(
        "results.html",
        lab_name=lab_name,
        papers=papers,
        warnings=warnings,
        error=error,
        keywords=keywords,
        history_enabled=bool(_supabase_client),
    )


@app.route("/mypage")
def mypage():
    history = fetch_web_history()
    return render_template("mypage.html", history=history, history_enabled=bool(_supabase_client))


@app.route("/mypage/reset", methods=["POST"])
def mypage_reset():
    reset_web_history()
    return redirect(url_for("mypage"))


if __name__ == "__main__":
    app.run(debug=True, port=5000)

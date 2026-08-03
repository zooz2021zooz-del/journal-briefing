import os
import secrets
import threading
import time
import uuid

from flask import Flask, render_template, request, redirect, url_for, session, jsonify

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

# Render 등 배포 환경의 프록시는 요청이 ~30초를 넘으면 강제로 끊어버립니다.
# 검색은 그보다 오래 걸릴 수 있어서, 검색 요청을 바로 응답하고 실제 작업은
# 백그라운드 스레드에서 처리한 뒤 클라이언트가 폴링해서 결과를 가져오게 합니다.
# (gunicorn 워커가 1개라는 전제 하에 메모리에 작업 상태를 둡니다.)
_jobs = {}
_jobs_lock = threading.Lock()
_JOB_TTL_SECONDS = 30 * 60


def _run_search_job(job_id, keywords, lab_profile, days_back, min_score, include_rss, lab_name):
    try:
        papers, warnings = run_search(keywords, lab_profile, days_back, min_score, include_rss)
        save_web_results(papers, lab_name)
        with _jobs_lock:
            _jobs[job_id].update(status="done", papers=papers, warnings=warnings)
    except Exception as e:
        with _jobs_lock:
            _jobs[job_id].update(status="error", error=str(e))


def _cleanup_old_jobs():
    cutoff = time.time() - _JOB_TTL_SECONDS
    stale = [jid for jid, job in _jobs.items() if job.get("created_at", 0) < cutoff]
    for jid in stale:
        _jobs.pop(jid, None)


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

    if not keywords:
        error = "키워드를 한 줄에 하나씩 최소 1개 이상 입력해주세요."
        return render_template("results.html", lab_name=lab_name, papers=[], warnings=[], error=error, keywords=keywords, history_enabled=bool(_supabase_client))
    if not lab_profile:
        error = "연구실 소개를 입력해주세요."
        return render_template("results.html", lab_name=lab_name, papers=[], warnings=[], error=error, keywords=keywords, history_enabled=bool(_supabase_client))

    with _jobs_lock:
        _cleanup_old_jobs()
        job_id = uuid.uuid4().hex
        _jobs[job_id] = {"status": "running", "lab_name": lab_name, "created_at": time.time()}

    thread = threading.Thread(
        target=_run_search_job,
        args=(job_id, keywords, lab_profile, days_back, min_score, include_rss, lab_name),
        daemon=True,
    )
    thread.start()

    return render_template("pending.html", job_id=job_id, lab_name=lab_name)


@app.route("/search-status/<job_id>")
def search_status(job_id):
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        return jsonify({"status": "not_found"})
    return jsonify({"status": job["status"]})


@app.route("/search-result/<job_id>")
def search_result(job_id):
    with _jobs_lock:
        job = _jobs.get(job_id)

    if not job:
        return render_template("results.html", lab_name="", papers=[], warnings=[], error="결과를 찾을 수 없어요 (시간이 지나 만료됐을 수 있어요). 다시 검색해주세요.", keywords=[], history_enabled=bool(_supabase_client))

    if job["status"] == "running":
        return render_template("pending.html", job_id=job_id, lab_name=job["lab_name"])

    if job["status"] == "error":
        return render_template("results.html", lab_name=job["lab_name"], papers=[], warnings=[], error=f"검색 중 오류가 발생했어요: {job.get('error')}", keywords=[], history_enabled=bool(_supabase_client))

    return render_template(
        "results.html",
        lab_name=job["lab_name"],
        papers=job.get("papers", []),
        warnings=job.get("warnings", []),
        error=None,
        keywords=[],
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

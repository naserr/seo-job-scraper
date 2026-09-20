"""
SEO Job Scraper Bot v7.0 (Strict Global Remote Sniper)
========================
این نسخه فقط از JSearch (LinkedIn, Indeed, Glassdoor) استفاده می‌کند.
تمامی منابع رایگان و اسپم به دلیل کیفیت پایین حذف شده‌اند.
تمرکز استراتژی بر روی مشاغل "حقیقتاً ریموت بین‌المللی" است.
فیلترهای بسیار سخت‌گیرانه‌ای برای حذف مشاغل محدود به داخل آمریکا (Geofencing) اعمال شده است.
"""

import html
import json
import logging
import os
import re
import time
import traceback
import urllib.parse
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

try:
    import gspread
    from google.oauth2.service_account import Credentials
    SHEETS_AVAILABLE = True
except ImportError:
    SHEETS_AVAILABLE = False

SCRIPT_DIR = Path(__file__).parent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger(__name__)

load_dotenv()

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN not set")
if not TELEGRAM_CHAT_ID:
    raise ValueError("TELEGRAM_CHAT_ID not set")

RAPIDAPI_KEY       = os.environ.get("RAPIDAPI_KEY", "")
GSHEET_CREDENTIALS = os.environ.get("GSHEET_CREDENTIALS", "")
GSHEET_ID          = os.environ.get("GSHEET_ID", "")
GSHEET_SHEET_NAME  = "Jobs"

SEEN_JOBS_FILE   = SCRIPT_DIR / "seen_jobs.txt"
MAX_SEEN_JOBS    = 3000
MAX_JOBS_PER_RUN = 20
MIN_FIT_SCORE    = 35
MAX_JOB_AGE_DAYS = 7

# ─── کلمات جستجو (سفارشی شده برای فرار از مشاغل آمریکا) ────────────
# استفاده مستقیم از کلمات کلیدی جهانی در قلب جستجوی گوگل/لینکدین
JSEARCH_QUERIES = {
    1: [
        "Technical Support Specialist remote worldwide", 
        "Customer Support remote anywhere", 
        "SaaS Support remote global"
    ],
    2: [
        "Tier 2 Support remote EMEA", 
        "Application Support remote contractor", 
        "API Support remote independent contractor"
    ],
    3: [
        "Technical Support Yerevan",
        "SaaS Support Yerevan"
    ],
}

# ─── کلمات ضروری (Whitelist) ────────────────────────────────────────────────
# حتما باید حداقل یکی از این کلمات در متن آگهی باشد تا ربات آن را تایید کند
REQUIRED_KEYWORDS = [
    "saas", "api", "n8n", "crm", "zendesk", "tier 1", "tier 2", 
    "tier i", "tier ii", "jira", "helpdesk", "freshdesk", "intercom", 
    "servicenow", "troubleshooting", "bug reporting", "root cause"
]

# ─── کلمات ضروری بین‌المللی (Global Whitelist) ────────────────────────────────
# آگهی باید حتماً یکی از این کلمات را داشته باشد تا نشان دهد مختص آمریکا نیست
GLOBAL_REQUIRED_KEYWORDS = [
    "worldwide", "anywhere", "global", "emea", "contractor", "b2b", 
    "1099", "independent contractor", "yerevan", "armenia", "offshore",
    "distributed team", "remote-first"
]

_DEFAULT_SKILLS = [
    "saas troubleshooting", "api integrations", "tier 2", "n8n", "workflow automation",
    "zendesk", "crm", "pipedrive", "bug reporting", "root cause analysis", "html", "sql"
]
_user_skills_env = os.environ.get("USER_SKILLS", "")
MY_SKILLS = [s.strip().lower() for s in _user_skills_env.split(",") if s.strip()] if _user_skills_env else _DEFAULT_SKILLS

# ─── کلمات ممنوعه (لیست سیاه بسیار سخت‌گیرانه علیه محدودیت‌های آمریکا) ──────────
BLACKLIST_KEYWORDS = [
    # محدودیت‌های جغرافیایی دقیق (آمریکا و اروپا)
    "united states only", "us only", "must reside in the us", "must reside in us", 
    "must live in the us", "must be based in the us", "must be based in us",
    "us work authorization", "authorized to work in the us", "us citizen", 
    "us citizens", "green card", "no visa sponsorship", "must be a us resident",
    "must be located in the us", "must be located in us", "security clearance", 
    "public trust", "uk residents only", "eu only",
    
    # وضعیت استخدام و مالیات داخلی آمریکا
    "w-2", "w2", "401(k)", "401k", "health insurance", "dental insurance", 
    "vision insurance", "medical, dental", "dental, vision", "federal contractor",
    
    # تخصص‌های غیرمرتبط شبکه و زیرساخت فیزیکی
    "noc", "msp", "high voltage", "ccna", "hardware", "physical server", 
    "construction", "hvac", "electrical",
    
    # حوزه‌های مالی و بانکی داخلی آمریکا
    "accounting", "ach payments", "gaap", "mortgage",
    
    # عناوین شغلی ارشد و نامربوط
    "manager", "director", "head of", "founder", "growth marketing", "vp", 
    "vice president", "internship", "unpaid", "volunteer", "commission only"
]

# ─── کلمات امتیازآور (جهش آگهی‌های منطبق به صدر لیست) ────────────────────────
BOOST_KEYWORDS = {
    # نوع قرارداد بین‌المللی و لوکیشن
    "independent contractor": 30,
    "b2b": 30,
    "1099": 30,
    "worldwide": 25,
    "anywhere in the world": 25,
    "emea": 25,
    "global remote": 20,
    "offshore": 20,
    "yerevan": 30,
    
    # تخصص‌های فنی
    "saas": 15,
    "api": 15,
    "n8n": 25,
    "zendesk": 15,
    "jira": 10,
    "tier 2": 15,
    "troubleshooting": 10,
    
    # ساعات کاری
    "est": 5,
    "cst": 5,
    "weekend shift": 10,
    "after-hours": 10
}

_SKILL_PATTERNS   = {s: re.compile(r"\b" + re.escape(s) + r"\b", re.I) for s in MY_SKILLS}
_BOOST_PATTERNS   = {kw: re.compile(r"\b" + re.escape(kw) + r"\b", re.I) for kw in BOOST_KEYWORDS}
_BLACKLIST_PATTERNS = {kw: re.compile(r"\b" + re.escape(kw.lower()) + r"\b", re.I) for kw in BLACKLIST_KEYWORDS}

# ── Prompt Template ─────────────────────────────────────────────────────────

CL_PROMPT_TEMPLATE = os.environ.get("CL_PROMPT", "")

def load_prompt_template() -> str:
    if CL_PROMPT_TEMPLATE:
        return CL_PROMPT_TEMPLATE.strip()
    try:
        prompt_file = SCRIPT_DIR / "prompt.txt"
        if prompt_file.exists():
            with open(prompt_file, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    return content
    except Exception as e:
        log.warning(f"Could not load prompt.txt: {e}")
    
    return (
        "Write a highly professional and concise cover letter for the '{title}' position at '{company}'.\n\n"
        "Focus strongly on my experience as a Technical Support Specialist (Tier 2), my expertise in SaaS troubleshooting, API integrations, and workflow automation (especially n8n). Emphasize my ability to resolve complex bugs, replicate issues in sandbox environments, and communicate effectively with engineering teams.\n\n"
        "Job link: {url}\n\n"
        "Keep it under 200 words, make it highly tailored to the job requirements, use a confident but empathetic tone, and end with a strong call to action for an interview. Do not use generic placeholders."
    )

# ── Seen Jobs Cache ─────────────────────────────────────────────────────────

def load_seen_jobs() -> OrderedDict:
    seen = OrderedDict()
    if SEEN_JOBS_FILE.exists():
        for line in SEEN_JOBS_FILE.read_text(encoding="utf-8").splitlines():
            if line.strip():
                seen[line.strip()] = True
        log.info(f"Loaded {len(seen)} seen IDs")
    else:
        log.info("No cache — starting fresh")
    return seen

def save_seen_jobs(seen: OrderedDict) -> None:
    ids = list(seen.keys())
    if len(ids) > MAX_SEEN_JOBS:
        ids = ids[-MAX_SEEN_JOBS:]
    SEEN_JOBS_FILE.write_text("\n".join(ids), encoding="utf-8")
    log.info(f"Saved {len(ids)} IDs to cache")

# ── Fit Score ───────────────────────────────────────────────────────────────

def calculate_fit_score(job: dict) -> tuple:
    score = 0
    matched_skills = []
    title    = (job.get("title") or "").lower()
    desc     = (job.get("description") or "").lower()
    combined = f"{title} {desc}"

    for kw, pts in BOOST_KEYWORDS.items():
        if _BOOST_PATTERNS[kw].search(combined):
            score += pts

    for skill in MY_SKILLS:
        if _SKILL_PATTERNS[skill].search(combined):
            matched_skills.append(skill)
            score += 7

    if re.search(r"\bsupport\b", title):
        score += 12
    if job.get("salary"):
        score += 10
    if job.get("remote"):
        score += 8
    
    if any(re.search(r"\b" + w + r"\b", title) for w in ["specialist", "advocate", "engineer", "tier 2", "tier ii"]):
        score += 10

    return min(score, 100), matched_skills[:4]

# ── JSearch API (موتور اصلی استخراج لینکدین و ایندید) ───────────────────────

def _should_run_p3() -> bool:
    return datetime.now(timezone.utc).day % 2 == 0

def search_jsearch(query: str) -> list:
    if not RAPIDAPI_KEY:
        log.error("RAPIDAPI_KEY is missing! JSearch requires an API key.")
        return []
        
    url = "https://jsearch.p.rapidapi.com/search"
    headers = {"x-rapidapi-key": RAPIDAPI_KEY, "x-rapidapi-host": "jsearch.p.rapidapi.com"}
    params = {"query": query, "num_pages": "1", "date_posted": "week", "work_from_home": "true"}

    for attempt in range(1, 4):
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=20)
            if resp.status_code == 429:
                log.warning("JSearch rate limit — waiting 60s")
                time.sleep(60)
                continue
            if resp.status_code == 403:
                log.error("JSearch 403 - Invalid or expired API Key.")
                return []
            resp.raise_for_status()
            data = resp.json()
            if data.get("status") != "OK":
                return []
            return [_normalize_jsearch(j) for j in data.get("data", [])]
        except requests.exceptions.Timeout:
            log.warning(f"JSearch timeout {attempt}/3")
        except Exception as e:
            log.error(f"JSearch error: {e}")
            return []
        if attempt < 3:
            time.sleep(5 * attempt)
    return []

def _normalize_jsearch(j: dict) -> dict:
    salary = j.get("job_salary_string", "")
    if not salary and j.get("job_min_salary"):
        lo = int(j["job_min_salary"])
        hi = int(j.get("job_max_salary") or lo)
        per = {"year": "/yr", "month": "/mo", "hour": "/hr"}.get((j.get("job_salary_period") or "").lower(), "")
        salary = f"${lo:,}-${hi:,}{per}" if lo != hi else f"${lo:,}+{per}"

    city, country = j.get("job_city") or "", j.get("job_country") or ""
    loc_parts = [p for p in (city, country) if p]
    loc = ", ".join(loc_parts) or "Remote"

    return {
        "id":           j.get("job_id", ""),
        "title":        j.get("job_title", ""),
        "company":      j.get("employer_name", ""),
        "description":  j.get("job_description", ""),
        "salary":       salary,
        "remote":       True,
        "url":          j.get("job_apply_link") or j.get("job_google_link") or "",
        "source":       j.get("job_publisher", "JSearch"),
        "source_emoji": "🔍",
        "posted_at":    (j.get("job_posted_at_datetime_utc") or "")[:10],
        "location":     loc,
    }

# ── Filters ─────────────────────────────────────────────────────────────────

def is_valid_job(job: dict) -> tuple:
    """بررسی می‌کند که آیا آگهی شرایط ضروری را دارد و در لیست سیاه نیست."""
    description = (job.get("description") or "").lower()
    title       = (job.get("title") or "").lower()
    combined    = f"{title} {description}"

    # 1. بررسی Blacklist (رد کردن بی‌رحمانه آگهی‌های محدود به آمریکا)
    for kw, pattern in _BLACKLIST_PATTERNS.items():
        if pattern.search(combined):
            return False, f"Blacklisted (US/Visa restriction): {kw}"
            
    # 2. بررسی Whitelist فنی (باید حتما مرتبط با IT/SaaS باشد)
    has_tech_required = False
    for req_kw in REQUIRED_KEYWORDS:
        if re.search(r"\b" + re.escape(req_kw) + r"\b", combined):
            has_tech_required = True
            break
            
    if not has_tech_required:
        return False, "Missing IT/SaaS technical keywords"

    # 3. بررسی Whitelist بین‌المللی (باید نشانه‌ای از کار جهانی یا قرارداد داشته باشد)
    has_global_required = False
    for req_kw in GLOBAL_REQUIRED_KEYWORDS:
        if re.search(r"\b" + re.escape(req_kw) + r"\b", combined):
            has_global_required = True
            break
            
    if not has_global_required:
        return False, "Missing Global/Contractor keywords (Likely US-only disguised as remote)"

    return True, ""

def is_too_old(job: dict) -> bool:
    posted = (job.get("posted_at") or "")[:10]
    if not posted:
        return False
    try:
        dt = datetime.strptime(posted, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).days > MAX_JOB_AGE_DAYS
    except Exception:
        return False

# ── Telegram ────────────────────────────────────────────────────────────────

def _score_bar(score: int) -> str:
    filled = round(score / 10)
    return "█" * filled + "░" * (10 - filled)

def format_job(job: dict, score: int, skills: list) -> str:
    title   = html.escape(job.get("title") or "No Title")
    company = html.escape(job.get("company") or "Unknown")
    salary  = job.get("salary") or ""
    source  = html.escape(job.get("source") or "")
    semoji  = job.get("source_emoji", "🌐")
    posted  = job.get("posted_at") or ""
    loc     = html.escape(job.get("location") or "Remote")

    lines = [
        f"💼 <b>{title}</b>",
        f"🏢 {company}",
        f"📍 {loc}",
    ]
    if salary:
        lines.append(f"💰 <b>{html.escape(str(salary))}</b>")
    lines.append(f"📊 {_score_bar(score)} {score}/100")
    if skills:
        lines.append(f"✅ {', '.join(html.escape(s) for s in skills)}")
    lines.append(f"{semoji} {source}")
    if posted:
        lines.append(f"📅 {posted}")

    return "\n".join(lines)

def build_job_buttons(job: dict) -> dict:
    url = job.get("url", "")
    if not url:
        return {}

    title   = job.get("title", "")
    company = job.get("company", "")

    template = load_prompt_template()
    prompt   = template.format(title=title, company=company, url=url)
    safe_prompt = urllib.parse.quote(prompt)
    chatgpt_url = f"https://chatgpt.com/?q={safe_prompt}"

    return {"inline_keyboard": [
        [{"text": "📝 Apply Now", "url": url}],
        [{"text": "🤖 ChatGPT Cover Letter", "url": chatgpt_url}]
    ]}

def send_telegram(text: str, reply_markup: dict = None, _retries: int = 3) -> bool:
    api_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "link_preview_options": {"is_disabled": True},
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    for attempt in range(1, _retries + 1):
        try:
            resp = requests.post(api_url, json=payload, timeout=15)
            if resp.ok:
                return True

            if resp.status_code == 429:
                retry_after = resp.json().get("parameters", {}).get("retry_after", 30)
                log.warning(f"Telegram Flood Wait — sleeping {retry_after}s (attempt {attempt}/{_retries})")
                time.sleep(retry_after + 1)
                continue

            log.error(f"Telegram {resp.status_code}: {resp.text[:200]}")
            return False
        except requests.exceptions.Timeout:
            log.warning(f"Telegram timeout (attempt {attempt}/{_retries})")
            if attempt < _retries:
                time.sleep(3)
        except Exception as e:
            log.error(f"Telegram error: {e}")
            return False
    return False

# ── Google Sheets ────────────────────────────────────────────────────────────

def get_sheets_client():
    if not SHEETS_AVAILABLE or not GSHEET_CREDENTIALS or not GSHEET_ID:
        return None
    try:
        creds = Credentials.from_service_account_info(
            json.loads(GSHEET_CREDENTIALS),
            scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"],
        )
        log.info("Google Sheets connected")
        return gspread.authorize(creds)
    except Exception as e:
        log.error(f"Sheets auth error: {e}")
        return None

def ensure_sheet_headers(client) -> None:
    if not client:
        return
    try:
        sheet = client.open_by_key(GSHEET_ID).worksheet(GSHEET_SHEET_NAME)
        if not sheet.row_values(1):
            sheet.insert_row(
                ["Job Title", "Company", "Source", "Apply Link", "Posted",
                 "Salary", "Fit Score", "Location", "Saved At (UTC)", "Status", "Cover Letter Link"],
                1,
            )
    except Exception as e:
        log.error(f"Sheet header error: {e}")

def batch_append_to_sheet(client, rows: list) -> None:
    if not client or not rows:
        return
    try:
        sheet = client.open_by_key(GSHEET_ID).worksheet(GSHEET_SHEET_NAME)
        sheet.append_rows(rows, value_input_option="USER_ENTERED")
        log.info(f"Batch appended {len(rows)} rows to Google Sheets")
    except Exception as e:
        log.error(f"Sheet batch append error: {e}")

# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    log.info(f"=== SaaS Support Job Scraper v7.0 started at {now} ===")

    seen_jobs = load_seen_jobs()
    sheets = get_sheets_client()
    ensure_sheet_headers(sheets)

    raw_jobs = []
    source_counts = {}

    # ── JSearch (موتور اصلی) ─────────────────────────────────────────────────
    jsearch_total = 0
    for priority in sorted(JSEARCH_QUERIES.keys()):
        if priority == 3 and not _should_run_p3():
            log.info("Skipping P3 JSearch queries (odd day)")
            continue
        for query in JSEARCH_QUERIES[priority]:
            try:
                jobs = search_jsearch(query)
                jsearch_total += len(jobs)
                raw_jobs.extend(jobs)
            except Exception as e:
                log.error(f"JSearch '{query}': {e}")
            time.sleep(1.5)
    source_counts["JSearch"] = jsearch_total

    # ── فیلتر + امتیازدهی ────────────────────────────────────────────────────
    seen_ids = set()
    title_keys = set()
    stats = {"filtered_out": 0, "seen": 0, "old": 0, "low_score": 0}
    qualified = []

    for job in raw_jobs:
        try:
            jid = job.get("id") or job.get("url") or ""
            title_key = f"{(job.get('title') or '').lower().strip()}|{(job.get('company') or '').lower().strip()}"

            if not jid:
                continue
            if jid in seen_jobs or jid in seen_ids:
                stats["seen"] += 1
                continue
            if title_key in title_keys:
                stats["seen"] += 1
                seen_ids.add(jid)
                seen_jobs[jid] = True
                continue

            seen_ids.add(jid)
            seen_jobs[jid] = True
            title_keys.add(title_key)

            # استفاده از فیلتر ترکیبی (Blacklist + Whitelist دوگانه)
            is_valid, reason = is_valid_job(job)
            if not is_valid:
                log.info(f"Filtered {job.get('title')}: {reason}")
                stats["filtered_out"] += 1
                continue

            if is_too_old(job):
                stats["old"] += 1
                continue

            score, skills = calculate_fit_score(job)
            if score < MIN_FIT_SCORE:
                stats["low_score"] += 1
                continue

            qualified.append((job, score, skills))
        except Exception as e:
            log.error(f"Processing error: {e}")

    qualified.sort(key=lambda x: x[1], reverse=True)

    log.info(
        f"Qualified: {len(qualified)} | Filtered: {stats['filtered_out']} | "
        f"Seen: {stats['seen']} | Old: {stats['old']} | Low: {stats['low_score']}"
    )

    # ── ارسال به تلگرام ──────────────────────────────────────────────────────
    active_sources = {k: v for k, v in source_counts.items() if v > 0}
    sources_line = " | ".join(f"{k}: {v}" for k, v in active_sources.items())

    if not qualified:
        send_telegram(
            f"🔍 <b>Daily Report</b>\n📅 {now}\n\n"
            f"No qualified jobs found.\n\n"
            f"📌 {sources_line or 'No sources'}\n"
            f"⛔ {stats['filtered_out']} filtered | "
            f"📉 {stats['low_score']} low score | "
            f"🔁 {stats['seen']} duplicates | "
            f"🕐 {stats['old']} old"
        )
        save_seen_jobs(seen_jobs)
        return

    send_telegram(
        f"🤖 <b>New Support Jobs</b>\n"
        f"📅 {now}\n\n"
        f"✅ <b>{len(qualified)}</b> jobs (sorted by fit)\n"
        f"⛔ {stats['filtered_out']} filtered | "
        f"📉 {stats['low_score']} low | "
        f"🔁 {stats['seen']} dupes\n\n"
        f"📌 {sources_line}\n"
        f"🤖 ChatGPT Cover Letter: ON\n"
        f"➖➖➖➖➖➖➖➖"
    )
    time.sleep(1.5)

    sent = 0
    sheet_rows = []

    for job, score, skills in qualified[:MAX_JOBS_PER_RUN]:
        try:
            buttons = build_job_buttons(job)
            msg = format_job(job, score, skills)

            if send_telegram(msg, reply_markup=buttons if buttons else None):
                sent += 1
                sheet_rows.append([
                    job.get("title", ""), job.get("company", ""),
                    job.get("source", ""), job.get("url", ""),
                    job.get("posted_at", ""), job.get("salary", ""),
                    score, job.get("location", ""),
                    datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
                    "New", "ChatGPT URL"
                ])

            time.sleep(1.5)
        except Exception as e:
            log.error(f"Send error: {e}")

    batch_append_to_sheet(sheets, sheet_rows)
    save_seen_jobs(seen_jobs)
    log.info(f"=== Done. Sent {sent}/{len(qualified)} ===")

if __name__ == "__main__":
    main()

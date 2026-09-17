import os
import re
import json
import requests
import google.generativeai as genai

# Configuration
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
WORKER_URL = os.environ.get("WORKER_URL", "https://bse-financial-analyzer.daksheshpatelin.workers.dev")

# Required browser headers to bypass BSE anti-bot blocking
BSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.bseindia.com/",
    "Origin": "https://www.bseindia.com"
}

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

def _strip_html(html):
    """Lightweight HTML-to-text: drop script/style blocks, strip tags,
    collapse whitespace. Good enough to feed Gemini without pulling in a
    full HTML parser dependency."""
    html = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()

def download_and_extract_document(url):
    """BSE's Latest Financial Results feed links to an XBRL HTML page
    (not a PDF) for each filing - fetch it and extract readable text."""
    print(f"Downloading result document: {url}")
    res = requests.get(url, headers=BSE_HEADERS, timeout=30)

    if res.status_code != 200:
        raise Exception(f"HTTP Error {res.status_code} when accessing BSE document.")

    text_content = _strip_html(res.text)

    if not text_content or len(text_content) < 50:
        raise Exception("Document had little/no extractable text - BSE request was likely blocked or page structure changed.")

    return text_content

def analyze_with_gemini(text_content, company_name):
    if not GEMINI_API_KEY:
        return "Gemini API Key missing."
        
    model = genai.GenerativeModel('gemini-2.5-flash')
    prompt = f"""
    Analyze the following financial results announcement for {company_name}.

    STEP 1 - Check for "special situation" red flags in the document, including:
    - Insolvency/bankruptcy proceedings (NCLT, IBC, CIRP, Resolution Professional, Interim RP, board powers suspended)
    - Fraud, misappropriation, or diversion of business/IP by past management, or an ongoing investigation
    - Trading restrictions imposed by the exchange (trade-for-trade, suspension, non-payment of listing fees)
    - Zero or near-zero revenue with no clear ongoing business activity (dormant/shell company)
    - Qualified or adverse audit opinion, or auditor's going-concern doubts
    - Large loan/debt default disclosed in the filing

    If ANY of these apply, your response MUST start with exactly one line in this format:
    REASON: <one short phrase naming the situation, e.g. "Under NCLT insolvency (CIRP), debt in default" or "Zero revenue - fraud investigation into former MD" or "Dormant shell, no operating activity">

    Follow it with 2-3 sentences explaining what this means practically (e.g. outcome depends on resolution plan, not on operating results). Do NOT then produce the normal revenue/profit/margin bullet-point analysis below - it isn't meaningful for a company in this state.

    STEP 2 - Only if NONE of the above red flags apply, provide a concise summary in bullet points covering:
    1. Key Financial Highlights (Revenue, Profit/Loss, Margins, YoY/QoQ growth if available)
    2. Operational Highlights or Management Commentary
    3. Dividend declarations or corporate actions (if any)

    Raw Document Text:
    {text_content[:15000]}
    """
    
    response = model.generate_content(prompt)
    return response.text

def send_telegram_summary(company, headline, summary, doc_url):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram credentials missing; skipping summary message.")
        return

    # If Gemini flagged a special situation (insolvency, fraud, dormant
    # shell, etc.), it prefixes its reply with "REASON: ...". Pull that
    # line out and show it as a bold warning up top instead of burying
    # it inside the regular analysis block.
    reason_line = None
    body = summary
    match = re.match(r"^\s*REASON:\s*(.+?)\s*\n(.*)$", summary, re.DOTALL)
    if match:
        reason_line = match.group(1).strip()
        body = match.group(2).strip()

    message = f"📊 <b>GEMINI FINANCIAL SUMMARY</b>\n\n" \
              f"🏢 <b>Company:</b> {company}\n" \
              f"📝 <b>Headline:</b> {headline}\n\n"

    if reason_line:
        message += f"⚠️ <b>REASON: {reason_line}</b>\n\n"

    message += f"<b>Analysis:</b>\n{body}\n\n" \
               f"📄 <a href='{doc_url}'>View Original BSE Result</a>"

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(url, json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    })

def mark_processed(fingerprints):
    """Tell the worker these alerts are done, so the next run doesn't
    re-download the same document and re-send the same Gemini summary."""
    if not fingerprints:
        return
    try:
        requests.post(f"{WORKER_URL}/alerts/mark-processed", json={"fingerprints": fingerprints}, timeout=15)
    except Exception as e:
        print(f"Warning: failed to mark alerts processed: {e}")

def main():
    print("Checking Worker for newly alerted financial results...")
    try:
        # ?pending=1 filters out alerts this pipeline has already summarized,
        # so the same 1-3 alerts aren't reprocessed/re-sent every run.
        res = requests.get(f"{WORKER_URL}/alerts?pending=1", timeout=15)
        if res.status_code != 200:
            print("No pending alerts returned from backend worker.")
            return
            
        data = res.json()
        alerts = data.get("items", [])
        
        if not alerts:
            print("No new, unprocessed financial announcements right now.")
            return

        done_fingerprints = []
        for alert in alerts[:3]:  # Process up to 3 recent alerts
            doc_url = alert.get("link")
            company = alert.get("company", "Company")
            headline = alert.get("title", "Financial Result")
            fingerprint = alert.get("fingerprint")

            if not doc_url:
                continue

            try:
                doc_text = download_and_extract_document(doc_url)
                summary = analyze_with_gemini(doc_text, company)
                send_telegram_summary(company, headline, summary, doc_url)
                if fingerprint:
                    done_fingerprints.append(fingerprint)
            except Exception as e:
                print(f"Skipping {company} due to error: {e}")
                # Mark as processed even on failure (e.g. BSE blocked the
                # download from GitHub's IPs) so a permanently-broken filing
                # doesn't get retried forever every run.
                if fingerprint:
                    done_fingerprints.append(fingerprint)

        mark_processed(done_fingerprints)

    except Exception as e:
        print(f"Pipeline execution error: {e}")

if __name__ == "__main__":
    main()
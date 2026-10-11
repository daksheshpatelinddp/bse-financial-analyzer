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
        
    model = genai.GenerativeModel('gemini-3.5-flash-lite')
    prompt = f"""
    Analyze the following financial results announcement for {company_name}.

    STEP 1 - Check for "special situation" red flags in the document. Known examples include:
    - Insolvency/bankruptcy proceedings (NCLT, IBC, CIRP, Resolution Professional, Interim RP, board powers suspended)
    - Fraud, misappropriation, or diversion of business/IP by past management, or an ongoing investigation
    - Trading restrictions imposed by the exchange (trade-for-trade, suspension, non-payment of listing fees)
    - Zero or near-zero revenue with no clear ongoing business activity (dormant/shell company)
    - Qualified or adverse audit opinion, or auditor's going-concern doubts
    - Large loan/debt default disclosed in the filing

    This list is NOT exhaustive. Also flag ANY other circumstance that would make the normal revenue/profit/margin numbers misleading or not meaningfully comparable - for example: a merger, demerger, or business transfer in progress; promoter share pledge invocation; a regulatory action, ban, or license suspension; litigation that freezes assets or operations; a one-time exceptional item dominating the results; or any other unusual event the document itself calls out as materially affecting the numbers.

    If ANY such situation applies (listed or not), your response MUST start with exactly one line in this format:
    REASON: <one short phrase naming the situation, e.g. "Under NCLT insolvency (CIRP), debt in default" or "Zero revenue - fraud investigation into former MD">

    Follow it with 2-3 sentences explaining what this means practically. Do NOT then produce the normal revenue/profit/margin bullet-point analysis below.

    STEP 2 - If NONE of the above red flags apply, provide a concise summary in bullet points covering:

    1. Key Financial Highlights - Revenue, Profit/Loss, and Margins with available YoY and QoQ growth performance breakdowns (explicitly note if YoY or QoQ comparative figures are absent in the filing).

    2. Sector-Appropriate Profitability Metric - First identify the company's business/sector from the segment reporting or business description in the document. Then:
       - If it is a BANK, NBFC, or other financial-services lender: report Net Interest Income (NII), Net Interest Margin (NIM), Gross NPA (GNPA), Net NPA (NNPA), and Provision Coverage Ratio, but ONLY the ones the document actually discloses.
       - If it is a general manufacturing, industrial, trading, or capital-intensive business: report EBITDA and EBITDA margin. If not directly stated, calculate as (Profit Before Tax + Finance Costs + Depreciation & Amortisation - Other Income) and clearly label it "(derived, not explicitly reported)".
       - If it is an IT/services, asset-light, or non-manufacturing business: report EBIT and EBIT margin instead of EBITDA. If not directly stated, calculate as (Profit Before Tax + Finance Costs - Other Income) and label it "(derived, not explicitly reported)".
       - If the sector is ambiguous or mixed, state your best judgment of the sector in one clause and proceed with whichever metric fits better.

    3. Expense Breakdown - major expense line items (e.g. cost of materials, employee costs, finance costs, depreciation, other expenses), each with available change metrics.

    4. Operational Highlights or Management Commentary - anything the document explicitly states about operations, outlook, capacity, order book, expansion, etc.

    5. Reading Between the Lines (ONLY include this section if the document gives no explicit forward-looking guidance/outlook) - Give a short, clearly-labeled analyst-style interpretation based strictly on qualitative cues actually present in the document: auditor's remarks/qualifications, tone and specificity of management's notes, mentions of capacity utilization, order book, delays, litigation, or unusual line items. Clearly mark this whole section as interpretation, e.g. by prefixing it "Interpretation (not stated explicitly by the company):".

    6. Dividend declarations or corporate actions (if any)

    Raw Document Text:
    {text_content[:15000]}
    """
    
    response = model.generate_content(prompt)
    return response.text

def send_telegram_summary(company, headline, summary, doc_url):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram credentials missing; skipping summary message.")
        return

    reason_line = None
    body = summary
    match = re.match(r"^\s*REASON:\s*(.+?)\s*\n(.*)$", summary, re.DOTALL)
    if match:
        reason_line = match.group(1).strip()
        body = match.group(2).strip()

    message = f"🚨 <b>GEMINI FINANCIAL SUMMARY</b>\n\n" \
              f"🏢 <b>Company:</b> {company}\n" \
              f"📋 <b>Headline:</b> {headline}\n\n"

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

                # Send Telegram summary regardless of YoY/QoQ availability
                send_telegram_summary(company, headline, summary, doc_url)

                if fingerprint:
                    done_fingerprints.append(fingerprint)

            except Exception as e:
                err_text = str(e)
                is_rate_limit = "429" in err_text or "quota" in err_text.lower()
                if is_rate_limit:
                    print(f"Leaving {company} pending (Gemini rate/quota limit hit): {e}")
                else:
                    print(f"Skipping {company} due to error: {e}")
                    if fingerprint:
                        done_fingerprints.append(fingerprint)

        mark_processed(done_fingerprints)

    except Exception as e:
        print(f"Pipeline execution error: {e}")

if __name__ == "__main__":
    main()

import os
import io
import json
import requests
import pdfplumber
import google.generativeai as genai

# Configuration
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
WORKER_URL = os.environ.get("WORKER_URL", "https://bse-financial-analyzer.daksheshpatelin.workers.dev")

# Required browser headers to bypass BSE anti-bot blocking
BSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/pdf,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.bseindia.com/",
    "Origin": "https://www.bseindia.com"
}

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

def download_and_extract_pdf(url):
    print(f"Downloading PDF: {url}")
    res = requests.get(url, headers=BSE_HEADERS, timeout=30)
    
    if res.status_code != 200:
        raise Exception(f"HTTP Error {res.status_code} when accessing BSE PDF.")
    
    if not res.content.startswith(b"%PDF"):
        raise Exception("Downloaded file is not a valid PDF. BSE request was likely blocked.")
    
    text_content = ""
    with pdfplumber.open(io.BytesIO(res.content)) as pdf:
        for page in pdf.pages:
            extracted = page.extract_text()
            if extracted:
                text_content += extracted + "\n"
                
    if not text_content.strip():
        raise Exception("PDF is image-based / scanned and has no extractable text.")
        
    return text_content

def analyze_with_gemini(text_content, company_name):
    if not GEMINI_API_KEY:
        return "Gemini API Key missing."
        
    model = genai.GenerativeModel('gemini-2.5-flash')
    prompt = f"""
    Analyze the following financial results announcement for {company_name}.
    Provide a concise summary in bullet points covering:
    1. Key Financial Highlights (Revenue, Profit/Loss, Margins, YoY/QoQ growth if available)
    2. Operational Highlights or Management Commentary
    3. Dividend declarations or corporate actions (if any)

    Raw Document Text:
    {text_content[:15000]}
    """
    
    response = model.generate_content(prompt)
    return response.text

def send_telegram_summary(company, headline, summary, pdf_url):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram credentials missing; skipping summary message.")
        return

    message = f"📊 <b>GEMINI FINANCIAL SUMMARY</b>\n\n" \
              f"🏢 <b>Company:</b> {company}\n" \
              f"📝 <b>Headline:</b> {headline}\n\n" \
              f"<b>Analysis:</b>\n{summary}\n\n" \
              f"📄 <a href='{pdf_url}'>View Original BSE PDF</a>"

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(url, json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    })

def mark_processed(fingerprints):
    """Tell the worker these alerts are done, so the next run (15 min later)
    doesn't re-download the same PDF and re-send the same Gemini summary."""
    if not fingerprints:
        return
    try:
        requests.post(f"{WORKER_URL}/alerts/mark-processed", json={"fingerprints": fingerprints}, timeout=15)
    except Exception as e:
        print(f"Warning: failed to mark alerts processed: {e}")

def main():
    print("Checking Worker for newly alerted PDFs...")
    try:
        # ?pending=1 filters out alerts this pipeline has already summarized,
        # so the same 1-3 alerts aren't reprocessed/re-sent every 15 minutes.
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
            pdf_url = alert.get("link")
            company = alert.get("company", "Company")
            headline = alert.get("title", "Financial Result")
            fingerprint = alert.get("fingerprint")

            if not pdf_url or not pdf_url.endswith(".pdf"):
                continue

            try:
                pdf_text = download_and_extract_pdf(pdf_url)
                summary = analyze_with_gemini(pdf_text, company)
                send_telegram_summary(company, headline, summary, pdf_url)
                if fingerprint:
                    done_fingerprints.append(fingerprint)
            except Exception as e:
                print(f"Skipping {company} due to error: {e}")
                # Mark as processed even on failure (e.g. BSE blocked the PDF
                # download from GitHub's IPs) so a permanently-broken filing
                # doesn't get retried forever every 15 minutes.
                if fingerprint:
                    done_fingerprints.append(fingerprint)

        mark_processed(done_fingerprints)

    except Exception as e:
        print(f"Pipeline execution error: {e}")

if __name__ == "__main__":
    main()
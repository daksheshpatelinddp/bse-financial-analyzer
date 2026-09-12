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

    message = f" <b>GEMINI FINANCIAL SUMMARY</b>\n\n" \
              f" <b>Company:</b> {company}\n" \
              f" <b>Headline:</b> {headline}\n\n" \
              f"<b>Analysis:</b>\n{summary}\n\n" \
              f" <a href='{pdf_url}'>View Original BSE PDF</a>"

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(url, json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    })

def main():
    print("Checking Worker for newly alerted PDFs...")
    try:
        res = requests.get(f"{WORKER_URL}/alerts", timeout=15)
        if res.status_code != 200:
            print("No pending alerts returned from backend worker.")
            return
            
        data = res.json()
        alerts = data.get("items", [])
        
        if not alerts:
            print("Watchlist is empty or no new financial announcements filed recently.")
            return

        for alert in alerts[:3]:  # Process up to 3 recent alerts
            pdf_url = alert.get("link")
            company = alert.get("company", "Company")
            headline = alert.get("title", "Financial Result")
            
            if not pdf_url or not pdf_url.endswith(".pdf"):
                continue
                
            try:
                pdf_text = download_and_extract_pdf(pdf_url)
                summary = analyze_with_gemini(pdf_text, company)
                send_telegram_summary(company, headline, summary, pdf_url)
            except Exception as e:
                print(f"Skipping {company} due to error: {e}")

    except Exception as e:
        print(f"Pipeline execution error: {e}")

if __name__ == "__main__":
    main()
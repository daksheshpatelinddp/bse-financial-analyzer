import os
import requests
import pdfplumber
from google import genai
from google.genai import types

# 1. Environment Setup
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

client = genai.Client(api_key=GEMINI_API_KEY)

def download_and_extract_pdf(pdf_url):
    """Downloads PDF from BSE and extracts raw text from financial table pages."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    response = requests.get(pdf_url, headers=headers, timeout=15)
    
    with open("temp_result.pdf", "wb") as f:
        f.write(response.content)

    extracted_text = ""
    with pdfplumber.open("temp_result.pdf") as pdf:
        # Scan first 6 pages for financial tables
        for page in pdf.pages[:6]:
            extracted_text += (page.extract_text() or "") + "\n"

    return extracted_text

def analyze_with_gemini(company_name, raw_text):
    """Processes extracted text through Gemini 2.5 Flash to generate structured metric insights."""
    prompt = f"""
    You are an expert Indian equity research analyst.
    Analyze the financial results document for standard quarterly figures (INR Crores).
    
    Company: {company_name}

    Extract the following metrics into a concise response:
    - Revenue from Operations (Quarterly & YoY change %)
    - Net Profit / PAT (Quarterly & YoY change %)
    - Operating Margin / EBITDA (%)
    - 2-3 key management highlights or operational notes
    - Overall Verdict: [Very Bullish / Bullish / Neutral / Bearish]

    Raw Document Text:
    {raw_text[:12000]}
    """

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt
    )
    return response.text

def send_telegram_insight(company_name, analysis, pdf_url):
    """Dispatches the AI output to Telegram."""
    message = (
        f"📊 <b>FINANCIAL RESULT INSIGHT: {company_name}</b>\n\n"
        f"{analysis}\n\n"
        f"📄 <a href='{pdf_url}'>Read Full BSE PDF</a>"
    )
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    requests.post(url, json=payload)

# Example execution entry point
if __name__ == "__main__":
    sample_pdf = "https://www.bseindia.com/xml-data/corpfiling/AttachLive/sample.pdf"
    sample_company = "KPIT Technologies"

    print("Extracting PDF text...")
    pdf_text = download_and_extract_pdf(sample_pdf)
    
    print("Running Gemini analysis...")
    insight = analyze_with_gemini(sample_company, pdf_text)
    
    print("Sending Telegram Alert...")
    send_telegram_insight(sample_company, insight, sample_pdf)
import os
import requests
import datetime

# Endpoint configuration
WORKER_URL = os.environ.get("WORKER_URL", "https://bse-financial-analyzer.daksheshpatelin.workers.dev")
BSE_CALENDAR_URL = "https://api.bseindia.com/BseIndiaAPI/api/GetResCalendar/w"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.bseindia.com/",
    "Origin": "https://www.bseindia.com"
}

def fetch_todays_bse_calendar():
    today_str = datetime.datetime.now().strftime("%Y%m%d") # Format YYYYMMDD
    print(f"Fetching BSE Results Calendar for date: {today_str}...")
    
    try:
        response = requests.get(BSE_CALENDAR_URL, headers=HEADERS, timeout=15)
        if response.status_code != 200:
            print(f"BSE API returned status code {response.status_code}")
            return []
            
        data = response.json()
        todays_scrips = []

        # Filter calendar items matching today's date
        for entry in data:
            # Entry date fields vary between 'result_date' or 'Res_Date'
            res_date = entry.get("result_date") or entry.get("Res_Date") or ""
            scrip_code = str(entry.get("scrip_cd") or entry.get("SCRIP_CD") or "").strip()
            
            if scrip_code:
                todays_scrips.append(scrip_code)
                
        print(f"Found {len(todays_scrips)} companies scheduled for results today.")
        return todays_scrips

    except Exception as e:
        print(f"Error fetching BSE calendar: {e}")
        return []

def update_cloudflare_watchlist(scrip_codes):
    print("Overwriting Cloudflare Worker Watchlist...")
    try:
        # Sends full list to overwrite the KV array in Cloudflare Worker
        response = requests.post(
            f"{WORKER_URL}/watchlist/overwrite", 
            json={"watchlist": scrip_codes},
            timeout=15
        )
        if response.status_code in [200, 201]:
            print("Watchlist successfully updated for the new day!")
        else:
            print(f"Failed to update worker. HTTP Status: {response.status_code}")
    except Exception as e:
        print(f"Error sending update request to Cloudflare Worker: {e}")

if __name__ == "__main__":
    scrips = fetch_todays_bse_calendar()
    update_cloudflare_watchlist(scrips)
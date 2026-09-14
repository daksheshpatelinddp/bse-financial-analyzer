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

def _normalize_date(raw):
    """BSE-style APIs report dates in a few different formats depending on
    endpoint - try the common ones and return YYYYMMDD, or None if unparseable."""
    raw = str(raw or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y%m%d", "%d-%b-%Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.datetime.strptime(raw[:len(fmt) if "T" not in fmt else 19], fmt).strftime("%Y%m%d")
        except ValueError:
            continue
    return None

def fetch_todays_bse_calendar():
    today_str = datetime.datetime.now().strftime("%Y%m%d") # Format YYYYMMDD
    print(f"Fetching BSE Results Calendar for date: {today_str}...")
    
    try:
        response = requests.get(BSE_CALENDAR_URL, headers=HEADERS, timeout=15)
        if response.status_code != 200:
            print(f"BSE API returned status code {response.status_code}")
            return None  # None = "fetch failed", distinct from [] = "genuinely zero results today"

        data = response.json()
        if not isinstance(data, list):
            print(f"Unexpected response shape from BSE calendar API: {type(data)}")
            return None

        todays_scrips = []
        unparsed_dates = 0

        # Filter calendar items matching today's date
        for entry in data:
            # Entry date fields vary between 'result_date' or 'Res_Date'
            res_date_raw = entry.get("result_date") or entry.get("Res_Date") or ""
            scrip_code = str(entry.get("scrip_cd") or entry.get("SCRIP_CD") or "").strip()
            if not scrip_code:
                continue

            normalized = _normalize_date(res_date_raw)
            if normalized is None:
                unparsed_dates += 1
                continue
            if normalized == today_str:
                todays_scrips.append(scrip_code)

        if unparsed_dates:
            print(f"Warning: {unparsed_dates} entries had a date field I couldn't parse "
                  f"(raw example needed - run manually and inspect the JSON to confirm field/format).")

        print(f"Found {len(todays_scrips)} companies scheduled for results today.")
        return todays_scrips

    except Exception as e:
        print(f"Error fetching BSE calendar: {e}")
        return None  # signal failure, not "zero companies today"

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
    if scrips is None:
        # Fetch/parse failed outright - do NOT touch the live watchlist.
        print("Skipping watchlist update: could not reliably fetch today's calendar.")
    elif len(scrips) == 0:
        # BSE returned a well-formed but empty list. During result season this
        # is almost certainly a sign something's wrong (endpoint/params/schema
        # changed) rather than a genuine zero-companies day - refuse to wipe
        # the watchlist and flag it loudly instead.
        print("WARNING: BSE calendar returned 0 companies. Not overwriting the "
              "watchlist - please check the BSE_CALENDAR_URL/response format manually.")
    else:
        update_cloudflare_watchlist(scrips)
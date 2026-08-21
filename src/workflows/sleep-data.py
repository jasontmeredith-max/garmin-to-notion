"""
Customized for Jason's "Sleep Tracker" database inside the Ultimate Life Planner.

This replaces the original src/workflows/sleep-data.py from chloevoyer/garmin-to-notion.
It pulls last night's sleep summary AND overnight HRV from Garmin Connect and writes
them into a Notion database with this schema:

  Name (title), Date (date), Bedtime (date), Wake Time (date),
  Total Sleep Duration (min), Deep Sleep (min), Light Sleep (min), REM Sleep (min),
  Awake Time (min), Sleep Score, Sleep Score Quality, Avg Overnight HRV (ms),
  HRV Status, Resting Heart Rate (bpm), Avg Respiration Rate (brpm), Nap, Notes

NOTE ON RELIABILITY: Garmin's Connect API is not officially documented, so a couple
of fields below (sleep score, HRV, respiration) are read defensively with .get() and
fall back to None/blank rather than crashing if Garmin changes a key name. If you run
this once (see README below) and a field shows up empty in Notion that you know Garmin
actually has for that night, check the printed DEBUG JSON in the Action log and adjust
the .get() paths near the "SLEEP SCORE", "HRV", and "RESPIRATION" comments below.
"""

from datetime import datetime

import pytz
from dotenv import load_dotenv

from src.helpers import get_garmin_client, get_notion_client

# Constants
local_tz = pytz.timezone("America/Chicago")

DEBUG = False  # flip to True to print raw Garmin JSON in the Action log for troubleshooting


def get_sleep_data(garmin, date_str):
    return garmin.get_sleep_data(date_str)


def get_hrv_data(garmin, date_str):
    try:
        return garmin.get_hrv_data(date_str)
    except Exception as e:
        print(f"Could not fetch HRV data: {e}")
        return None


def to_minutes(seconds):
    return round((seconds or 0) / 60)


def format_iso(timestamp_ms):
    """Garmin GMT timestamps (ms since epoch) -> ISO datetime string for Notion."""
    return (
        datetime.utcfromtimestamp(timestamp_ms / 1000).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        if timestamp_ms else None
    )


def title_case(s):
    return s.replace("_", " ").title() if s else None


def sleep_entry_exists(client, database_id, sleep_date):
    query = client.databases.query(
        database_id=database_id,
        filter={"property": "Date", "date": {"equals": sleep_date}},
    )
    results = query.get("results", [])
    return results[0] if results else None


def build_properties(sleep_data, hrv_data, sleep_date):
    daily_sleep = sleep_data.get("dailySleepDTO", {}) or {}

    deep_sec = daily_sleep.get("deepSleepSeconds", 0)
    light_sec = daily_sleep.get("lightSleepSeconds", 0)
    rem_sec = daily_sleep.get("remSleepSeconds", 0)
    awake_sec = daily_sleep.get("awakeSleepSeconds", 0)
    total_sec = (deep_sec or 0) + (light_sec or 0) + (rem_sec or 0)

    bedtime_iso = format_iso(daily_sleep.get("sleepStartTimestampGMT"))
    wake_iso = format_iso(daily_sleep.get("sleepEndTimestampGMT"))

    # --- SLEEP SCORE ---
    sleep_scores = daily_sleep.get("sleepScores", {}) or {}
    overall = sleep_scores.get("overall", {}) or {}
    sleep_score = overall.get("value")
    sleep_score_quality = title_case(overall.get("qualifierKey"))

    # --- RESPIRATION --- (sometimes on dailySleepDTO, sometimes top-level)
    avg_respiration = daily_sleep.get("avgRespirationValue") or sleep_data.get("avgRespirationValue")

    # --- RESTING HEART RATE --- (top-level on the sleep response)
    resting_hr = sleep_data.get("restingHeartRate")

    # --- HRV ---
    hrv_summary = (hrv_data or {}).get("hrvSummary", {}) or {}
    avg_hrv = hrv_summary.get("lastNightAvg")
    hrv_status = title_case(hrv_summary.get("status"))

    if DEBUG:
        print("DEBUG sleep_data:", sleep_data)
        print("DEBUG hrv_data:", hrv_data)

    date_label = datetime.strptime(sleep_date, "%Y-%m-%d").strftime("%b %-d, %Y")

    properties = {
        "Name": {"title": [{"text": {"content": f"Sleep - {date_label}"}}]},
        "Date": {"date": {"start": sleep_date}},
    }

    if bedtime_iso:
        properties["Bedtime"] = {"date": {"start": bedtime_iso}}
    if wake_iso:
        properties["Wake Time"] = {"date": {"start": wake_iso}}

    properties["Total Sleep Duration (min)"] = {"number": to_minutes(total_sec)}
    properties["Deep Sleep (min)"] = {"number": to_minutes(deep_sec)}
    properties["Light Sleep (min)"] = {"number": to_minutes(light_sec)}
    properties["REM Sleep (min)"] = {"number": to_minutes(rem_sec)}
    properties["Awake Time (min)"] = {"number": to_minutes(awake_sec)}

    if sleep_score is not None:
        properties["Sleep Score"] = {"number": sleep_score}
    if sleep_score_quality:
        properties["Sleep Score Quality"] = {"rich_text": [{"text": {"content": sleep_score_quality}}]}
    if avg_hrv is not None:
        properties["Avg Overnight HRV (ms)"] = {"number": avg_hrv}
    if hrv_status:
        properties["HRV Status"] = {"rich_text": [{"text": {"content": hrv_status}}]}
    if resting_hr:
        properties["Resting Heart Rate (bpm)"] = {"number": resting_hr}
    if avg_respiration:
        properties["Avg Respiration Rate (brpm)"] = {"number": avg_respiration}

    properties["Notes"] = {"rich_text": [{"text": {"content": "Auto-synced from Garmin Connect."}}]}

    return properties


def create_sleep_entry(client, database_id, sleep_data, hrv_data, sleep_date, skip_zero_sleep=True):
    daily_sleep = sleep_data.get("dailySleepDTO", {}) or {}
    total_sec = sum(
        (daily_sleep.get(k, 0) or 0) for k in ["deepSleepSeconds", "lightSleepSeconds", "remSleepSeconds"]
    )
    if skip_zero_sleep and total_sec == 0:
        print(f"Skipping sleep data for {sleep_date} — total sleep is 0 (no Garmin sync for that night yet).")
        return

    properties = build_properties(sleep_data, hrv_data, sleep_date)
    client.pages.create(parent={"database_id": database_id}, properties=properties, icon={"emoji": "💤"})
    print(f"Created sleep entry for: {sleep_date}")


def main():
    load_dotenv()

    garmin_client, _ = get_garmin_client()
    notion_client, notion_dbs = get_notion_client()

    database_id = notion_dbs.sleep
    if not database_id:
        raise ValueError("NOTION_SLEEP_DB_ID is not set.")

    today = datetime.now(local_tz).date()
    sleep_date = today.isoformat()

    sleep_data = get_sleep_data(garmin_client, sleep_date)
    if not sleep_data or not sleep_data.get("dailySleepDTO"):
        print(f"No sleep data available yet for {sleep_date}.")
        return

    if sleep_entry_exists(notion_client, database_id, sleep_date):
        print(f"Sleep entry for {sleep_date} already exists in Notion — skipping.")
        return

    hrv_data = get_hrv_data(garmin_client, sleep_date)
    create_sleep_entry(notion_client, database_id, sleep_data, hrv_data, sleep_date)


if __name__ == "__main__":
    main()
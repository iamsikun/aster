from datetime import datetime, timedelta
import pytz, os

def tz():
    return pytz.timezone(os.getenv("TZ", "America/Chicago"))

def now_local():
    return datetime.now(tz())

def today_str():
    return now_local().strftime("%Y-%m-%d")

def days_ago(n: int):
    return now_local() - timedelta(days=n)

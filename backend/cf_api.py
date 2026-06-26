import time
import requests

CF_BASE = "https://codeforces.com/api"


def _get(url: str, params: dict = None, retries: int = 3) -> dict:
    last_err = None
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=15)
            data = r.json()
            if data.get("status") == "OK":
                return data["result"]
            raise ValueError(data.get("comment", "Codeforces API error"))
        except Exception as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(2)
    raise last_err


def fetch_user_info(handle: str) -> dict:
    result = _get(f"{CF_BASE}/user.info", {"handles": handle})
    if not result:
        raise ValueError(f"Handle '{handle}' not found on Codeforces.")
    return result[0]


def fetch_user_submissions(handle: str) -> list:
    return _get(f"{CF_BASE}/user.status", {"handle": handle, "from": 1, "count": 10000})


def fetch_user_rating_history(handle: str) -> list:
    """
    Returns the user's contest-by-contest rating change history.
    Users who have never competed in a rated contest get an empty list back
    (CF returns a 'not rated' comment in that case -- not a real error).
    """
    try:
        return _get(f"{CF_BASE}/user.rating", {"handle": handle})
    except Exception as e:
        if "not rated" in str(e).lower() or "not found" in str(e).lower():
            return []
        raise


def fetch_all_problems() -> list:
    data = _get(f"{CF_BASE}/problemset.problems")
    return data.get("problems", [])

import re
import requests
from src.utils.logger import setup_logger

logger = setup_logger("unsubscribe")

_URL_KEYWORDS = re.compile(
    r'unsubscribe|unsub|opt.?out|d[eé]sabonner?|desinscri|se.d[eé]sinscrire|remove.me',
    re.IGNORECASE,
)


def extract_unsubscribe(email: dict) -> dict:
    """
    Returns {"url": str|None, "mailto": str|None, "method": "http"|"mailto"|"one_click"|None}
    Priority: List-Unsubscribe-Post (one-click) > List-Unsubscribe HTTP > body link > mailto
    """
    header = email.get("list_unsubscribe", "") or ""
    post_header = email.get("list_unsubscribe_post", "") or ""

    http_urls = re.findall(r'<(https?://[^>]+)>', header)
    mailto_addrs = re.findall(r'<(mailto:[^>]+)>', header)

    # RFC 8058 one-click: List-Unsubscribe-Post + HTTP URL
    if post_header and http_urls:
        return {"url": http_urls[0], "mailto": None, "method": "one_click"}

    if http_urls:
        return {"url": http_urls[0], "mailto": None, "method": "http"}

    # Search body for unsubscribe links
    body = email.get("body", "")
    # Match href URLs near unsubscribe keywords
    href_pattern = re.findall(r'href=["\']?(https?://[^\s"\'<>]+)["\']?', body, re.IGNORECASE)
    for url in href_pattern:
        if _URL_KEYWORDS.search(url):
            return {"url": url, "mailto": None, "method": "http"}

    # Plain-text URLs
    plain_urls = re.findall(r'https?://\S+', body)
    for url in plain_urls:
        if _URL_KEYWORDS.search(url):
            clean = url.rstrip('.,)')
            return {"url": clean, "mailto": None, "method": "http"}

    if mailto_addrs:
        return {"url": None, "mailto": mailto_addrs[0], "method": "mailto"}

    return {"url": None, "mailto": None, "method": None}


def do_http_unsubscribe(url: str, one_click: bool = False) -> tuple[bool, str]:
    """Visit the unsubscribe URL. Returns (success, message)."""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; MailAgent/1.0)",
        }
        if one_click:
            resp = requests.post(url, data={"List-Unsubscribe": "One-Click"}, headers=headers, timeout=15, allow_redirects=True)
        else:
            resp = requests.get(url, headers=headers, timeout=15, allow_redirects=True)

        if resp.status_code < 400:
            logger.info(f"Unsubscribe HTTP {resp.status_code}: {url}")
            return True, f"Requête envoyée (HTTP {resp.status_code})"
        else:
            logger.warning(f"Unsubscribe HTTP {resp.status_code}: {url}")
            return False, f"Erreur HTTP {resp.status_code}"
    except requests.exceptions.Timeout:
        return False, "Timeout — le serveur ne répond pas"
    except Exception as e:
        logger.error(f"Unsubscribe error: {e}")
        return False, str(e)

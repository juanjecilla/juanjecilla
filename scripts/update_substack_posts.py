#!/usr/bin/env python3
import os
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

PUBLICATION = os.getenv("SUBSTACK_PUBLICATION", "codingpit.substack.com")
FEED_URL = os.getenv("SUBSTACK_FEED_URL", f"https://{PUBLICATION}/feed")
LATEST_COUNT = int(os.getenv("SUBSTACK_LATEST_COUNT", "3"))
README_PATH = os.getenv("README_PATH", "README.md")
LATEST_TAG = os.getenv("SUBSTACK_LATEST_TAG", "SUBSTACK_LATEST")
FETCH_TIMEOUT_SECONDS = int(os.getenv("SUBSTACK_FETCH_TIMEOUT_SECONDS", "30"))
FETCH_RETRIES = int(os.getenv("SUBSTACK_FETCH_RETRIES", "3"))


def fetch_url(url, headers=None):
    base_headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": f"https://{PUBLICATION}/",
    }
    if headers:
        base_headers.update(headers)

    last_error = None
    for attempt in range(1, FETCH_RETRIES + 1):
        req = urllib.request.Request(url, headers=base_headers)
        try:
            with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_SECONDS) as resp:
                return resp.read()
        except (urllib.error.HTTPError, urllib.error.URLError) as exc:
            last_error = exc
            if attempt == FETCH_RETRIES:
                break
            time.sleep(attempt)

    raise RuntimeError(f"Failed to fetch {url}: {last_error}") from last_error


def parse_rss_items(xml_bytes):
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise RuntimeError(f"Failed to parse RSS feed: {exc}") from exc

    def strip_tag(tag):
        return tag.split("}")[-1] if "}" in tag else tag

    root_tag = strip_tag(root.tag)
    items = []

    if root_tag == "rss":
        channel = root.find("channel")
        if channel is None:
            return items
        for item in channel.findall("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            if not link:
                link = (item.findtext("guid") or "").strip()
            if title and link:
                items.append((title, link))
    elif root_tag == "feed":
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        entries = root.findall("atom:entry", ns)
        if not entries:
            entries = root.findall("entry")
        for entry in entries:
            title = (entry.findtext("atom:title", default="", namespaces=ns) or "").strip()
            link_elem = entry.find("atom:link[@rel='alternate']", ns)
            if link_elem is None:
                link_elem = entry.find("atom:link", ns)
            link = (link_elem.get("href") if link_elem is not None else "").strip()
            if title and link:
                items.append((title, link))
    else:
        raise RuntimeError(f"Unsupported feed format: {root.tag}")

    return items


def fetch_latest_posts(limit):
    xml_bytes = fetch_url(FEED_URL)
    items = parse_rss_items(xml_bytes)
    return items[:limit]


def escape_title(title):
    title = re.sub(r"\s+", " ", title).strip()
    return title.replace("[", "\\[").replace("]", "\\]")


def format_posts(posts):
    return [f"- [{escape_title(title)}]({link})" for title, link in posts]


def replace_section(content, tag, lines):
    start = f"<!-- {tag}:START -->"
    end = f"<!-- {tag}:END -->"
    pattern = re.compile(
        rf"{re.escape(start)}.*?{re.escape(end)}",
        re.DOTALL,
    )

    if not pattern.search(content):
        raise RuntimeError(f"Missing section tags for {tag} in {README_PATH}.")

    replacement = "\n".join([start, *lines, end])
    return pattern.sub(replacement, content, count=1)


def main():
    latest_posts = fetch_latest_posts(LATEST_COUNT)

    with open(README_PATH, "r", encoding="utf-8") as handle:
        content = handle.read()

    content = replace_section(content, LATEST_TAG, format_posts(latest_posts))

    with open(README_PATH, "w", encoding="utf-8") as handle:
        handle.write(content)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

#!/usr/bin/env python3
import json
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET

PLAYLIST_ID_ENV = os.getenv("YOUTUBE_PODCAST_PLAYLIST_ID", "").strip()
PLAYLIST_URL_ENV = os.getenv("YOUTUBE_PODCAST_PLAYLIST_URL", "").strip()
CHANNEL_HANDLE = os.getenv("YOUTUBE_CHANNEL_HANDLE", "Welcometolasecta").strip()
PODCAST_TITLE_HINT = os.getenv("YOUTUBE_PODCAST_TITLE", "").strip()
LATEST_COUNT = int(os.getenv("PODCAST_LATEST_COUNT", "3"))
README_PATH = os.getenv("README_PATH", "README.md")
LATEST_TAG = os.getenv("PODCAST_LATEST_TAG", "PODCAST_LATEST")


def fetch_url(url, headers=None):
    base_headers = {"User-Agent": "podcast-readme-updater"}
    if headers:
        base_headers.update(headers)
    req = urllib.request.Request(url, headers=base_headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def extract_playlist_id(value):
    if not value:
        return None
    match = re.search(r"[?&]list=([A-Za-z0-9_-]+)", value)
    if match:
        return match.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{10,}", value):
        return value
    return None


def extract_title(node):
    if not isinstance(node, dict):
        return ""
    title = node.get("title")
    if isinstance(title, dict):
        if "simpleText" in title:
            return title["simpleText"].strip()
        runs = title.get("runs")
        if isinstance(runs, list) and runs:
            return "".join(run.get("text", "") for run in runs).strip()
    return ""


def extract_json_object(text, start_index):
    depth = 0
    in_string = False
    escape = False
    for i in range(start_index, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == "\"":
                in_string = False
        else:
            if ch == "\"":
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start_index : i + 1]
    raise RuntimeError("Failed to extract ytInitialData JSON payload.")


def parse_yt_initial_data(html):
    markers = [
        "var ytInitialData =",
        "window[\"ytInitialData\"] =",
        "ytInitialData =",
    ]
    for marker in markers:
        idx = html.find(marker)
        if idx == -1:
            continue
        start = html.find("{", idx)
        if start == -1:
            continue
        payload = extract_json_object(html, start)
        return json.loads(payload)
    raise RuntimeError("Unable to locate ytInitialData on the YouTube page.")


def collect_playlist_candidates(data):
    candidates = {}

    def visit(node):
        if isinstance(node, dict):
            if "playlistRenderer" in node:
                renderer = node["playlistRenderer"]
                pid = renderer.get("playlistId")
                if pid and pid not in candidates:
                    candidates[pid] = extract_title(renderer)
            if "gridPlaylistRenderer" in node:
                renderer = node["gridPlaylistRenderer"]
                pid = renderer.get("playlistId")
                if pid and pid not in candidates:
                    candidates[pid] = extract_title(renderer)
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for item in node:
                visit(item)

    visit(data)
    return [(pid, title) for pid, title in candidates.items()]


def discover_playlist_id(handle, title_hint):
    url = f"https://www.youtube.com/@{handle}/podcasts"
    html = fetch_url(url).decode("utf-8", errors="replace")
    data = parse_yt_initial_data(html)
    candidates = collect_playlist_candidates(data)

    if not candidates:
        raise RuntimeError(
            "No podcast playlists found. Set YOUTUBE_PODCAST_PLAYLIST_ID or "
            "YOUTUBE_PODCAST_PLAYLIST_URL."
        )

    if title_hint:
        lowered_hint = title_hint.lower()
        for pid, title in candidates:
            if lowered_hint in title.lower():
                return pid
        available = ", ".join(
            f"{title or 'Untitled'} ({pid})" for pid, title in candidates
        )
        raise RuntimeError(
            "No podcast playlist matched YOUTUBE_PODCAST_TITLE. "
            f"Available playlists: {available}"
        )

    if len(candidates) == 1:
        return candidates[0][0]

    available = ", ".join(f"{title or 'Untitled'} ({pid})" for pid, title in candidates)
    raise RuntimeError(
        "Multiple podcast playlists found. Set YOUTUBE_PODCAST_PLAYLIST_ID or "
        f"YOUTUBE_PODCAST_TITLE. Available playlists: {available}"
    )


def parse_atom_entries(xml_bytes):
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise RuntimeError(f"Failed to parse YouTube feed: {exc}") from exc

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    entries = root.findall("atom:entry", ns) or root.findall("entry")

    items = []
    for entry in entries:
        title = (entry.findtext("atom:title", default="", namespaces=ns) or "").strip()
        link_elem = entry.find("atom:link[@rel='alternate']", ns)
        if link_elem is None:
            link_elem = entry.find("atom:link", ns)
        link = (link_elem.get("href") if link_elem is not None else "").strip()

        if not title or not link:
            continue
        if title.lower() in {"private video", "deleted video"}:
            continue
        items.append((title, link))

    return items


def fetch_latest_episodes(playlist_id, limit):
    feed_url = f"https://www.youtube.com/feeds/videos.xml?playlist_id={playlist_id}"
    xml_bytes = fetch_url(feed_url)
    items = parse_atom_entries(xml_bytes)
    return items[:limit]


def escape_title(title):
    title = re.sub(r"\s+", " ", title).strip()
    return title.replace("[", "\\[").replace("]", "\\]")


def format_episodes(episodes):
    return [f"- [{escape_title(title)}]({link})" for title, link in episodes]


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
    playlist_id = extract_playlist_id(PLAYLIST_ID_ENV) or extract_playlist_id(
        PLAYLIST_URL_ENV
    )
    if not playlist_id:
        playlist_id = discover_playlist_id(CHANNEL_HANDLE, PODCAST_TITLE_HINT)

    latest_episodes = fetch_latest_episodes(playlist_id, LATEST_COUNT)

    with open(README_PATH, "r", encoding="utf-8") as handle:
        content = handle.read()

    content = replace_section(content, LATEST_TAG, format_episodes(latest_episodes))

    with open(README_PATH, "w", encoding="utf-8") as handle:
        handle.write(content)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

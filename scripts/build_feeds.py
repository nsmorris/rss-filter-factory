#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import re
import sys
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import format_datetime, parsedate_to_datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import feedparser
import yaml


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
CONFIG = ROOT / "filters.yml"


TEXT_FIELDS = {
    "author": ("author",),
    "title": ("title",),
    "summary": ("summary", "description"),
    "content": ("content",),
    "category": ("category", "tags"),
    "link": ("link",),
}


@dataclass
class Item:
    title: str
    link: str
    guid: str
    published: datetime
    author: str = ""
    summary: str = ""
    categories: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        return self.guid or self.link or self.title


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return " ".join(clean_text(v) for v in value)
    if isinstance(value, dict):
        return " ".join(clean_text(v) for v in value.values())
    return re.sub(r"\s+", " ", html.unescape(str(value))).strip()


def entry_values(entry: Any, field: str) -> list[str]:
    if field == "any":
        values: list[str] = []
        for name in TEXT_FIELDS:
            values.extend(entry_values(entry, name))
        return values

    values = []
    for attr in TEXT_FIELDS[field]:
        if attr == "content":
            for content in entry.get("content", []) or []:
                values.append(clean_text(content.get("value", "")))
        elif attr == "tags":
            values.extend(clean_text(tag.get("term", "")) for tag in entry.get("tags", []) or [])
        else:
            values.append(clean_text(entry.get(attr, "")))
    return [v for v in values if v]


def has_contains(entry: Any, field: str, needles: list[str]) -> bool:
    haystacks = [v.lower() for v in entry_values(entry, field)]
    return any(needle.lower() in haystack for needle in needles for haystack in haystacks)


def has_regex(entry: Any, field: str, patterns: list[str]) -> bool:
    haystacks = entry_values(entry, field)
    return any(re.search(pattern, haystack, re.I) for pattern in patterns for haystack in haystacks)


def item_matches(entry: Any, filters: dict[str, Any]) -> bool:
    include_checks: list[bool] = []

    for field in (*TEXT_FIELDS.keys(), "any"):
        contains = filters.get(f"{field}_contains") or []
        regexes = filters.get(f"{field}_regex") or []
        exclude_contains = filters.get(f"exclude_{field}_contains") or []
        exclude_regexes = filters.get(f"exclude_{field}_regex") or []

        if exclude_contains and has_contains(entry, field, list(exclude_contains)):
            return False
        if exclude_regexes and has_regex(entry, field, list(exclude_regexes)):
            return False
        if contains:
            include_checks.append(has_contains(entry, field, list(contains)))
        if regexes:
            include_checks.append(has_regex(entry, field, list(regexes)))

    return any(include_checks) if include_checks else True


def parse_entry(entry: Any) -> Item:
    published = now_utc()
    if getattr(entry, "published_parsed", None):
        published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
    elif entry.get("published"):
        try:
            published = parsedate_to_datetime(entry.get("published"))
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
            else:
                published = published.astimezone(timezone.utc)
        except (TypeError, ValueError):
            published = now_utc()

    categories = tuple(clean_text(tag.get("term", "")) for tag in entry.get("tags", []) or [] if tag.get("term"))
    guid = clean_text(entry.get("id") or entry.get("guid") or entry.get("link") or entry.get("title"))

    return Item(
        title=clean_text(entry.get("title")) or "Untitled",
        link=clean_text(entry.get("link")),
        guid=guid,
        published=published,
        author=clean_text(entry.get("author")),
        summary=clean_text(entry.get("summary") or entry.get("description")),
        categories=categories,
    )


def fetch_feed(url: str, user_agent: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    with urllib.request.urlopen(req, timeout=30) as response:
        data = response.read()
    parsed = feedparser.parse(data)
    if parsed.bozo and not parsed.entries:
        raise RuntimeError(f"Could not parse feed {url}: {parsed.bozo_exception}")
    return parsed


def read_existing_items(path: Path) -> list[Item]:
    if not path.exists():
        return []
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return []

    items: list[Item] = []
    for node in root.findall("./channel/item"):
        def text(name: str) -> str:
            child = node.find(name)
            return clean_text(child.text if child is not None else "")

        published = now_utc()
        if text("pubDate"):
            try:
                published = parsedate_to_datetime(text("pubDate")).astimezone(timezone.utc)
            except (TypeError, ValueError):
                published = now_utc()
        items.append(
            Item(
                title=text("title"),
                link=text("link"),
                guid=text("guid") or text("link"),
                published=published,
                author=text("author"),
                summary=text("description"),
                categories=tuple(clean_text(c.text) for c in node.findall("category") if c.text),
            )
        )
    return items


def add_text(parent: ET.Element, tag: str, value: Any) -> ET.Element:
    child = ET.SubElement(parent, tag)
    child.text = clean_text(value)
    return child


def write_feed(path: Path, feed_config: dict[str, Any], items: list[Item], site: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    ET.register_namespace("atom", "http://www.w3.org/2005/Atom")
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")

    base_url = (site.get("base_url") or "").rstrip("/") + "/"
    output = feed_config["output"].lstrip("/")
    feed_url = base_url + output if base_url else output

    add_text(channel, "title", feed_config["title"])
    add_text(channel, "description", feed_config.get("description") or f"Filtered feed from {feed_config['source']}")
    add_text(channel, "link", feed_config.get("source_link") or feed_config["source"])
    add_text(channel, "language", "en")
    add_text(channel, "generator", "rss-filter-factory")
    add_text(channel, "lastBuildDate", format_datetime(now_utc()))
    atom_link = ET.SubElement(channel, "{http://www.w3.org/2005/Atom}link")
    atom_link.set("href", feed_url)
    atom_link.set("rel", "self")
    atom_link.set("type", "application/rss+xml")

    for item in items:
        node = ET.SubElement(channel, "item")
        add_text(node, "title", item.title)
        add_text(node, "link", item.link)
        guid = add_text(node, "guid", item.guid or item.link)
        guid.set("isPermaLink", "false")
        add_text(node, "pubDate", format_datetime(item.published))
        if item.author:
            add_text(node, "author", item.author)
        add_text(node, "description", item.summary)
        for category in item.categories:
            add_text(node, "category", category)

    tree = ET.ElementTree(rss)
    ET.indent(tree, space="  ")
    tree.write(path, encoding="utf-8", xml_declaration=True)


def write_index(feeds: list[dict[str, Any]], site: dict[str, str]) -> None:
    DOCS.mkdir(parents=True, exist_ok=True)
    title = html.escape(site.get("title") or "RSS Filter Factory")
    description = html.escape(site.get("description") or "")
    rows = []
    for feed in feeds:
        output = html.escape(feed["output"])
        rows.append(
            f'<li><a href="{output}">{html.escape(feed["title"])}</a>'
            f'<span>{html.escape(feed.get("description", ""))}</span></li>'
        )

    (DOCS / "index.html").write_text(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    body {{ font: 16px/1.45 system-ui, -apple-system, BlinkMacSystemFont, sans-serif; margin: 3rem auto; max-width: 760px; padding: 0 1rem; color: #1f2520; }}
    h1 {{ font-size: 1.8rem; margin-bottom: .25rem; }}
    p {{ color: #526056; }}
    ul {{ list-style: none; padding: 0; }}
    li {{ border-top: 1px solid #d9dfd8; padding: 1rem 0; }}
    a {{ color: #0b66c3; font-weight: 700; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    span {{ display: block; color: #526056; margin-top: .2rem; }}
  </style>
</head>
<body>
  <h1>{title}</h1>
  <p>{description}</p>
  <ul>
    {''.join(rows)}
  </ul>
</body>
</html>
""",
        encoding="utf-8",
    )


def build(config_path: Path) -> int:
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    site = config.get("site") or {}
    defaults = config.get("defaults") or {}
    feeds = config.get("feeds") or []
    user_agent = defaults.get("user_agent") or "rss-filter-factory/1.0"

    failures = 0
    for feed_config in feeds:
        output_path = DOCS / feed_config["output"]
        max_items = int(feed_config.get("max_items") or defaults.get("max_items") or 50)
        existing = read_existing_items(output_path)

        try:
            parsed = fetch_feed(feed_config["source"], user_agent)
            fresh = [
                parse_entry(entry)
                for entry in parsed.entries
                if item_matches(entry, feed_config.get("filters") or {})
            ]
        except Exception as exc:
            print(f"ERROR {feed_config['slug']}: {exc}", file=sys.stderr)
            failures += 1
            continue

        by_key: dict[str, Item] = {}
        for item in [*existing, *fresh]:
            if item.key:
                by_key[item.key] = item

        merged = sorted(by_key.values(), key=lambda item: item.published, reverse=True)[:max_items]
        write_feed(output_path, feed_config, merged, site)
        print(f"Wrote {output_path.relative_to(ROOT)} with {len(merged)} items ({len(fresh)} fresh matches)")

    write_index(feeds, site)
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(CONFIG))
    args = parser.parse_args()
    return build(Path(args.config))


if __name__ == "__main__":
    raise SystemExit(main())

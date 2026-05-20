# RSS Filter Factory

Tiny static RSS republisher for filtering public feeds into smaller public feeds.

The rule is deliberately boring: public feed in, filtered public RSS out. No paid services, no private credentials, no browser scraping, no database, no server to babysit.

## Feeds

Configured feeds live in `filters.yml`. Generated feeds are written to `docs/feeds/` and served by GitHub Pages.

Current feed:

- `Ben Golliver - ESPN`: `/feeds/ben-golliver-espn.xml`

## Local Build

```bash
python3 -m pip install -r requirements.txt
python3 scripts/build_feeds.py
```

Or with `uv`:

```bash
uv run --with feedparser --with PyYAML python scripts/build_feeds.py
```

## Filter Shape

```yaml
feeds:
  - slug: example
    title: Example Filter
    source: https://example.com/feed.xml
    output: feeds/example.xml
    filters:
      author_contains:
        - Some Author
      title_contains:
        - keyword
      exclude_title_contains:
        - sponsored
```

Supported fields:

- `author_contains`, `title_contains`, `summary_contains`, `content_contains`, `category_contains`, `link_contains`, `any_contains`
- Regex variants: `author_regex`, `title_regex`, `summary_regex`, `content_regex`, `category_regex`, `link_regex`, `any_regex`
- Excludes use the same names with `exclude_` prefix.

Include rules are ORed. Exclude rules always win. Existing generated items are merged back in so a filtered feed does not lose older matches just because the upstream source is a short rolling feed.


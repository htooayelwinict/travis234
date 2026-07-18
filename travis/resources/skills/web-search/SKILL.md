---
name: web-search
description: Use when the user explicitly asks for web search, current facts, latest news, recent public information, or sports/current-result lookup.
---

# Web Search

Use this skill only for web/current-information tasks. Do not use it for local
repo inspection, code edits, or ordinary reasoning.

## Rules

- Use `curl` for network retrieval.
- Prefer shell tools already common in the sandbox: `sed`, `awk`, `perl`, `grep`, and `head`.
- Use only the shell/curl pattern below for normal web-search execution.
- Prefer Google News RSS for news/current-result lookup:
  `https://news.google.com/rss/search?q=<encoded-query>&hl=en-US&gl=US&ceid=US:en`
- Keep output small: show at most 5 useful results.
- Do not print raw HTML/XML.
- Do not write files.
- If live search fails, say exactly what failed and suggest a direct source.

## Minimal command pattern

```bash
query='latest news'
encoded=$(
  printf '%s' "$query" |
    sed -e 's/%/%25/g' -e 's/ /+/g' -e 's/&/%26/g' -e 's/#/%23/g' -e 's/?/%3F/g'
)
url="https://news.google.com/rss/search?q=${encoded}&hl=en-US&gl=US&ceid=US:en"

curl --fail --location --silent --show-error --max-time 15 -A 'Mozilla/5.0' "$url" |
perl -0ne '
  sub clean {
    my $s = shift // "";
    $s =~ s/<[^>]+>//g;
    $s =~ s/&amp;/&/g; $s =~ s/&lt;/</g; $s =~ s/&gt;/>/g;
    $s =~ s/&quot;/"/g; $s =~ s/&#39;/\\x27/g;
    $s =~ s/^\s+|\s+$//g;
    return $s;
  }
  while (m{<item>(.*?)</item>}sg && ++$n <= 5) {
    my $item = $1;
    my ($title) = $item =~ m{<title>(.*?)</title>}s;
    my ($link) = $item =~ m{<link>(.*?)</link>}s;
    my ($date) = $item =~ m{<pubDate>(.*?)</pubDate>}s;
    print "$n. " . clean($title) . "\n";
    print "   date: " . clean($date) . "\n" if clean($date);
    print "   source: " . clean($link) . "\n" if clean($link);
  }
'
```

Replace `latest news` with the user's requested search.

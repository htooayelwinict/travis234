# OffSec Landing Design

## Goal

Add an OffSec product lane to the main Travis234 README landing page without
repositioning or weakening the core coding-agent product.

## Audience and position

Lead with hands-on practitioners: DFIR analysts, authorized lab users,
consultants, and security researchers. Make the resulting claims legible to
security teams without promising autonomous attacks or unsupported outcomes.

The message is: Travis234 OffSec is a persistent AI terminal teammate for
investigation work that exceeds one prompt or one context window.

## Information architecture

Add an early `Travis234 OffSec` section after the existing core feature cards.
It contains a concise headline, the two installation paths, and evidence-led
capability cards for host-native Kali/VPN work, persistent terminal processes,
session/compaction continuity, model and provider flexibility, tactical DFIR
skills, and optional npx sandboxing.

Add a short operator-control statement: the user supplies targets, routes,
credentials, and authorization; the product is intended for private,
authorized, or evidence-analysis contexts.

## Copy rules

- Do not call the product an autonomous pentester or promise findings.
- Do not imply npx can see host localhost services; point proxy users to
  `host.docker.internal` on Docker Desktop and host-native execution for VPN
  or local-evidence work.
- Link to the OffSec branch/package names only where currently published.
- Keep the existing cybernetic terminal visual identity; distinguish OffSec
  through wording rather than a separate brand.

## Verification

Verify Markdown headings and anchor links, command syntax, published package
names, and an unchanged core quick-start path. Do not stage unrelated dirty
main-worktree files.

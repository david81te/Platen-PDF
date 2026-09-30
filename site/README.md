# freepdf4you.com

The marketing site for Platen PDF. One static page, no build step, no
dependencies — Vercel serves this directory as-is.

## Files

| File | What it is |
| --- | --- |
| `index.html` | the whole site: markup, CSS and JSON-LD structured data |
| `logo.png` | the app icon, used in the header and footer |
| `og-image.png` | 1200x630 card shown when the link is shared; regenerate with the snippet in the repo history |
| `favicon.ico` | the multi-size icon, same one embedded in the exe |
| `robots.txt` | allows every crawler, including the AI answer engines, and points at the sitemap |
| `sitemap.xml` | one URL; update `lastmod` when the page changes materially |
| `vercel.json` | caching and security headers |

## Two links must be filled in before this goes live

1. **Donate** — `index.html` has `https://ko-fi.com/REPLACE-ME`. Swap in the real
   Ko-fi, Buy Me a Coffee, GitHub Sponsors or Stripe payment link.
2. **Download** — points at
   `https://github.com/david81te/Platen-PDF/releases/latest/download/PlatenPDF-Setup.zip`.
   That URL only works once the repository is public **and** a release exists
   with the zip attached. A private repository returns 404 to visitors.

GitHub Releases is the practical host: the file is 140 MB, which is over
GitHub's 100 MB limit for files *in* a repository but well inside the 2 GB
limit for release *assets*. Vercel is not the place for it.

## Deploying

```powershell
vercel --prod        # from this directory
```

## About the structured data

`index.html` carries a schema.org `@graph` with `WebSite`, `Person`,
`SoftwareApplication` and `FAQPage`. Two rules to keep it working:

- **Every FAQ question in the schema must also be visible on the page.**
  Search engines treat schema that describes invisible content as spam.
- **No `aggregateRating` or `review` blocks.** There are no real ratings to
  report, and inventing them is both against Google's guidelines and simply
  false. Add them only when genuine reviews exist to point at.

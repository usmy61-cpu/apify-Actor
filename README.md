# 🇨🇭 Swiss Job Scraper — Apify Actor

A powerful, multi-strategy Apify actor that scrapes job listings from Swiss and international job boards. Each site is handled with a scraping approach matched to its specific security mechanism.

---

## 📋 Supported Sites & Strategies

| Site | Difficulty | Strategy |
|---|---|---|
| LinkedIn Jobs | 🔴 5/5 Very Hard | JobSpy (TLS fingerprint bypass) + Residential Proxy |
| Indeed Switzerland | 🟠 3/5 Medium | JobSpy (primary) → Playwright+stealth fallback |
| Jobs.ch | 🟠 3/5 Medium | Playwright + XHR/fetch API interception |
| Jobscout24.ch | 🟢 2/5 Easy | requests + BeautifulSoup + JSON-LD |
| Topjobs.ch | 🟢 2/5 Easy | requests + BeautifulSoup + JSON-LD |
| Alpha.ch | 🟢 2/5 Easy | RSS feed detection → requests + BeautifulSoup |
| **Any custom URL** | Auto | JSON-LD → XHR intercept → CSS patterns → Playwright |

---

## 🚀 Deployment to Apify

### Option A: Apify CLI (recommended)

```bash
# 1. Install Apify CLI
npm install -g apify-cli

# 2. Login
apify login

# 3. Navigate to actor folder
cd apify-swiss-jobs

# 4. Push to Apify
apify push
```

### Option B: Apify Console (manual upload)

1. Go to [console.apify.com](https://console.apify.com)
2. Click **Actors** → **Create new actor**
3. Choose **Deploy from ZIP** or connect your Git repository
4. Upload the entire `apify-swiss-jobs/` folder

### Option C: GitHub Integration

1. Push this folder to a GitHub repository
2. In Apify Console → Actors → Create → **Link GitHub repo**
3. Apify will auto-build on every push

---

## ⚙️ Input Configuration (Apify UI)

When you run the actor in Apify Console, you'll see a form with these fields:

### Keywords
List of job search terms. Each keyword is searched on every enabled site.
```json
["software engineer", "data analyst", "product manager"]
```

### Location
Geographic filter string.
```
Switzerland          ← entire country
Zurich               ← specific city
Zurich, Switzerland  ← city + country (most precise)
Remote               ← remote jobs
```

### Websites
Array of site objects. Set `enabled: false` to skip a site.
You can add any custom website to this list:
```json
[
  { "name": "LinkedIn",   "url": "https://ch.linkedin.com/jobs/", "enabled": true },
  { "name": "Indeed",     "url": "https://ch.indeed.com",         "enabled": true },
  { "name": "Jobs.ch",    "url": "https://www.jobs.ch",           "enabled": true },
  { "name": "Jobscout24", "url": "https://www.jobscout24.ch",     "enabled": true },
  { "name": "Topjobs",    "url": "https://www.topjobs.ch",        "enabled": true },
  { "name": "Alpha.ch",   "url": "https://www.alpha.ch",          "enabled": true },
  { "name": "MyCustomSite", "url": "https://jobs.mycompany.com",  "enabled": true }
]
```

### Max Results Per Site Per Keyword
- `0` = unlimited (crawl all pages)
- `50` = default (good balance)
- `500` = maximum allowed

### Proxy Configuration
Select **Apify Residential Proxies** for best results:
```json
{ "useApifyProxy": true, "apifyProxyGroups": ["RESIDENTIAL"] }
```

### Delay Between Requests (ms)
- `1000` = 1 second (fast, higher block risk)
- `2000` = 2 seconds (default, recommended)
- `5000` = 5 seconds (safe, slow)

### Language Filter
Languages to search on multilingual sites:
```json
["en", "de"]      ← English + German (default)
["en", "de", "fr"] ← Add French for Romandy jobs
```

---

## 📦 Output Schema

Each job listing is saved as a JSON record in the Apify Dataset:

```json
{
  "title":          "Senior Software Engineer",
  "company":        "Google Switzerland GmbH",
  "location":       "Zurich",
  "jobType":        "Full-time",
  "salary":         "CHF 150,000 – 200,000",
  "salaryMin":      150000,
  "salaryMax":      200000,
  "salaryCurrency": "CHF",
  "description":    "We are looking for a Senior Software Engineer...",
  "requirements":   "5+ years of experience in distributed systems...",
  "isRemote":       false,
  "postedDate":     "2024-05-01",
  "url":            "https://ch.linkedin.com/jobs/view/12345",
  "source":         "LinkedIn",
  "sourceUrl":      "https://ch.linkedin.com/jobs/",
  "keyword":        "software engineer",
  "scrapedAt":      "2024-05-15T10:30:00+00:00"
}
```

### Field Notes
| Field | Notes |
|---|---|
| `salary` | Human-readable string if available (e.g. "CHF 120,000 – 150,000") |
| `salaryMin/Max` | Numeric values when available; `null` if not disclosed |
| `jobType` | Normalized: Full-time, Part-time, Contract, Internship, Remote, Hybrid |
| `requirements` | Extracted from dedicated section or parsed from description |
| `isRemote` | `true/false/null` — inferred from title/description if not explicit |

---

## 🧪 Local Testing

```bash
# Install dependencies
pip install -r requirements.txt
playwright install chromium

# Set test input
cat > storage/key_value_stores/default/INPUT.json << 'EOF'
{
  "keywords": ["python developer"],
  "location": "Zurich",
  "websites": [
    { "name": "Jobscout24", "url": "https://www.jobscout24.ch", "enabled": true }
  ],
  "maxResultsPerSitePerKeyword": 5,
  "delayBetweenRequestsMs": 2000
}
EOF

# Run
python -m src.main
```

---

## 🏗️ Project Structure

```
apify-swiss-jobs/
├── .actor/
│   ├── actor.json          # Actor metadata & dataset view
│   └── input_schema.json   # Apify UI form definition
├── src/
│   ├── main.py             # Entry point & orchestration loop
│   ├── router.py           # URL → scraper routing
│   ├── scrapers/
│   │   ├── linkedin.py     # JobSpy (TLS fingerprint bypass)
│   │   ├── indeed.py       # JobSpy + Playwright fallback
│   │   ├── jobs_ch.py      # Playwright + XHR interception
│   │   ├── jobscout24.py   # requests + BeautifulSoup
│   │   ├── topjobs.py      # requests + BeautifulSoup
│   │   ├── alpha_ch.py     # RSS → BeautifulSoup
│   │   └── generic.py      # Auto-detect for custom URLs
│   └── utils/
│       ├── normalizer.py   # Unified output schema
│       ├── proxy.py        # Proxy format converters
│       └── stealth.py      # Playwright fingerprint evasion
├── Dockerfile              # Apify Python + Playwright base image
└── requirements.txt        # Python dependencies
```

---

## ➕ Adding a Custom Website

To add a new site beyond the 6 built-in ones, simply add it to the `websites` input:

```json
{ "name": "MyJobBoard", "url": "https://www.myjobboard.com", "enabled": true }
```

The **generic scraper** will automatically:
1. Try JSON-LD structured data (schema.org/JobPosting)
2. Intercept XHR/REST API calls via Playwright
3. Match common job card CSS patterns
4. Fall back to full-page Playwright scrape

For best results on a custom site, you can also create a dedicated scraper in `src/scrapers/` and register it in `src/router.py`.

---

## ⚠️ Legal & Ethical Notes

- This actor only collects **publicly visible** data — no login bypass
- Uses **rate limiting** (configurable delay) to avoid overloading servers
- **GDPR applies** in Switzerland and EU — only store/process data you have legitimate purpose for
- Always review a site's Terms of Service before scraping
- LinkedIn explicitly prohibits scraping in their ToS — use the [LinkedIn Jobs API](https://developer.linkedin.com/) for production use cases

---

## 🐛 Troubleshooting

| Issue | Cause | Fix |
|---|---|---|
| LinkedIn returns 0 results | IP blocked or rate limited | Enable Residential Proxies; increase delay |
| Indeed returns 0 results | Cloudflare challenge | JobSpy handles this; try increasing delay |
| Jobs.ch returns partial data | XHR not intercepted | Check browser DevTools Network tab for API endpoint |
| Custom site returns 0 | JS-rendered, no JSON-LD | Add a dedicated scraper for that site |
| Actor times out | Too many sites/keywords | Reduce `maxResultsPerSitePerKeyword` or split into multiple runs |

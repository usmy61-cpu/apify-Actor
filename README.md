# 🔍 Job Scraper Actor

An Apify Actor that scrapes job listings from **LinkedIn** and **any custom website**,  
filtered by **keyword** and **location**. Output is saved as a **plain text file**.

---

## 📦 Output Sample

```
============================================================
           JOB SCRAPER RESULTS
============================================================
  Keyword   : React Developer
  Location  : London, UK
  Total Jobs: 12
============================================================

------------------------------------------------------------
  JOB #1
------------------------------------------------------------
  Job Title   : Senior React Developer
  Company     : Acme Corp
  Location    : London, UK
  Job Type    : Full-time
  Salary      : £65,000 - £80,000/year
  Posted      : 2 days ago
  Source      : LinkedIn

  DESCRIPTION :
    We are looking for an experienced React developer to
    join our growing team...

  REQUIREMENTS:
    • 3+ years of React experience
    • TypeScript proficiency
    • Experience with REST APIs
------------------------------------------------------------
```

---

## 🚀 How to Deploy (GitHub → Apify)

### Step 1 — Push to GitHub

1. Create a new repository on [github.com](https://github.com)
2. Upload all files from this folder into the repository
3. Make sure the repository is **Public** (or connect your GitHub to Apify)

### Step 2 — Create Actor on Apify

1. Go to [apify.com](https://apify.com) → Sign in
2. Click **"Create new"** → **"Actor"**
3. Choose **"Link to GitHub"**
4. Select your repository
5. Apify will auto-detect the `Dockerfile` and `.actor/` folder
6. Click **"Save & Build"**

### Step 3 — Run the Actor

1. Go to your Actor page → **"Input"** tab
2. Fill in:
   - **Job Keyword** – e.g. `React Developer`
   - **Location** – e.g. `London, UK`
   - **Scrape LinkedIn** – toggle on/off
   - **Custom URLs** – paste any job site URLs
   - **Max Jobs Per Source** – e.g. `20`
3. Click **"Start"**

### Step 4 — Download Results

1. After the run finishes, go to **"Storage"** tab
2. Click **"Key-Value Store"** → find **OUTPUT**
3. Click **Download** to get your `.txt` file

---

## ⚙️ Input Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| keyword | String | ✅ | Job title to search, e.g. "Data Analyst" |
| location | String | ✅ | City or country, e.g. "Remote" or "Dubai" |
| scrapeLinkedIn | Boolean | – | Default: true |
| customUrls | List | – | Add any job site page URLs |
| maxJobsPerSource | Number | – | Default: 20, max: 100 |
| proxyConfig | Proxy | – | Recommended: Residential |

---

## 🛡️ Notes

- LinkedIn requires **residential proxies** — enable them in Proxy Configuration input
- Custom sites may need retries if they block bots — re-run if some results are missing
- Plain text output is in **Key-Value Store → OUTPUT**
- Raw data is also saved in the **Dataset** tab for easy viewing

---

## 🗂️ File Structure

```
job-scraper-actor/
├── .actor/
│   ├── actor.json          ← Actor metadata
│   └── input_schema.json   ← Input form definition
├── src/
│   ├── main.js             ← Entry point
│   ├── linkedin.js         ← LinkedIn scraper
│   ├── custom.js           ← Custom website scraper
│   └── formatter.js        ← Plain text formatter
├── Dockerfile              ← Build instructions for Apify
├── package.json
└── README.md
```

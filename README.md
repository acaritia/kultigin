# KULTIGIN — LinkedIn & GitHub Intelligence Framework

KULTIGIN is a next-generation Open Source Intelligence (OSINT) framework for LinkedIn and GitHub. It performs person profile intelligence, company employee discovery, GitHub account correlation, and code/repository scanning — all without requiring an account, personal cookies, or session credentials.

---

## [1] Key Features

### A. LinkedIn Profile & Company Intelligence
1. **Zero-Cookie & Zero-Cost Discovery:**
   - Pulls public profiles and company employee rosters without creating an account, purchasing an API key, or providing personal session cookies.
2. **Personal Data & Browser Isolation:**
   - Does not interact with personal browsers or LinkedIn accounts; operates safely and in isolation.
3. **HTTP 999 Anti-Bot Bypass:**
   - Circumvents LinkedIn's standard bot protection (HTTP 999 Request Blocked) via a custom crawler emulation protocol.
4. **Enriched Profile Data:**
   - **Profile Picture (PP):** Automatically detects the highest-resolution real profile photo URL.
   - **Identity & Bio:** Full name, headline, location, follower count, and summary text.
   - **Experience & Education:** Past companies, roles, date ranges, and graduation details.
   - **Certifications & Languages:** Earned certifications, issuing organizations, dates, and known languages.
   - **Skills & Competencies:** Professional skill pool scraped from Schema.org and the DOM.
   - **Contact & External Profile Dossiers (OSINT Links):** GitHub, Twitter/X, personal website/portfolio, and public emails shared in the profile.
   - **Recent Posts:** All recent posts, text, dates, like counts, and post links.
   - **Masking / Censorship Filter:** Strips meaningless asterisked (********) text returned by LinkedIn's servers.
5. **Enhanced Company Information:**
   - Company size (employee count range), industry, headquarters location, website, follower count, and specialties.
6. **BFS Graph Employee Network Discovery (Breadth-First Search):**
   - Recursively traverses public company feeds, job listings, and seed employees' "People Also Viewed" graphs to map the employee roster without cookies.

### B. GitHub OSINT & Code/Repo Discovery
1. **Extracting GitHub Accounts of Company / Organization Employees:**
   - Identifies organization members (`/orgs/{org}/members`) and users whose company field matches the target name (`company:"{name}"`).
   - If a previously scraped LinkedIn employee list exists, automatically detects and correlates it.
2. **GitHub Username Prediction from Employee Lists:**
   - Applies Turkish-to-Latin character transliteration (c, g, i, o, s, u).
   - Generates permutations from first-last name combinations (first.last, flast, firstlast, first-last, etc.).
   - Validates with concurrent (multi-threaded) HTTP checks.
   - **Active Repo Filter:** Lists only accounts with at least one public repository (`public_repos > 0`), eliminating empty-account noise.
3. **Bio, Repo, and Code Scanner (Hunter):**
   - Hunts for keywords in profile bio, location, and company fields.
   - Scans repository names and descriptions for target keywords.
   - Performs deep in-file code search (GitHub Code Search) using a PAT token.

### C. Configuration & Token Management
- GitHub Personal Access Token (PAT), LinkedIn `li_at` cookie, default thread count, and timeout values are managed centrally via `config.json` in the project root.

---

## [2] Installation

Install dependencies:
```bash
pip install -r requirements.txt
```

To define default settings and tokens, edit `config.json` in the project root or update settings from the interactive menu:
```json
{
  "github_token": "",
  "linkedin_li_at": "",
  "default_threads": 10,
  "timeout": 20
}
```

---

## [3] Automatic Output Directory Structure

When KULTIGIN runs, results are stored under `outputs/` in an organized hierarchy:

```text
outputs/
├── profiles/
│   └── <username>/
│       ├── <username>.json
│       ├── <username>.md
│       └── <username>_info.txt
├── batches/
│   └── batch_YYYYMMDD_HHMMSS/
│       ├── <user_1>.json
│       ├── <user_1>.md
│       ├── batch_summary.json
│       └── batch_usernames.txt
├── companies/
│   └── <company_slug>/
│       ├── <company_slug>.json
│       ├── <company_slug>.md
│       ├── <company_slug>_usernames.txt
│       └── employees/               (only if --deep is used)
│           ├── <employee_1>.json
│           └── <employee_1>.md
└── github/
    ├── companies/
    │   └── <company_slug>/
    │       ├── <company_slug>.json
    │       └── <company_slug>_links.txt
    ├── predictions/
    │   └── <list_name>/
    │       ├── <list_name>.json
    │       └── <list_name>_links.txt
    ├── bio_hunters/
    │   └── <keyword>/
    └── repo_hunters/
        └── <keyword>/
```

---

## [4] Usage Guide

### 1. Interactive Terminal Menu
Running without any arguments opens the full-featured terminal menu:
```bash
python kultigin.py
```

Main Menu Options:
- [1] Single Person Profile Intelligence
- [2] Batch Profile Extraction from File/List
- [3] Company Information & Employee Discovery
- [4] GitHub OSINT & Code/Repo Discovery
- [5] Settings & Config Management (Tokens, Threads)
- [6] Exit

---

### 2. CLI — LinkedIn Operations

**Single Profile Scan:**
```bash
# Automatically saves to outputs/profiles/satyandella/
python kultigin.py -u satyandella

# URL format:
python kultigin.py -u "https://www.linkedin.com/in/satyandella"
```

**Company & Employee Discovery:**
```bash
# Extract company info and employees, save to outputs/companies/companyname/
python kultigin.py --company companyname --limit 50

# Print only employee usernames line-by-line (pipe/script friendly)
python kultigin.py --company companyname --nicks-only

# Deep-scan each employee profile individually, save under employees/
python kultigin.py --company companyname --limit 30 --deep
```

**Batch List Scan:**
```bash
# Comma-separated targets:
python kultigin.py -u satyanadella,ellaison

# Read list from file:
python kultigin.py --list targets.txt
```

---

### 3. CLI — GitHub OSINT Operations

**Extract GitHub Accounts of Company / Organization Employees:**
```bash
python kultigin.py --github-company microsoft --github-limit 50
```

**Predict & Validate GitHub Accounts from a Name List:**
```bash
# Comma-separated names:
python kultigin.py --github-names "John Doe, Jane Smith"

# Name list from file:
python kultigin.py --github-names names.txt
```

**Keyword Hunt in Bio / Profile Info:**
```bash
python kultigin.py --github-hunt-users "cyber security turkey"
```

**Keyword Hunt in Repositories:**
```bash
python kultigin.py --company companyname --github-hunt-repos "api,secret,config"
```

---

## [5] Technical Architecture & Security

1. **Crawler Emulation:** Request headers sent to LinkedIn and GitHub servers are crafted in a specific format to bypass bot-detection mechanisms (HTTP 999, 403, etc.).
2. **BFS Colleague Network Graph:** A cookie-free employee set is built by traversing reference links found in company seed employees' profiles.
3. **Permutation Algorithm:** Internationally compliant username variations are generated from Turkish first-last name combinations and filtered for activity.
4. **Data Security:** Session cookies used during processing are immediately cleared from memory upon completion, leaving no unnecessary residue.

#!/usr/bin/env python3
r"""
================================================================================
 ____  __.____ ___.____  ___________.___  ________.___ _______   
|    |/ _|    |   \    | \__    ___/|   |/  _____/|   |\      \  
|      < |    |   /    |   |    |   |   /   \  ___|   |/   |   \ 
|    |  \|    |  /|    |___|    |   |   \    \_\  \   /    |    \
|____|__ \______/ |_______ \____|   |___|\______  /___\____|__  /
        \/                \/                    \/            \/ 

  LinkedIn OSINT & Profile Intelligence Framework
================================================================================
A modular, zero-cookie, zero-credential LinkedIn intelligence scraper and employee
discovery engine. Bypasses HTTP 999 anti-bot walls via crawler emulation, parses
Schema.org JSON-LD, extracts unmasked positions, certifications, educations,
languages, posts, profile pictures, and performs company employee discovery.

Author: swirner
================================================================================
"""

import os
import sys
import re
import json
import time
import random
import string
import base64
import argparse
from urllib.parse import urlparse, quote, unquote, quote_plus
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any, Union, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

# Fix Windows console UTF-8 encoding issues
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# Networking
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Parsing
try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False

# Browser Cookie extraction support
try:
    import rookiepy
    ROOKIEPY_AVAILABLE = True
except ImportError:
    ROOKIEPY_AVAILABLE = False

try:
    import browser_cookie3
    BROWSER_COOKIE3_AVAILABLE = True
except ImportError:
    BROWSER_COOKIE3_AVAILABLE = False

# Rich terminal output support
try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.text import Text
    from rich.prompt import Prompt, Confirm
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn
    from rich import print as rprint
    RICH_AVAILABLE = True
    console = Console(force_terminal=True)
except ImportError:
    RICH_AVAILABLE = False
    console = None

# GitHub OSINT Integration
try:
    from github_discoverer import (
        load_config, save_config, GitHubDiscoverer, GitHubPresenter,
        run_github_interactive_menu
    )
    GITHUB_AVAILABLE = True
except ImportError:
    GITHUB_AVAILABLE = False


# ==============================================================================
# Configuration & Constants
# ==============================================================================

DEFAULT_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

# Social crawler user-agents that completely bypass LinkedIn's HTTP 999 anti-bot walls
DEFAULT_BYPASS_USER_AGENTS = [
    "WhatsApp/2.21.12.21 A",
    "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
    "Twitterbot/1.0",
    "TelegramBot (like TwitterBot)",
    "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)",
    "LinkedInBot/1.0 (compatible; Mozilla/5.0; Apache-HttpClient +http://www.linkedin.com)",
]

LINKEDIN_BASE_URL = "https://www.linkedin.com"
VOYAGER_BASE_URL = "https://www.linkedin.com/voyager/api"


# ==============================================================================
# Data Models
# ==============================================================================

@dataclass
class PostActivity:
    text: str = ""
    date_published: Optional[str] = None
    likes_count: Optional[int] = None
    url: Optional[str] = None


@dataclass
class Experience:
    title: str = ""
    company: str = ""
    company_url: Optional[str] = None
    location: Optional[str] = None
    date_range: Optional[str] = None
    duration: Optional[str] = None
    description: Optional[str] = None
    employment_type: Optional[str] = None


@dataclass
class Education:
    school: str = ""
    degree: Optional[str] = None
    field_of_study: Optional[str] = None
    date_range: Optional[str] = None
    grade: Optional[str] = None
    activities: Optional[str] = None
    description: Optional[str] = None


@dataclass
class Certification:
    name: str = ""
    authority: Optional[str] = None
    date: Optional[str] = None
    license_number: Optional[str] = None
    url: Optional[str] = None


@dataclass
class LinkedInProfile:
    urn_id: Optional[str] = None
    username: str = ""
    public_url: str = ""
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    full_name: str = ""
    headline: str = ""
    location: str = ""
    industry: Optional[str] = None
    summary: Optional[str] = None
    profile_picture_url: Optional[str] = None
    background_picture_url: Optional[str] = None
    follower_count: Optional[int] = None
    connections_count: Optional[int] = None
    experiences: List[Experience] = field(default_factory=list)
    educations: List[Education] = field(default_factory=list)
    certifications: List[Certification] = field(default_factory=list)
    skills: List[str] = field(default_factory=list)
    languages: List[str] = field(default_factory=list)
    posts: List[PostActivity] = field(default_factory=list)
    contact_info: Dict[str, Any] = field(default_factory=dict)
    raw_data: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Converts the profile to a clean JSON-serializable dictionary."""
        data = asdict(self)
        if not data.get('raw_data'):
            data.pop('raw_data', None)
        return data


@dataclass
class CompanyEmployee:
    username: str
    full_name: Optional[str] = None
    role_or_title: Optional[str] = None
    public_url: str = ""
    profile_data: Optional[LinkedInProfile] = None


@dataclass
class LinkedInCompany:
    name: str
    slug: str
    public_url: str
    tagline: Optional[str] = None
    description: Optional[str] = None
    industry: Optional[str] = None
    website: Optional[str] = None
    location: Optional[str] = None
    followers_count: Optional[int] = None
    logo_url: Optional[str] = None
    company_size: Optional[str] = None
    specialties: List[str] = field(default_factory=list)
    employees: List[CompanyEmployee] = field(default_factory=list)
    recent_updates: List[PostActivity] = field(default_factory=list)

    @property
    def usernames(self) -> List[str]:
        """Returns a clean list of all discovered employee usernames."""
        return [e.username for e in self.employees if e.username]

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data['usernames'] = self.usernames
        return data


# ==============================================================================
# Helper & Logging Utilities (Zero-Emoji Clean Badges)
# ==============================================================================

class Logger:
    @staticmethod
    def info(msg: str):
        if RICH_AVAILABLE and console:
            console.print(f"[bold blue][INFO][/bold blue] {msg}")
        else:
            print(f"[INFO] {msg}")

    @staticmethod
    def success(msg: str):
        if RICH_AVAILABLE and console:
            console.print(f"[bold green][+][/bold green] {msg}")
        else:
            print(f"[+] {msg}")

    @staticmethod
    def warning(msg: str):
        if RICH_AVAILABLE and console:
            console.print(f"[bold yellow][!][/bold yellow] {msg}")
        else:
            print(f"[WARN] {msg}")

    @staticmethod
    def error(msg: str):
        if RICH_AVAILABLE and console:
            console.print(f"[bold red][-][/bold red] {msg}")
        else:
            print(f"[-] {msg}")

    @staticmethod
    def header(title: str):
        if RICH_AVAILABLE and console:
            console.print(Panel(f"[bold cyan]{title}[/bold cyan]", border_style="cyan", expand=False))
        else:
            print("\n" + "=" * 60)
            print(f" {title} ")
            print("=" * 60)


def clean_text(text: Optional[str]) -> str:
    """Strips whitespace, extra newlines, and non-breaking spaces."""
    if not text:
        return ""
    text = re.sub(r'[\r\n\t]+', ' ', text)
    text = re.sub(r'\s{2,}', ' ', text)
    return text.strip()


def is_masked(text: Optional[str]) -> bool:
    """Detects server-side asterisk masking (e.g. '******' or 'J*** D**')."""
    if not text:
        return False
    clean = text.strip()
    if not clean:
        return False
    asterisk_count = clean.count('*')
    if asterisk_count >= 3 and (asterisk_count / len(clean)) > 0.4:
        return True
    if clean.startswith('***') or clean.endswith('***'):
        return True
    return False


def is_placeholder_image(url: Optional[str]) -> bool:
    """Detects default placeholder, ghost SVG avatars, badges, and generic UI assets."""
    if not url:
        return True
    url_lower = url.lower()
    if 'data:image' in url_lower:
        return True
    if 'bgaqk7x4ntjz0wg67d8u723eb' in url_lower:
        return True
    if any(k in url_lower for k in ['ghost_person', 'ghost-person', 'default-avatar', 'ghost_company']):
        return True
    if any(k in url_lower for k in ['static.licdn.com', 'static-exp1.licdn.com', 'sc/h/', 'badge']):
        return True
    return False


def is_invalid_profile_picture(url: Optional[str]) -> bool:
    """Detects banners, course thumbnails, company/school logos, feed images that are NOT personal profile photos."""
    if not url or is_placeholder_image(url):
        return True
    u = url.lower()
    # Profile backgrounds & banners
    if any(k in u for k in ['profile-backgroundimage', 'backgroundimage', 'background_image', 'cover-image', 'banner']):
        return True
    # Courses / Learning thumbnails
    if any(k in u for k in ['learning-public-crop', 'course-thumbnail', 'course-image', 'course']):
        return True
    # Company / School / Group / Organization logos & banners
    if any(k in u for k in ['company-logo', 'company-background', 'school-logo', 'group-logo', 'mini-company']):
        return True
    # Feed shares, articles, ornaments
    if any(k in u for k in ['feedshare', 'feed-share', 'article-cover', 'sync-ornament']):
        return True
    return False


def is_verified_profile_photo(url: Optional[str]) -> bool:
    """Returns True if the URL unambiguously represents a genuine personal profile photo."""
    if not url or is_invalid_profile_picture(url):
        return False
    u = url.lower()
    return any(k in u for k in [
        'profile-displayphoto',
        'profile-framedphoto',
        'profilephoto',
        'displayimagereference',
        'shrinknp_'
    ])


NON_COMPANY_WORDS = {
    'turkey', 'türkiye', 'istanbul', 'ankara', 'izmir', 'remote', 'freelance', 
    'student', 'graduate', 'bsc', 'msc', 'phd', 'self-employed', 'open to work',
    'looking for opportunities', 'seeking new opportunities', 'professional profile',
    'linkedin profile', 'computer engineering', 'software engineering', 'bachelor',
    'master', 'doctorate', 'engineer', 'developer', 'consultant', 'specialist'
}


def extract_company_from_text(text: str) -> Optional[Tuple[str, str]]:
    """
    Attempts to extract (title, company) from headline or title text.
    Supports English ('Role at Company', 'Role @ Company', 'Role - Company')
    and Turkish ('X bünyesinde Y', 'X şirketinde Y', 'Y - X').
    """
    if not text:
        return None
    clean = text.strip()

    # Pattern 1: Title at/@/chez/bei Company
    m = re.search(r'^(.*?)\s+(?:at|@|chez|bei)\s+(.+)$', clean, re.IGNORECASE)
    if m:
        t, c = m.group(1).strip(), m.group(2).strip()
        c_clean = re.sub(r'[\(\[\{].*?[\)\]\}]', '', c).strip(' -–—|:,')
        if len(c_clean) >= 2 and c_clean.lower() not in NON_COMPANY_WORDS:
            return (t or "Position", c_clean)

    # Pattern 2: Turkish '... bünyesinde / şirketinde / firmasında / grubunda ...'
    m_tr = re.search(r'^(.*?)\s+(?:bünyesinde|şirketinde|firmasında|grubunda|holdinginde)\s+(.+)$', clean, re.IGNORECASE)
    if m_tr:
        c, t = m_tr.group(1).strip(), m_tr.group(2).strip()
        if len(c) >= 2 and c.lower() not in NON_COMPANY_WORDS:
            return (t or "Position", c)

    m_tr2 = re.search(r'^(.*?)\s+([A-ZÇĞİÖŞÜa-zçğıöşü0-9\s]+)\s+(?:bünyesinde|şirketinde|firmasında)$', clean, re.IGNORECASE)
    if m_tr2:
        t, c = m_tr2.group(1).strip(), m_tr2.group(2).strip()
        if len(c) >= 2 and c.lower() not in NON_COMPANY_WORDS:
            return (t or "Position", c)

    # Pattern 3: 'Title - Company' or 'Title | Company' (when 2 parts)
    parts = re.split(r'\s+[-–—|]\s+', clean)
    if len(parts) == 2:
        t, c = parts[0].strip(), parts[1].strip()
        c_clean = c.lower()
        if len(c) >= 2 and c_clean not in NON_COMPANY_WORDS and not any(w in c_clean for w in ['open to work', 'looking for', 'engineer', 'developer', 'consultant', 'specialist', 'manager', 'lead']):
            return (t or "Position", c)

    return None


def extract_username(url_or_username: str) -> str:
    """Extracts a normalized LinkedIn username from any URL format or string, supporting Unicode characters."""
    if not url_or_username:
        return ""
    text = url_or_username.strip()
    text = text.split('?')[0].split('#')[0].split('&')[0]
    
    match = re.search(r'linkedin\.com/in/([^/?#\s]+)', text, re.IGNORECASE)
    if match:
        return unquote(match.group(1)).strip().strip('/')
    match2 = re.search(r'/in/([^/?#\s]+)', text, re.IGNORECASE)
    if match2:
        return unquote(match2.group(1)).strip().strip('/')
        
    return unquote(text).strip().strip('/')


def extract_clean_username(raw_url: str) -> Optional[str]:
    """Strict validator that filters out noise, search queries, and directories."""
    if not raw_url:
        return None
    raw_url = raw_url.strip()
    m = re.search(r'(?:linkedin\.com)?/in/([^/?#\s]+)', raw_url, re.IGNORECASE)
    if m:
        uname = unquote(m.group(1).strip().strip('/'))
        uname = uname.split('?')[0].split('&')[0].split('#')[0]
        uname_lower = uname.lower()
        if len(uname) >= 3 and not uname_lower.startswith(('dir', 'search', 'unavailable', 'pulse', 'company', 'jobs', 'school', 'feed', 'login', 'signup', 'learning', 'help', 'safety', 'policies', 'settings', 'legal', 'home', '+', '%')):
            if not any(bad in uname for bad in ['+', '=', '&', ':', ';', '<', '>', '"', "'"]):
                if uname_lower not in ['feed', 'jobs', 'posts', 'about', 'people', 'life', 'overview', 'security', 'mycompany', 'check', 'admin']:
                    return uname
    return None


def extract_company_slug(url_or_slug: str) -> str:
    """Extracts the company slug from a LinkedIn URL or raw input string."""
    if not url_or_slug:
        return ""
    text = url_or_slug.strip()
    text = text.split('?')[0].split('#')[0].split('&')[0]
    match = re.search(r'linkedin\.com/company/([^/?#\s]+)', text, re.IGNORECASE)
    if match:
        return unquote(match.group(1)).strip().strip('/')
    match2 = re.search(r'/company/([^/?#\s]+)', text, re.IGNORECASE)
    if match2:
        return unquote(match2.group(1)).strip().strip('/')
    return unquote(text).strip().strip('/')


def parse_cookie_input(raw_cookie_str: str) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Parses cookie strings or key=value pairs into a dictionary."""
    cookies = {}
    rescued_args = {}
    if not raw_cookie_str:
        return cookies, rescued_args

    if os.path.isfile(raw_cookie_str):
        try:
            with open(raw_cookie_str, 'r', encoding='utf-8') as f:
                raw_cookie_str = f.read().strip()
        except Exception:
            pass

    # Extract rescued arguments if passed by mistake in string
    out_m = re.search(r'(?:^|\s)-o\s+([^\s]+)', raw_cookie_str)
    if out_m:
        rescued_args['output'] = out_m.group(1)
        raw_cookie_str = raw_cookie_str.replace(out_m.group(0), '')
        
    md_m = re.search(r'(?:^|\s)--md\s+([^\s]+)', raw_cookie_str)
    if md_m:
        rescued_args['md'] = md_m.group(1)
        raw_cookie_str = raw_cookie_str.replace(md_m.group(0), '')

    for part in re.split(r'[;\n]', raw_cookie_str):
        part = part.strip()
        if not part or '=' not in part:
            continue
        key, val = part.split('=', 1)
        key = re.sub(r'[^a-zA-Z0-9_\-]', '', key.strip())
        val = val.strip().strip('"\'\\ ')
        val = val.split()[0]
        if key and val:
            if key == 'JSESSIONID':
                j_match = re.search(r'ajax:[a-zA-Z0-9_\-]+', val)
                cookies[key] = j_match.group(0) if j_match else val
            else:
                cookies[key] = val

    return cookies, rescued_args


def get_browser_linkedin_cookies() -> Dict[str, str]:
    """
    Extracts LinkedIn authentication cookies (li_at, JSESSIONID) directly from local browsers.
    Checks Brave, Chrome, Edge, Firefox, Opera, Opera GX, Vivaldi, Arc, LibreWolf.
    """
    found: Dict[str, str] = {}
    domains = ['.linkedin.com', 'linkedin.com', 'www.linkedin.com']

    # 1. Try rookiepy (super fast, multi-browser support)
    if ROOKIEPY_AVAILABLE:
        browser_list = [
            ('Brave', rookiepy.brave),
            ('Edge', rookiepy.edge),
            ('Chrome', rookiepy.chrome),
            ('Firefox', rookiepy.firefox),
            ('Opera', rookiepy.opera),
            ('Opera GX', rookiepy.opera_gx),
            ('Vivaldi', rookiepy.vivaldi),
            ('Arc', rookiepy.arc),
            ('LibreWolf', rookiepy.librewolf),
        ]
        for b_name, b_fn in browser_list:
            try:
                cookies = b_fn(domains=domains)
                li_at = None
                jsessionid = None
                for c in cookies:
                    name = c.get('name') if isinstance(c, dict) else getattr(c, 'name', '')
                    val = c.get('value') if isinstance(c, dict) else getattr(c, 'value', '')
                    if name == 'li_at' and val:
                        li_at = str(val).strip()
                    elif name == 'JSESSIONID' and val:
                        jsessionid = str(val).strip()
                if li_at:
                    found['li_at'] = li_at
                    if jsessionid:
                        found['JSESSIONID'] = jsessionid
                    Logger.success(f"Tarayicidan ({b_name}) LinkedIn oturumu (li_at) basariyla alindi.")
                    return found
            except Exception:
                continue

    # 2. Try browser_cookie3 as fallback if rookiepy is not installed
    elif BROWSER_COOKIE3_AVAILABLE:
        for b_name, b_fn in [
            ('Firefox', browser_cookie3.firefox),
            ('Edge', browser_cookie3.edge),
            ('Brave', browser_cookie3.brave),
            ('Chrome', browser_cookie3.chrome),
            ('Opera', browser_cookie3.opera),
        ]:
            try:
                cj = b_fn(domain_name='linkedin.com')
                li_at = None
                jsessionid = None
                for c in cj:
                    if getattr(c, 'name', '') == 'li_at' and getattr(c, 'value', ''):
                        li_at = str(c.value).strip()
                    elif getattr(c, 'name', '') == 'JSESSIONID' and getattr(c, 'value', ''):
                        jsessionid = str(c.value).strip()
                if li_at:
                    found['li_at'] = li_at
                    if jsessionid:
                        found['JSESSIONID'] = jsessionid
                    Logger.success(f"Tarayicidan ({b_name}) LinkedIn oturumu (li_at) basariyla alindi.")
                    return found
            except Exception:
                continue

    return found


# ==============================================================================
# Profile Intelligence Engine
# ==============================================================================

class LinkedInFetcher:
    """
    Core profile intelligence extractor.
    Supports multi-UA failover and semantic Schema.org parsing.
    """

    def __init__(
        self,
        username_or_url: str,
        cookies: Optional[Dict[str, str]] = None,
        proxy: Optional[str] = None,
        timeout: int = 20,
        user_agent: Optional[str] = None
    ):
        self.username = extract_username(username_or_url)
        if not self.username:
            raise ValueError(f"Invalid LinkedIn URL or username provided: {username_or_url}")

        self.public_url = f"https://www.linkedin.com/in/{quote(self.username)}"
        self.cookies = cookies or {}
        self.proxy = proxy
        self.timeout = timeout
        self.user_agent = user_agent or DEFAULT_BYPASS_USER_AGENTS[0]
        self.session = requests.Session()
        self._setup_session()

    def _setup_session(self):
        retries = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
        adapter = HTTPAdapter(max_retries=retries)
        self.session.mount('https://', adapter)
        self.session.mount('http://', adapter)

        if self.proxy:
            self.session.proxies.update({'http': self.proxy, 'https': self.proxy})

        for k, v in self.cookies.items():
            clean_k = re.sub(r'[^a-zA-Z0-9_\-]', '', str(k))
            clean_v = str(v).strip('"\'\\ ').split()[0]
            if clean_k and clean_v:
                self.session.cookies.set(clean_k, clean_v, domain='.linkedin.com')

    def is_authenticated(self) -> bool:
        return 'li_at' in self.cookies

    def _get_csrf_token(self) -> Optional[str]:
        raw_csrf = self.cookies.get('JSESSIONID') or self.session.cookies.get('JSESSIONID')
        if not raw_csrf:
            return None
        match = re.search(r'ajax:[a-zA-Z0-9_\-]+', raw_csrf)
        if match:
            return match.group(0)
        cleaned = re.sub(r'[^a-zA-Z0-9_\-:]', '', raw_csrf).strip()
        return cleaned if cleaned else None

    def fetch(self) -> LinkedInProfile:
        if self.is_authenticated():
            try:
                return self._fetch_via_voyager_api()
            except Exception as e:
                Logger.warning(f"Voyager API failed ({e}), falling back to Public Web extraction...")

        return self._fetch_via_web_html()

    def _fetch_via_voyager_api(self) -> LinkedInProfile:
        url = f"{VOYAGER_BASE_URL}/identity/dash/profiles?q=memberIdentity&memberIdentity={self.username}&decorationId=com.linkedin.voyager.dash.deco.identity.profile.FullProfileWithEntities-83"
        csrf_token = self._get_csrf_token()
        headers = {
            'X-RestLi-Protocol-Version': '2.0.0',
            'X-Li-Lang': 'en_US',
            'X-Li-Track': '{"clientVersion":"1.12.*"}',
            'X-Li-Page-Instance': 'urn:li:page:d_flagship3_profile_view_base;dummy',
            'Accept': 'application/vnd.linkedin.normalized+json+2.1',
            'User-Agent': DEFAULT_USER_AGENTS[0],
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'same-origin',
        }
        if csrf_token:
            headers['csrf-token'] = csrf_token

        response = self.session.get(url, headers=headers, timeout=self.timeout)
        if response.status_code == 404:
            raise FileNotFoundError(f"LinkedIn member '{self.username}' not found.")
        if response.status_code in [401, 403]:
            raise PermissionError("Voyager API authentication failed. Check li_at / JSESSIONID cookies.")

        data = response.json()
        return self._parse_voyager_json(data)

    def _parse_voyager_json(self, data: Dict[str, Any]) -> LinkedInProfile:
        profile = LinkedInProfile(
            username=self.username,
            public_url=self.public_url,
            raw_data=data
        )

        def get_text(val: Any) -> str:
            if not val:
                return ""
            if isinstance(val, str):
                return val.strip()
            if isinstance(val, dict):
                if 'text' in val:
                    return str(val['text']).strip()
                if 'localized' in val and isinstance(val['localized'], dict) and val['localized']:
                    return str(list(val['localized'].values())[0]).strip()
                if 'name' in val:
                    return str(val['name']).strip()
            return str(val).strip()

        included = data.get('included', [])
        profile_entity = None
        for item in included:
            type_name = item.get('$type', '')
            if 'Profile' in type_name or 'com.linkedin.voyager.dash.identity.profile.Profile' in type_name:
                profile_entity = item
                break

        if not profile_entity:
            profile_entity = data.get('profile') or data.get('data', {})

        if isinstance(profile_entity, dict) and profile_entity:
            profile.first_name = get_text(profile_entity.get('firstName'))
            profile.last_name = get_text(profile_entity.get('lastName'))
            if profile.first_name or profile.last_name:
                profile.full_name = f"{profile.first_name} {profile.last_name}".strip()
            else:
                profile.full_name = get_text(profile_entity.get('name')) or get_text(profile_entity.get('fullName'))

            profile.headline = get_text(profile_entity.get('headline'))
            profile.summary = get_text(profile_entity.get('summary')) or get_text(profile_entity.get('about'))
            profile.industry = get_text(profile_entity.get('industryName')) or get_text(profile_entity.get('industry'))
            profile.location = get_text(profile_entity.get('geoLocationName')) or get_text(profile_entity.get('locationName'))
            profile.urn_id = profile_entity.get('entityUrn', '')

            picture = profile_entity.get('profilePicture', {}) or profile_entity.get('picture', {})
            if isinstance(picture, str) and not is_placeholder_image(picture):
                profile.profile_picture_url = picture
            elif isinstance(picture, dict):
                display_img = picture.get('displayImageReference', {}).get('vectorImage', {}) or picture.get('vectorImage', {})
                if display_img:
                    root_url = display_img.get('rootUrl', '')
                    artifacts = display_img.get('artifacts', [])
                    if artifacts and root_url:
                        cand_pp = root_url + artifacts[-1].get('fileIdentifyingUrlPathSegment', '')
                        if not is_placeholder_image(cand_pp):
                            profile.profile_picture_url = cand_pp

        for item in included:
            type_name = item.get('$type', '')
            if 'Position' in type_name or 'com.linkedin.voyager.dash.identity.profile.Position' in type_name:
                title = get_text(item.get('title'))
                company = get_text(item.get('companyName')) or get_text(item.get('company', {}).get('name'))
                if title or company:
                    profile.experiences.append(Experience(
                        title=title or "Position",
                        company=company or "Company",
                        location=get_text(item.get('locationName')),
                        description=get_text(item.get('description'))
                    ))

            elif 'Education' in type_name or 'com.linkedin.voyager.dash.identity.profile.Education' in type_name:
                school = get_text(item.get('schoolName')) or get_text(item.get('school', {}).get('name'))
                if school:
                    profile.educations.append(Education(
                        school=school,
                        degree=get_text(item.get('degreeName')),
                        field_of_study=get_text(item.get('fieldOfStudy')),
                        description=get_text(item.get('description'))
                    ))

            elif 'Certification' in type_name or 'com.linkedin.voyager.dash.identity.profile.Certification' in type_name:
                c_name = get_text(item.get('name'))
                if c_name:
                    profile.certifications.append(Certification(
                        name=c_name,
                        authority=get_text(item.get('authority')) or get_text(item.get('company', {}).get('name')),
                        url=item.get('url')
                    ))

            elif 'Skill' in type_name or 'com.linkedin.voyager.dash.identity.profile.Skill' in type_name:
                s_name = get_text(item.get('name'))
                if s_name and s_name not in profile.skills:
                    profile.skills.append(s_name)

        return profile

    def _fetch_via_web_html(self) -> LinkedInProfile:
        candidate_uas = list(DEFAULT_BYPASS_USER_AGENTS)
        if self.user_agent not in candidate_uas:
            candidate_uas.insert(0, self.user_agent)

        last_error = None
        for ua in candidate_uas:
            try:
                headers = {
                    'User-Agent': ua,
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                    'Accept-Language': 'en-US,en;q=0.9,tr;q=0.8',
                    'Accept-Encoding': 'gzip, deflate, br'
                }
                response = self.session.get(
                    self.public_url,
                    headers=headers,
                    timeout=self.timeout,
                    allow_redirects=True
                )

                if response.status_code == 404:
                    raise FileNotFoundError(f"LinkedIn profile not found for '{self.username}' (Status 404).")

                if response.status_code == 200 and len(response.text) > 3000:
                    profile = self._parse_html_and_jsonld(response.text)
                    if profile.full_name or profile.headline or profile.posts:
                        return profile
            except FileNotFoundError:
                raise
            except Exception as e:
                last_error = e
                continue

        raise PermissionError(
            f"Could not bypass LinkedIn anti-bot wall: {last_error or 'HTTP 999'}.\n"
            "LinkedIn may be temporarily throttling this IP. Please try again with proxies (--proxy) or valid session cookies."
        )

    def _extract_profile_picture(self, soup: BeautifulSoup, json_ld_items: List[Dict[str, Any]], html: str = "") -> Optional[str]:
        """
        Extracts the cleanest, highest-resolution Profile Picture URL.
        Multi-layered detection pipeline:
        1. OpenGraph & Twitter image meta tags
        2. Schema.org JSON-LD Person image
        3. Visual DOM elements (top-card avatar, delayed/src/srcset urls)
        4. Background CSS style attributes on profile card
        5. Embedded raw HTML regex scan for genuine profile CDN media
        Filters out ghost placeholders, SVG badges, course thumbnails, company logos, and banners.
        """
        candidates: List[Tuple[int, str]] = []

        def score_url(url: str) -> int:
            if not url or is_invalid_profile_picture(url):
                return -1
            u = url.lower()
            score = 10
            if any(k in u for k in ['profile-displayphoto', 'profile-framedphoto', 'profilephoto', 'displayimagereference']):
                score += 100
            if 'shrink_800_800' in u or '800_800' in u:
                score += 50
            elif 'shrink_400_400' in u or '400_400' in u:
                score += 30
            elif 'shrink_200_200' in u or '200_200' in u:
                score += 15
            elif 'shrink_100_100' in u or '100_100' in u:
                score += 5
            return score

        def add_candidate(url: Optional[str], base_priority: int = 0):
            if not url or not isinstance(url, str):
                return
            cand = url.replace('&amp;', '&').strip()
            if cand.startswith('//'):
                cand = 'https:' + cand
            if not cand.startswith('http'):
                return
            s = score_url(cand)
            if s > 0:
                candidates.append((s + base_priority, cand))

        # 1. OpenGraph & Twitter Meta Tags (frequently contains highest-resolution profile photo)
        for meta_spec in [('property', 'og:image'), ('name', 'twitter:image'), ('property', 'twitter:image')]:
            tag = soup.find('meta', attrs={meta_spec[0]: meta_spec[1]})
            if tag and tag.get('content'):
                add_candidate(tag['content'], base_priority=40)

        # 2. Schema.org JSON-LD Person image
        for it in json_ld_items:
            img = it.get('image')
            if isinstance(img, dict):
                add_candidate(img.get('contentUrl') or img.get('url'), base_priority=35)
            elif isinstance(img, str):
                add_candidate(img, base_priority=35)
            elif isinstance(img, list):
                for sub_img in img:
                    if isinstance(sub_img, dict):
                        add_candidate(sub_img.get('contentUrl') or sub_img.get('url'), base_priority=35)
                    elif isinstance(sub_img, str):
                        add_candidate(sub_img, base_priority=35)

        # 3. DOM <img> tags - check delayed-url, data-src, srcset, and src
        for img_tag in soup.find_all('img'):
            classes = " ".join(img_tag.get('class', []))
            parent_classes = " ".join(img_tag.parent.get('class', [])) if img_tag.parent else ""
            combined_cls = (classes + " " + parent_classes).lower()
            is_top_card = any(k in combined_cls for k in [
                'top-card', 'profile-image', 'pv-top-card', 'profile-photo', 'artdeco-entity-image', 'entity-image'
            ])
            alt_matches = False
            alt_val = img_tag.get('alt', '').lower()
            if alt_val and self.username.lower() in alt_val:
                alt_matches = True

            bonus = 30 if (is_top_card or alt_matches) else 0

            # Direct attributes
            for attr in ['data-delayed-url', 'data-src', 'data-original', 'data-ghost-url', 'src']:
                val = img_tag.get(attr)
                if val and not val.startswith('data:image'):
                    add_candidate(val, base_priority=bonus)

            # Responsive srcset
            srcset = img_tag.get('srcset')
            if srcset:
                for part in srcset.split(','):
                    url_seg = part.strip().split()[0]
                    if url_seg and not url_seg.startswith('data:image'):
                        add_candidate(url_seg, base_priority=bonus)

        # 4. Background style attributes on top card
        for el in soup.find_all(attrs={'style': re.compile(r'url\(')}):
            style_str = el.get('style', '')
            if 'profile-backgroundimage' not in style_str.lower():
                m = re.search(r'url\([\'"]?(https://media\.licdn\.com/dms/image/[^\'")]+)', style_str)
                if m:
                    add_candidate(m.group(1), base_priority=15)

        # 5. Raw HTML Regex Scan (safety net for lazy-loaded JSON / inline state)
        if html:
            raw_matches = re.findall(
                r'https://media\.licdn\.com/dms/image/(?:v\d+/)?[\w\-]+/(?:profile-(?:displayphoto|framedphoto)|profilephoto)[^"\'\s<>&]+',
                html
            )
            for m in raw_matches:
                add_candidate(m, base_priority=25)

        if not candidates:
            return None

        # Sort by total score descending
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]

    def _extract_background_picture(self, soup: BeautifulSoup) -> Optional[str]:
        """Extracts profile background/banner image URL if present."""
        for img_tag in soup.find_all('img'):
            src = img_tag.get('src') or img_tag.get('data-delayed-url') or ''
            src = src.replace('&amp;', '&').strip()
            if 'profile-backgroundimage' in src.lower():
                return src
        for el in soup.find_all(attrs={'style': re.compile(r'url\(')}):
            m = re.search(r'url\([\'"]?(https://media\.licdn\.com/dms/image/[^\'")]+)', el['style'])
            if m and 'profile-backgroundimage' in m.group(1).lower():
                return m.group(1).replace('&amp;', '&')
        return None

    def _parse_html_and_jsonld(self, html: str) -> LinkedInProfile:
        profile = LinkedInProfile(
            username=self.username,
            public_url=self.public_url
        )

        if not BS4_AVAILABLE:
            title_match = re.search(r'<title>(.*?)</title>', html, re.IGNORECASE)
            if title_match:
                title = title_match.group(1).replace(' | LinkedIn', '')
                parts = title.split(' - ')
                profile.full_name = parts[0].strip()
                if len(parts) > 1:
                    profile.headline = parts[1].strip()
            return profile

        soup = BeautifulSoup(html, 'html.parser')
        json_ld_items = []

        # 1. Parse Schema.org JSON-LD Graph
        json_ld_tags = soup.find_all('script', type='application/ld+json')
        for tag in json_ld_tags:
            try:
                data = json.loads(tag.string or '{}')
                items = data if isinstance(data, list) else data.get('@graph', [data])
                for item in items:
                    json_ld_items.append(item)
                    item_type = item.get('@type', '')

                    if item_type in ['Person', 'http://schema.org/Person']:
                        if not profile.full_name:
                            p_name = clean_text(item.get('name', ''))
                            if not is_masked(p_name):
                                profile.full_name = p_name

                        if not profile.headline and item.get('jobTitle'):
                            job_t = item.get('jobTitle')
                            raw_job = job_t if isinstance(job_t, str) else ", ".join(job_t)
                            if not is_masked(raw_job):
                                profile.headline = clean_text(raw_job)

                        if not profile.summary and item.get('description'):
                            p_desc = clean_text(item.get('description'))
                            if not is_masked(p_desc):
                                profile.summary = p_desc

                        if 'address' in item:
                            addr = item['address']
                            if isinstance(addr, dict):
                                loc_parts = [addr.get('addressLocality'), addr.get('addressRegion'), addr.get('addressCountry')]
                                profile.location = ", ".join([p for p in loc_parts if p and not is_masked(p)])

                        stats = item.get('interactionStatistic', {})
                        if isinstance(stats, dict) and 'userInteractionCount' in stats:
                            try:
                                profile.follower_count = int(stats['userInteractionCount'])
                            except (ValueError, TypeError):
                                pass

                        knows_lang = item.get('knowsLanguage')
                        if knows_lang:
                            langs = knows_lang if isinstance(knows_lang, list) else [knows_lang]
                            for l in langs:
                                l_name = clean_text(l.get('name') if isinstance(l, dict) else str(l))
                                if l_name and not is_masked(l_name) and l_name not in profile.languages:
                                    profile.languages.append(l_name)

                        # Parse worksFor
                        works_for = item.get('worksFor')
                        if works_for:
                            orgs = works_for if isinstance(works_for, list) else [works_for]
                            for org in orgs:
                                org_name = clean_text(org.get('name') if isinstance(org, dict) else str(org))
                                if org_name and not is_masked(org_name):
                                    if not any(e.company.lower() == org_name.lower() for e in profile.experiences):
                                        profile.experiences.append(Experience(
                                            title=profile.headline or "Position",
                                            company=org_name
                                        ))

                        # Parse hasOccupation (alternative Schema.org pattern for roles & companies)
                        has_occ = item.get('hasOccupation')
                        if has_occ:
                            occs = has_occ if isinstance(has_occ, list) else [has_occ]
                            for occ in occs:
                                if isinstance(occ, dict):
                                    o_title = clean_text(occ.get('name') or occ.get('roleName'))
                                    o_comp = None
                                    o_wf = occ.get('worksFor')
                                    if isinstance(o_wf, dict):
                                        o_comp = clean_text(o_wf.get('name'))
                                    elif isinstance(o_wf, str):
                                        o_comp = clean_text(o_wf)
                                    if o_comp and not is_masked(o_comp):
                                        if not any(e.company.lower() == o_comp.lower() for e in profile.experiences):
                                            profile.experiences.append(Experience(
                                                title=o_title or profile.headline or "Position",
                                                company=o_comp
                                            ))

                        alumni_of = item.get('alumniOf')
                        if alumni_of:
                            schools = alumni_of if isinstance(alumni_of, list) else [alumni_of]
                            for sch in schools:
                                sch_name = clean_text(sch.get('name') if isinstance(sch, dict) else str(sch))
                                if sch_name and not is_masked(sch_name):
                                    d_range = None
                                    member = sch.get('member') if isinstance(sch, dict) else None
                                    if isinstance(member, dict):
                                        s_date = str(member.get('startDate', '')).strip()
                                        e_date = str(member.get('endDate', '')).strip()
                                        if s_date or e_date:
                                            d_range = f"{s_date} - {e_date}".strip(' -')

                                    if not any(ed.school.lower() == sch_name.lower() for ed in profile.educations):
                                        profile.educations.append(Education(school=sch_name, date_range=d_range))

                        knows_about = item.get('knowsAbout')
                        if knows_about:
                            sk_list = knows_about if isinstance(knows_about, list) else [knows_about]
                            for s in sk_list:
                                s_name = clean_text(s.get('name') if isinstance(s, dict) else str(s))
                                if s_name and not is_masked(s_name) and s_name not in profile.skills:
                                    profile.skills.append(s_name)

                        same_as = item.get('sameAs')
                        if same_as:
                            links = same_as if isinstance(same_as, list) else [same_as]
                            valid_links = [str(l).strip() for l in links if str(l).strip()]
                            if valid_links:
                                profile.contact_info['external_profiles'] = valid_links

                    elif item_type in ['DiscussionForumPosting', 'SocialMediaPosting', 'Article']:
                        post_text = clean_text(item.get('text', '') or item.get('headline', ''))
                        if post_text:
                            p_likes = None
                            int_stat = item.get('interactionStatistic', {})
                            if isinstance(int_stat, dict):
                                p_likes = int_stat.get('userInteractionCount')
                            
                            post_obj = PostActivity(
                                text=post_text,
                                date_published=item.get('datePublished', ''),
                                likes_count=p_likes,
                                url=item.get('url') or item.get('mainEntityOfPage')
                            )
                            profile.posts.append(post_obj)

            except Exception:
                continue

        # 2. Extract Profile Picture & Background Banner (passes full HTML for regex fallback)
        profile.profile_picture_url = self._extract_profile_picture(soup, json_ld_items, html=html)
        profile.background_picture_url = self._extract_background_picture(soup)

        # 3. Fallback to Meta Tags for Title / Headline & Identity
        if not profile.full_name or not profile.headline or profile.headline.lower() in ["professional profile", "linkedin profile"]:
            for meta_t in [
                soup.find('meta', property='og:title'),
                soup.find('meta', attrs={'name': 'twitter:title'}),
                soup.find('title')
            ]:
                if meta_t:
                    t_content = meta_t.get('content') or meta_t.string or ""
                    if t_content:
                        clean_t = t_content.replace(' | LinkedIn', '').replace(' - LinkedIn', '').replace(' | Professional Profile', '').replace(' on LinkedIn', '').strip()
                        parts = [p.strip() for p in re.split(r'\s+[-–—|]\s+', clean_t) if p.strip()]
                        if parts:
                            if not profile.full_name:
                                profile.full_name = clean_text(parts[0])
                            if len(parts) > 1 and (not profile.headline or profile.headline.lower() in ["professional profile", "linkedin profile"]):
                                cand_hl = clean_text(" - ".join(parts[1:]))
                                if not is_masked(cand_hl):
                                    profile.headline = cand_hl
                        break

        # 4. Parse Meta Description for Experience, Education, Location, and Connection Count
        og_desc = soup.find('meta', property='og:description') or soup.find('meta', attrs={'name': 'description'})
        if og_desc and og_desc.get('content'):
            raw_desc = clean_text(og_desc['content'])
            if not profile.summary:
                profile.summary = raw_desc

            # Extract Experience / Deneyim from meta description
            exp_match = re.search(r'(?:Experience|Deneyim)\s*:\s*([^·\n|]+)', raw_desc, re.IGNORECASE)
            if exp_match:
                raw_exp_str = exp_match.group(1).strip()
                split_comps = [c.strip() for c in raw_exp_str.split(',') if c.strip()]
                for comp_str in split_comps:
                    pair = extract_company_from_text(comp_str)
                    if pair:
                        cand_t, cand_c = pair
                        if not any(e.company.lower() == cand_c.lower() for e in profile.experiences):
                            profile.experiences.append(Experience(title=cand_t, company=cand_c))
                    else:
                        if comp_str and not any(e.company.lower() == comp_str.lower() for e in profile.experiences):
                            profile.experiences.append(Experience(title=profile.headline or "Position", company=comp_str))

            # Extract Education / Egitim from meta description
            edu_match = re.search(r'(?:Education|Eğitim)\s*:\s*([^·\n|]+)', raw_desc, re.IGNORECASE)
            if edu_match:
                raw_edu_str = edu_match.group(1).strip()
                split_edus = [ed.strip() for ed in raw_edu_str.split(',') if ed.strip()]
                for ed_str in split_edus:
                    if ed_str and not any(ed.school.lower() == ed_str.lower() for ed in profile.educations):
                        profile.educations.append(Education(school=ed_str))

            # Extract Location / Konum from meta description
            loc_match = re.search(r'(?:Location|Konum)\s*:\s*([^·\n|]+)', raw_desc, re.IGNORECASE)
            if loc_match and not profile.location:
                cand_loc = loc_match.group(1).strip()
                if not is_masked(cand_loc):
                    profile.location = cand_loc
            elif not profile.location and '·' in raw_desc:
                loc_cand = raw_desc.split('·')[0].strip()
                if len(loc_cand) < 50 and not is_masked(loc_cand) and not any(kw in loc_cand.lower() for kw in ['experience', 'education', 'view', 'profile']):
                    profile.location = loc_cand

            conn_match = re.search(r'([\d,]+)\+?\s+(?:connections|bağlantı)', raw_desc, re.IGNORECASE)
            if conn_match:
                try:
                    profile.connections_count = int(conn_match.group(1).replace(',', ''))
                except ValueError:
                    pass

        # 5. Parse Visual DOM Experience Section
        exp_container = None
        for sec in soup.find_all(['section', 'div']):
            sec_h = sec.find(['h2', 'h3'])
            if sec_h and any(k in sec_h.get_text().lower() for k in ['experience', 'deneyim', 'iş deneyimi', 'work experience']):
                exp_container = sec
                break

        if exp_container:
            for item in exp_container.find_all(['li', 'div'], recursive=True):
                title_el = item.find(['h3', 'span'], class_=re.compile(r'(?:title|job|role)', re.I)) or item.find('h3')
                company_el = item.find(['h4', 'span', 'p'], class_=re.compile(r'(?:company|subtitle|org)', re.I)) or item.find('h4')
                if title_el and company_el:
                    t_txt = clean_text(title_el.get_text())
                    c_txt = clean_text(company_el.get_text())
                    if t_txt and c_txt and not is_masked(c_txt) and len(c_txt) < 100:
                        c_clean = c_txt.split('·')[0].split('\n')[0].strip()
                        if c_clean and not any(e.company.lower() == c_clean.lower() for e in profile.experiences):
                            date_el = item.find(['span', 'time', 'p'], class_=re.compile(r'(?:date|duration|time)', re.I))
                            loc_el = item.find(['span', 'p'], class_=re.compile(r'(?:location|place)', re.I))
                            profile.experiences.append(Experience(
                                title=t_txt,
                                company=c_clean,
                                date_range=clean_text(date_el.get_text()) if date_el else None,
                                location=clean_text(loc_el.get_text()) if loc_el else None
                            ))

        # 6. Fallback DOM Headline if still missing
        if not profile.headline or profile.headline.lower() in ["professional profile", "linkedin profile"]:
            for hl_tag, hl_attrs in [
                ('h2', {'class': re.compile(r'top-card.*headline', re.I)}),
                ('div', {'class': re.compile(r'top-card.*headline', re.I)}),
                ('p', {'class': re.compile(r'top-card.*first-subline', re.I)}),
                ('h2', {'class': re.compile(r'pv-top-card--headline', re.I)}),
                ('div', {'class': re.compile(r'text-body-medium', re.I)})
            ]:
                dom_hl = soup.find(hl_tag, attrs=hl_attrs)
                if dom_hl:
                    cand_hl = clean_text(dom_hl.get_text())
                    if cand_hl and not is_masked(cand_hl) and len(cand_hl) < 160:
                        profile.headline = cand_hl
                        break

        # 7. Extract Company from Headline if profile.experiences is STILL empty
        if not profile.experiences and profile.headline:
            pair = extract_company_from_text(profile.headline)
            if pair:
                h_title, h_comp = pair
                if h_comp:
                    profile.experiences.append(Experience(
                        title=h_title or profile.headline,
                        company=h_comp
                    ))

        # 8. Fallback Headline from primary experience if headline is still missing
        if (not profile.headline or profile.headline.lower() in ["professional profile", "linkedin profile"]) and profile.experiences:
            exp0 = profile.experiences[0]
            if exp0.company and exp0.title and exp0.title != "Position":
                profile.headline = f"{exp0.title} at {exp0.company}"
            elif exp0.company:
                profile.headline = exp0.company

        # 9. Parse Visual DOM Skills
        for s_sec in soup.find_all(['section', 'div']):
            sec_h = s_sec.find(['h2', 'h3'])
            if sec_h and any(k in sec_h.get_text().lower() for k in ['skills', 'yetenekler', 'competencies']):
                for li in s_sec.find_all('li'):
                    s_txt = clean_text(li.get_text())
                    if s_txt and len(s_txt) < 50 and not is_masked(s_txt):
                        first_line = s_txt.split('\n')[0].strip()
                        if first_line and first_line not in profile.skills:
                            profile.skills.append(first_line)

        # 10. Parse Visual DOM Certifications
        cert_container = None
        for heading in soup.find_all(['h2', 'h3']):
            h_text = heading.get_text().strip().lower()
            if 'licenses & certifications' in h_text or 'certifications' in h_text or 'sertifikalar' in h_text:
                cert_container = heading.find_parent('section') or heading.find_parent('div')
                break

        if cert_container:
            for item in cert_container.find_all('li'):
                title_el = item.find(['h3', 'h4', 'span'])
                if title_el:
                    c_name = clean_text(title_el.get_text())
                    if c_name and not is_masked(c_name) and len(c_name) < 100:
                        authority = None
                        issue_date = None
                        sub_els = item.find_all(['p', 'span'])
                        for sub in sub_els:
                            txt = clean_text(sub.get_text())
                            if not txt or txt == c_name:
                                continue
                            if any(w in txt.lower() for w in ['issued', 'veriliş', 'tarih', 'expires']):
                                issue_date = txt
                            elif not authority and len(txt) < 60 and not is_masked(txt):
                                authority = txt

                        if not any(c.name.lower() == c_name.lower() for c in profile.certifications):
                            profile.certifications.append(Certification(
                                name=c_name,
                                authority=authority,
                                date=issue_date
                            ))

        # 11. Extract Contact Info & External Links (GitHub, Twitter, Websites, Emails)
        for a in soup.find_all('a', href=True):
            h = a['href']
            if 'github.com/' in h and not any(ign in h.lower() for ign in ['github.com/login', 'github.com/about', 'github.com/pricing', 'github.com/features']):
                m = re.search(r'github\.com/([a-zA-Z0-9_\-]+)', h)
                if m and m.group(1).lower() not in ['site', 'join']:
                    profile.contact_info['github'] = f"https://github.com/{m.group(1)}"
            elif 'twitter.com/' in h or 'x.com/' in h:
                m = re.search(r'(?:twitter|x)\.com/([a-zA-Z0-9_]+)', h)
                if m and m.group(1).lower() not in ['intent', 'share']:
                    profile.contact_info['twitter'] = f"https://x.com/{m.group(1)}"

        # Search public text for direct emails
        email_cand = re.findall(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+', html)
        clean_emails = [e for e in email_cand if not any(ign in e.lower() for ign in ['example.com', 'sentry.io', 'schema.org', 'linkedin.com', 'w3.org'])]
        if clean_emails:
            profile.contact_info['emails'] = list(set(clean_emails))[:3]

        return profile


# ==============================================================================
# LinkedIn Company & Employee Discovery Engine
# ==============================================================================

class LinkedInCompanyFetcher:
    """
    Extracts company intelligence and discovers affiliated employees.
    Supports Zero-Cookie Multi-Source Discovery and Deep Voyager Search Pagination.
    """

    def __init__(self, company_name_or_url: str, cookies: Optional[Dict[str, str]] = None, proxy: Optional[str] = None, timeout: int = 20):
        self.slug = extract_company_slug(company_name_or_url)
        self.public_url = f"https://www.linkedin.com/company/{quote(self.slug)}"
        self.cookies = cookies or {}
        self.proxy = proxy
        self.timeout = timeout
        self.session = requests.Session()
        self._setup_session()

    def _setup_session(self):
        retries = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
        adapter = HTTPAdapter(max_retries=retries)
        self.session.mount('https://', adapter)
        self.session.mount('http://', adapter)

        if self.proxy:
            self.session.proxies.update({'http': self.proxy, 'https': self.proxy})

        if self.is_authenticated():
            self.session.headers.update({
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9,tr;q=0.8',
            })
        else:
            self.session.headers.update({
                'User-Agent': 'WhatsApp/2.21.12.21 A',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9,tr;q=0.8',
            })

        for k, v in self.cookies.items():
            clean_k = re.sub(r'[^a-zA-Z0-9_\-]', '', str(k))
            clean_v = str(v).strip('"\'\\ ').split()[0]
            if clean_k and clean_v:
                self.session.cookies.set(clean_k, clean_v, domain='.linkedin.com')

        # Ensure matching JSESSIONID exists when authenticated
        if self.is_authenticated() and not self.cookies.get('JSESSIONID') and not self.session.cookies.get('JSESSIONID'):
            dummy_j = "ajax:4820194829104820"
            self.session.cookies.set('JSESSIONID', f'"{dummy_j}"', domain='.linkedin.com')

    def is_authenticated(self) -> bool:
        return 'li_at' in self.cookies

    def _get_csrf_token(self) -> Optional[str]:
        raw_csrf = self.cookies.get('JSESSIONID') or self.session.cookies.get('JSESSIONID')
        if not raw_csrf:
            return None
        match = re.search(r'ajax:[a-zA-Z0-9_\-]+', raw_csrf)
        if match:
            return match.group(0)
        cleaned = re.sub(r'[^a-zA-Z0-9_\-:]', '', raw_csrf).strip()
        return cleaned if cleaned else None

    def _resolve_company_slug(self, query: str) -> str:
        """Attempts to resolve a company slug when direct match returns 404."""
        variations = [
            f"{query}-group", f"{query}group", f"{query}-com", f"{query}com",
            f"{query}-technologies", f"{query}-technology", f"{query}-tech",
            f"{query}-security", f"{query}-official", f"{query}-turkey", f"{query}-tr"
        ]
        for var in variations:
            try:
                v_url = f"{LINKEDIN_BASE_URL}/company/{var}/"
                r_v = self.session.get(v_url, timeout=4)
                if r_v.status_code == 200:
                    return var
            except Exception:
                pass
        try:
            b_url = f"https://search.brave.com/search?q={quote_plus('site:linkedin.com/company ' + query)}"
            r_b = requests.get(b_url, headers={'User-Agent': DEFAULT_USER_AGENTS[0]}, timeout=5)
            if r_b.status_code == 200:
                m = re.search(r'linkedin\.com/company/([a-zA-Z0-9\-_]+)', r_b.text)
                if m:
                    return m.group(1).lower().strip('/')
        except Exception:
            pass
        return query

    def fetch(self, deep_scrape_employees: bool = False, max_employees: int = 50, quiet: bool = False, interactive: bool = False) -> LinkedInCompany:
        if not quiet:
            Logger.info(f"Querying company intelligence for '[bold cyan]{self.slug}[/bold cyan]'...")
        
        response = self.session.get(self.public_url, timeout=self.timeout)
        if response.status_code == 404:
            resolved_slug = self._resolve_company_slug(self.slug)
            if resolved_slug and resolved_slug != self.slug:
                if not quiet:
                    Logger.info(f"Auto-resolved company slug '[bold yellow]{self.slug}[/bold yellow]' -> '[bold green]{resolved_slug}[/bold green]'")
                self.slug = resolved_slug
                self.public_url = f"{LINKEDIN_BASE_URL}/company/{self.slug}"
                response = self.session.get(self.public_url, timeout=self.timeout)

        if response.status_code == 404:
            raise FileNotFoundError(f"Company page not found for '{self.slug}' (Status 404).")

        company = self._parse_company_page(response.text)
        
        # Robust Organization URN extraction across various formats
        org_id = None
        for pattern in [
            r'urn:li:organization:(\d+)',
            r'urn:li:fsd_company:(\d+)',
            r'urn:li:company:(\d+)',
            r'"companyId":\s*["\']?(\d+)',
            r'"objectUrn":\s*"urn:li:organization:(\d+)"',
            r'"entityUrn":\s*"urn:li:fs_normalized_company:(\d+)"',
            r'normalized_company%3A(\d+)',
        ]:
            m = re.search(pattern, response.text)
            if m:
                org_id = m.group(1)
                break

        # Discover employees
        if not self.is_authenticated():
            if not quiet:
                Logger.info("Zero-Cookie Mode active. Harvesting employees from multi-source public feeds & search engines...")
            self._harvest_employees_zero_cookie(company, max_results=max_employees, org_id=org_id)
            if not quiet:
                Logger.success(f"Zero-Cookie Discovery complete: Found {len(company.employees)} employees.")
        elif org_id:
            if not quiet:
                Logger.info(f"Authenticated session detected. Querying Employee Search for Org ID [cyan]{org_id}[/cyan] (Limit: {max_employees})...")
            voyager_emps = self._search_employees_via_voyager(org_id, max_results=max_employees, quiet=quiet, interactive=interactive)
            if voyager_emps:
                seen = {e.username for e in company.employees}
                for v_emp in voyager_emps:
                    if v_emp.username not in seen:
                        seen.add(v_emp.username)
                        company.employees.append(v_emp)
                if not quiet:
                    Logger.success(f"Authenticated Employee Search complete: Discovered {len(company.employees)} total employees.")
            else:
                if not quiet:
                    Logger.warning("Authenticated search returned no results. Falling back to Zero-Cookie multi-source discovery...")
                self._harvest_employees_zero_cookie(company, max_results=max_employees, org_id=org_id)
                if not quiet:
                    Logger.success(f"Zero-Cookie Discovery complete: Found {len(company.employees)} employees.")

        # Deep scrape discovered employees if requested
        if deep_scrape_employees and company.employees:
            target_emps = company.employees[:max_employees]
            if not quiet:
                Logger.info(f"Deep scraping {len(target_emps)} discovered employee profiles (Zero-Cookie modu)...")

            if RICH_AVAILABLE and console and not quiet:
                with Progress(
                    SpinnerColumn(),
                    TextColumn("[bold cyan]{task.description}[/bold cyan]"),
                    BarColumn(bar_width=25),
                    TextColumn("[bold green]{task.completed}/{task.total}[/bold green] profil"),
                    TimeElapsedColumn(),
                    console=console
                ) as deep_progress:
                    deep_task = deep_progress.add_task("Profiller taranıyor...", total=len(target_emps))
                    for idx, emp in enumerate(target_emps, 1):
                        deep_progress.update(deep_task, description=f"Profil: [cyan]{emp.username}[/cyan]")
                        try:
                            time.sleep(1.0)
                            fetcher = LinkedInFetcher(emp.username, cookies=None, proxy=self.proxy, timeout=self.timeout)
                            emp_profile = fetcher.fetch()
                            emp.profile_data = emp_profile
                            if emp_profile.full_name:
                                emp.full_name = emp_profile.full_name
                            if emp_profile.headline:
                                emp.role_or_title = emp_profile.headline
                        except Exception as e:
                            pass
                        deep_progress.update(deep_task, advance=1)
            else:
                for idx, emp in enumerate(target_emps, 1):
                    try:
                        time.sleep(1.0)
                        # CRITICAL: Do NOT reuse company session cookies for scraping individual profiles
                        fetcher = LinkedInFetcher(emp.username, cookies=None, proxy=self.proxy, timeout=self.timeout)
                        emp_profile = fetcher.fetch()
                        emp.profile_data = emp_profile
                        if emp_profile.full_name:
                            emp.full_name = emp_profile.full_name
                        if emp_profile.headline:
                            emp.role_or_title = emp_profile.headline
                    except Exception as e:
                        if not quiet:
                            Logger.warning(f"Could not deep scrape {emp.username}: {e}")

        return company

    def _harvest_employees_zero_cookie(self, company: LinkedInCompany, max_results: int = 50, org_id: Optional[str] = None):
        """Discovers company employees via public subpages, JSON-LD feeds, search engines, and BFS peer graph harvesting without cookies."""
        seen = {e.username for e in company.employees}
        seed_pool = set(seen)

        def add_emp(uname: str, name: Optional[str] = None, title: Optional[str] = None):
            uname = unquote(uname.strip().strip('/')).lower()
            if not uname:
                return
            # Sanitize search snippet artifacts from names
            if name and any(ign in name.lower() for ign in ['linkedin', '›', 'view', 'profile', 'http', 'more', 'takip', 'follow', 'sign in']):
                name = None
            if uname not in seen:
                if len(company.employees) < max_results:
                    seen.add(uname)
                    company.employees.append(CompanyEmployee(
                        username=uname,
                        full_name=name,
                        role_or_title=title,
                        public_url=f"https://www.linkedin.com/in/{uname}"
                    ))
            else:
                # Update existing employee record with enriched full name or verified title
                for e in company.employees:
                    if e.username == uname:
                        if name and (not e.full_name or 'linkedin' in e.full_name.lower()):
                            e.full_name = name
                        if title and (not e.role_or_title or e.role_or_title.startswith('Staff Affiliate') or e.role_or_title == 'Indexed Employee'):
                            e.role_or_title = title
                        break

        # Distinctive keyword filter to avoid false positives on generic industry words
        STOPWORDS = {
            'security', 'cyber', 'siber', 'technologies', 'technology', 'teknoloji',
            'holding', 'group', 'grup', 'ltd', 'inc', 'corp', 'corporation', 'anonim',
            'sirketi', 'şirketi', 'company', 'services', 'solutions', 've', 'the',
            'danismanlik', 'danışmanlık', 'yazilim', 'yazılım', 'bilisim', 'bilişim',
            'ticaret', 'llc', 'gmbh', 'co', 'international', 'turkey', 'türkiye'
        }
        keywords = [self.slug.lower(), company.name.lower()]
        for part in re.split(r'[\s\-_]+', company.name):
            p_clean = re.sub(r'[^a-zA-Z0-9]', '', part).lower()
            if len(p_clean) >= 3 and p_clean not in STOPWORDS:
                keywords.append(p_clean)

        # 1. Scrape Public Subpages (Overview, Posts, Jobs, Life, About, People) with Multiple User-Agents
        for sub in ["", "/posts", "/jobs", "/life", "/about", "/people"]:
            for ua in [DEFAULT_BYPASS_USER_AGENTS[0], DEFAULT_USER_AGENTS[0]]:
                try:
                    sub_url = f"https://www.linkedin.com/company/{self.slug}{sub}"
                    r = self.session.get(sub_url, headers={'User-Agent': ua, 'Accept-Language': 'en-US,en;q=0.9'}, timeout=self.timeout)
                    if r.status_code == 200:
                        if BS4_AVAILABLE:
                            soup = BeautifulSoup(r.text, 'html.parser')
                            for a in soup.find_all('a', href=True):
                                u = extract_clean_username(a['href'])
                                if u:
                                    seed_pool.add(u)
                                    t_txt = clean_text(a.get_text())
                                    name_c = t_txt if (t_txt and len(t_txt) < 40 and not any(k in t_txt.lower() for k in ['view', 'profile', 'linkedin', 'more', 'takip', 'follow'])) else None
                                    add_emp(u, name=name_c, title=f"Staff Affiliate ({self.slug})")
                        for m in re.findall(r'https?://[a-z0-9\.]*linkedin\.com/in/([a-zA-Z0-9\-_%]+)', r.text):
                            u = extract_clean_username(f"https://www.linkedin.com/in/{m}")
                            if u:
                                seed_pool.add(u)
                except Exception:
                    pass

        # 2. Scrape Public Guest Jobs API for Recruiters & Hiring Posters
        for start_idx in [0, 25]:
            if len(company.employees) >= max_results:
                break
            try:
                job_url = f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords={quote_plus(company.name)}&start={start_idx}"
                r_job = self.session.get(job_url, headers={'User-Agent': DEFAULT_USER_AGENTS[0]}, timeout=self.timeout)
                if r_job.status_code == 200:
                    for m in re.findall(r'https?://[a-z0-9\.]*linkedin\.com/in/([a-zA-Z0-9\-_%]+)', r_job.text):
                        u = extract_clean_username(f"https://www.linkedin.com/in/{m}")
                        if u:
                            seed_pool.add(u)
                            add_emp(u, title=f"Recruitment / Hiring ({self.slug})")
            except Exception:
                pass

        # 3. Query Fast Public Search Indexes (Yahoo / Mojeek / Brave multi-query dorking)
        if len(company.employees) < max_results:
            search_queries = [
                f'site:linkedin.com/in "{company.name}"',
                f'site:linkedin.com/in "{self.slug}"',
                f'site:linkedin.com/in "at {company.name}"'
            ]
            for sq in search_queries:
                try:
                    y_url = f"https://search.yahoo.com/search?p={quote_plus(sq)}&b=11"
                    r_y = requests.get(y_url, headers={'User-Agent': DEFAULT_USER_AGENTS[0], 'Accept-Language': 'en-US,en;q=0.9'}, timeout=5)
                    if r_y.status_code == 200 and BS4_AVAILABLE:
                        soup = BeautifulSoup(r_y.text, 'html.parser')
                        for a in soup.find_all('a', href=True):
                            u = extract_clean_username(a['href'])
                            if u:
                                seed_pool.add(u)
                                t_txt = clean_text(a.get_text())
                                name_c = t_txt.split('-')[0].replace(' | LinkedIn', '').strip() if t_txt else None
                                add_emp(u, name=name_c if (name_c and len(name_c) < 40) else None, title="Indexed Employee")
                except Exception:
                    pass

        # 4. Phase 4: Automated BFS Peer Graph Harvester (Colleague Network Expansion)
        # Recursively traverses "People Also Viewed" / "More Profiles for You" peer graphs to uncover coworkers
        if seed_pool:
            candidate_queue = list(seed_pool)
            visited = set(seed_pool)

            def check_profile_peer(uname: str) -> Optional[dict]:
                time.sleep(random.uniform(0.04, 0.10))
                url = f"https://www.linkedin.com/in/{quote(uname)}"
                uas = list(DEFAULT_BYPASS_USER_AGENTS)
                random.shuffle(uas)

                for ua in uas[:3]:
                    try:
                        p_resp = requests.get(url, headers={'User-Agent': ua, 'Accept-Language': 'en-US,en;q=0.9'}, timeout=self.timeout)
                        if p_resp.status_code == 200:
                            p_soup = BeautifulSoup(p_resp.text, 'html.parser') if BS4_AVAILABLE else None
                            p_name = ""
                            p_works_for = []
                            p_peers = set()

                            # Peer links in DOM & regex
                            if p_soup:
                                for a in p_soup.find_all('a', href=True):
                                    pu = extract_clean_username(a['href'])
                                    if pu and pu != uname.lower():
                                        p_peers.add(pu)
                            for link in re.findall(r'https?://[a-z0-9\.]*linkedin\.com/in/([a-zA-Z0-9\-_%]+)', p_resp.text):
                                pu = extract_clean_username(f"https://www.linkedin.com/in/{link}")
                                if pu and pu != uname.lower():
                                    p_peers.add(pu)

                            # Title & Meta Description
                            title_text = ""
                            meta_desc = ""
                            if p_soup:
                                title_elem = p_soup.find('title')
                                title_text = title_elem.string if title_elem else ""
                                og_d = p_soup.find('meta', property='og:description')
                                meta_desc = og_d['content'] if og_d else ""
                            else:
                                t_m = re.search(r'<title>(.*?)</title>', p_resp.text, re.IGNORECASE)
                                title_text = t_m.group(1) if t_m else ""

                            role_str = None
                            # JSON-LD Graph
                            if p_soup:
                                for s in p_soup.find_all('script', type='application/ld+json'):
                                    try:
                                        d = json.loads(s.string or '{}')
                                        items = d if isinstance(d, list) else d.get('@graph', [d])
                                        for it in items:
                                            if it.get('@type') == 'Person':
                                                if not p_name:
                                                    p_name = it.get('name', '')
                                                if not role_str and it.get('jobTitle'):
                                                    jt = it.get('jobTitle')
                                                    role_str = jt if isinstance(jt, str) else ", ".join(jt)
                                                wf = it.get('worksFor', [])
                                                if isinstance(wf, dict):
                                                    wf = [wf]
                                                for w in wf:
                                                    org = w.get('name', '')
                                                    if org:
                                                        p_works_for.append(org)
                                    except Exception:
                                        pass

                            if not p_name and title_text:
                                parts = [p.strip() for p in re.split(r'\s+[-–—|]\s+', title_text.replace(' | LinkedIn', '').strip()) if p.strip()]
                                if parts:
                                    p_name = parts[0]
                                if len(parts) >= 2 and not role_str:
                                    role_str = parts[1]

                            if not role_str and title_text:
                                parts = [p.strip() for p in re.split(r'\s+[-–—|]\s+', title_text.replace(' | LinkedIn', '').strip()) if p.strip()]
                                if len(parts) >= 2:
                                    role_str = parts[1]

                            # Affiliation Verification
                            is_match = False
                            raw_low = p_resp.text.lower()
                            for kw in keywords:
                                for w in p_works_for:
                                    if kw in w.lower():
                                        is_match = True
                                        break
                                if kw in title_text.lower() or kw in meta_desc.lower() or f"/company/{kw}" in raw_low or kw in raw_low:
                                    is_match = True
                                if is_match:
                                    break

                            return {
                                'username': uname,
                                'name': p_name,
                                'role': role_str,
                                'is_match': is_match,
                                'peers': list(p_peers)
                            }
                        elif p_resp.status_code == 429:
                            time.sleep(0.25)
                            continue
                        elif p_resp.status_code == 404:
                            return None
                    except Exception:
                        pass
                return None

            rounds = 0
            while candidate_queue and len(company.employees) < max_results and rounds < 8:
                batch_size = 12
                current_batch = candidate_queue[:batch_size]
                candidate_queue = candidate_queue[batch_size:]

                with ThreadPoolExecutor(max_workers=6) as executor:
                    futures = {executor.submit(check_profile_peer, u): u for u in current_batch}
                    for fut in as_completed(futures):
                        res = fut.result()
                        if res:
                            if res['is_match']:
                                add_emp(res['username'], name=res['name'], title=res['role'])
                                # Prioritize coworker colleagues at the front of queue
                                for peer in res['peers']:
                                    if peer not in visited:
                                        visited.add(peer)
                                        candidate_queue.insert(0, peer)
                            else:
                                for peer in res['peers']:
                                    if peer not in visited and len(candidate_queue) < 200:
                                        visited.add(peer)
                                        candidate_queue.append(peer)
                        if len(company.employees) >= max_results:
                            break
                rounds += 1

    def _search_employees_via_voyager(self, org_id: str, max_results: int = 50, quiet: bool = False, interactive: bool = False) -> List[CompanyEmployee]:
        """
        Searches company employees via authenticated LinkedIn Search & People endpoints.
        Supports faceted multi-keyword & geo rotation to bypass the 1,000 LinkedIn results cap,
        rich visual progress bar, and interactive checkpoints every 1,000 employees.
        """
        discovered_employees: List[CompanyEmployee] = []
        seen = set()

        def add_cand(uname: str, name: Optional[str] = None, role: Optional[str] = None) -> bool:
            uname = unquote(uname.strip().strip('/')).lower()
            if not uname or uname in seen:
                return False
            if uname in ['feed', 'jobs', 'posts', 'about', 'people', 'life', 'company', 'admin', 'search', 'login', 'dir', 'signup']:
                return False
            seen.add(uname)
            discovered_employees.append(CompanyEmployee(
                username=uname,
                full_name=name or None,
                role_or_title=role or None,
                public_url=f"https://www.linkedin.com/in/{uname}"
            ))
            return True

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9,tr;q=0.8',
        }

        # Facet search segments to break past the 1,000-cap when requesting large rosters:
        # Segment 0: General unconstrained search
        # Segment 1..N: Cities, roles, and letters
        facet_keywords = [
            None, # Unconstrained first
            # Locations
            "Istanbul", "Ankara", "Izmir", "Bursa", "Antalya", "London", "Berlin", "New York", "San Francisco", "Remote", "Dubai", "Amsterdam",
            # Roles / Departments
            "Engineer", "Developer", "Manager", "Analyst", "Security", "Specialist", "Consultant", "Director", "Lead", "Designer", "Sales", "Marketing", "HR", "Operations", "Product", "Intern", "Founder", "Architect",
            # Alphabetical rotation
            *list("abcdefghijklmnopqrstuvwxyz")
        ]

        checkpoint_step = 1000
        next_checkpoint = checkpoint_step

        # Progress bar setup
        show_progress = RICH_AVAILABLE and console and not quiet
        progress = None
        task = None
        if show_progress:
            progress = Progress(
                SpinnerColumn(),
                TextColumn("[bold cyan]{task.description}[/bold cyan]"),
                BarColumn(bar_width=25),
                TextColumn("[bold green]{task.completed}/{task.total}[/bold green]"),
                TimeElapsedColumn(),
                console=console,
                transient=False
            )
            progress.start()
            task = progress.add_task("Çalışanlar taranıyor...", total=max_results)

        try:
            for seg_idx, kw in enumerate(facet_keywords):
                if len(discovered_employees) >= max_results:
                    break

                kw_label = f"'{kw}'" if kw else "Genel Arama"
                if progress and task is not None:
                    progress.update(task, description=f"Filtre: {kw_label} (Toplam: {len(discovered_employees)})")

                # LinkedIn search max 100 pages per query
                max_pages = min(100, ((max_results - len(discovered_employees)) // 10) + 3)
                consecutive_empty = 0

                for page in range(1, max_pages + 1):
                    if len(discovered_employees) >= max_results:
                        break

                    # Checkpoint prompt every 1000 employees if interactive
                    if interactive and len(discovered_employees) >= next_checkpoint:
                        if progress:
                            progress.stop()
                        rprint(f"\n[bold yellow]═══ CHECKPOINT: {len(discovered_employees)} Tekil Çalışan Toplandı ═══[/bold yellow]")
                        rprint("[dim]• Devam etmek için Enter'a basın.[/dim]")
                        rprint("[dim]• Yeni bir token / cookie (li_at) yapıştırmak isterseniz buraya yapıştırın.[/dim]")
                        rprint("[dim]• Aramayı burada bitirip kaydetmek için 'h', 'n' veya 'tamam' yazın.[/dim]")
                        ans = Prompt.ask("[bold cyan]Seçiminiz[/bold cyan]", default="").strip()
                        if ans.lower() in ['h', 'n', 'q', 'exit', 'tamam', 'dur']:
                            return discovered_employees
                        elif len(ans) > 20 and 'AQED' in ans:
                            # Update with new cookie
                            new_li = ans.split()[0].strip('; "\'')
                            self.cookies['li_at'] = new_li
                            self.session.cookies.set('li_at', new_li, domain='.linkedin.com')
                            Logger.success("Oturum cookie'si yeni token ile güncellendi!")
                        next_checkpoint += checkpoint_step
                        if progress:
                            progress.start()

                    # Build URL
                    if kw:
                        url = f"https://www.linkedin.com/search/results/people/?currentCompany=%5B%22{org_id}%22%5D&keywords={quote(kw)}&page={page}"
                    else:
                        url = f"https://www.linkedin.com/search/results/people/?currentCompany=%5B%22{org_id}%22%5D&page={page}"

                    try:
                        resp = self.session.get(url, headers=headers, timeout=self.timeout)
                        if resp.status_code == 200:
                            resp.encoding = 'utf-8'
                            page_new = 0
                            if BS4_AVAILABLE:
                                soup = BeautifulSoup(resp.text, 'html.parser')
                                # 1. Primary: Search result person card anchors (tabindex="0" or lockup-title)
                                cards = [a for a in soup.find_all('a', attrs={'tabindex': '0'}) if '/in/' in a.get('href', '')]
                                if not cards:
                                    cards = soup.find_all('a', attrs={'data-view-name': 'search-result-lockup-title'})
                                
                                if cards:
                                    for card in cards:
                                        href = card.get('href', '')
                                        m = re.search(r'/in/([^/?#\s]+)', href)
                                        if not m:
                                            continue
                                        u = unquote(m.group(1))

                                        # Extract person name
                                        inner_title = card.find('a', href=re.compile(r'/in/')) or card
                                        raw_name = inner_title.get_text(strip=True) if inner_title else ''
                                        p_name = None
                                        if raw_name:
                                            clean_n = raw_name.split('•')[0].strip()
                                            clean_n = re.sub(r'\s+', ' ', clean_n)
                                            if clean_n and len(clean_n) < 60 and not any(k in clean_n.lower() for k in ['linkedin', 'takip', 'follow', 'view', 'profile', 'bağlantı']):
                                                p_name = clean_n

                                        # Extract headline / job title
                                        p_role = None
                                        p_tags = card.find_all('p')
                                        if len(p_tags) > 1:
                                            cand_role = p_tags[1].get_text(strip=True)
                                            if cand_role and not any(k in cand_role.lower() for k in ['ortak', 'mutual', 'bağlantı', 'connection', 'shared']):
                                                p_role = cand_role

                                        if add_cand(u, name=p_name, role=p_role):
                                            page_new += 1
                                            if progress and task is not None:
                                                progress.update(task, advance=1, description=f"Filtre: {kw_label} (Toplam: {len(discovered_employees)})")
                                            if len(discovered_employees) >= max_results:
                                                break
                                else:
                                    # Fallback: find candidate links strictly excluding mutual connections
                                    candidate_links = soup.find_all('a', href=re.compile(r'/in/'))
                                    for cand_a in candidate_links:
                                        href = cand_a.get('href', '')
                                        # Skip links inside mutual connection blocks
                                        parent_txt = cand_a.parent.get_text(' ', strip=True).lower() if cand_a.parent else ''
                                        grand_txt = cand_a.parent.parent.get_text(' ', strip=True).lower() if (cand_a.parent and cand_a.parent.parent) else ''
                                        if any(ign in parent_txt or ign in grand_txt for ign in ['ortak', 'mutual', 'bağlantı', 'connection', 'shared']):
                                            continue
                                        m = re.search(r'/in/([^/?#\s]+)', href)
                                        if not m:
                                            continue
                                        u = unquote(m.group(1))
                                        name_text = cand_a.get_text(strip=True)
                                        p_name = name_text if (name_text and len(name_text) < 60 and not any(k in name_text.lower() for k in ['linkedin', 'takip', 'follow', 'view', 'profile', 'bağlantı'])) else None
                                        if add_cand(u, name=p_name):
                                            page_new += 1
                                            if progress and task is not None:
                                                progress.update(task, advance=1, description=f"Filtre: {kw_label} (Toplam: {len(discovered_employees)})")
                                            if len(discovered_employees) >= max_results:
                                                break
                            else:
                                for u in re.findall(r'/in/([a-zA-Z0-9_\-%]+)', resp.text):
                                    if add_cand(u):
                                        page_new += 1
                                        if progress and task is not None:
                                            progress.update(task, advance=1, description=f"Filtre: {kw_label} (Toplam: {len(discovered_employees)})")
                                        if len(discovered_employees) >= max_results:
                                            break

                            if page_new == 0:
                                consecutive_empty += 1
                                if consecutive_empty >= 2:
                                    break
                            else:
                                consecutive_empty = 0

                            time.sleep(0.8)
                    except Exception as e:
                        if not quiet:
                            Logger.warning(f"Arama sayfası ({kw_label} s.{page}) alınamadı: {e}")
                        break

            # If still needed, also check company people tab
            if len(discovered_employees) < max_results:
                try:
                    people_url = f"https://www.linkedin.com/company/{quote(self.slug)}/people/"
                    p_resp = self.session.get(people_url, headers=headers, timeout=self.timeout)
                    if p_resp.status_code == 200:
                        for u in re.findall(r'/in/([a-zA-Z0-9_\-%]+)', p_resp.text):
                            if add_cand(u):
                                if progress and task is not None:
                                    progress.update(task, advance=1)
                                if len(discovered_employees) >= max_results:
                                    break
                except Exception:
                    pass

        finally:
            if progress:
                progress.stop()

        return discovered_employees

    def _parse_company_page(self, html: str) -> LinkedInCompany:
        company = LinkedInCompany(
            name=self.slug.replace('-', ' ').title(),
            slug=self.slug,
            public_url=self.public_url
        )

        if not BS4_AVAILABLE:
            return company

        soup = BeautifulSoup(html, 'html.parser')

        # 1. Meta Tags
        og_title = soup.find('meta', property='og:title')
        if og_title and og_title.get('content'):
            raw_t = og_title['content'].replace(' | LinkedIn', '').strip()
            company.name = raw_t

        og_desc = soup.find('meta', property='og:description') or soup.find('meta', attrs={'name': 'description'})
        if og_desc and og_desc.get('content'):
            desc = clean_text(og_desc['content'])
            company.description = desc
            
            f_match = re.search(r'([\d,]+)\s+followers', desc, re.IGNORECASE)
            if f_match:
                try:
                    company.followers_count = int(f_match.group(1).replace(',', ''))
                except ValueError:
                    pass

        og_img = soup.find('meta', property='og:image')
        if og_img and og_img.get('content'):
            company.logo_url = og_img['content']

        # 2. JSON-LD Graph for Organization details & Posts
        for s in soup.find_all('script', type='application/ld+json'):
            try:
                data = json.loads(s.string or '{}')
                items = data if isinstance(data, list) else data.get('@graph', [data])
                for item in items:
                    item_type = item.get('@type', '')
                    if item_type in ['Organization', 'Corporation', 'LocalBusiness', 'NGO']:
                        if not company.name or company.name.lower() == company.slug:
                            company.name = clean_text(item.get('name', '')) or company.name
                        if item.get('description') and not company.description:
                            company.description = clean_text(item.get('description'))
                        if item.get('url') or item.get('sameAs'):
                            c_url = item.get('url') or item.get('sameAs')
                            if isinstance(c_url, list):
                                c_url = c_url[0]
                            company.website = str(c_url).strip()
                        if item.get('numberOfEmployees'):
                            emp_data = item.get('numberOfEmployees')
                            if isinstance(emp_data, dict):
                                company.company_size = f"{emp_data.get('minValue', '')}-{emp_data.get('maxValue', '')} employees".strip('- ')
                            else:
                                company.company_size = f"{emp_data} employees"
                        if 'address' in item:
                            addr = item['address']
                            if isinstance(addr, dict):
                                locs = [addr.get('addressLocality'), addr.get('addressRegion'), addr.get('addressCountry')]
                                company.location = ", ".join([l for l in locs if l])
                        if item.get('knowsAbout'):
                            ka = item.get('knowsAbout')
                            company.specialties = ka if isinstance(ka, list) else [str(ka)]
                        if item.get('industry') and not company.industry:
                            company.industry = clean_text(item.get('industry'))
                    elif item_type in ['DiscussionForumPosting', 'Article']:
                        p_txt = clean_text(item.get('text', '') or item.get('headline', ''))
                        if p_txt:
                            company.recent_updates.append(PostActivity(
                                text=p_txt,
                                date_published=item.get('datePublished'),
                                url=item.get('mainEntityOfPage') or item.get('url')
                            ))
            except Exception:
                pass

        # 3. DOM Fallbacks for Company Details
        if not company.website:
            web_tag = soup.find('a', attrs={'data-control-name': 'topcard_website'}) or soup.find('a', href=re.compile(r'^https?://(?!www\.linkedin)'))
            if web_tag and web_tag.get('href'):
                cand_w = web_tag['href'].strip()
                if not any(ign in cand_w for ign in ['licdn.com', 'linkedin.com', 'google.com', 'help.linkedin']):
                    company.website = cand_w

        if not company.company_size:
            size_m = re.search(r'([\d,]+(?:\s*-\s*[\d,]+)?\s+(?:employees|çalışan))', html, re.IGNORECASE)
            if size_m:
                company.company_size = size_m.group(1).strip()

        if not company.industry and company.description:
            # First segment of og:description often contains industry
            # e.g. "Computer Software · Redmond, Washington · 200,000+ followers"
            parts = company.description.split('·')
            if len(parts) >= 2:
                ind_cand = parts[0].strip()
                if len(ind_cand) < 40 and not any(k in ind_cand.lower() for k in ['welcome', 'about', 'linkedin']):
                    company.industry = ind_cand
                if not company.location and len(parts) >= 3:
                    loc_cand = parts[1].strip()
                    if len(loc_cand) < 50:
                        company.location = loc_cand

        return company


# ==============================================================================
# Output Presenters & Exporters (Zero-Emoji Clean Badges)
# ==============================================================================

class ProfilePresenter:
    """Formats and exports LinkedIn profile and company data."""

    @staticmethod
    def render_rich_card(profile: LinkedInProfile):
        if not RICH_AVAILABLE or not console:
            ProfilePresenter.render_plain_card(profile)
            return

        grid = Table.grid(expand=True, padding=(0, 2))
        grid.add_column(style="bold white", justify="left")

        title_text = Text()
        title_text.append(profile.full_name or profile.username, style="bold cyan")
        if profile.headline:
            title_text.append(f"\n{profile.headline}", style="italic bright_white")
        
        meta_parts = []
        if profile.experiences and profile.experiences[0].company:
            meta_parts.append(f"[Company: {profile.experiences[0].company}]")
        if profile.location:
            meta_parts.append(f"[Location: {profile.location}]")
        if profile.industry:
            meta_parts.append(f"[Industry: {profile.industry}]")
        if profile.follower_count:
            meta_parts.append(f"[{profile.follower_count:,} Followers]")
        elif profile.connections_count:
            meta_parts.append(f"[{profile.connections_count}+ Connections]")
            
        if meta_parts:
            title_text.append(f"\n{'  |  '.join(meta_parts)}", style="dim")

        if profile.profile_picture_url:
            title_text.append(f"\n[PP: {profile.profile_picture_url}]", style="green")
        else:
            title_text.append("\n[PP: Gizli / Halka Acik Paylasilmamis]", style="dim yellow")

        grid.add_row(title_text)

        panel = Panel(
            grid,
            title="[bold yellow]KULTIGIN // LINKEDIN PROFILE DOSSIER[/bold yellow]",
            subtitle=f"[dim cyan]{profile.public_url}[/dim cyan]",
            border_style="bright_blue",
            padding=(1, 2)
        )
        console.print(panel)

        if profile.summary:
            console.print(Panel(profile.summary, title="[bold green]About / Summary[/bold green]", border_style="green"))

        if profile.skills:
            skill_badges = [f"[bold green]{s}[/bold green]" for s in profile.skills]
            skills_text = "  •  ".join(skill_badges)
            console.print(Panel(skills_text, title=f"[bold yellow]Skills & Competencies ({len(profile.skills)})[/bold yellow]", border_style="yellow"))

        if profile.contact_info:
            contact_parts = []
            if 'github' in profile.contact_info:
                contact_parts.append(f"[bold cyan]GitHub Profile:[/bold cyan] {profile.contact_info['github']}")
            if 'twitter' in profile.contact_info:
                contact_parts.append(f"[bold cyan]X / Twitter:[/bold cyan] {profile.contact_info['twitter']}")
            if 'emails' in profile.contact_info:
                contact_parts.append(f"[bold cyan]Public Email(s):[/bold cyan] {', '.join(profile.contact_info['emails'])}")
            if 'external_profiles' in profile.contact_info:
                ext_str = "  |  ".join(profile.contact_info['external_profiles'][:4])
                contact_parts.append(f"[bold cyan]External Dossiers:[/bold cyan] {ext_str}")
            if contact_parts:
                console.print(Panel("\n".join(contact_parts), title="[bold cyan]Contact & External Profiles (OSINT Links)[/bold cyan]", border_style="cyan"))

        if profile.experiences:
            exp_table = Table(title="Work Experience", title_style="bold magenta", border_style="magenta", expand=True)
            exp_table.add_column("Role / Title", style="bold white", width=26)
            exp_table.add_column("Company", style="cyan", width=22)
            exp_table.add_column("Dates / Duration", style="yellow", width=18)
            exp_table.add_column("Location & Details", style="dim")

            for exp in profile.experiences:
                loc_desc = exp.location or ""
                if exp.description:
                    loc_desc = f"{loc_desc}\n{exp.description[:120]}..." if loc_desc else exp.description[:140]
                exp_table.add_row(
                    exp.title or "Position",
                    exp.company or "N/A",
                    exp.date_range or exp.duration or "-",
                    loc_desc or "-"
                )
            console.print(exp_table)

        if profile.educations:
            edu_table = Table(title="Education", title_style="bold blue", border_style="blue", expand=True)
            edu_table.add_column("School / University", style="bold white", width=28)
            edu_table.add_column("Degree & Field", style="cyan", width=28)
            edu_table.add_column("Year / Dates", style="yellow")

            for edu in profile.educations:
                degree_field = f"{edu.degree or ''} {edu.field_of_study or ''}".strip()
                edu_table.add_row(
                    edu.school or "N/A",
                    degree_field or "-",
                    edu.date_range or "-"
                )
            console.print(edu_table)

        if profile.certifications:
            cert_table = Table(title="Licenses & Certifications", title_style="bold green", border_style="green", expand=True)
            cert_table.add_column("Certification Name", style="bold white")
            cert_table.add_column("Issuing Authority", style="cyan")
            cert_table.add_column("Issue Date", style="yellow")

            for cert in profile.certifications:
                cert_table.add_row(
                    cert.name or "N/A",
                    cert.authority or "-",
                    cert.date or "-"
                )
            console.print(cert_table)

        if profile.posts:
            post_table = Table(title="Recent Posts & Published Activities", title_style="bold cyan", border_style="cyan", expand=True)
            post_table.add_column("Date", style="yellow", width=14)
            post_table.add_column("Activity / Post Snippet", style="white")
            post_table.add_column("Likes", style="green", width=8)

            for post in profile.posts[:5]:
                dt = post.date_published.split('T')[0] if post.date_published else "-"
                txt = post.text[:140] + "..." if len(post.text) > 140 else post.text
                likes = str(post.likes_count) if post.likes_count is not None else "-"
                post_table.add_row(dt, txt, likes)
            console.print(post_table)

        if profile.languages:
            lang_str = "  |  ".join(profile.languages)
            console.print(f"[bold yellow]Languages:[/bold yellow] [white]{lang_str}[/white]")

    @staticmethod
    def render_company_card(company: LinkedInCompany):
        if not RICH_AVAILABLE or not console:
            print(f"Company: {company.name} | Followers: {company.followers_count} | Website: {company.website}")
            for emp in company.employees:
                print(f" - {emp.username} ({emp.public_url})")
            return

        grid = Table.grid(expand=True, padding=(0, 2))
        grid.add_column(style="bold white", justify="left")

        title_text = Text()
        title_text.append(company.name, style="bold cyan")
        
        meta_parts = []
        if company.industry:
            meta_parts.append(f"[Industry: {company.industry}]")
        if company.location:
            meta_parts.append(f"[HQ: {company.location}]")
        if company.company_size:
            meta_parts.append(f"[Size: {company.company_size}]")
        if company.followers_count:
            meta_parts.append(f"[{company.followers_count:,} Followers]")
        if company.website:
            meta_parts.append(f"[Website: {company.website}]")

        if meta_parts:
            title_text.append(f"\n{'  |  '.join(meta_parts)}", style="yellow")

        grid.add_row(title_text)

        panel = Panel(
            grid,
            title="[bold yellow]KULTIGIN // COMPANY INTELLIGENCE[/bold yellow]",
            subtitle=f"[dim cyan]{company.public_url}[/dim cyan]",
            border_style="bright_magenta",
            padding=(1, 2)
        )
        console.print(panel)

        if company.description:
            console.print(Panel(company.description, title="[bold green]About Company[/bold green]", border_style="green"))

        if company.specialties:
            sp_badges = [f"[bold cyan]{s}[/bold cyan]" for s in company.specialties]
            console.print(Panel("  •  ".join(sp_badges), title="[bold cyan]Specialties & Focus Areas[/bold cyan]", border_style="cyan"))

        if company.employees:
            emp_table = Table(title=f"Discovered Affiliated Staff & Employees ({len(company.employees)})", title_style="bold cyan", border_style="cyan", expand=True)
            emp_table.add_column("Username / Nick", style="bold yellow", width=22)
            emp_table.add_column("Full Name", style="bold white", width=24)
            emp_table.add_column("Role / Headline", style="cyan")
            emp_table.add_column("Profile URL", style="dim", width=34)

            for emp in company.employees:
                emp_table.add_row(
                    emp.username,
                    emp.full_name or "-",
                    emp.role_or_title or "-",
                    emp.public_url
                )
            console.print(emp_table)

            # Line-by-line usernames box
            nicks_text = "\n".join(company.usernames)
            console.print(Panel(nicks_text, title="[bold green]Discovered Employee Usernames (Line-by-Line)[/bold green]", border_style="green"))

    @staticmethod
    def render_plain_card(profile: LinkedInProfile):
        print("\n" + "=" * 60)
        print(f"Profile: {profile.full_name or profile.username}")
        if profile.headline:
            print(f"Role: {profile.headline}")
        if profile.experiences and profile.experiences[0].company:
            print(f"Company: {profile.experiences[0].company}")
        if profile.location:
            print(f"Location: {profile.location}")
        if profile.follower_count:
            print(f"Followers: {profile.follower_count:,}")
        if profile.profile_picture_url:
            print(f"Profile Picture: {profile.profile_picture_url}")
        else:
            print("Profile Picture: Gizli / Halka Acik Paylasilmamis")
        print(f"URL: {profile.public_url}")
        print("=" * 60)

    @staticmethod
    def export_json(data_obj: Union[LinkedInProfile, LinkedInCompany], filepath: str):
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data_obj.to_dict(), f, indent=2, ensure_ascii=False)
        Logger.success(f"Saved JSON export to: [bold white]{filepath}[/bold white]")

    @staticmethod
    def export_usernames_file(company: LinkedInCompany, filepath: str):
        with open(filepath, 'w', encoding='utf-8') as f:
            for u in company.usernames:
                f.write(f"{u}\n")
        Logger.success(f"Saved {len(company.usernames)} employee usernames line-by-line to: [bold white]{filepath}[/bold white]")

    @staticmethod
    def export_markdown(profile: LinkedInProfile, filepath: str):
        lines = [
            f"# {profile.full_name or profile.username}",
            f"> **{profile.headline}**",
            "",
            f"- **LinkedIn URL:** [{profile.public_url}]({profile.public_url})",
            f"- **Location:** {profile.location or 'N/A'}",
            f"- **Industry:** {profile.industry or 'N/A'}",
        ]
        if profile.experiences and profile.experiences[0].company:
            lines.append(f"- **Current Company:** {profile.experiences[0].company}")
        if profile.follower_count:
            lines.append(f"- **Followers:** {profile.follower_count:,}")
        if profile.connections_count:
            lines.append(f"- **Connections:** {profile.connections_count}+")
        lines.append("")

        if profile.profile_picture_url:
            lines.append(f"![Profile Photo]({profile.profile_picture_url})\n")

        if profile.summary:
            lines.extend(["## About", profile.summary, ""])

        if profile.experiences:
            lines.append("## Work Experience")
            for exp in profile.experiences:
                lines.append(f"### {exp.title} - **{exp.company}**")
                if exp.date_range or exp.location:
                    lines.append(f"*{exp.date_range or ''} | {exp.location or ''}*")
                if exp.description:
                    lines.append(f"{exp.description}\n")
            lines.append("")

        if profile.educations:
            lines.append("## Education")
            for edu in profile.educations:
                lines.append(f"### {edu.school}")
                if edu.degree or edu.field_of_study:
                    lines.append(f"**Degree:** {edu.degree or ''} {edu.field_of_study or ''}")
                if edu.date_range:
                    lines.append(f"**Dates:** {edu.date_range}")
                if edu.description:
                    lines.append(f"{edu.description}\n")
            lines.append("")

        if profile.certifications:
            lines.append("## Licenses & Certifications")
            for cert in profile.certifications:
                auth_str = f" - {cert.authority}" if cert.authority else ""
                lines.append(f"- **{cert.name}**{auth_str} ({cert.date or 'No date'})")
            lines.append("")

        if profile.languages:
            lines.append(f"## Languages\n{', '.join(profile.languages)}\n")

        if profile.posts:
            lines.append("## Recent Posts")
            for post in profile.posts:
                dt = f" ({post.date_published.split('T')[0]})" if post.date_published else ""
                likes = f" [Likes: {post.likes_count}]" if post.likes_count is not None else ""
                lines.append(f"- {post.text}{dt}{likes}")
                if post.url:
                    lines.append(f"  Link: [{post.url}]({post.url})")
            lines.append("")

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write("\n".join(lines))
        Logger.success(f"Saved Markdown dossier to: [bold white]{filepath}[/bold white]")

    @staticmethod
    def export_company_markdown(company: LinkedInCompany, filepath: str):
        lines = [
            f"# {company.name}",
            f"> **{company.tagline or 'LinkedIn Organization Dossier'}**",
            "",
            f"- **Public URL:** [{company.public_url}]({company.public_url})",
            f"- **Followers:** {company.followers_count:,}" if company.followers_count else "- **Followers:** N/A",
            f"- **Discovered Employees:** {len(company.employees)}",
        ]
        if company.logo_url:
            lines.append(f"- **Logo:** ![Company Logo]({company.logo_url})")
        lines.append("")

        if company.description:
            lines.extend(["## About Company", company.description, ""])

        if company.employees:
            lines.extend([
                "## Discovered Staff & Employees",
                "",
                "| Username | Full Name | Role / Headline | Profile URL |",
                "|---|---|---|---|"
            ])
            for e in company.employees:
                n = e.full_name or "-"
                r = e.role_or_title or "-"
                lines.append(f"| `{e.username}` | {n} | {r} | [{e.username}]({e.public_url}) |")
            lines.append("")

        if company.recent_updates:
            lines.extend(["## Recent Company Posts & Updates", ""])
            for post in company.recent_updates:
                dt = f" ({post.date_published.split('T')[0]})" if post.date_published else ""
                likes = f" [Likes: {post.likes_count}]" if post.likes_count is not None else ""
                lines.append(f"- {post.text}{dt}{likes}")
                if post.url:
                    lines.append(f"  Link: [{post.url}]({post.url})")
            lines.append("")

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write("\n".join(lines))
        Logger.success(f"Saved Company Markdown dossier to: [bold white]{filepath}[/bold white]")


# ==============================================================================
# Structured Storage & Workspace Manager (Automated Folder Hierarchy)
# ==============================================================================

class KULTIGINStorage:
    """Manages clean, structured output directories for single profiles, batch jobs, and companies."""

    @staticmethod
    def get_base_dir(custom_dir: Optional[str] = None) -> str:
        base = custom_dir if (custom_dir and custom_dir != '.') else "outputs"
        os.makedirs(base, exist_ok=True)
        return base

    @staticmethod
    def get_profile_dir(username: str, base_dir: Optional[str] = None) -> str:
        base = KULTIGINStorage.get_base_dir(base_dir)
        p_dir = os.path.join(base, "profiles", username)
        os.makedirs(p_dir, exist_ok=True)
        return p_dir

    @staticmethod
    def get_batch_dir(batch_name: Optional[str] = None, base_dir: Optional[str] = None) -> str:
        base = KULTIGINStorage.get_base_dir(base_dir)
        name = batch_name or f"batch_{time.strftime('%Y%m%d_%H%M%S')}"
        b_dir = os.path.join(base, "batches", name)
        os.makedirs(b_dir, exist_ok=True)
        return b_dir

    @staticmethod
    def get_company_dir(company_slug: str, base_dir: Optional[str] = None) -> str:
        base = KULTIGINStorage.get_base_dir(base_dir)
        c_dir = os.path.join(base, "companies", company_slug)
        os.makedirs(c_dir, exist_ok=True)
        return c_dir

    @staticmethod
    def save_single_profile(profile: LinkedInProfile, base_dir: Optional[str] = None, custom_json: Optional[str] = None, custom_md: Optional[str] = None) -> str:
        target_dir = KULTIGINStorage.get_profile_dir(profile.username or "unknown_profile", base_dir)

        json_path = custom_json or os.path.join(target_dir, f"{profile.username or 'profile'}.json")
        md_path = custom_md or os.path.join(target_dir, f"{profile.username or 'profile'}.md")

        ProfilePresenter.export_json(profile, json_path)
        ProfilePresenter.export_markdown(profile, md_path)

        # Also write clean info summary
        summary_path = os.path.join(target_dir, f"{profile.username or 'profile'}_info.txt")
        with open(summary_path, 'w', encoding='utf-8') as f:
            f.write(f"Username: {profile.username}\n")
            f.write(f"Full Name: {profile.full_name}\n")
            f.write(f"Headline: {profile.headline}\n")
            f.write(f"Location: {profile.location}\n")
            f.write(f"URL: {profile.public_url}\n")
            if profile.profile_picture_url:
                f.write(f"Profile Picture: {profile.profile_picture_url}\n")

        Logger.success(f"Structured profile dossiers saved to: [bold cyan]{target_dir}[/bold cyan]")
        return target_dir

    @staticmethod
    def save_company(company: LinkedInCompany, base_dir: Optional[str] = None, custom_json: Optional[str] = None, custom_txt: Optional[str] = None) -> str:
        target_dir = KULTIGINStorage.get_company_dir(company.slug or "unknown_company", base_dir)

        json_path = custom_json or os.path.join(target_dir, f"{company.slug}.json")
        md_path = os.path.join(target_dir, f"{company.slug}.md")
        txt_path = custom_txt or os.path.join(target_dir, f"{company.slug}_usernames.txt")

        ProfilePresenter.export_json(company, json_path)
        ProfilePresenter.export_company_markdown(company, md_path)
        ProfilePresenter.export_usernames_file(company, txt_path)

        # Deep scraped employees subfolder
        deep_emps = [e for e in company.employees if e.profile_data]
        if deep_emps:
            emp_sub_dir = os.path.join(target_dir, "employees")
            os.makedirs(emp_sub_dir, exist_ok=True)
            for emp in deep_emps:
                if emp.profile_data:
                    e_json = os.path.join(emp_sub_dir, f"{emp.username}.json")
                    e_md = os.path.join(emp_sub_dir, f"{emp.username}.md")
                    ProfilePresenter.export_json(emp.profile_data, e_json)
                    ProfilePresenter.export_markdown(emp.profile_data, e_md)
            Logger.success(f"Saved {len(deep_emps)} deep employee dossiers to: [bold cyan]{emp_sub_dir}[/bold cyan]")

        Logger.success(f"Structured company dataset saved to: [bold cyan]{target_dir}[/bold cyan]")
        return target_dir

    @staticmethod
    def save_batch(profiles: List[LinkedInProfile], batch_name: Optional[str] = None, base_dir: Optional[str] = None) -> str:
        target_dir = KULTIGINStorage.get_batch_dir(batch_name, base_dir)

        summary = {
            'total_targets': len(profiles),
            'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            'profiles': []
        }

        usernames_list = []
        for p in profiles:
            p_json = os.path.join(target_dir, f"{p.username}.json")
            p_md = os.path.join(target_dir, f"{p.username}.md")
            ProfilePresenter.export_json(p, p_json)
            ProfilePresenter.export_markdown(p, p_md)
            usernames_list.append(p.username)
            summary['profiles'].append({
                'username': p.username,
                'full_name': p.full_name,
                'headline': p.headline,
                'location': p.location,
                'followers': p.follower_count,
                'url': p.public_url
            })

        summary_path = os.path.join(target_dir, "batch_summary.json")
        with open(summary_path, 'w', encoding='utf-8') as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)

        txt_path = os.path.join(target_dir, "batch_usernames.txt")
        with open(txt_path, 'w', encoding='utf-8') as f:
            for u in usernames_list:
                f.write(f"{u}\n")

        Logger.success(f"Saved complete batch dataset ({len(profiles)} profiles) to: [bold cyan]{target_dir}[/bold cyan]")
        return target_dir


# ==============================================================================
# Interactive Terminal Menu (Zero-Emoji Clean UI)
# ==============================================================================

def run_interactive_menu():
    while True:
        cfg = load_config() if GITHUB_AVAILABLE else {}
        if RICH_AVAILABLE and console:
            console.clear()
            banner = """[bold cyan]
 ____  __.____ ___.____  ___________.___  ________.___ _______   
|    |/ _|    |   \\    | \\__    ___/|   |/  _____/|   |\\      \\  
|      < |    |   /    |   |    |   |   /   \\  ___|   |/   |   \\ 
|    |  \\|    |  /|    |___|    |   |   \\    \\_\\  \\   /    |    \\
|____|__ \\______/ |_______ \\____|   |___|\\______  /___\\____|__  /
        \\/                \\/                    \\/            \\/ [/bold cyan]
  [bold yellow]KULTIGIN v4.1[/bold yellow] - [dim]LinkedIn & GitHub Intelligence Framework[/dim]
"""
            console.print(banner)
            menu_table = Table(show_header=False, box=None, padding=(0, 2))
            menu_table.add_column(style="bold yellow", width=6)
            menu_table.add_column(style="bold white")
            menu_table.add_row("[1]", "Tekil Kisi Profili Cek (Single Profile Intelligence)")
            menu_table.add_row("[2]", "Toplu Liste Profili Cek (Batch Extraction from File/List)")
            menu_table.add_row("[3]", "Sirket Bilgileri & Calisanlari Cikar (Company & Employee Discovery)")
            menu_table.add_row("[4]", "GitHub OSINT & Kod/Repo Kesfi (GitHub Discoverer)")
            menu_table.add_row("[5]", "Ayarlar & Config Yonetimi (Tokens, Threads)")
            menu_table.add_row("[6]", "Cikis (Exit)")
            console.print(Panel(menu_table, title="[bold green]Ana Menu[/bold green]", border_style="cyan"))

            choice = Prompt.ask("\n[bold cyan]Seciminiz[/bold cyan]", choices=["1", "2", "3", "4", "5", "6"], default="1")
        else:
            print("\n=== KULTIGIN MENU ===")
            print("1 - Tekil Profil Cek")
            print("2 - Toplu Profil Cek")
            print("3 - Sirket ve Calisanlari Cikar")
            print("4 - GitHub OSINT & Kod/Repo Kesfi")
            print("5 - Ayarlar & Config Yonetimi")
            print("6 - Cikis")
            choice = input("Secim: ").strip()

        if choice == "1":
            target = Prompt.ask("Kullanici adi veya LinkedIn URL'si girin").strip() if RICH_AVAILABLE else input("Hedef: ").strip()
            if target:
                fetcher = LinkedInFetcher(target)
                p = fetcher.fetch()
                ProfilePresenter.render_rich_card(p)
                KULTIGINStorage.save_single_profile(p)

        elif choice == "2":
            list_input = Prompt.ask("Kullanici listesi (virgulle ayirarak veya dosya yolu)").strip() if RICH_AVAILABLE else input("Liste: ").strip()
            targets = []
            if os.path.isfile(list_input):
                with open(list_input, 'r', encoding='utf-8') as f:
                    targets = [line.strip() for line in f if line.strip()]
            else:
                targets = [t.strip() for t in list_input.split(',') if t.strip()]

            if targets:
                Logger.info(f"Toplu isleme baslaniyor ({len(targets)} hedef)...")
                batch_profiles = []
                for t in targets:
                    try:
                        Logger.info(f"Isleniyor: {t}")
                        f_inst = LinkedInFetcher(t)
                        p_inst = f_inst.fetch()
                        batch_profiles.append(p_inst)
                        time.sleep(1.0)
                    except Exception as e:
                        Logger.error(f"Hata ({t}): {e}")
                if batch_profiles:
                    KULTIGINStorage.save_batch(batch_profiles)

        elif choice == "3":
            comp_input = Prompt.ask("Sirket adi veya LinkedIn Company URL'si girin").strip() if RICH_AVAILABLE else input("Sirket: ").strip()
            if comp_input:
                comp_cookies = {}
                Logger.info("Tarayicidan LinkedIn oturumu kontrol ediliyor...")
                b_cookies = get_browser_linkedin_cookies()
                if b_cookies:
                    comp_cookies.update(b_cookies)
                else:
                    default_li = cfg.get("linkedin_li_at", "") if cfg else ""
                    prompt_label = "Tarayici oturumu alinamadi. Manuel Cookie (li_at) [Enter: Zero-Cookie Modu]"
                    if default_li:
                        prompt_label = f"Tarayici oturumu alinamadi. Cookie (li_at) [Enter: config.json ({default_li[:6]}...)]"
                    cookie_in = Prompt.ask(prompt_label, default=default_li).strip() if RICH_AVAILABLE else input(f"Cookie [{default_li}]: ").strip()
                    if not cookie_in and default_li:
                        cookie_in = default_li
                    if cookie_in:
                        parsed_c, _ = parse_cookie_input(cookie_in)
                        comp_cookies.update(parsed_c)
                        if 'li_at' not in comp_cookies and re.match(r'^[a-zA-Z0-9_\-%=]+$', cookie_in):
                            comp_cookies['li_at'] = cookie_in

                limit_str = Prompt.ask("Maksimum cekilecek calisan sayisi", default="50").strip() if RICH_AVAILABLE else "50"
                try:
                    limit_val = int(limit_str)
                except ValueError:
                    limit_val = 50

                deep = Confirm.ask("Bulunan calisan profilleri tek tek derinlemesine taransin mi? (Not: Cerezsiz taranir)") if RICH_AVAILABLE else False
                c_fetcher = LinkedInCompanyFetcher(comp_input, cookies=comp_cookies)
                comp = c_fetcher.fetch(deep_scrape_employees=deep, max_employees=limit_val, interactive=True)
                ProfilePresenter.render_company_card(comp)
                KULTIGINStorage.save_company(comp)
                # CRITICAL: Clean up and clear cookie so subsequent operations or profile queries never use it
                comp_cookies.clear()

        elif choice == "4":
            if GITHUB_AVAILABLE:
                run_github_interactive_menu()
            else:
                Logger.error("github_discoverer modulu yuklenemedi.")

        elif choice == "5":
            if RICH_AVAILABLE and console:
                console.print(Panel(json.dumps(cfg, indent=2), title="[bold cyan]Mevcut Ayarlar (config.json)[/bold cyan]", border_style="cyan"))
                up_choice = Confirm.ask("Ayarlari guncellemek ister misiniz?", default=False)
                if up_choice:
                    gh_tok = Prompt.ask("GitHub PAT Token", default=cfg.get("github_token", "")).strip()
                    li_at = Prompt.ask("LinkedIn li_at Cookie", default=cfg.get("linkedin_li_at", "")).strip()
                    threads = Prompt.ask("Varsayilan Thread Sayisi", default=str(cfg.get("default_threads", 10))).strip()
                    cfg["github_token"] = gh_tok
                    cfg["linkedin_li_at"] = li_at
                    cfg["default_threads"] = int(threads) if threads.isdigit() else 10
                    save_config(cfg)
                    console.print("[bold green][+][/bold green] Ayarlar 'config.json' dosyasina basariyla kaydedildi!")
            else:
                print(f"Mevcut config: {cfg}")

        elif choice == "6":
            Logger.info("KULTIGIN kapatiliyor. Iyi calismalar!")
            break

        if RICH_AVAILABLE:
            input("\nDevam etmek icin Enter'a basin...")


# ==============================================================================
# CLI Entry Point
# ==============================================================================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="KULTIGIN - LinkedIn OSINT & Profile Intelligence Framework",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Interactive Menu:
  python KULTIGIN.py

  # 1. Single Profile Extraction (Auto-saved to outputs/profiles/<username>/):
  python KULTIGIN.py -u hedef

  # 2. Company & Employee Discovery (Auto-saved to outputs/companies/<company>/):
  python KULTIGIN.py --company companyname --limit 30

  # 3. Output Only Employee Usernames/Nicks Line-by-Line:
  python KULTIGIN.py --company companyname --nicks-only

  # 4. Company Full Roster with Deep Scraping (Saved to employees/ subfolder):
  python KULTIGIN.py --company companyname --limit 50 --deep

  # 5. Batch Extraction (Auto-saved to outputs/batches/<batch_name>/):
  python KULTIGIN.py --list targets.txt --out-dir ./outputs
"""
    )

    parser.add_argument('-u', '--url', '--username', dest='url', help='LinkedIn vanity username or full profile URL (e.g. "hedef" or comma-separated list)')
    parser.add_argument('--company', dest='company', help='LinkedIn company slug or URL to extract company info and discover staff (e.g. "companyname")')
    parser.add_argument('--limit', '--max-employees', dest='max_employees', type=int, default=50, help='Maximum employees to discover/scrape in company mode (default: 50)')
    parser.add_argument('--deep', dest='deep', action='store_true', help='Deep scrape each discovered company employee profile')
    parser.add_argument('--nicks-only', '--usernames-only', dest='nicks_only', action='store_true', help='Print only employee usernames/nicks line-by-line to stdout')
    parser.add_argument('-l', '--list', dest='list_file', help='Path to a text file with LinkedIn profile usernames/URLs (one per line)')
    parser.add_argument('--out-dir', dest='out_dir', default='outputs', help='Root directory for structured dossier exports (default: outputs)')
    parser.add_argument('-c', '--cookie', dest='cookie', help='LinkedIn cookies (e.g. "li_at=AQED...; JSESSIONID=\\"ajax:...\\"") or cookie file path')
    parser.add_argument('--li-at', dest='li_at', help='Raw li_at session cookie')
    parser.add_argument('--jsessionid', dest='jsessionid', help='Raw JSESSIONID cookie')
    parser.add_argument('-o', '--output', dest='output', help='Custom output path for JSON export')
    parser.add_argument('--md', '--markdown', dest='md', help='Custom output path for Markdown dossier')
    parser.add_argument('--proxy', dest='proxy', help='HTTP/HTTPS proxy URL (e.g. "http://127.0.0.1:8080")')
    parser.add_argument('--timeout', dest='timeout', type=int, default=20, help='Request timeout in seconds (default: 20)')
    parser.add_argument('--user-agent', dest='user_agent', help='Custom User-Agent string')
    parser.add_argument('--json-only', dest='json_only', action='store_true', help='Output raw JSON directly to stdout without fancy styling')
    parser.add_argument('-i', '--interactive', '--menu', dest='menu', action='store_true', help='Launch interactive terminal menu')

    # GitHub OSINT Options
    parser.add_argument('--github-company', dest='gh_company', help='GitHub company or organization to extract accounts for (e.g. "microsoft")')
    parser.add_argument('--github-names', dest='gh_names', help='Predict and verify GitHub accounts from comma-separated names or name list file')
    parser.add_argument('--github-hunt-users', dest='gh_hunt_users', help='Hunt GitHub users matching keyword (e.g. "red team turkey")')
    parser.add_argument('--github-hunt-repos', dest='gh_hunt_repos', help='Hunt GitHub repos matching keyword or comma-separated keywords')
    parser.add_argument('--github-token', dest='gh_token', help='GitHub Personal Access Token (defaults to config.json or GITHUB_TOKEN env)')
    parser.add_argument('--github-limit', dest='gh_limit', type=int, default=50, help='Maximum GitHub results to fetch (default: 50)')

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    # If no arguments provided or interactive flag set, launch menu
    if len(sys.argv) == 1 or args.menu:
        run_interactive_menu()
        return

    # Dispatch GitHub OSINT modes
    if args.gh_company or args.gh_names or args.gh_hunt_users or args.gh_hunt_repos:
        if not GITHUB_AVAILABLE:
            Logger.error("GitHub Discoverer modulu yuklenemedi. Lutfen 'github_discoverer.py' dosyasini kontrol edin.")
            sys.exit(1)

        discoverer = GitHubDiscoverer(token=args.gh_token)

        if args.gh_company:
            Logger.header(f"KULTIGIN // GitHub Company Discovery: {args.gh_company}")
            users = discoverer.discover_company_accounts(args.gh_company, max_results=args.gh_limit)
            GitHubPresenter.render_company_results(args.gh_company, users)
            out_dir = GitHubPresenter.save_results("companies", args.gh_company, users)
            Logger.success(f"Sonuclar kaydedildi: {out_dir}")
            return

        if args.gh_names:
            names = []
            if os.path.isfile(args.gh_names):
                with open(args.gh_names, 'r', encoding='utf-8') as f:
                    names = [line.strip() for line in f if line.strip()]
            else:
                names = [n.strip() for n in args.gh_names.split(',') if n.strip()]

            Logger.header(f"KULTIGIN // GitHub Profile Prediction ({len(names)} targets)")
            users = discoverer.predict_and_verify_profiles(names)
            GitHubPresenter.render_prediction_results(users)
            out_dir = GitHubPresenter.save_results("predictions", "cli_batch", users)
            Logger.success(f"Sonuclar kaydedildi: {out_dir}")
            return

        if args.gh_hunt_users:
            Logger.header(f"KULTIGIN // GitHub User Hunt: {args.gh_hunt_users}")
            users = discoverer.hunt_profiles_by_keyword(args.gh_hunt_users, pages=max(1, args.gh_limit // 30))
            GitHubPresenter.render_keyword_results(args.gh_hunt_users, users)
            out_dir = GitHubPresenter.save_results("bio_hunters", args.gh_hunt_users, users)
            Logger.success(f"Sonuclar kaydedildi: {out_dir}")
            return

        if args.gh_hunt_repos:
            kws = [k.strip() for k in args.gh_hunt_repos.split(',') if k.strip()]
            Logger.header(f"KULTIGIN // GitHub Repo Hunt: {', '.join(kws)}")
            # If target list is not provided, use company or target if available or search repos directly
            repos = discoverer.hunt_repos_by_keywords([args.company] if args.company else ["github"], kws)
            GitHubPresenter.render_keyword_results(", ".join(kws), repos)
            out_dir = GitHubPresenter.save_results("repo_hunters", "-".join(kws)[:30], repos)
            Logger.success(f"Sonuclar kaydedildi: {out_dir}")
            return

    # Parse cookies
    cookies = {}
    if args.cookie:
        parsed_c, rescued = parse_cookie_input(args.cookie)
        cookies.update(parsed_c)
        if not args.output and 'output' in rescued:
            args.output = rescued['output']
        if not args.md and 'md' in rescued:
            args.md = rescued['md']

    if args.li_at:
        cookies['li_at'] = re.sub(r'[^a-zA-Z0-9_\-%=]', '', args.li_at.strip())
    if args.jsessionid:
        raw_j = args.jsessionid.strip().strip('"\'\\')
        j_match = re.search(r'ajax:[a-zA-Z0-9_\-]+', raw_j)
        cookies['JSESSIONID'] = j_match.group(0) if j_match else raw_j

    # 1. Company Discovery Mode
    if args.company:
        company_cookies = dict(cookies)
        if not company_cookies.get('li_at'):
            Logger.info("Tarayicidan LinkedIn oturum cookie'si aranıyor...")
            b_cookies = get_browser_linkedin_cookies()
            if b_cookies:
                company_cookies.update(b_cookies)

        if not args.json_only and not args.nicks_only:
            Logger.header("KULTIGIN // Company & Employee Intelligence Extractor")
            Logger.info(f"Target Company: [bold cyan]{args.company}[/bold cyan]")
            if company_cookies.get('li_at'):
                Logger.info(f"Session: [green]{', '.join(company_cookies.keys())}[/green] (Tarayici Oturumu ile Voyager Arama Aktif - Limit: {args.max_employees})")
            else:
                Logger.info("Mode: [bold cyan]Zero-Cookie Public Discovery[/bold cyan]")
        try:
            c_fetcher = LinkedInCompanyFetcher(args.company, cookies=company_cookies, proxy=args.proxy, timeout=args.timeout)
            comp = c_fetcher.fetch(deep_scrape_employees=args.deep, max_employees=args.max_employees, quiet=args.nicks_only, interactive=not (args.json_only or args.nicks_only))

            if args.nicks_only:
                for u in comp.usernames:
                    print(u)
                return

            if args.json_only:
                print(json.dumps(comp.to_dict(), indent=2, ensure_ascii=False))
            else:
                ProfilePresenter.render_company_card(comp)

            # Save in structured outputs/companies/<company_slug>/ folder
            KULTIGINStorage.save_company(comp, base_dir=args.out_dir, custom_json=args.output)
            return
        except Exception as e:
            Logger.error(f"Failed to fetch company: {e}")
            sys.exit(1)

    # 2. Batch Profile Extraction from File or Comma List
    targets = []
    if args.list_file:
        if os.path.isfile(args.list_file):
            with open(args.list_file, 'r', encoding='utf-8') as f:
                targets = [line.strip() for line in f if line.strip()]
        else:
            Logger.error(f"List file not found: {args.list_file}")
            sys.exit(1)
    elif args.url and ',' in args.url:
        targets = [t.strip() for t in args.url.split(',') if t.strip()]

    if targets:
        Logger.header(f"KULTIGIN // Batch Processing ({len(targets)} Targets)")
        batch_profiles = []
        for idx, t in enumerate(targets, 1):
            try:
                Logger.info(f"[{idx}/{len(targets)}] Scraping: [bold cyan]{t}[/bold cyan]")
                fetcher = LinkedInFetcher(t, cookies=cookies, proxy=args.proxy, timeout=args.timeout, user_agent=args.user_agent)
                p = fetcher.fetch()
                batch_profiles.append(p)
                time.sleep(1.0)
            except Exception as e:
                Logger.error(f"Failed to fetch {t}: {e}")

        if batch_profiles:
            KULTIGINStorage.save_batch(batch_profiles, base_dir=args.out_dir)
        Logger.success("Batch extraction complete!")
        return

    # 3. Single Profile Extraction
    if not args.url:
        Logger.error("No target specified. Use -u <username>, --company <company>, --list <file>, or run without args for Interactive Menu.")
        sys.exit(1)

    if not args.json_only:
        Logger.header("KULTIGIN // LinkedIn Profile Intelligence Extractor")
        Logger.info(f"Target: [bold cyan]{args.url}[/bold cyan]")
        if cookies:
            Logger.info(f"Session: [green]{', '.join(cookies.keys())}[/green] (Authenticated Mode)")
        else:
            Logger.info("Mode: [bold cyan]Zero-Cookie Public Intelligence Extractor[/bold cyan] (100% Free / No Account Needed)")

    try:
        fetcher = LinkedInFetcher(
            username_or_url=args.url,
            cookies=cookies,
            proxy=args.proxy,
            timeout=args.timeout,
            user_agent=args.user_agent
        )
        profile = fetcher.fetch()

        if args.json_only:
            print(json.dumps(profile.to_dict(), indent=2, ensure_ascii=False))
        else:
            ProfilePresenter.render_rich_card(profile)

        KULTIGINStorage.save_single_profile(profile, base_dir=args.out_dir, custom_json=args.output, custom_md=args.md)

    except PermissionError as e:
        Logger.error(f"Authentication / Challenge Error: {e}")
        sys.exit(1)
    except FileNotFoundError as e:
        Logger.error(f"Not Found: {e}")
        sys.exit(1)
    except Exception as e:
        Logger.error(f"Failed to fetch profile: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

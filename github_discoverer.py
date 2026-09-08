"""
KULTIGIN // GitHub OSINT & Code Intelligence Engine
Integrates organization discovery, name permutation username correlation,
active repo filtering, profile bio/metadata hunting, and repository code search.
Zero-emoji clean UI with Rich terminal formatting and automated dataset exports.
"""

import os
import sys
import json
import time
import re
import argparse
from typing import List, Dict, Any, Optional, Tuple, Set
from urllib.parse import quote_plus
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn
    from rich.prompt import Prompt, Confirm
    from rich import print as rprint
    RICH_AVAILABLE = True
    console = Console()
except ImportError:
    RICH_AVAILABLE = False
    console = None

# Transliteration table for Turkish characters to standard Latin ASCII
TR_TO_EN = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")


def load_config() -> Dict[str, Any]:
    """Loads configuration from config.json, creating a default one if missing."""
    default_cfg = {
        "github_token": "",
        "linkedin_li_at": "",
        "default_threads": 10,
        "timeout": 20
    }
    if not os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(default_cfg, f, indent=2)
        except Exception:
            pass
        return default_cfg

    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
            # Ensure all keys exist
            for k, v in default_cfg.items():
                if k not in cfg:
                    cfg[k] = v
            return cfg
    except Exception:
        return default_cfg


def save_config(cfg: Dict[str, Any]):
    """Persists updated configuration to config.json."""
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass


class GitHubDiscoverer:
    """Core GitHub OSINT and Intelligence engine."""

    def __init__(self, token: Optional[str] = None, timeout: int = 15, max_threads: int = 10):
        self.config = load_config()
        self.token = (token or self.config.get("github_token") or "").strip()
        self.timeout = timeout or self.config.get("timeout", 20)
        self.max_threads = max_threads or self.config.get("default_threads", 10)
        self.rate_limit_hit = False

    def get_headers(self) -> Dict[str, str]:
        headers = {
            'Accept': 'application/vnd.github.v3+json',
            'User-Agent': 'KULTIGIN-GitHub-OSINT/1.0'
        }
        if self.token:
            headers['Authorization'] = f'token {self.token}'
        return headers

    def check_rate_limit(self) -> Dict[str, Any]:
        """Checks current GitHub API rate limit status."""
        url = "https://api.github.com/rate_limit"
        try:
            r = requests.get(url, headers=self.get_headers(), timeout=self.timeout)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        return {}

    # --------------------------------------------------------------------------
    # 1. Company & Organization Intelligence
    # --------------------------------------------------------------------------

    def discover_company_accounts(self, company_name: str, max_results: int = 100) -> List[Dict[str, Any]]:
        """
        Discovers GitHub accounts associated with a company or organization.
        Queries both the Organization Members endpoint and GitHub User Search.
        """
        discovered: Dict[str, Dict[str, Any]] = {}
        headers = self.get_headers()

        clean_slug = re.sub(r'[^a-zA-Z0-9_\-]', '', company_name.lower())

        # 1. Try Organization Members API if company has an official GitHub Org
        org_url = f"https://api.github.com/orgs/{clean_slug}/members"
        page = 1
        while len(discovered) < max_results:
            try:
                r = requests.get(org_url, headers=headers, params={'per_page': 50, 'page': page}, timeout=self.timeout)
                if r.status_code == 200:
                    members = r.json()
                    if not members:
                        break
                    for m in members:
                        uname = m.get('login')
                        if uname and uname not in discovered:
                            discovered[uname] = {
                                'login': uname,
                                'html_url': m.get('html_url', f"https://github.com/{uname}"),
                                'avatar_url': m.get('avatar_url', ''),
                                'source': f"Organization Member ({clean_slug})",
                                'name': '',
                                'company': clean_slug,
                                'bio': '',
                                'location': '',
                                'public_repos': 0
                            }
                    page += 1
                elif r.status_code == 404:
                    # Not a registered GitHub org, continue to search
                    break
                elif r.status_code in [403, 429]:
                    self.rate_limit_hit = True
                    break
                else:
                    break
            except Exception:
                break

        # 2. Query GitHub User Search for users who list the company
        search_queries = [
            f'company:"{company_name}"',
            f'"{company_name}" type:user'
        ]

        for q in search_queries:
            if len(discovered) >= max_results or self.rate_limit_hit:
                break
            page = 1
            while len(discovered) < max_results:
                search_url = "https://api.github.com/search/users"
                params = {'q': q, 'per_page': 30, 'page': page}
                try:
                    r = requests.get(search_url, headers=headers, params=params, timeout=self.timeout)
                    if r.status_code == 200:
                        data = r.json()
                        items = data.get('items', [])
                        if not items:
                            break
                        for item in items:
                            uname = item.get('login')
                            if uname and uname not in discovered:
                                discovered[uname] = {
                                    'login': uname,
                                    'html_url': item.get('html_url', f"https://github.com/{uname}"),
                                    'avatar_url': item.get('avatar_url', ''),
                                    'source': f"Search: {q}",
                                    'name': '',
                                    'company': company_name,
                                    'bio': '',
                                    'location': '',
                                    'public_repos': 0
                                }
                                if len(discovered) >= max_results:
                                    break
                        page += 1
                        time.sleep(1.0)
                    elif r.status_code in [403, 429]:
                        self.rate_limit_hit = True
                        break
                    else:
                        break
                except Exception:
                    break

        # 3. Enrich top discovered profiles with full details (name, bio, repos)
        results = list(discovered.values())
        if self.token and results:
            enrich_pool = results[:min(len(results), max_results)]
            with ThreadPoolExecutor(max_workers=min(self.max_threads, 10)) as executor:
                futures = {executor.submit(self.get_user_profile, u['login']): u for u in enrich_pool}
                for fut in as_completed(futures):
                    u_dict = futures[fut]
                    prof = fut.result()
                    if prof:
                        u_dict.update({
                            'name': prof.get('name') or '',
                            'company': prof.get('company') or u_dict['company'],
                            'bio': (prof.get('bio') or '').replace('\r', '').replace('\n', ' '),
                            'location': prof.get('location') or '',
                            'public_repos': prof.get('public_repos', 0),
                            'followers': prof.get('followers', 0),
                            'blog': prof.get('blog') or '',
                            'email': prof.get('email') or ''
                        })

        return results

    def get_user_profile(self, username: str) -> Optional[Dict[str, Any]]:
        """Fetches detailed user profile data."""
        url = f"https://api.github.com/users/{username}"
        try:
            r = requests.get(url, headers=self.get_headers(), timeout=self.timeout)
            if r.status_code == 200:
                return r.json()
            elif r.status_code in [403, 429]:
                self.rate_limit_hit = True
        except Exception:
            pass
        return None

    # --------------------------------------------------------------------------
    # 2. Name Permutation & Username Correlation
    # --------------------------------------------------------------------------

    @staticmethod
    def clean_and_split_name(name: str) -> List[str]:
        """Cleans and tokenizes full name into Latin characters."""
        cleaned = name.translate(TR_TO_EN).lower().strip()
        # Remove common titles or separators
        cleaned = re.sub(r'[^a-z0-9\s]', ' ', cleaned)
        return [p for p in cleaned.split() if len(p) >= 2]

    @staticmethod
    def generate_username_combinations(name_parts: List[str]) -> List[str]:
        """
        Generates common GitHub username patterns from name tokens.
        Example for 'Ahmet Yilmaz':
          ahmetyilmaz, ahmet.yilmaz, ahmet-yilmaz, ahmet_yilmaz,
          ayilmaz, a.yilmaz, a_yilmaz, yilmazahmet, yilmaz.ahmet, etc.
        """
        combos = set()
        if not name_parts:
            return []

        if len(name_parts) == 1:
            combos.add(name_parts[0])
            combos.add(f"{name_parts[0]}1")
            combos.add(f"{name_parts[0]}dev")

        elif len(name_parts) == 2:
            f, l = name_parts[0], name_parts[1]
            combos.update([
                f"{f}{l}",
                f"{f}.{l}",
                f"{f}-{l}",
                f"{f}_{l}",
                f"{f[0]}{l}",
                f"{f[0]}.{l}",
                f"{f[0]}-{l}",
                f"{f[0]}_{l}",
                f"{f}{l[0]}",
                f"{f}.{l[0]}",
                f"{l}{f}",
                f"{l}.{f}",
                f"{l}-{f}",
                f"{l}_{f}",
                f"{l}{f[0]}",
            ])

        elif len(name_parts) >= 3:
            f, m, l = name_parts[0], name_parts[1], name_parts[-1]
            combos.update([
                f"{f}{m}{l}",
                f"{f}.{m}.{l}",
                f"{f}{l}",
                f"{f}.{l}",
                f"{f}-{l}",
                f"{f}_{l}",
                f"{f[0]}{m[0]}{l}",
                f"{f[0]}{l}",
                f"{m}{l}",
                f"{m}.{l}",
                f"{f[0]}.{l}",
                f"{l}{f}",
            ])

        return [c for c in combos if len(c) >= 3 and not c.startswith('.') and not c.endswith('.')]

    def predict_and_verify_profiles(self, names_or_lines: List[str], filter_active_repos_only: bool = True, progress_callback=None) -> List[Dict[str, Any]]:
        """
        Generates username combinations for each person and verifies active GitHub accounts concurrently.
        """
        self.rate_limit_hit = False
        target_map: Dict[str, str] = {}  # username -> original full name
        for line in names_or_lines:
            raw = line.strip()
            if not raw:
                continue
            # Handle LinkedIn username/url or clean name
            if "linkedin.com/in/" in raw:
                raw = raw.split("/in/")[1].strip("/?# \n")
            parts = self.clean_and_split_name(raw)
            if not parts:
                continue
            combos = self.generate_username_combinations(parts)
            for c in combos:
                if c not in target_map:
                    target_map[c] = " ".join([p.capitalize() for p in parts])

        candidates = list(target_map.keys())
        matches: List[Dict[str, Any]] = []

        def check_single(u: str) -> Optional[Dict[str, Any]]:
            if self.rate_limit_hit:
                return None
            url = f"https://api.github.com/users/{u}"
            try:
                r = requests.get(url, headers=self.get_headers(), timeout=self.timeout)
                if r.status_code == 200:
                    data = r.json()
                    pub_repos = data.get('public_repos', 0)
                    if filter_active_repos_only and pub_repos == 0:
                        return None
                    return {
                        'login': u,
                        'original_name': target_map.get(u, ''),
                        'github_name': data.get('name') or '',
                        'html_url': data.get('html_url', f"https://github.com/{u}"),
                        'bio': (data.get('bio') or '').replace('\r', '').replace('\n', ' '),
                        'company': data.get('company') or '',
                        'location': data.get('location') or '',
                        'public_repos': pub_repos,
                        'followers': data.get('followers', 0),
                        'blog': data.get('blog') or ''
                    }
                elif r.status_code in [403, 429]:
                    self.rate_limit_hit = True
            except Exception:
                pass
            return None

        with ThreadPoolExecutor(max_workers=self.max_threads) as executor:
            futures = {executor.submit(check_single, u): u for u in candidates}
            for fut in as_completed(futures):
                if self.rate_limit_hit:
                    break
                res = fut.result()
                if res:
                    matches.append(res)
                if progress_callback:
                    progress_callback(1)

        return matches

    # --------------------------------------------------------------------------
    # 3. Profile & Repository Keyword Hunter
    # --------------------------------------------------------------------------

    def hunt_profiles_by_keyword(self, keyword: str, pages: int = 3) -> List[Dict[str, Any]]:
        """
        Searches GitHub users and verifies keyword in profile fields (Bio, Company, Location, Name, Blog).
        """
        self.rate_limit_hit = False
        headers = self.get_headers()
        base_url = "https://api.github.com/search/users"
        found: List[Dict[str, Any]] = []
        kw_lower = keyword.lower()

        for page in range(1, pages + 1):
            if self.rate_limit_hit:
                break
            params = {'q': keyword, 'page': page, 'per_page': 30}
            try:
                r = requests.get(base_url, headers=headers, params=params, timeout=self.timeout)
                if r.status_code == 200:
                    items = r.json().get('items', [])
                    if not items:
                        break
                    for item in items:
                        u = item.get('login')
                        prof = self.get_user_profile(u)
                        if prof:
                            matched_fields = []
                            for f in ['login', 'name', 'company', 'blog', 'location', 'email', 'bio', 'twitter_username']:
                                val = prof.get(f)
                                if val and isinstance(val, str) and kw_lower in val.lower():
                                    matched_fields.append(f"{f.capitalize()}: {val.strip()}")
                            if matched_fields:
                                found.append({
                                    'login': u,
                                    'html_url': prof.get('html_url'),
                                    'name': prof.get('name') or '',
                                    'matched_fields': matched_fields,
                                    'bio': (prof.get('bio') or '').replace('\r', '').replace('\n', ' '),
                                    'company': prof.get('company') or '',
                                    'public_repos': prof.get('public_repos', 0)
                                })
                        time.sleep(0.3)
                    time.sleep(1.5)
                elif r.status_code in [403, 429]:
                    self.rate_limit_hit = True
                    break
                else:
                    break
            except Exception:
                break

        return found

    def hunt_repos_by_keywords(self, targets: List[str], keywords: List[str]) -> List[Dict[str, Any]]:
        """
        Scans repositories of target users/organizations for names or descriptions matching keywords.
        """
        self.rate_limit_hit = False
        headers = self.get_headers()
        kw_lower_list = [k.lower().strip() for k in keywords if k.strip()]
        results: List[Dict[str, Any]] = []

        def check_user_repos(username: str) -> List[Dict[str, Any]]:
            if self.rate_limit_hit or not username:
                return []
            clean_u = username.split('/')[-1].replace('.git', '').strip()
            url = f"https://api.github.com/users/{clean_u}/repos"
            user_matches = []
            try:
                r = requests.get(url, headers=headers, params={'per_page': 100, 'type': 'all'}, timeout=self.timeout)
                if r.status_code == 200:
                    repos = r.json()
                    for repo in repos:
                        r_name = repo.get('name', '').lower()
                        r_desc = (repo.get('description') or '').lower()
                        matched_kws = [kw for kw in kw_lower_list if kw in r_name or kw in r_desc]
                        if matched_kws:
                            user_matches.append({
                                'username': clean_u,
                                'repo_name': repo.get('name'),
                                'html_url': repo.get('html_url'),
                                'description': repo.get('description') or '',
                                'matched_keywords': matched_kws
                            })
                elif r.status_code in [403, 429]:
                    self.rate_limit_hit = True
            except Exception:
                pass
            return user_matches

        with ThreadPoolExecutor(max_workers=min(self.max_threads, 12)) as executor:
            futures = {executor.submit(check_user_repos, t): t for t in targets}
            for fut in as_completed(futures):
                if self.rate_limit_hit:
                    break
                res = fut.result()
                if res:
                    results.extend(res)

        return results

    def hunt_code_by_keyword(self, username_or_org: str, keyword: str) -> List[Dict[str, Any]]:
        """
        Performs deep Code Search API lookup for specific sensitive keywords inside user's repositories.
        """
        if not self.token:
            return []

        headers = self.get_headers()
        query = f'"{keyword}" user:{username_or_org}'
        url = f"https://api.github.com/search/code?q={quote_plus(query)}"
        findings: List[Dict[str, Any]] = []

        try:
            r = requests.get(url, headers=headers, timeout=self.timeout)
            if r.status_code == 200:
                data = r.json()
                for item in data.get('items', [])[:20]:
                    findings.append({
                        'repo_name': item.get('repository', {}).get('name', ''),
                        'file_name': item.get('name', ''),
                        'html_url': item.get('html_url', ''),
                        'path': item.get('path', '')
                    })
            elif r.status_code in [403, 429]:
                self.rate_limit_hit = True
        except Exception:
            pass

        return findings


# ==============================================================================
# Terminal UI & Exporters
# ==============================================================================

class GitHubPresenter:
    """Renders and exports GitHub intelligence findings."""

    @staticmethod
    def render_company_results(company_name: str, results: List[Dict[str, Any]]):
        if not RICH_AVAILABLE or not console:
            print(f"\n[+] GitHub Company Discovery: {company_name} ({len(results)} accounts)")
            for r in results[:20]:
                print(f"  {r['login']} ({r.get('name', '-')}) -> {r['html_url']} [Repos: {r.get('public_repos', 0)}]")
            return

        table = Table(
            title=f"GitHub Accounts Affiliated with '{company_name}' ({len(results)})",
            title_style="bold cyan",
            border_style="cyan",
            expand=True
        )
        table.add_column("Username / Nick", style="bold yellow", width=20)
        table.add_column("Full Name", style="bold white", width=22)
        table.add_column("Company / Source", style="dim cyan", width=24)
        table.add_column("Public Repos", style="green", width=12, justify="center")
        table.add_column("Profile Link", style="blue")

        for r in results:
            table.add_row(
                r.get('login', ''),
                r.get('name') or "-",
                r.get('source') or r.get('company') or "-",
                str(r.get('public_repos', 0)),
                r.get('html_url', '')
            )

        console.print(table)

    @staticmethod
    def render_prediction_results(matches: List[Dict[str, Any]]):
        if not RICH_AVAILABLE or not console:
            print(f"\n[+] Active GitHub Account Matches ({len(matches)})")
            for m in matches:
                print(f"  {m['original_name']} -> {m['html_url']} [Repos: {m['public_repos']}]")
            return

        table = Table(
            title=f"Discovered Active GitHub Profiles ({len(matches)})",
            title_style="bold green",
            border_style="green",
            expand=True
        )
        table.add_column("Staff / Target Name", style="bold cyan", width=24)
        table.add_column("GitHub Username", style="bold yellow", width=20)
        table.add_column("Public Repos", style="bold green", width=12, justify="center")
        table.add_column("Bio / Headline", style="italic white")
        table.add_column("GitHub URL", style="blue")

        for m in matches:
            table.add_row(
                m.get('original_name', ''),
                m.get('login', ''),
                str(m.get('public_repos', 0)),
                (m.get('bio') or '-')[:50],
                m.get('html_url', '')
            )

        console.print(table)

    @staticmethod
    def render_keyword_results(keyword: str, findings: List[Dict[str, Any]]):
        if not RICH_AVAILABLE or not console:
            print(f"\n[+] Keyword Hunter Matches for '{keyword}' ({len(findings)})")
            for f in findings:
                print(f"  {f.get('login') or f.get('repo_name')} -> {f.get('html_url')}")
            return

        table = Table(
            title=f"Keyword Matches for '{keyword}' ({len(findings)})",
            title_style="bold magenta",
            border_style="magenta",
            expand=True
        )
        table.add_column("Target / Owner", style="bold yellow", width=20)
        table.add_column("Item / Match Description", style="white")
        table.add_column("URL / Link", style="blue")

        for f in findings:
            target = f.get('login') or f.get('username') or f.get('repo_name', '')
            desc = ""
            if 'matched_fields' in f:
                desc = " | ".join(f['matched_fields'][:2])
            elif 'matched_keywords' in f:
                desc = f"Repo: {f.get('repo_name')} [Matches: {', '.join(f['matched_keywords'])}]"
            elif 'file_name' in f:
                desc = f"Code File: {f.get('repo_name')}/{f.get('file_name')}"
            else:
                desc = f.get('description') or "-"

            table.add_row(target, desc, f.get('html_url', ''))

        console.print(table)

    @staticmethod
    def save_results(category: str, identifier: str, data: List[Dict[str, Any]]) -> str:
        """Saves discovered intelligence into structured outputs/github/<category>/<identifier>/ directory."""
        clean_id = re.sub(r'[^a-zA-Z0-9_\-]', '_', identifier.lower())
        out_dir = os.path.join("outputs", "github", category, clean_id)
        os.makedirs(out_dir, exist_ok=True)

        json_path = os.path.join(out_dir, f"{clean_id}.json")
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        txt_path = os.path.join(out_dir, f"{clean_id}_links.txt")
        with open(txt_path, 'w', encoding='utf-8') as f:
            for item in data:
                u = item.get('login') or item.get('username') or ''
                url = item.get('html_url', '')
                f.write(f"{u}\t{url}\n")

        if RICH_AVAILABLE and console:
            console.print(f"[bold green][+][/bold green] Results exported to: [bold cyan]{out_dir}[/bold cyan]")
        else:
            print(f"[+] Results exported to: {out_dir}")
        return out_dir


# ==============================================================================
# Interactive Submenu for KULTIGIN
# ==============================================================================

def run_github_interactive_menu():
    """Interactive GitHub OSINT & Code Hunting Menu."""
    cfg = load_config()
    current_token = cfg.get("github_token", "")

    while True:
        if RICH_AVAILABLE and console:
            console.clear()
            token_display = f"[green]{current_token[:4]}...{current_token[-4:]}[/green]" if len(current_token) > 8 else "[yellow]YOK (Rate limit kısıtlı olabilir)[/yellow]"
            banner = f"""[bold magenta]
   ____ _ _   _   _       _       ___  ____ ___ _   _ _____ 
  / ___(_) |_| | | |_   _| |__   / _ \\/ ___|_ _| \\ | |_   _|
 | |  _| | __| |_| | | | | '_ \\ | | | \\___ \\| ||  \\| | | |  
 | |_| | | |_|  _  | |_| | |_) || |_| |___) | || |\\  | | |  
  \\____|_|\\__|_| |_|\\__,_|_.__/  \\___/|____/___|_| \\_| |_|  
[/bold magenta]
  [bold yellow]KULTIGIN // GitHub OSINT & Code Intelligence[/bold yellow]
  [dim]Aktif Token (config.json):[/dim] {token_display}
"""
            console.print(banner)
            menu_table = Table(show_header=False, box=None, padding=(0, 2))
            menu_table.add_column(style="bold yellow", width=6)
            menu_table.add_column(style="bold white")
            menu_table.add_row("[1]", "Sirket / Kurum Calisanlarinin GitHub Hesaplarini Cikar (Org & User Discovery)")
            menu_table.add_row("[2]", "Calisan Listesinden GitHub Hesaplarini Tahmin Et & Dogrula (Username Predictor)")
            menu_table.add_row("[3]", "Profil ve Repolarda Anahtar Kelime Tarayici (Bio / Repo / Code Hunter)")
            menu_table.add_row("[4]", "GitHub PAT Token Ayarla / Guncelle (Update Config)")
            menu_table.add_row("[5]", "Ana Menuye Don (Back to Main Menu)")
            console.print(Panel(menu_table, title="[bold green]GitHub OSINT Menusu[/bold green]", border_style="magenta"))

            choice = Prompt.ask("\n[bold cyan]Seciminiz[/bold cyan]", choices=["1", "2", "3", "4", "5"], default="1")
        else:
            print("\n=== GITHUB OSINT MENU ===")
            print("1 - Sirket Calisanlarinin GitHub Hesaplarini Cikar")
            print("2 - Calisan Listesinden GitHub Hesaplarini Tahmin Et")
            print("3 - Profil ve Repolarda Kelime Ara (Hunter)")
            print("4 - GitHub Token Ayarla")
            print("5 - Ana Menuye Don")
            choice = input("Secim: ").strip()

        engine = GitHubDiscoverer(token=current_token)

        if choice == "1":
            comp = Prompt.ask("Sirket adi veya GitHub Organization slug'i girin (Orn: priviasecurity, microsoft)").strip() if RICH_AVAILABLE else input("Sirket: ").strip()
            if comp:
                limit_str = Prompt.ask("Maksimum sonuc sayisi", default="50").strip() if RICH_AVAILABLE else "50"
                limit = int(limit_str) if limit_str.isdigit() else 50

                # Check if a LinkedIn usernames file exists for this company
                possible_txt = os.path.join("outputs", "companies", comp.lower(), f"{comp.lower()}_usernames.txt")
                correlate = False
                extra_lines = []
                if os.path.isfile(possible_txt):
                    correlate = Confirm.ask(f"[bold cyan]{possible_txt}[/bold cyan] bulundu! Calisan isimleri de taranip eslestirilsin mi?", default=True) if RICH_AVAILABLE else True
                    if correlate:
                        with open(possible_txt, 'r', encoding='utf-8') as f:
                            extra_lines = [line.strip() for line in f if line.strip()]

                if RICH_AVAILABLE and console:
                    console.print(f"[INFO] '{comp}' icin GitHub hesaplari kesfediliyor...")
                results = engine.discover_company_accounts(comp, max_results=limit)

                if correlate and extra_lines:
                    if RICH_AVAILABLE and console:
                        console.print(f"[INFO] LinkedIn personel listesinden ({len(extra_lines)} kisi) olasi GitHub hesaplari tahmin ediliyor...")
                    predicted = engine.predict_and_verify_profiles(extra_lines, filter_active_repos_only=True)
                    # Merge unique
                    seen_logins = {r['login'].lower() for r in results}
                    for p in predicted:
                        if p['login'].lower() not in seen_logins:
                            p['source'] = f"Predicted from: {p.get('original_name')}"
                            results.append(p)
                            seen_logins.add(p['login'].lower())

                GitHubPresenter.render_company_results(comp, results)
                GitHubPresenter.save_results("companies", comp, results)

        elif choice == "2":
            w_input = Prompt.ask("Ad-soyad listesi (virgulle ayirarak veya dosya yolu)").strip() if RICH_AVAILABLE else input("Liste/Dosya: ").strip()
            names = []
            if os.path.isfile(w_input):
                with open(w_input, 'r', encoding='utf-8') as f:
                    names = [line.strip() for line in f if line.strip()]
            else:
                names = [n.strip() for n in w_input.split(',') if n.strip()]

            if names:
                filter_active = Confirm.ask("Sadece icinde aktif repo olan dolu hesaplar listelensin mi?", default=True) if RICH_AVAILABLE else True
                if RICH_AVAILABLE and console:
                    console.print(f"[INFO] {len(names)} kisi icin varyasyonlar uretiliyor ve sorgulaniyor...")
                matches = engine.predict_and_verify_profiles(names, filter_active_repos_only=filter_active)
                GitHubPresenter.render_prediction_results(matches)
                out_name = os.path.splitext(os.path.basename(w_input))[0] if os.path.isfile(w_input) else "ad_soyad_tarama"
                GitHubPresenter.save_results("predictions", out_name, matches)

        elif choice == "3":
            if RICH_AVAILABLE and console:
                console.print("\n[bold cyan]Kelime Tarama Turu:[/bold cyan]")
                console.print("  [1] Profil Bilgilerinde Ara (Bio, Company, Location, Isim)")
                console.print("  [2] Kullanici/Kurum Repolarinin Adlarinda ve Aciklamalarinda Ara")
                console.print("  [3] Kod Icinde Derin Arama (Code Search - PAT token gereklidir)")
                h_type = Prompt.ask("Seciminiz", choices=["1", "2", "3"], default="1")
            else:
                print("1- Profillerde ara, 2- Repolarda ara, 3- Kod icinde ara")
                h_type = input("Secim: ").strip()

            if h_type == "1":
                kw = Prompt.ask("Aranacak anahtar kelime (Orn: developer, siber, freelance)").strip() if RICH_AVAILABLE else input("Kelime: ").strip()
                if kw:
                    res = engine.hunt_profiles_by_keyword(kw, pages=3)
                    GitHubPresenter.render_keyword_results(kw, res)
                    GitHubPresenter.save_results("bio_hunters", kw, res)

            elif h_type == "2":
                t_input = Prompt.ask("Hedef kullanici veya kurumlar (virgulle ayirarak veya dosya yolu)").strip() if RICH_AVAILABLE else input("Hedefler: ").strip()
                kws = Prompt.ask("Aranacak kelimeler (virgulle ayrilmis, Orn: test,api,secret,db)").strip() if RICH_AVAILABLE else input("Kelimeler: ").strip()
                targets = []
                if os.path.isfile(t_input):
                    with open(t_input, 'r', encoding='utf-8') as f:
                        targets = [line.strip() for line in f if line.strip()]
                else:
                    targets = [t.strip() for t in t_input.split(',') if t.strip()]

                kw_list = [k.strip() for k in kws.split(',') if k.strip()]
                if targets and kw_list:
                    res = engine.hunt_repos_by_keywords(targets, kw_list)
                    GitHubPresenter.render_keyword_results(kws, res)
                    GitHubPresenter.save_results("repo_hunters", kw_list[0], res)

            elif h_type == "3":
                target_user = Prompt.ask("Hedef kullanici veya organizasyon (Orn: octocat)").strip() if RICH_AVAILABLE else input("Hedef: ").strip()
                kw = Prompt.ask("Kod dosyalarinda aranacak kelime (Orn: password, token, api_key)").strip() if RICH_AVAILABLE else input("Kelime: ").strip()
                if target_user and kw:
                    res = engine.hunt_code_by_keyword(target_user, kw)
                    GitHubPresenter.render_keyword_results(kw, res)
                    GitHubPresenter.save_results("code_hunters", f"{target_user}_{kw}", res)

        elif choice == "4":
            new_t = Prompt.ask("Yeni GitHub Personal Access Token (PAT)", default="").strip() if RICH_AVAILABLE else input("Token: ").strip()
            if new_t:
                cfg['github_token'] = new_t
                save_config(cfg)
                current_token = new_t
                if RICH_AVAILABLE and console:
                    console.print("[bold green][+][/bold green] GitHub Token 'config.json' dosyasina kaydedildi!")
                else:
                    print("[+] Token kaydedildi.")

        elif choice == "5":
            break

        if RICH_AVAILABLE:
            input("\nDevam etmek icin Enter'a basin...")


if __name__ == "__main__":
    run_github_interactive_menu()

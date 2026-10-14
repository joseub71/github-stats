#!/usr/bin/env python3
"""Render languages.svg from the lines *I* changed in my own commits.

The upstream card weights languages by total repository size, so a large repo I
barely touched dominates it. This script instead clones every repo I have commit
contributions in, runs `git log --numstat --author=<my emails>`, maps each file
to a language by extension and sums additions + deletions.

Env: ACCESS_TOKEN (classic PAT: repo, read:user, user:email), EXCLUDE_REPOS
(comma-separated globs, optional). Args: <template.svg> <output.svg>.

Repository names are never printed: Actions logs are public on a public repo.
"""
import fnmatch
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from collections import Counter
from pathlib import PurePosixPath

TOKEN = os.environ["ACCESS_TOKEN"]
EXCLUDE = [p.strip() for p in os.environ.get("EXCLUDE_REPOS", "").split(",") if p.strip()]

# extension -> (label, color). Colors follow GitHub Linguist.
LANGS = {
    ".ts": ("TypeScript", "#3178c6"), ".mts": ("TypeScript", "#3178c6"), ".cts": ("TypeScript", "#3178c6"),
    ".tsx": ("TSX (React)", "#61dafb"),
    ".js": ("JavaScript", "#f1e05a"), ".mjs": ("JavaScript", "#f1e05a"), ".cjs": ("JavaScript", "#f1e05a"),
    ".jsx": ("JSX (React)", "#e8d44d"),
    ".vue": ("Vue", "#41b883"), ".svelte": ("Svelte", "#ff3e00"), ".astro": ("Astro", "#ff5a03"),
    ".py": ("Python", "#3572A5"), ".php": ("PHP", "#4F5D95"), ".rb": ("Ruby", "#701516"),
    ".go": ("Go", "#00ADD8"), ".rs": ("Rust", "#dea584"), ".java": ("Java", "#b07219"),
    ".kt": ("Kotlin", "#A97BFF"), ".swift": ("Swift", "#F05138"), ".dart": ("Dart", "#00B4AB"),
    ".m": ("Objective-C", "#438eff"), ".mm": ("Objective-C++", "#6866fb"),
    ".c": ("C", "#555555"), ".h": ("C", "#555555"), ".cpp": ("C++", "#f34b7d"), ".cs": ("C#", "#178600"),
    ".sql": ("SQL", "#e38c00"), ".graphql": ("GraphQL", "#e10098"), ".prisma": ("Prisma", "#0c344b"),
    ".html": ("HTML", "#e34c26"), ".css": ("CSS", "#663399"), ".scss": ("SCSS", "#c6538c"),
    ".sass": ("Sass", "#a53b70"), ".less": ("Less", "#1d365d"),
    ".sh": ("Shell", "#89e051"), ".bash": ("Shell", "#89e051"), ".zsh": ("Shell", "#89e051"),
    ".tf": ("HCL", "#844FBA"), ".hcl": ("HCL", "#844FBA"),
    ".zig": ("Zig", "#ec915c"), ".lua": ("Lua", "#000080"),
}
BASENAMES = {"Dockerfile": ("Dockerfile", "#384d54"), "Makefile": ("Makefile", "#427819")}

# Vendored / generated paths that would inflate the numbers without being my code.
SKIP_DIRS = {"node_modules", "vendor", "dist", "build", ".next", ".nuxt", ".output", "coverage",
             "generated", "__generated__", "out", ".venv", "venv", "site-packages"}
SKIP_SUFFIXES = (".min.js", ".min.css", ".d.ts", ".map", ".snap", ".lock")
# A single file change bigger than this is almost always generated or vendored.
MAX_LINES_PER_FILE_CHANGE = 3000


def gh(path, body=None):
    req = urllib.request.Request(
        "https://api.github.com" + path,
        data=json.dumps(body).encode() if body else None,
        headers={"Authorization": f"bearer {TOKEN}", "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(req) as res:
        return json.load(res)


def graphql(query, **variables):
    data = gh("/graphql", {"query": query, "variables": variables})
    if data.get("errors"):
        raise RuntimeError(data["errors"])
    return data["data"]


def my_repos():
    viewer = graphql("{ viewer { login contributionsCollection { contributionYears } } }")["viewer"]
    repos = set()
    for year in viewer["contributionsCollection"]["contributionYears"]:
        data = graphql(
            """query($from: DateTime!, $to: DateTime!) { viewer { contributionsCollection(from: $from, to: $to) {
                 commitContributionsByRepository(maxRepositories: 100) { repository { nameWithOwner isFork } } } } }""",
            **{"from": f"{year}-01-01T00:00:00Z", "to": f"{year}-12-31T23:59:59Z"},
        )
        for item in data["viewer"]["contributionsCollection"]["commitContributionsByRepository"]:
            if not item["repository"]["isFork"]:
                repos.add(item["repository"]["nameWithOwner"])
    return viewer["login"], sorted(r for r in repos if not any(fnmatch.fnmatch(r, p) for p in EXCLUDE))


def classify(path):
    p = PurePosixPath(path.split(" => ")[-1].rstrip("}"))
    if SKIP_DIRS.intersection(p.parts) or p.name.endswith(SKIP_SUFFIXES):
        return None
    return BASENAMES.get(p.name) or LANGS.get(p.suffix.lower())


def count(login, repo, emails, workdir):
    dest = os.path.join(workdir, repo.replace("/", "_"))
    url = f"https://{login}:{TOKEN}@github.com/{repo}.git"
    clone = subprocess.run(["git", "clone", "--quiet", "--bare", "--filter=blob:limit=1m", "--no-tags",
                            "--single-branch", url, dest], capture_output=True)
    if clone.returncode:
        return Counter(), False
    authors = [a for e in emails for a in ("--author", e)]
    log = subprocess.run(["git", "-C", dest, "log", "--no-merges", "--numstat", "--pretty=tformat:", *authors],
                         capture_output=True, text=True)
    totals = Counter()
    for line in log.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 3 or not parts[0].isdigit() or not parts[1].isdigit():
            continue  # blank separators and binary files ("-")
        changed = int(parts[0]) + int(parts[1])
        lang = classify(parts[2])
        if lang and changed <= MAX_LINES_PER_FILE_CHANGE:
            totals[lang] += changed
    subprocess.run(["rm", "-rf", dest])
    return totals, True


def render(template, totals):
    total = sum(totals.values()) or 1
    progress, items = [], []
    for i, ((name, color), n) in enumerate(totals.most_common()):
        pct = 100 * n / total
        progress.append(f'<span style="background-color: {color}; width: {pct:.3f}%;" class="progress-item"></span>')
        items.append(
            f'<li style="animation-delay: {(i + 1) * 150}ms;">'
            f'<svg xmlns="http://www.w3.org/2000/svg" class="octicon" style="fill: {color};" viewBox="0 0 16 16" '
            f'version="1.1" width="16" height="16"><path fill-rule="evenodd" d="M8 4a4 4 0 100 8 4 4 0 000-8z"></path></svg>'
            f'<span class="lang">{name}</span><span class="percent">{pct:.2f}%</span></li>\n'
        )
    return template.replace("{{ progress }}", "".join(progress)).replace("{{ lang_list }}", "".join(items))


def main():
    template_path, output_path = sys.argv[1:3]
    emails = [e["email"] for e in gh("/user/emails")]
    login, repos = my_repos()
    totals, cloned = Counter(), 0
    with tempfile.TemporaryDirectory() as workdir:
        for repo in repos:
            repo_totals, ok = count(login, repo, emails, workdir)
            totals += repo_totals
            cloned += ok
    print(f"repos: {cloned}/{len(repos)} analysed")
    for (name, _), n in totals.most_common():
        print(f"{name}: {n}")
    if not totals:
        sys.exit("no language data; keeping the previous card")
    with open(template_path) as f:
        svg = render(f.read(), totals)
    with open(output_path, "w") as f:
        f.write(svg)


if __name__ == "__main__":
    main()

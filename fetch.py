import json, os, random, re, sys, time
from datetime import datetime, timedelta, timezone
import requests
GH = "https://api.github.com"
GROQ = "https://api.groq.com/openai/v1/chat/completions"
BAD = ["awesome-", "tutorial", "leetcode", "interview", "cheatsheet", "dotfiles", "homework"]
BAD_IMG = ["shields.io", "badge", "badgen", "travis-ci", "codecov", "coveralls", "sponsor", "buymeacoffee", "ko-fi", "/actions/"]
OK = {"ai", "web", "mobile", "devtools", "cli", "games", "data", "security", "systems", "design", "other"}
groq_stopped = False
def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default
def save_atomic(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)
def search_repos(token):
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    today = datetime.now(timezone.utc).date()
    ranges = [(0, 7), (8, 14), (15, 21), (22, 30)]
    items = []
    for i, (newer, older) in enumerate(ranges):
        start = (today - timedelta(days=older)).isoformat()
        end = (today - timedelta(days=newer)).isoformat()
        params = {"q": f"created:{start}..{end} stars:>50", "sort": "stars", "order": "desc", "per_page": 100}
        r = requests.get(f"{GH}/search/repositories", headers=headers, params=params, timeout=30)
        r.raise_for_status()
        items.extend(r.json().get("items", []))
        if i < 3:
            time.sleep(2)
    dedup = {}
    for x in items:
        if "id" in x:
            dedup[x["id"]] = x
    return list(dedup.values())
def should_drop(repo, blocklist):
    if repo.get("fork") or repo.get("archived"):
        return True
    desc = (repo.get("description") or "").strip()
    if not desc:
        return True
    try:
        pushed = datetime.strptime(repo.get("pushed_at", ""), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except Exception:
        return True
    if pushed < datetime.now(timezone.utc) - timedelta(days=60):
        return True
    text = " ".join([(repo.get("name") or "").lower(), desc.lower(), " ".join(repo.get("topics") or []).lower()])
    return any(w in text for w in BAD) or (repo.get("full_name") or "") in blocklist
def collect_images(readme, full_name, branch):
    hits = []
    for m in re.finditer(r"!\[[^\]]*\]\(([^)]+)\)", readme):
        hits.append((m.start(), m.group(1).strip().split()[0]))
    for m in re.finditer(r"<img[^>]+src=[\"']([^\"']+)[\"']", readme, flags=re.IGNORECASE):
        hits.append((m.start(), m.group(1).strip()))
    hits.sort(key=lambda x: x[0])
    out, seen = [], set()
    for _, raw in hits:
        u = raw
        if not raw.startswith("http"):
            u = f"https://raw.githubusercontent.com/{full_name}/{branch}/{raw.lstrip('./').lstrip('/')}"
        elif "github.com" in raw and "/blob/" in raw:
            u = raw.replace("https://github.com", "https://raw.githubusercontent.com").replace("/blob/", "/")
        low = u.lower()
        if any(x in low for x in BAD_IMG):
            continue
        if low.endswith((".svg", ".mp4", ".mov", ".webm")):
            continue
        ok = low.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")) or "https://github.com/user-attachments/assets/" in u
        if not ok or not u.startswith("https://") or u in seen:
            continue
        seen.add(u)
        out.append(u)
        if len(out) == 5:
            break
    return out
def trim_readme(readme):
    keep = []
    for line in readme.splitlines():
        low = line.lower()
        if "![" in line or "<img" in low or any(x in low for x in BAD_IMG):
            continue
        keep.append(line)
    return "\n".join(keep)[:3000]
def groq_call(key, full_name, desc, readme_text):
    global groq_stopped
    msg = (
        "You write short factual descriptions of GitHub projects for a feed. Return ONLY a JSON object with exactly these keys:\n"
        "\"pitch\": 1 to 2 sentences, max 160 characters, plainly stating what the project is and what it does. Facts only. No opinions and no marketing words such as cool, powerful, amazing, blazing fast, revolutionary, game-changing. Do not start with the repo name.\n"
        "\"category\": exactly one of: ai, web, mobile, devtools, cli, games, data, security, systems, design, other.\n"
        "\"skip\": true if the project is a tutorial, a course, notes, a list of links, a copy of another project, spam, or too vague to describe. Otherwise false.\n\n"
        f"Repo: {full_name}\nDescription: {desc}\nREADME:\n{readme_text}"
    )
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    body = {
        "model": "openai/gpt-oss-20b",
        "messages": [{"role": "user", "content": msg}],
        "response_format": {"type": "json_object"},
        "reasoning_effort": "low",
        "temperature": 0.3,
        "max_completion_tokens": 1000,
    }
    tries_429 = 0
    for _ in range(2):
        while True:
            r = requests.post(GROQ, headers=headers, json=body, timeout=60)
            if r.status_code == 429:
                tries_429 += 1
                try:
                    wait = float(r.headers.get("retry-after"))
                    pause = wait + 1
                except (TypeError, ValueError):
                    wait, pause = 20, 20
                if tries_429 >= 5 or wait > 90:
                    groq_stopped = True
                    return None
                time.sleep(pause)
                continue
            if r.status_code >= 400:
                return None
            content = (((r.json().get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
            if not content:
                break
            try:
                return json.loads(content)
            except Exception:
                break
    return None
def trim_pitch(pitch):
    if len(pitch) <= 200:
        return pitch
    kept = ""
    for sentence in re.findall(r"[^.!?]+[.!?]+", pitch):
        candidate = (kept + " " + sentence.strip()).strip()
        if len(candidate) > 200:
            break
        kept = candidate
    if kept:
        return kept
    cut = pitch[:200]
    if " " in cut:
        cut = cut[:cut.rfind(" ")]
    else:
        cut = cut[:197]
    cut = cut.rstrip()
    while len(cut) + 3 > 200 and " " in cut:
        cut = cut[:cut.rfind(" ")]
    return cut + "..."
def main():
    github_token = os.environ.get("GITHUB_TOKEN")
    groq_key = os.environ.get("GROQ_API_KEY")
    if not github_token:
        print("Missing GITHUB_TOKEN environment variable.")
        sys.exit(1)
    if not groq_key:
        print("Missing GROQ_API_KEY environment variable.")
        sys.exit(1)
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    cache = load_json("cache.json", {})
    blocklist = set()
    if os.path.exists("blocklist.txt"):
        with open("blocklist.txt", "r", encoding="utf-8") as f:
            blocklist = {x.strip() for x in f if x.strip()}
    repos = search_repos(github_token)
    kept = []
    for i, repo in enumerate(repos, 1):
        full_name = repo.get("full_name", "unknown/unknown")
        rid = str(repo.get("id"))
        status, reason, called_groq = "failed", "", False
        try:
            if should_drop(repo, blocklist):
                status = "skipped"
            else:
                headers = {"Authorization": f"Bearer {github_token}", "Accept": "application/vnd.github.raw+json"}
                rr = requests.get(f"{GH}/repos/{full_name}/readme", headers=headers, timeout=30)
                if rr.status_code != 200:
                    status = "skipped"
                else:
                    images = collect_images(rr.text, full_name, repo.get("default_branch") or "main")
                    cached = cache.get(rid)
                    ai = cached if cached is not None else groq_call(groq_key, full_name, repo.get("description") or "", trim_readme(rr.text))
                    called_groq = cached is None
                    if ai is None:
                        status, reason = "failed", "groq"
                    elif ai.get("skip"):
                        cache[rid], status = {"skip": True}, "skipped"
                    else:
                        cat = str(ai.get("category") or "other").lower()
                        if cat not in OK:
                            cat = "other"
                        pitch = trim_pitch(str(ai.get("pitch") or "").strip())
                        if cached and cached.get("images") is not None:
                            images = cached.get("images")
                        item = {"id": int(repo["id"]), "full_name": full_name, "url": f"https://github.com/{full_name}", "pitch": pitch, "category": cat, "images": images}
                        kept.append(item)
                        cache[rid], status = {"pitch": pitch, "category": cat, "images": images}, "kept"
            if called_groq and not groq_stopped:
                time.sleep(15)
        except Exception as e:
            status, reason = "failed", e.__class__.__name__
        finally:
            save_atomic("cache.json", cache)
            print(f"{i}/{len(repos)} {full_name} ... {status}{(' (' + reason + ')') if reason else ''}")
        if groq_stopped:
            break
        if limit is not None and len(kept) >= limit:
            break
    out_repos = []
    for repo in repos:
        cached = cache.get(str(repo.get("id")))
        if not cached or cached.get("skip"):
            continue
        full_name = repo.get("full_name", "unknown/unknown")
        out_repos.append({"id": int(repo["id"]), "full_name": full_name, "url": f"https://github.com/{full_name}", "pitch": str(cached.get("pitch") or "").strip(), "category": str(cached.get("category") or "other"), "images": cached.get("images") or []})
    random.shuffle(out_repos)
    out = {"generated_at": datetime.now(timezone.utc).isoformat(), "repos": out_repos}
    if limit is None and len(out_repos) < 20:
        print("Warning: kept fewer than 20 repos; not overwriting repos.json")
    else:
        save_atomic("repos.json", out)
    with_images = sum(1 for x in out_repos if x.get("images"))
    cats = {}
    for x in out_repos:
        cats[x["category"]] = cats.get(x["category"], 0) + 1
    print(f"Kept: {len(out_repos)}")
    print(f"With images: {with_images}")
    print("By category:", json.dumps(cats, ensure_ascii=False))
if __name__ == "__main__":
    main()

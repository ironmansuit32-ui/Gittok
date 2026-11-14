# Gittok

A simple web app starter folder.

## Run it

```bash
cd Gittok
npm install
npm start
```

Then open http://localhost:3000

## Refresh the repo data

`public/repos.json` is the dataset served by the front end and is produced by
`fetch.py`, which searches GitHub, summarizes each repository and writes the
result to `repos.json` (copy it into `public/` to serve it).

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export GITHUB_TOKEN=...      # required
export GROQ_API_KEY=...      # required
python fetch.py 20           # optional: max number of repos to keep
```

Repositories listed in `blocklist.txt` (one `owner/name` per line) are skipped.
Already-analyzed repos are cached in `cache.json`, which is ignored by git.

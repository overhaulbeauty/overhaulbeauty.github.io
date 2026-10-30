"""Cloud poster for @overhaulbeauty (Ryan 2026-10-30: the PC is OFF Sat 31.10 + Sun 01.11, so GitHub Actions posts).

Runs in .github/workflows/weekend-post.yml. Reads cloud/weekend.json; for today's entry (WIB) it posts the Instagram Reel
(with cover) and the Threads video + link reply, then commits cloud/done/<date>-<platform>.txt so a later run never
posts twice. Results and errors go to Telegram. Standard library only. Tokens come from GitHub secrets and are never
printed.

MODE: post (default) | morning (09:00 status) | dry (checks accounts + video links, posts nothing)
"""
import datetime as dt
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

IG = "https://graph.instagram.com"
TH = "https://graph.threads.net/v1.0"
WIB = dt.timezone(dt.timedelta(hours=7))
HERE = os.path.dirname(os.path.abspath(__file__))
DONE = os.path.join(HERE, "done")
ENV = {k: os.environ.get(k, "").strip() for k in ("IG_TOKEN", "TH_TOKEN", "TG_TOKEN", "TG_CHAT")}
SECRETS = [v for v in ENV.values() if v]


def clean(text):
    text = str(text)
    for s in SECRETS:
        text = text.replace(s, "***")
    return text


def http(url, data=None, timeout=120):
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {clean(e.read().decode(errors='replace'))[:300]}") from None


def tg(text):
    if not (ENV["TG_TOKEN"] and ENV["TG_CHAT"]):
        print("TELEGRAM (not configured):", text)
        return
    try:
        http(f"https://api.telegram.org/bot{ENV['TG_TOKEN']}/sendMessage",
             {"chat_id": ENV["TG_CHAT"], "text": text, "disable_web_page_preview": "true"}, timeout=30)
    except Exception as e:
        print("telegram failed:", clean(e))


def wait(base, container, token, done=("FINISHED", "PUBLISHED"), field="status_code", wait_s=600):
    deadline = time.time() + wait_s
    status = "IN_PROGRESS"
    while time.time() < deadline:
        fields = "status,error_message" if field == "status" else f"{field},status"
        r = http(f"{base}/{container}?fields={fields}&access_token={token}")
        status = r.get(field) or r.get("status") or status
        if status in done:
            return
        if status in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"processing failed: {r.get('status') or status}")
        time.sleep(10)
    raise RuntimeError(f"still {status} after {wait_s} s")


def ig_uid():
    me = http(f"{IG}/me?fields=user_id,username&access_token={ENV['IG_TOKEN']}")
    return me.get("user_id") or me.get("id"), me.get("username")


def th_uid():
    me = http(f"{TH}/me?fields=id,username&access_token={ENV['TH_TOKEN']}")
    return me["id"], me.get("username")


def post_instagram(p):
    uid, _ = ig_uid()
    data = {"media_type": "REELS", "video_url": p["video_url"], "caption": p["instagram"][:2200],
            "share_to_feed": "true", "access_token": ENV["IG_TOKEN"]}
    if p.get("cover_url"):
        data["cover_url"] = p["cover_url"]
    c = http(f"{IG}/{uid}/media", data)
    wait(IG, c["id"], ENV["IG_TOKEN"])
    m = http(f"{IG}/{uid}/media_publish", {"creation_id": c["id"], "access_token": ENV["IG_TOKEN"]})
    return http(f"{IG}/{m['id']}?fields=permalink&access_token={ENV['IG_TOKEN']}").get("permalink", m["id"])


def post_threads(p):
    """Main post = video + short text (no topic tag: video + topic tag fails, 2026-10-28), then the link reply."""
    uid, _ = th_uid()
    ids = []
    for i, text in enumerate(p["threads"]):
        data = {"text": text, "access_token": ENV["TH_TOKEN"]}
        if i == 0:
            data.update({"media_type": "VIDEO", "video_url": p["video_url"]})
        else:
            data.update({"media_type": "TEXT", "reply_to_id": ids[-1]})
        c = http(f"{TH}/{uid}/threads", data)
        wait(TH, c["id"], ENV["TH_TOKEN"], field="status")
        ids.append(http(f"{TH}/{uid}/threads_publish", {"creation_id": c["id"], "access_token": ENV["TH_TOKEN"]})["id"])
        time.sleep(3)
    return http(f"{TH}/{ids[0]}?fields=permalink&access_token={ENV['TH_TOKEN']}").get("permalink", ids[0])


def done_path(date, platform):
    return os.path.join(DONE, f"{date}-{platform}.txt")


def mark_done(date, platform, link):
    os.makedirs(DONE, exist_ok=True)
    with open(done_path(date, platform), "w") as f:
        f.write(f"{dt.datetime.now(WIB):%Y-%m-%d %H:%M} {link}\n")
    subprocess.run(["git", "add", done_path(date, platform)], check=True)
    subprocess.run(["git", "commit", "-m", f"cloud: {date} {platform} posted"], check=True)
    for _ in range(3):  # the site may have moved on (video uploads): rebase and retry
        if subprocess.run(["git", "push"]).returncode == 0:
            return
        subprocess.run(["git", "pull", "--rebase"])
    print("WARNING: done marker not pushed")


def main():
    mode = (os.environ.get("MODE") or "").strip() or ("morning" if os.environ.get("EVENT") == "0 2 * * *" else "post")
    now = dt.datetime.now(WIB)
    date = (os.environ.get("DATE") or "").strip() or now.date().isoformat()
    with open(os.path.join(HERE, "weekend.json"), encoding="utf-8") as f:
        posts = {p["date"]: p for p in json.load(f)["posts"]}
    p = posts.get(date)
    print(f"mode {mode}, date {date}, now {now:%H:%M} WIB, entry: {'yes' if p else 'no'}")
    if not p:
        if mode == "dry":
            tg(f"Cloud poster dry run: nothing planned for {date}.")
        return
    todo = [x for x in ("instagram", "threads") if not os.path.exists(done_path(date, x))]

    if mode == "morning":
        tg(f"GOOD MORNING (cloud) - your PC is off, GitHub posts today's Eps {p['ep']} ({p['day']}) at {p['time']} "
           f"on {' + '.join(x.capitalize() for x in todo) or 'nothing (already done)'}. TikTok = the post you scheduled.")
        return

    if mode == "dry":
        lines = [f"Cloud poster DRY RUN for Eps {p['ep']} ({p['day']}), nothing posted:"]
        for name, fn in (("Instagram", ig_uid), ("Threads", th_uid)):
            try:
                lines.append(f"- {name}: OK (@{fn()[1]})")
            except Exception as e:
                lines.append(f"- {name}: PROBLEM {clean(e)[:200]}")
        for key in ("video_url", "cover_url"):
            try:
                with urllib.request.urlopen(urllib.request.Request(p[key], method="HEAD"), timeout=30) as r:
                    lines.append(f"- {key}: OK ({r.status})")
            except Exception as e:
                lines.append(f"- {key}: PROBLEM {clean(e)[:120]}")
        lines.append(f"- still to post: {', '.join(todo) or 'nothing'}")
        tg("\n".join(lines))
        print("\n".join(lines))
        return

    if now.hour < 13 or now.hour >= 21:
        print("outside the posting window (13:00-21:00 WIB)")
        return
    results, failed = [], []
    for platform, fn in (("instagram", post_instagram), ("threads", post_threads)):
        if platform not in todo:
            continue
        for attempt in range(1, 4):
            try:
                link = fn(p)
                mark_done(date, platform, link)
                results.append(f"{platform.capitalize()}: {link}")
                break
            except Exception as e:
                err = clean(e)[:300]
                print(f"{platform} attempt {attempt}: {err}")
                if "log in to www.instagram.com" in err or attempt == 3:
                    failed.append(f"{platform.upper()} NOT POSTED: {err}")
                    break
                time.sleep(60)
    if results:
        tg(f"POSTED (cloud, PC off) - Eps {p['ep']} ({p['day']})\n\n" + "\n".join(results)
           + "\n\nTikTok: your scheduled post.")
    if failed:
        tg(f"Eps {p['ep']} ({p['day']}), cloud poster:\n" + "\n".join(failed)
           + "\nIt tries again at the next run (14:20 / 14:50 WIB).")
        sys.exit(1)


if __name__ == "__main__":
    main()

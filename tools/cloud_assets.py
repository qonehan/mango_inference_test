"""Google Drive 원본 자료 업로드·복원 도구 (표준 라이브러리만 사용).

사용법 (저장소 루트에서):
  python tools/cloud_assets.py upload <경로목록.txt>   # Drive 업로드 → 재다운로드 SHA-256 검증 → manifest·ASSETS.md·.gitignore 갱신
  python tools/cloud_assets.py sync                    # manifest 기준 누락 원본 복원(캐시 → Drive)
  python tools/cloud_assets.py pull                    # 원본 캐시 보관 → git pull --ff-only → sync
  python tools/cloud_assets.py cache                   # 현재 원본을 .git/cloud-assets-cache에 보관
  python tools/cloud_assets.py check                   # manifest 원본이 스테이징·추적 중인지 점검

인증: gcloud auth login qonehan@snu.ac.kr --enable-gdrive-access 로 학교 계정을 로그인해 둔다.
토큰은 실행할 때마다 gcloud에서 받아 메모리에서만 사용하며 파일에 저장하지 않는다.
GOOGLE_DRIVE_ACCESS_TOKEN 환경변수가 있으면 그것을 우선 사용한다.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

MANIFEST = "assets.manifest.json"
ASSETS_MD = "ASSETS.md"
GITIGNORE = ".gitignore"
API = "https://www.googleapis.com/drive/v3"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
FOLDER_MIME = "application/vnd.google-apps.folder"


def repo_root():
    return subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip()


def git_path(name):
    return subprocess.check_output(["git", "rev-parse", "--path-format=absolute", "--git-path", name], text=True).strip()


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def matches(path, expected):
    """원본 해시 일치 여부. 텍스트는 Windows CRLF → LF 정규화 해시도 허용한다."""
    if sha256_file(path) == expected:
        return True
    if os.path.splitext(path)[1].lower() in (".html", ".json", ".jsonl", ".md", ".csv", ".txt", ".log"):
        with open(path, "rb") as f:
            return sha256_bytes(f.read().replace(b"\r\n", b"\n")) == expected
    return False


def load_manifest():
    with open(MANIFEST, encoding="utf-8") as f:
        return json.load(f)


def save_manifest(m):
    m["files"].sort(key=lambda a: a["path"])
    with open(MANIFEST, "w", encoding="utf-8", newline="\n") as f:
        json.dump(m, f, ensure_ascii=False, indent=2)
        f.write("\n")


def find_gcloud():
    for cand in (shutil.which("gcloud"), shutil.which("gcloud.cmd"),
                 r"C:\Program Files (x86)\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd",
                 os.path.expandvars(r"%LOCALAPPDATA%\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd")):
        if cand and os.path.exists(cand):
            return cand
    return None


class Drive:
    def __init__(self, account):
        token = os.environ.get("GOOGLE_DRIVE_ACCESS_TOKEN")
        if not token:
            g = find_gcloud()
            if not g:
                sys.exit("gcloud가 없습니다. 학교 계정 인증을 먼저 설정하세요.")
            r = subprocess.run([g, "auth", "print-access-token", f"--account={account}"], capture_output=True, text=True)
            if r.returncode != 0:
                sys.exit(f"{account} 인증이 필요합니다: gcloud auth login {account} --enable-gdrive-access\n{r.stderr.strip()}")
            token = r.stdout.strip()
        self.h = {"Authorization": f"Bearer {token}"}
        email = self.get(f"{API}/about?fields=user(emailAddress)")["user"]["emailAddress"]
        if email.lower() != account.lower():
            sys.exit(f"잘못된 Google 계정({email}). 중단합니다.")
        self.folders = {}

    def req(self, url, data=None, method=None, headers=None, raw=False):
        h = dict(self.h)
        h.update(headers or {})
        r = urllib.request.Request(url, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(r, timeout=600) as resp:
                body = resp.read()
                return (body, resp.headers) if raw else (json.loads(body) if body else {})
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"{e.code} {url}: {e.read()[:500]!r}") from None

    def get(self, url):
        return self.req(url)

    def child_folder(self, parent, name):
        key = (parent, name)
        if key in self.folders:
            return self.folders[key]
        q = (f"name = '{name.replace(chr(92), chr(92)*2).replace(chr(39), chr(92)+chr(39))}' and '{parent}' in parents "
             f"and mimeType = '{FOLDER_MIME}' and trashed = false")
        res = self.get(f"{API}/files?" + urllib.parse.urlencode({"q": q, "fields": "files(id)", "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"}))
        if res["files"]:
            fid = res["files"][0]["id"]
        else:
            meta = json.dumps({"name": name, "mimeType": FOLDER_MIME, "parents": [parent]}).encode()
            fid = self.req(f"{API}/files?fields=id&supportsAllDrives=true", meta, "POST", {"Content-Type": "application/json; charset=UTF-8"})["id"]
        self.folders[key] = fid
        return fid

    def folder_for(self, root_id, rel_dir):
        fid = root_id
        for part in [p for p in rel_dir.split("/") if p]:
            fid = self.child_folder(fid, part)
        return fid

    def upload(self, parent, name, data):
        meta = json.dumps({"name": name, "parents": [parent]}).encode()
        _, hdr = self.req(f"{UPLOAD}?uploadType=resumable&supportsAllDrives=true&fields=id", meta, "POST",
                          {"Content-Type": "application/json; charset=UTF-8", "X-Upload-Content-Length": str(len(data))}, raw=True)
        res = self.req(hdr["Location"], data, "PUT", {"Content-Length": str(len(data))})
        return res["id"]

    def download(self, fid):
        body, _ = self.req(f"{API}/files/{fid}?alt=media&supportsAllDrives=true", raw=True)
        return body


def gitignore_line(path):
    esc = "".join("\\" + c if c in "[]*?!#" else c for c in path)
    return "/" + esc


def cmd_upload(listfile):
    m = load_manifest()
    root_id = m["drive_folder_url"].rstrip("/").split("/")[-1]
    with open(listfile, encoding="utf-8") as f:
        paths = [l.strip().replace("\\", "/") for l in f if l.strip()]
    known = {a["path"]: a for a in m["files"]}
    drive = Drive(m["account"])
    tracked = set(subprocess.check_output(["git", "ls-files", "-z"]).decode("utf-8").split("\0"))
    new_entries, failures = [], []
    for i, p in enumerate(paths, 1):
        if not os.path.isfile(p):
            failures.append(f"없음: {p}")
            continue
        with open(p, "rb") as f:
            data = f.read()
        h = sha256_bytes(data)
        if p in known and known[p]["sha256"] == h:
            print(f"[{i}/{len(paths)}] 이미 업로드됨: {p}")
            continue
        try:
            d, name = os.path.split(p)
            fid = drive.upload(drive.folder_for(root_id, d), name, data)
            back = drive.download(fid)
            if sha256_bytes(back) != h:
                failures.append(f"재다운로드 해시 불일치: {p}")
                continue
        except Exception as e:  # noqa: BLE001
            failures.append(f"업로드 실패: {p}: {e}")
            continue
        blob = None
        if p in tracked:
            blob = subprocess.check_output(["git", "rev-parse", f"HEAD:{p}"], text=True).strip()
        entry = {"path": p, "size": len(data), "sha256": h, "git_blob_sha": blob, "drive_file_id": fid,
                 "url": f"https://drive.google.com/file/d/{fid}/view?usp=drivesdk", "verification": "downloaded_sha256_match"}
        known[p] = entry
        new_entries.append(entry)
        print(f"[{i}/{len(paths)}] 검증 완료 {len(data):,}B {p}", flush=True)
    m["files"] = list(known.values())
    save_manifest(m)
    if new_entries:
        update_assets_md(new_entries)
        update_gitignore([e["path"] for e in new_entries])
        tracked_new = [e["path"] for e in new_entries if e["path"] in tracked]
        if tracked_new:
            subprocess.check_call(["git", "rm", "--cached", "-q", "--"] + tracked_new)
        cache_dir = git_path("cloud-assets-cache")
        os.makedirs(cache_dir, exist_ok=True)
        for e in new_entries:
            dst = os.path.join(cache_dir, e["sha256"])
            if not os.path.exists(dst):
                shutil.copyfile(e["path"], dst)
        save_state({e["path"]: e["sha256"] for e in new_entries})
    total = sum(e["size"] for e in new_entries)
    print(f"\n업로드·검증 {len(new_entries)}개, {total:,} bytes. 실패 {len(failures)}개")
    for f_ in failures:
        print(" -", f_)
    sys.exit(1 if failures else 0)


def update_assets_md(entries):
    with open(ASSETS_MD, encoding="utf-8") as f:
        lines = f.read().split("\n")
    existing = {l.split("`")[1] for l in lines if l.startswith("| `")}
    rows = [f"| `{e['path']}` | [Drive]({e['url']}) | {e['size']:,} |" for e in entries if e["path"] not in existing]
    rows_at = [i for i, l in enumerate(lines) if l.startswith("| `")]
    if rows_at:
        last = rows_at[-1]
    else:  # 빈 표: 머리글 구분선(|---) 바로 뒤에 넣는다
        last = next(i for i, l in enumerate(lines) if l.startswith("|---"))
    lines[last + 1:last + 1] = rows
    with open(ASSETS_MD, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))


def update_gitignore(paths):
    with open(GITIGNORE, encoding="utf-8") as f:
        text = f.read()
    have = set(text.split("\n"))
    add = [gitignore_line(p) for p in paths if gitignore_line(p) not in have]
    if add:
        with open(GITIGNORE, "a", encoding="utf-8", newline="\n") as f:
            if not text.endswith("\n"):
                f.write("\n")
            f.write("\n".join(add) + "\n")


def load_state():
    p = git_path("cloud-assets-state.json")
    if os.path.exists(p):
        with open(p, encoding="utf-8-sig") as f:
            return json.load(f)
    return {}


def save_state(update):
    st = load_state()
    st.update(update)
    with open(git_path("cloud-assets-state.json"), "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)


def cmd_cache():
    if not os.path.exists(MANIFEST):
        return
    cache_dir = git_path("cloud-assets-cache")
    os.makedirs(cache_dir, exist_ok=True)
    n = 0
    for a in load_manifest()["files"]:
        p, dst = a["path"], os.path.join(cache_dir, a["sha256"])
        if os.path.exists(dst) or not os.path.isfile(p):
            continue
        with open(p, "rb") as f:
            b = f.read()
        for cand in (b, b.replace(b"\r\n", b"\n")):
            if sha256_bytes(cand) == a["sha256"]:
                with open(dst, "wb") as f:
                    f.write(cand)
                n += 1
                break
    print(f"캐시 보관 {n}개")


def cmd_sync():
    m = load_manifest()
    cache_dir = git_path("cloud-assets-cache")
    state = load_state()
    pending, conflicts, ok, restored = [], [], 0, 0
    for a in m["files"]:
        p = a["path"]
        if os.path.isfile(p):
            if matches(p, a["sha256"]):
                ok += 1
                state[p] = a["sha256"]
            elif state.get(p) == sha256_file(p):
                pending.append(a)  # 이전 검증본에서 클라우드만 갱신됨
            else:
                conflicts.append(p)  # 로컬 수정본: 덮어쓰지 않음
            continue
        src = os.path.join(cache_dir, a["sha256"])
        if os.path.isfile(src) and sha256_file(src) == a["sha256"]:
            os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
            shutil.copyfile(src, p)
            state[p] = a["sha256"]
            restored += 1
        else:
            pending.append(a)
    if pending:
        drive = Drive(m["account"])
        for a in pending:
            b = drive.download(a["drive_file_id"])
            if sha256_bytes(b) != a["sha256"]:
                sys.exit(f"다운로드 해시 불일치: {a['path']}")
            os.makedirs(os.path.dirname(a["path"]) or ".", exist_ok=True)
            tmp = a["path"] + ".cloud-tmp"
            with open(tmp, "wb") as f:
                f.write(b)
            os.replace(tmp, a["path"])
            state[a["path"]] = a["sha256"]
            restored += 1
            print("복원(Drive):", a["path"])
    with open(git_path("cloud-assets-state.json"), "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    print(f"원본 {len(m['files'])}개: 일치 {ok}, 복원 {restored}, 로컬 수정본 보존 {len(conflicts)}")
    for c in conflicts:
        print(" - 로컬 수정본(수동 확인 필요):", c)


def cmd_pull():
    cmd_cache()
    r = subprocess.run(["git", "pull", "--ff-only"])
    if r.returncode != 0:
        sys.exit("git pull 실패: 복원을 건너뜁니다. (미커밋 변경은 stash 후 다시 시도)")
    cmd_sync()


def cmd_check():
    m = load_manifest()
    tracked = set(subprocess.check_output(["git", "ls-files", "-z"]).decode("utf-8").split("\0"))
    staged = set(subprocess.check_output(["git", "diff", "--cached", "--name-only", "--diff-filter=d", "-z"]).decode("utf-8").split("\0"))
    bad = [a["path"] for a in m["files"] if a["path"] in tracked or a["path"] in staged]
    big = [p for p in staged if p and os.path.isfile(p) and os.path.getsize(p) > 10 * 1024 * 1024]
    for p in bad:
        print("원본이 Git에 포함됨:", p)
    for p in big:
        print("10MiB 초과 스테이징:", p)
    print("점검 통과" if not (bad or big) else "점검 실패")
    sys.exit(1 if bad or big else 0)


if __name__ == "__main__":
    os.chdir(repo_root())
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    c = sys.argv[1]
    {"upload": lambda: cmd_upload(sys.argv[2]), "sync": cmd_sync, "pull": cmd_pull,
     "cache": cmd_cache, "check": cmd_check}.get(c, lambda: sys.exit(__doc__))()

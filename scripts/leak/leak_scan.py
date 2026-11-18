#!/usr/bin/env python3
"""hommy leak scanner: personal identifiers never enter a commit, a path name, a commit header or a message.

Usage (normally through scripts/leak-scan.sh):
  leak_scan.py [opts] [<path>...]                  git work trees (tracked + untracked, not ignored) or plain files/dirs
  leak_scan.py [opts] --staged [<repo>]            the index (what `git commit` would record)
  leak_scan.py [opts] --push <local> <remote> [<repo>]   every blob, path, commit header and message the push sends
  leak_scan.py [opts] --history [<repo>]           everything reachable from every ref (branches, tags, pull heads)
  leak_scan.py --self-test                         runtime probes against a temporary list (no real list needed)
Options:
  --list FILE         identifiers (default $LEAK_IDENTIFIERS, else ~/.config/hommy-build/leak-identifiers.txt)
  --identities FILE   allowed commit emails (default <repo>/.github/commit-identities)
  --images FILE       reviewed images and PDFs (default <repo>/scripts/leak/images.txt)
  --no-identities     skip the commit identity check      --identities-only   only the commit identity check
  --required          fail (exit 2) without the list; also HOMMY_LEAK_REQUIRED=1 or `git config hommy.leak.required true`

Output: `path:line: leak #N (category)`, `blob|commit <sha12>: path name: leak #N`, `commit|tag <sha12>: message: leak #N`,
`commit <sha12>: <author|committer> email not allowed`, `path: image not reviewed`; never the matched text. #N is the
line number in the list. Last line: `leak scan: <files> files, <commits> commits, <hits> hits (identifiers: <n> |
skipped)`. Exit 0 clean, 1 hit, 2 no list where one is required / usage.

Matching: text and identifiers NFC-normalised and case-folded; a hit needs a boundary (the characters before and after
are not Unicode letters or digits). Category `phone`: every phone-like run is reduced to digits and hits when it holds
the identifier's digits (or their last 9 when it has >= 10). Text sources: UTF-8 files; XML inside office archives
(tags removed, runs joined, and again with tags as spaces); zip members; gzip; any other binary through its printable
runs (>= 4 chars). Images and PDFs also need an entry (path + SHA-256) in images.txt.
"""
import gzip
import hashlib
import io
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unicodedata
import zipfile

MAX_BLOB = 64 * 1024 * 1024
ZERO = re.compile(r"^0+$")
PHONE_RUN = re.compile(r"[+(]?\d[\d\s().-]{4,}\d")  # >= 6 chars: the shortest phone entry kept
PHONE_SHAPE = re.compile(r"^\s*\+?[\d\s().-]{7,}\s*$")  # a phone-shaped identifier in any category also gets the phone rule
OFFICE = (".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp", ".numbers", ".pages", ".key")
ZIPS = OFFICE + (".zip", ".jar")
IMAGES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".heif", ".tif", ".tiff", ".bmp", ".ico", ".icns",
          ".pdf")
XML_BREAK = re.compile(r"</(?:w:p|a:p|text:p|text:h|row|tr|p|div|si|c)>|<(?:w:br|w:tab|text:tab|text:s|br)\b[^>]*/?>")
XML_TAG = re.compile(r"<[^>]*>")
DEFAULT_LIST = os.path.join(os.path.expanduser("~"), ".config", "hommy-build", "leak-identifiers.txt")


def norm(s):
    return unicodedata.normalize("NFC", s).casefold()


def bounded(text, start, end):
    return (start == 0 or not text[start - 1].isalnum()) and (end >= len(text) or not text[end].isalnum())


class Identifiers:
    """The list: line N holds `<category>\\t<identifier>`; `#` comments and blank lines keep their numbers."""

    def __init__(self, entries):
        self.words = []  # (n, category, normalised text)
        self.phones = []  # (n, category, digits)
        for n, cat, ident in entries:
            if cat == "phone" or PHONE_SHAPE.match(ident):
                d = re.sub(r"\D", "", ident)
                if len(d) >= 6:
                    self.phones.append((n, cat, d))
            if cat != "phone":
                w = norm(ident.strip())
                if w:
                    self.words.append((n, cat, w))
        self.by_word = {}
        for n, cat, w in self.words:
            self.by_word.setdefault(w, (n, cat))
        alts = sorted(self.by_word, key=len, reverse=True)
        self.by_first = {}  # every word starting with a character, longest first
        for w in alts:
            self.by_first.setdefault(w[0], []).append(w)
        self.rx = re.compile("(?=(" + "|".join(re.escape(w) for w in alts) + "))") if alts else None

    @classmethod
    def load(cls, path):
        entries = []
        with open(path, encoding="utf-8") as f:
            for n, line in enumerate(f, 1):
                line = line.rstrip("\n")
                if not line.strip() or line.lstrip().startswith("#") or "\t" not in line:
                    continue
                cat, ident = line.split("\t", 1)
                entries.append((n, cat.strip(), ident))
        return cls(entries)

    def __len__(self):
        return len(self.words) + len(self.phones)

    def hits(self, line):
        """[(n, category)] in one line of text (already any str)."""
        t = norm(line)
        out = []
        if self.rx:
            for m in self.rx.finditer(t):  # positions where some word starts; each word there is judged alone
                i = m.start(1)
                for w in self.by_first[t[i]]:
                    if t.startswith(w, i) and bounded(t, i, i + len(w)):
                        out.append(self.by_word[w])
        if self.phones:
            for m in PHONE_RUN.finditer(t):
                d = re.sub(r"\D", "", m.group(0))
                for n, cat, p in self.phones:
                    if p in d or (len(p) >= 10 and p[-9:] in d):
                        out.append((n, cat))
        return out


def printable_runs(data):
    """Like strings(1), keeping UTF-8 sequences: runs of >= 4 printable bytes, decoded."""
    out = []
    for m in re.finditer(rb"[\t\x20-\x7e\x80-\xff]{4,}", data):
        out.append(m.group(0).decode("utf-8", "ignore"))
    return out


def xml_texts(data):
    s = data.decode("utf-8", "ignore")
    spaced = XML_TAG.sub(" ", XML_BREAK.sub("\n", s))
    joined = XML_TAG.sub("", XML_BREAK.sub("\n", s))
    return [spaced, joined]


def texts_of(name, data, depth=0):
    """[(display name, line number or 0, text)] for a blob; recursing into archives."""
    low = name.lower()
    if depth < 3 and low.endswith(ZIPS):
        try:
            z = zipfile.ZipFile(io.BytesIO(data))
            out = []
            for info in z.infolist():
                if info.is_dir() or info.file_size > MAX_BLOB:
                    continue
                member = z.read(info)
                sub = name + "!" + info.filename
                out.append((sub, 0, info.filename))  # a member's name is text too
                if low.endswith(OFFICE) and info.filename.lower().endswith((".xml", ".rels")):
                    out += [(sub, 0, t) for t in xml_texts(member)]
                else:
                    out += texts_of(sub, member, depth + 1)
            return out
        except (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError, EOFError):
            pass
    if depth < 3 and low.endswith(".gz"):
        try:
            return texts_of(name[:-3], gzip.decompress(data)[:MAX_BLOB], depth + 1)
        except (OSError, EOFError):
            pass
    if b"\0" not in data[:8192]:
        try:
            text = data.decode("utf-8")
            return [(name, i, line) for i, line in enumerate(text.split("\n"), 1)]
        except UnicodeDecodeError:
            pass
    return [(name, 0, "\n".join(printable_runs(data)))]


def blob_sha(data):
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


class Scan:
    def __init__(self, ids, images, base):
        self.ids, self.images, self.base = ids, images, base
        self.hits, self.files, self.commits = [], 0, 0

    def add(self, line):
        self.hits.append(line)

    def path_hit(self, path):
        if not self.ids:
            return []
        found = []
        for part in [path] + path.split("/"):
            found += self.ids.hits(part)
        return sorted(set(found))

    def blob(self, path, data, sha=None):
        self.files += 1
        sha = sha or blob_sha(data)
        bad_path = self.path_hit(path)
        for n, _cat in bad_path:
            self.add(f"blob {sha[:12]}: path name: leak #{n}")
        shown = f"blob {sha[:12]}" if bad_path else path
        if path.lower().endswith(IMAGES):
            if self.images.get(path) != hashlib.sha256(data).hexdigest():
                self.add(f"{shown}: image not reviewed (scripts/leak/images.txt)")
        if not self.ids:
            return
        if len(data) > MAX_BLOB:
            self.add(f"{shown}: too large to scan (> {MAX_BLOB} bytes)")
            return
        seen = set()
        for name, line, text in texts_of(path, data):
            if bad_path:
                name = shown + name[len(path):]
            for n, cat in self.ids.hits(text):
                key = (name, line, n)
                if key not in seen:
                    seen.add(key)
                    self.add(f"{name}:{line}: leak #{n} ({cat})")

    def tag(self, sha, data, identities, check_ids, check_text):
        head, _, msg = data.decode("utf-8", "replace").partition("\n\n")
        name = re.search(r"^tag (.*)$", head, re.M)
        tagger = re.search(r"^tagger (.*) <([^>]*)>", head, re.M)
        if check_ids and tagger and tagger.group(2).strip().lower() not in identities:
            self.add(f"tag {sha[:12]}: tagger email not allowed")
        if not (check_text and self.ids):
            return
        for what, text in (("name", name and name.group(1)), ("header", tagger and " ".join(tagger.groups())),
                           ("message", msg)):
            found = set()
            for line in (text or "").split("\n"):
                found.update(self.ids.hits(line))
            for n, _cat in sorted(found):
                self.add(f"tag {sha[:12]}: {what}: leak #{n}")


def git(repo, *args, inp=None, check=True):
    r = subprocess.run(["git", "-C", repo, *args], input=inp, capture_output=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:2])} failed: {r.stderr.decode('utf-8', 'ignore').strip()[:200]}")
    return r.stdout


def cat_objects(repo, shas):
    """{sha: (type, data)} via one `git cat-file --batch`."""
    if not shas:
        return {}
    out = git(repo, "cat-file", "--batch", inp=("\n".join(shas) + "\n").encode())
    res, i = {}, 0
    for sha in shas:
        nl = out.index(b"\n", i)
        head = out[i:nl].decode().split()
        if len(head) < 3:  # missing
            i = nl + 1
            continue
        size = int(head[2])
        res[sha] = (head[1], out[nl + 1:nl + 1 + size])
        i = nl + 1 + size + 1
    return res


def load_images(path):
    out = {}
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    parts = line.split()
                    if len(parts) >= 2:
                        out[parts[0]] = parts[1].lower()
    return out


def load_identities(path):
    out = set()
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.split("#", 1)[0].strip().lower()
                if line:
                    out.add(line)
    return out


def toplevel(path):
    r = subprocess.run(["git", "-C", path, "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def scan_paths(scan, paths):
    for p in paths:
        p = os.path.abspath(p)
        d = p if os.path.isdir(p) else os.path.dirname(p)
        top = toplevel(d)
        if top:
            rel = os.path.relpath(p, top)
            spec = [] if rel == "." else ["--", rel]
            names = git(top, "ls-files", "-z", "--cached", "--others", "--exclude-standard", *spec).split(b"\0")
            for n in sorted(set(x.decode("utf-8", "surrogateescape") for x in names if x)):
                f = os.path.join(top, n)
                if os.path.isfile(f) and not os.path.islink(f):
                    with open(f, "rb") as fh:
                        scan.blob(os.path.relpath(f, scan.base), fh.read())
        elif os.path.isfile(p):
            with open(p, "rb") as fh:
                scan.blob(os.path.relpath(p, scan.base), fh.read())
        else:
            for root, dirs, files in os.walk(p):
                dirs[:] = sorted(x for x in dirs if x not in (".git", "node_modules", ".dev"))
                for n in sorted(files):
                    f = os.path.join(root, n)
                    if os.path.isfile(f) and not os.path.islink(f):
                        with open(f, "rb") as fh:
                            scan.blob(os.path.relpath(f, scan.base), fh.read())


def scan_staged(scan, repo):
    names = git(repo, "diff", "--cached", "--name-only", "--diff-filter=ACMRT", "-z").split(b"\0")
    names = [n.decode("utf-8", "surrogateescape") for n in names if n]
    if not names:
        return
    entries = {}
    for line in git(repo, "ls-files", "-s", "-z", "--", *names).split(b"\0"):
        if line:
            meta, path = line.split(b"\t", 1)
            mode, sha = meta.split()[:2]
            if mode != b"160000":
                entries[path.decode("utf-8", "surrogateescape")] = sha.decode()
    objs = cat_objects(repo, list(entries.values()))
    for path, sha in sorted(entries.items()):
        if sha in objs:
            scan.blob(path, objs[sha][1], sha)


def range_args(repo, local, remote):
    if ZERO.match(local):
        return None
    if ZERO.match(remote) or subprocess.run(["git", "-C", repo, "cat-file", "-e", remote + "^{commit}"],
                                            capture_output=True).returncode != 0:
        return [local, "--not", "--remotes"]  # a new branch: everything no remote-tracking ref has
    return [local, "^" + remote]


def scan_commits(scan, repo, revs, identities, check_ids, check_text):
    commits = [c for c in git(repo, "rev-list", *revs).decode().split() if c]
    scan.commits += len(commits)
    if commits:
        fmt = "%H%x00%an%x00%ae%x00%cn%x00%ce%x00%B%x1e"
        out = git(repo, "log", "--no-walk=unsorted", "--format=" + fmt, *commits).decode("utf-8", "replace")
        for rec in out.split("\x1e"):
            rec = rec.lstrip("\n")
            if not rec:
                continue
            sha, an, ae, cn, ce, msg = rec.split("\0", 5)
            if check_ids:
                if ae.strip().lower() not in identities:
                    scan.add(f"commit {sha[:12]}: author email not allowed")
                if ce.strip().lower() not in identities:
                    scan.add(f"commit {sha[:12]}: committer email not allowed")
            if check_text and scan.ids:
                head = sorted(set(scan.ids.hits(" ".join([an, ae, cn, ce]))))
                for n, _cat in head:
                    scan.add(f"commit {sha[:12]}: header: leak #{n}")
                found = set()
                for line in msg.split("\n"):
                    found.update(scan.ids.hits(line))
                for n, _cat in sorted(found):
                    scan.add(f"commit {sha[:12]}: message: leak #{n}")
    if check_text and scan.ids and commits:
        # Every path a commit adds (renames and copies included): rev-list --objects names each object once, under
        # its first path, and leaves out objects the remote has, so a rename of a known blob shows no new name there.
        out = git(repo, "log", "--stdin", "--no-walk=unsorted", "--format=%x01%H", "--name-only", "-z",
                  "--no-renames", "--diff-filter=A", "--diff-merges=first-parent",
                  inp=("\n".join(commits) + "\n").encode()).decode("utf-8", "surrogateescape")
        for rec in out.split("\x01"):
            sha, _, names = rec.partition("\n")
            found = set()
            for path in names.split("\0"):
                if path.strip("\n"):
                    found.update(n for n, _cat in scan.path_hit(path.strip("\n")))
            for n in sorted(found):
                scan.add(f"commit {sha[:12]}: path name: leak #{n}")
    objs = []
    for line in git(repo, "rev-list", "--objects", *revs).decode("utf-8", "surrogateescape").split("\n"):
        if " " in line:
            sha, path = line.split(" ", 1)
            objs.append((sha, path))
    found = cat_objects(repo, [s for s, _ in objs])
    for sha, path in objs:
        if sha not in found:
            continue
        typ, data = found[sha]
        if typ == "blob" and check_text:
            scan.blob(path, data, sha)
        elif typ == "tag":
            scan.tag(sha, data, identities, check_ids, check_text)


def required(repo, flag):
    if flag or os.environ.get("HOMMY_LEAK_REQUIRED") in ("1", "true"):
        return True
    if repo:
        r = subprocess.run(["git", "-C", repo, "config", "--bool", "hommy.leak.required"], capture_output=True,
                           text=True)
        return r.stdout.strip() == "true"
    return False


def main(argv):
    opts = {"list": None, "identities": None, "images": None, "required": False, "ids": True, "only_ids": False}
    args = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--list", "--identities", "--images") and i + 1 < len(argv):
            opts[a[2:]] = argv[i + 1]
            i += 2
            continue
        if a == "--required":
            opts["required"] = True
        elif a == "--no-identities":
            opts["ids"] = False
        elif a == "--identities-only":
            opts["only_ids"] = True
        elif a in ("-h", "--help"):
            print(__doc__)
            return 0
        else:
            args.append(a)
        i += 1
    if args[:1] == ["--self-test"]:
        return self_test()
    mode = args[0] if args and args[0] in ("--staged", "--push", "--history") else "paths"
    if mode == "--push":
        if len(args) < 3:
            print("usage: --push <local> <remote> [<repo>]", file=sys.stderr)
            return 2
        repo_arg = args[3] if len(args) > 3 else "."
    elif mode in ("--staged", "--history"):
        repo_arg = args[1] if len(args) > 1 else "."
    else:
        repo_arg = None
    paths = args if mode == "paths" else []
    if mode == "paths" and not paths:
        paths = ["."]
    repo = toplevel(repo_arg) if repo_arg else toplevel(paths[0] if os.path.isdir(paths[0]) else
                                                      os.path.dirname(os.path.abspath(paths[0])))
    if repo_arg and not repo and mode != "--history":
        print(f"leak scan: not a git repository: {repo_arg}", file=sys.stderr)
        return 2
    if mode == "--history" and not repo:  # a bare mirror
        repo = os.path.abspath(repo_arg)
    base = repo or os.getcwd()

    ids = None
    lst = opts["list"] or os.environ.get("LEAK_IDENTIFIERS") or DEFAULT_LIST
    if not opts["only_ids"]:
        if os.path.exists(lst):
            ids = Identifiers.load(lst)
        elif required(repo, opts["required"]):
            print("leak scan: no identifiers list (set LEAK_IDENTIFIERS); this clone requires one", file=sys.stderr)
            return 2
        else:
            print("leak scan: no identifiers list: identifier scan skipped (identities and images still run)",
                  file=sys.stderr)
    identities = load_identities(opts["identities"] or (repo and os.path.join(repo, ".github", "commit-identities")))
    images = load_images(opts["images"] or (repo and os.path.join(repo, "scripts", "leak", "images.txt")))
    scan = Scan(ids, images, base)
    check_text = not opts["only_ids"]
    check_ids = opts["ids"] or opts["only_ids"]
    try:
        if mode == "paths":
            if check_text:
                scan_paths(scan, paths)
        elif mode == "--staged":
            if check_text:
                scan_staged(scan, repo)
        elif mode == "--push":
            revs = range_args(repo, args[1], args[2])
            if revs:
                scan_commits(scan, repo, revs, identities, check_ids, check_text)
        else:
            scan_commits(scan, repo, ["--all"], identities, check_ids, check_text)
    except RuntimeError as e:
        print(f"leak scan: {e}", file=sys.stderr)
        return 2
    for h in scan.hits:
        print(h)
    idn = "skipped" if ids is None else str(len(ids))
    if opts["only_ids"]:
        idn = "not checked"
    print(f"leak scan: {scan.files} files, {scan.commits} commits, {len(scan.hits)} hits (identifiers: {idn})")
    return 1 if scan.hits else 0


# ---- self-test ---------------------------------------------------------------------------------------------------

def _word():
    return "".join(["bré", "vi", "nox"])  # NFD on purpose; a synthetic word no real list holds


def _phone():
    return "".join(["+1", " 555", " 010", " 0187"])


def _short():
    return "".join(["86", ".", "04", "51"])  # 7 chars, 6 digits, outside the phone category


def self_test():
    tmp = tempfile.mkdtemp(prefix="hm-leak-selftest-")
    word = unicodedata.normalize("NFC", _word())
    phone_digits = re.sub(r"\D", "", _phone())
    me = os.path.abspath(__file__)
    failures, cases = [], 0
    lst = os.path.join(tmp, "list.txt")
    with open(lst, "w", encoding="utf-8") as f:
        f.write("# self-test list\ncanary\t" + word + "\nphone\t" + _phone() + "\naddress\t" + _short() + "\n")
    env = dict(os.environ, HOMMY_LEAK_REQUIRED="1")
    env.pop("LEAK_IDENTIFIERS", None)

    def run(*args, cwd=None):
        r = subprocess.run([sys.executable, me, "--list", lst, *args], capture_output=True, text=True, env=env,
                           cwd=cwd)
        out = r.stdout + r.stderr
        return r.returncode, out

    def probe(name, content, want_hit, filename="probe.txt", binary=False):
        nonlocal cases
        cases += 1
        d = os.path.join(tmp, "c%02d" % cases)
        os.makedirs(d)
        p = os.path.join(d, filename)
        with open(p, "wb" if binary else "w", **({} if binary else {"encoding": "utf-8"})) as f:
            f.write(content)
        rc, out = run(d)
        if (rc == 1) != want_hit or rc not in (0, 1) or word.casefold() in norm(out):
            failures.append(name)
        shutil.rmtree(d)
        return out

    def office(xml_runs):
        b = io.BytesIO()
        with zipfile.ZipFile(b, "w") as z:
            z.writestr("[Content_Types].xml", "<Types/>")
            z.writestr("word/document.xml", "<w:document><w:body><w:p>" + "".join(
                "<w:r><w:t>%s</w:t></w:r>" % r for r in xml_runs) + "</w:p></w:body></w:document>")
        return b.getvalue()

    def zipped(name, text):
        b = io.BytesIO()
        with zipfile.ZipFile(b, "w") as z:
            z.writestr(name, text)
        return b.getvalue()

    try:
        probe("word lower", "x " + word.lower() + " y\n", True)
        probe("word upper", "x " + word.upper() + " y\n", True)
        probe("word mixed", "x " + word[:3].upper() + word[3:] + " y\n", True)
        probe("word NFD", "x " + unicodedata.normalize("NFD", word) + " y\n", True)
        probe("word glued to letters", "x a" + word + "z y\n", False)
        probe("word glued to digits", "x 7" + word + " y\n", False)
        probe("word between punctuation", "(" + word + ").\n", True)
        probe("word after underscore", "key_" + word + "_x\n", True)
        out = probe("word in a path name", "nothing here\n", True, filename="notes-" + word + ".md")
        if word in out or "notes-" in out:
            failures.append("path name printed")
        cases += 1
        probe("phone spaces", "call " + _phone() + "\n", True)
        probe("phone dashes", "call %s-%s-%s-%s\n" % (phone_digits[0], phone_digits[1:4], phone_digits[4:7],
                                                       phone_digits[7:]), True)
        probe("phone parentheses", "call +1 (%s) %s-%s\n" % (phone_digits[1:4], phone_digits[4:7], phone_digits[7:]),
              True)
        probe("phone plus", "call +" + phone_digits + "\n", True)
        changed = phone_digits[:-1] + str((int(phone_digits[-1]) + 1) % 10)
        probe("phone one digit changed", "call +" + changed + "\n", False)
        probe("short phone-shaped entry alone", "code " + _short() + ", ok\n", True)
        probe("inside .zip", zipped("inner/notes.txt", "hello " + word + "\n"), True, "bundle.zip", True)
        probe("inside .docx split across runs", office(["hello ", word[:3], word[3:], " there"]), True,
              "doc.docx", True)
        probe("inside .gz", gzip.compress(("hello " + word + "\n").encode()), True, "notes.txt.gz", True)
        db = os.path.join(tmp, "probe.sqlite")
        con = sqlite3.connect(db)
        con.execute("create table t (v text)")
        con.execute("insert into t values (?)", ("hello " + word + " there",))
        con.commit()
        con.close()
        with open(db, "rb") as f:
            data = f.read()
        os.remove(db)
        probe("inside SQLite", data, True, "store.sqlite", True)
        probe("unlisted .png", b"\x89PNG\r\n\x1a\n" + b"\0" * 32, True, "pic.png", True)
        # Commits: a message hit and a foreign author email, through --push like the pre-push hook.
        repo = os.path.join(tmp, "repo")
        os.makedirs(os.path.join(repo, ".github"))
        genv = dict(env, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", GIT_AUTHOR_NAME="Probe",
                    GIT_COMMITTER_NAME="Probe", GIT_AUTHOR_EMAIL="ok@example.invalid",
                    GIT_COMMITTER_EMAIL="ok@example.invalid")
        subprocess.run(["git", "init", "-q", repo], env=genv, check=True)
        with open(os.path.join(repo, ".github", "commit-identities"), "w") as f:
            f.write("ok@example.invalid\n")
        with open(os.path.join(repo, "a.txt"), "w") as f:
            f.write("clean\n")
        subprocess.run(["git", "-C", repo, "add", "-A"], env=genv, check=True)
        subprocess.run(["git", "-C", repo, "commit", "-q", "-m", "fix " + word], env=genv, check=True)
        cases += 1
        rc, out = run("--push", "HEAD", "0" * 40, repo)
        if rc != 1 or ": message: leak #2" not in out or word in out:
            failures.append("commit message")
        genv2 = dict(genv, GIT_AUTHOR_EMAIL="someone@example.invalid")
        subprocess.run(["git", "-C", repo, "commit", "-q", "--allow-empty", "-m", "clean"], env=genv2, check=True)
        cases += 1
        rc, out = run("--identities-only", "--push", "HEAD", "HEAD~1", repo)
        if rc != 1 or "author email not allowed" not in out or "committer email not allowed" in out:
            failures.append("foreign author email")
        # gitleaks: a bot-token-shaped string, built at run time.
        cases += 1
        gl = os.environ.get("HOMMY_GITLEAKS") or shutil.which("gitleaks")
        if not gl:
            failures.append("gitleaks token (gitleaks missing: scripts/tools.sh ensure gitleaks)")
        else:
            import random
            rnd = random.SystemRandom()
            alnum = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
            tok = "%d:AA%s" % (rnd.randint(10 ** 8, 10 ** 9 - 1), "".join(rnd.choice(alnum) for _ in range(33)))
            d = os.path.join(tmp, "gl")
            os.makedirs(d)
            with open(os.path.join(d, "bot.env"), "w") as f:
                f.write("TELEGRAM_BOT_TOKEN=" + tok + "\n")
            r = subprocess.run([gl, "dir", "--no-banner", "--redact", d], capture_output=True, text=True)
            if r.returncode != 1 or tok in r.stdout + r.stderr:
                failures.append("gitleaks token")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    left = os.path.exists(tmp)
    print(f"self-test: temp dir {tmp} ({'LEFT BEHIND' if left else 'removed'})")
    if failures or left:
        print("self-test: FAIL: " + ", ".join(failures + (["temp dir left"] if left else [])))
        return 1
    print(f"self-test: PASS ({cases} cases)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

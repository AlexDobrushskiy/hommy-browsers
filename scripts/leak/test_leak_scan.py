"""Unit tests for scripts/leak/leak_scan.py (python3 -m unittest discover -s scripts/leak). Synthetic identifiers only."""
import gzip
import io
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import leak_scan as ls  # noqa: E402

SCANNER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "leak_scan.py")
WORD = "qu" + "ixotl"
CYR = "жук"  # a Cyrillic word
ACC = "zéphyrine"  # NFD on purpose
PHONE = "+44 20 7946 " + "0321"  # an Ofcom drama number


def ids():
    return ls.Identifiers([(3, "name", WORD), (4, "name", CYR), (5, "place", ACC), (7, "phone", PHONE),
                           (8, "canary", "+1 555 010 " + "0144")])


def nums(hits):
    return sorted({n for n, _ in hits})


class Boundary(unittest.TestCase):
    def test_latin(self):
        i = ids()
        self.assertEqual(nums(i.hits("a " + WORD + " b")), [3])
        self.assertEqual(nums(i.hits(WORD.upper())), [3])
        self.assertEqual(nums(i.hits("x" + WORD)), [])
        self.assertEqual(nums(i.hits(WORD + "s")), [])

    def test_digits_underscore_punctuation(self):
        i = ids()
        self.assertEqual(nums(i.hits("7" + WORD)), [])
        self.assertEqual(nums(i.hits(WORD + "9")), [])
        self.assertEqual(nums(i.hits("_" + WORD + "_")), [3])
        self.assertEqual(nums(i.hits("<" + WORD + ">,")), [3])
        self.assertEqual(nums(i.hits("a/" + WORD + ".md")), [3])

    def test_cyrillic(self):
        i = ids()
        self.assertEqual(nums(i.hits("привет " + CYR.upper() + "!")), [4])
        self.assertEqual(nums(i.hits(CYR + "а")), [])  # glued to a Cyrillic letter

    def test_nfc_nfd(self):
        i = ids()
        import unicodedata
        self.assertEqual(nums(i.hits(unicodedata.normalize("NFC", ACC))), [5])
        self.assertEqual(nums(i.hits(unicodedata.normalize("NFD", ACC).upper())), [5])

    def test_overlapping_candidates(self):
        i = ls.Identifiers([(1, "x", "ab"), (2, "x", "abc")])
        self.assertEqual(nums(i.hits("abc")), [2])
        self.assertEqual(nums(i.hits("ab cd")), [1])

    def test_prefix_entry_when_longer_fails_boundary(self):
        # review r1 F4: the longest candidate failing its boundary hid a shorter one at the same position
        i = ls.Identifiers([(1, "host", "nimbo"), (2, "host", "nimbo.lan")])
        self.assertEqual(nums(i.hits("nimbo.landing")), [1])
        self.assertEqual(nums(i.hits("nimbo.lan")), [1, 2])


class Phone(unittest.TestCase):
    def test_separators(self):
        i = ids()
        for s in ("+44 20 7946 0321", "+44-20-7946-0321", "(020) 7946 0321", "+442079460321", "0044 20 7946 0321"):
            self.assertEqual(nums(i.hits("call " + s)), [7], s)

    def test_last_nine(self):
        self.assertEqual(nums(ids().hits("tel 079460321")), [7])  # the identifier's last 9 digits
        self.assertEqual(nums(ids().hits("tel 79460321")), [])  # 8 of them are not enough
        self.assertEqual(nums(ids().hits("tel 0 20 7946 0321")), [7])

    def test_one_digit_changed(self):
        self.assertEqual(nums(ids().hits("+44 20 7946 0322")), [])

    def test_phone_shaped_identifier_in_other_category(self):
        self.assertEqual(nums(ids().hits("ring 1-555-010-0144")), [8])

    def test_short_phone_shaped_identifier_in_other_category(self):
        # review r1 F1: a 7-char / 6-digit entry outside `phone` matched nothing on its own
        i = ls.Identifiers([(2, "address", "42.1234"), (3, "address", "421234")])
        self.assertEqual(nums(i.hits("x 42.1234 y")), [2])  # #3 (6 chars) is not phone-shaped: a word only
        self.assertEqual(nums(i.hits("x 42.1234, y")), [2])
        self.assertEqual(nums(i.hits("zip 421234/")), [2, 3])
        self.assertEqual(nums(i.hits("zip 421235")), [])

    def test_short_phone_entry(self):
        i = ls.Identifiers([(2, "phone", "12 34 56")])
        self.assertEqual(nums(i.hits("tel 123456.")), [2])
        self.assertEqual(nums(i.hits("tel 12-34-56")), [2])


class Extraction(unittest.TestCase):
    def texts(self, name, data):
        return " ".join(t for _, _, t in ls.texts_of(name, data))

    def test_utf8_lines(self):
        out = ls.texts_of("a.txt", b"one\ntwo " + WORD.encode() + b"\n")
        self.assertIn(("a.txt", 2, "two " + WORD), out)

    def test_zip_member_and_name(self):
        b = io.BytesIO()
        with zipfile.ZipFile(b, "w") as z:
            z.writestr(WORD + "/n.txt", "x")
            z.writestr("in.txt", "y " + WORD)
        t = self.texts("a.zip", b.getvalue())
        self.assertEqual(t.count(WORD), 2)

    def test_docx_runs_joined_and_spaced(self):
        b = io.BytesIO()
        with zipfile.ZipFile(b, "w") as z:
            z.writestr("word/document.xml", "<w:p><w:r><w:t>%s</w:t></w:r><w:r><w:t>%s</w:t></w:r></w:p>"
                       "<w:p><w:r><w:t>next</w:t></w:r></w:p>" % (WORD[:2], WORD[2:]))
        out = [t for _, _, t in ls.texts_of("a.docx", b.getvalue())]
        self.assertTrue(any(WORD in t for t in out))
        self.assertTrue(any(nums(ids().hits(t)) == [3] for t in out))

    def test_gz(self):
        self.assertIn(WORD, self.texts("a.log.gz", gzip.compress(b"x " + WORD.encode())))

    def test_printable_runs(self):
        data = b"\x00\x01" + WORD.encode() + b"\x00ab\x00" + "café bar".encode()
        runs = ls.printable_runs(data)
        self.assertIn(WORD, runs)
        self.assertNotIn("ab", runs)
        self.assertIn("café bar", runs)


def git(repo, *args, env=None):
    return subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True, text=True, env=env).stdout


class Repo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.lst = os.path.join(self.dir, "list.txt")
        with open(self.lst, "w") as f:
            f.write("# test\n\nname\t" + WORD + "\n")  # WORD is #3
        self.repo = os.path.join(self.dir, "r")
        self.env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
                        GIT_AUTHOR_NAME="T", GIT_COMMITTER_NAME="T", GIT_AUTHOR_EMAIL="ok@example.invalid",
                        GIT_COMMITTER_EMAIL="ok@example.invalid")
        self.env.pop("LEAK_IDENTIFIERS", None)
        self.env.pop("HOMMY_LEAK_REQUIRED", None)
        subprocess.run(["git", "init", "-q", "-b", "main", self.repo], check=True, env=self.env)
        os.makedirs(os.path.join(self.repo, ".github"))
        self.write(".github/commit-identities", "ok@example.invalid\n")
        self.commit("init")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rel, data):
        p = os.path.join(self.repo, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb" if isinstance(data, bytes) else "w") as f:
            f.write(data)

    def commit(self, msg, env=None):
        git(self.repo, "add", "-A", env=self.env)
        git(self.repo, "commit", "-q", "--allow-empty", "-m", msg, env=env or self.env)
        return git(self.repo, "rev-parse", "HEAD", env=self.env).strip()

    def scan(self, *args, lst=True):
        a = [sys.executable, SCANNER] + (["--list", self.lst] if lst else []) + list(args)
        r = subprocess.run(a, capture_output=True, text=True, env=self.env, cwd=self.repo)
        return r.returncode, r.stdout, r.stderr

    def test_worktree_hit_and_summary(self):
        self.write("docs/a.md", "line\nhello " + WORD + "\n")
        rc, out, _ = self.scan(".")
        self.assertEqual(rc, 1)
        self.assertIn("docs/a.md:2: leak #3 (name)", out)
        self.assertNotIn(WORD, out)
        self.assertRegex(out.splitlines()[-1], r"^leak scan: \d+ files, 0 commits, 1 hits \(identifiers: 1\)$")

    def test_ignored_files_skipped(self):
        self.write(".gitignore", "secret/\n")
        self.write("secret/a.txt", WORD)
        rc, out, _ = self.scan(".")
        self.assertEqual(rc, 0, out)

    def test_path_name_hit_hides_path(self):
        self.write("docs/" + WORD + "-notes.md", "x " + WORD + "\n")
        rc, out, _ = self.scan(".")
        self.assertEqual(rc, 1)
        self.assertRegex(out, r"blob [0-9a-f]{12}: path name: leak #3")
        self.assertRegex(out, r"blob [0-9a-f]{12}:1: leak #3")
        self.assertNotIn(WORD, out)
        self.assertNotIn("-notes", out)

    def test_images_list(self):
        png = b"\x89PNG\r\n\x1a\n" + b"\0" * 16
        self.write("img/a.png", png)
        rc, out, _ = self.scan(".")
        self.assertEqual(rc, 1)
        self.assertIn("img/a.png: image not reviewed", out)
        import hashlib
        self.write("scripts/leak/images.txt", "# reviewed\nimg/a.png %s\n" % hashlib.sha256(png).hexdigest())
        rc, out, _ = self.scan(".")
        self.assertEqual(rc, 0, out)

    def test_staged(self):
        self.write("a.txt", WORD)
        self.write("b.txt", WORD)
        git(self.repo, "add", "a.txt", env=self.env)
        rc, out, _ = self.scan("--staged", ".")
        self.assertEqual(rc, 1)
        self.assertIn("a.txt:1: leak #3", out)
        self.assertNotIn("b.txt", out)

    def test_push_new_and_existing_branch(self):
        base = git(self.repo, "rev-parse", "HEAD", env=self.env).strip()
        self.write("a.txt", "clean\n")
        c1 = self.commit("feat: " + WORD)
        self.write("b.txt", "x " + WORD + "\n")
        c2 = self.commit("clean")
        # existing branch: only commits after the remote's sha
        rc, out, _ = self.scan("--push", c2, c1, ".")
        self.assertEqual(rc, 1)
        self.assertIn("b.txt:1: leak #3", out)
        self.assertNotIn("message", out)
        self.assertIn("1 commits", out)
        # new branch: every commit no remote-tracking ref has (none here: all three)
        rc, out, _ = self.scan("--push", c2, "0" * 40, ".")
        self.assertIn("commit %s: message: leak #3" % c1[:12], out)
        self.assertIn("3 commits", out)
        # a remote-tracking ref limits it
        git(self.repo, "update-ref", "refs/remotes/origin/main", base, env=self.env)
        rc, out, _ = self.scan("--push", c2, "0" * 40, ".")
        self.assertIn("2 commits", out)
        # a delete sends nothing
        rc, out, _ = self.scan("--push", "0" * 40, c2, ".")
        self.assertEqual(rc, 0)

    def test_header_and_identities(self):
        env = dict(self.env, GIT_AUTHOR_EMAIL=WORD + "@example.invalid", GIT_AUTHOR_NAME="Probe")
        c = self.commit("clean", env=env)
        rc, out, _ = self.scan("--push", c, c + "~1", ".")
        self.assertIn("commit %s: header: leak #3" % c[:12], out)
        self.assertIn("commit %s: author email not allowed" % c[:12], out)
        self.assertNotIn("committer email not allowed", out)
        self.assertNotIn(WORD, out)
        rc, out, _ = self.scan("--no-identities", "--push", c, c + "~1", ".")
        self.assertNotIn("email not allowed", out)
        rc, out, _ = self.scan("--identities-only", "--push", c, c + "~1", ".")
        self.assertIn("author email not allowed", out)
        self.assertNotIn("header", out)

    def test_history_covers_all_refs(self):
        git(self.repo, "switch", "-q", "-c", "side", env=self.env)
        self.write("s.txt", WORD)
        self.commit("side")
        git(self.repo, "switch", "-q", "main", env=self.env)
        git(self.repo, "update-ref", "refs/pull/1/head", "side", env=self.env)
        git(self.repo, "branch", "-q", "-D", "side", env=self.env)
        rc, out, _ = self.scan("--history", ".")
        self.assertEqual(rc, 1)
        self.assertIn("s.txt:1: leak #3", out)

    def test_push_rename_of_known_blob(self):
        # review r1 F2: a rename to a leaking name sends no new blob, only trees
        self.write("docs/notes.md", "clean\n")
        self.write("dir/a.txt", "clean a\n")
        base = self.commit("clean")
        git(self.repo, "update-ref", "refs/remotes/origin/main", base, env=self.env)
        git(self.repo, "mv", "docs/notes.md", "docs/notes-" + WORD + ".md", env=self.env)
        self.commit("rename a file")
        git(self.repo, "mv", "dir", WORD + "-dir", env=self.env)
        c = self.commit("rename a directory")
        rc, out, _ = self.scan("--push", c, base, ".")
        self.assertEqual(rc, 1, out)
        self.assertEqual(out.count("path name: leak #3"), 2, out)
        self.assertNotIn(WORD, out)
        self.assertNotIn("notes-", out)
        rc, out, _ = self.scan("--push", c, "0" * 40, ".")
        self.assertEqual(rc, 1, out)
        rc, out, _ = self.scan("--history", ".")
        self.assertEqual(rc, 1, out)
        self.assertIn("path name: leak #3", out)

    def test_annotated_tag(self):
        # review r1 F6: a pushed annotated tag's message and tagger
        c = git(self.repo, "rev-parse", "HEAD", env=self.env).strip()
        git(self.repo, "tag", "-a", "v0.0.1", "-m", "for " + WORD, env=self.env)
        tag = git(self.repo, "rev-parse", "v0.0.1", env=self.env).strip()
        git(self.repo, "update-ref", "refs/remotes/origin/main", c, env=self.env)
        rc, out, _ = self.scan("--push", tag, "0" * 40, ".")
        self.assertEqual(rc, 1, out)
        self.assertIn("tag %s: message: leak #3" % tag[:12], out)
        self.assertNotIn(WORD, out)
        env = dict(self.env, GIT_COMMITTER_EMAIL="someone@example.invalid")
        git(self.repo, "tag", "-a", "v0.0.2", "-m", "clean", env=env)
        tag2 = git(self.repo, "rev-parse", "v0.0.2", env=self.env).strip()
        rc, out, _ = self.scan("--identities-only", "--push", tag2, "0" * 40, ".")
        self.assertEqual(rc, 1, out)
        self.assertIn("tag %s: tagger email not allowed" % tag2[:12], out)
        rc, out, _ = self.scan("--history", ".")
        self.assertIn("tag %s: message: leak #3" % tag[:12], out)

    def test_oversized_blob_is_a_hit(self):
        # review r1 F5: a blob over the size cap was counted as scanned with no notice
        old = ls.MAX_BLOB
        ls.MAX_BLOB = 16
        try:
            scan = ls.Scan(ls.Identifiers([(3, "name", WORD)]), {}, self.repo)
            scan.blob("big.bin", b"x" * 17)
            self.assertEqual(scan.hits, ["big.bin: too large to scan (> 16 bytes)"])
        finally:
            ls.MAX_BLOB = old

    def test_required_without_list(self):
        missing = os.path.join(self.dir, "nope.txt")
        rc, out, err = self.scan("--list", missing, ".", lst=False)
        self.assertEqual(rc, 0)
        self.assertIn("identifiers: skipped", out)
        git(self.repo, "config", "hommy.leak.required", "true", env=self.env)
        rc, out, err = self.scan("--list", missing, ".", lst=False)
        self.assertEqual(rc, 2)
        self.assertIn("requires", err)


if __name__ == "__main__":
    unittest.main()

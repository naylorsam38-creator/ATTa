"""v117 (checklist A): .env is data, never shell — strict parser, and no code path may execute an env file.

    cd ATTa && python3 -m unittest tests.test_v117_envfile -v

Every attack is checked two ways: the parser refuses the file, AND a marker file the attack would create is
never created, whichever consumer (check / get / run / bash run) reads it."""
import os, re, subprocess, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUNDLE = HERE.parent
DEP = BUNDLE / "04-deployment"
sys.path.insert(0, str(DEP))
import envfile  # noqa: E402

ENVPY = str(DEP / "envfile.py")


def envpy(*args, env=None):
    return subprocess.run([sys.executable, ENVPY, *args], capture_output=True, text=True, env=env, timeout=30)


class Tmp(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory(prefix="atta-env-")
        self.t = Path(self._d.name)

    def tearDown(self):
        self._d.cleanup()

    def write(self, text, name=".env"):
        f = self.t / name
        f.write_bytes(text.encode() if isinstance(text, str) else text)
        return f


class Injection(Tmp):
    """Each line would run `touch MARKER` if the file were sourced by a shell. None may run, all are refused."""
    VECTORS = {
        "backticks": "APP_BUILDER_USER=`touch {m}`",
        "backticks_in_double_quotes": 'APP_BUILDER_USER="`touch {m}`"',
        "command_substitution": "APP_BUILDER_USER=$(touch {m})",
        "command_substitution_quoted": 'APP_BUILDER_USER="$(touch {m})"',
        "command_substitution_single_quoted": "APP_BUILDER_USER='$(touch {m})'",
        "parameter_expansion": "APP_BUILDER_USER=${{HOME}}",
        "semicolon_command": "APP_BUILDER_USER=a; touch {m}",
        "and_command": "APP_BUILDER_USER=a && touch {m}",
        "pipe_command": "APP_BUILDER_USER=a | touch {m}",
        "bare_command": "touch {m}",
        "export_prefix": "export APP_BUILDER_USER=a",
        "space_before_equals": "APP_BUILDER_USER =a",
        "lowercase_name": "app_builder_user=a",
        "dashed_name": "APP-BUILDER=a",
        "backslash_escape": "APP_BUILDER_USER=a\\nb",
        "line_continuation": "APP_BUILDER_USER=a\\\ntouch {m}",
        "carriage_return_injection": "APP_BUILDER_USER=a\rtouch {m}",
        "vertical_tab_line_break": "APP_BUILDER_USER=a\x0btouch {m}",
        "form_feed_line_break": "APP_BUILDER_USER=a\x0ctouch {m}",
        "nul_byte": "APP_BUILDER_USER=a\x00;touch {m}",
        "unterminated_quote": 'APP_BUILDER_USER="a\ntouch {m}',
        "text_after_quote": 'APP_BUILDER_USER="a"; touch {m}',
        "stray_quote": "APP_BUILDER_USER=a'b",
        "ld_preload": "LD_PRELOAD={m}.so",
        "pythonpath": "PYTHONPATH={m}",
        "path_override": "PATH={m}:/usr/bin",
        "bash_env": "BASH_ENV={m}",
        "node_options": "NODE_OPTIONS=--require={m}",
        "bash_function_export": "BASH_FUNC_ls%%=() {{ touch {m}; }}",
    }

    def test_every_vector_refused_by_every_consumer_and_never_runs(self):
        for i, (label, vec) in enumerate(self.VECTORS.items()):
            with self.subTest(label):
                m = self.t / f"pwned-{i}"
                f = self.write("APP_BUILDER_ROOT=/srv/app-builder\n" + vec.format(m=m) + "\n", f"env{i}")
                with self.assertRaises(envfile.EnvFileError):
                    envfile.load(str(f))
                r = envpy("check", str(f))
                self.assertEqual(r.returncode, 1, (label, r.stderr))
                self.assertRegex(r.stderr, r"line \d", label)
                self.assertNotEqual(envpy("get", str(f), "APP_BUILDER_USER").returncode, 0, label)
                self.assertNotEqual(envpy("run", str(f), "--all", "--", "true").returncode, 0, label)
                self.assertFalse(m.exists(), f"{label}: the attack ran")

    def test_the_old_way_really_ran_code(self):
        # Why this matters: sourcing a file runs it.
        m = self.t / "pwned-old"
        f = self.write(f"APP_BUILDER_USER=$(touch {m})\n")
        subprocess.run(["bash", "-c", f'set -a; . "{f}"; set +a'], capture_output=True)
        self.assertTrue(m.exists())

    def test_errors_never_echo_values(self):
        for text in ("ANTHROPIC_API_KEY=sk-secret-value$x\n", 'ANTHROPIC_API_KEY="sk-secret-value\n',
                     "ANTHROPIC_API_KEY=sk-secret-value'x\n", "ANTHROPIC_API_KEY=sk-secret-value #c\n"):
            with self.assertRaises(envfile.EnvFileError) as c:
                envfile.parse(text)
            self.assertNotIn("sk-secret-value", str(c.exception))


class Semantics(Tmp):
    def test_duplicates_are_refused_with_both_lines_named(self):
        with self.assertRaises(envfile.EnvFileError) as c:
            envfile.parse("APP_BUILDER_PORT=8787\n# comment\nAPP_BUILDER_PORT=9999\n")
        self.assertIn("line 3", str(c.exception))
        self.assertIn("line 1", str(c.exception))

    def test_duplicate_unknown_names_are_refused_too(self):
        with self.assertRaises(envfile.EnvFileError):
            envfile.parse("SOMETHING=1\nSOMETHING=2\n")

    def test_spaces_and_quotes(self):
        vals, warns = envfile.parse(
            "APP_BUILDER_A='p a s s'\n"
            'APP_BUILDER_B="it\'s fine"\n'
            "APP_BUILDER_C='say \"hi\"'\n"
            'APP_BUILDER_D="  padded  "\n'
            "APP_BUILDER_E=\n"
            'APP_BUILDER_F=""\n'
            "APP_BUILDER_G='a,b c.d/e:f@g=h&x=1?y'\n"
            "APP_BUILDER_H='#not-a-comment'\n"
            "APP_BUILDER_I=\"tab\there\"\n"
            "  APP_BUILDER_J=indented  \n")
        self.assertEqual(vals, {"APP_BUILDER_A": "p a s s", "APP_BUILDER_B": "it's fine",
                                "APP_BUILDER_C": 'say "hi"', "APP_BUILDER_D": "  padded  ", "APP_BUILDER_E": "",
                                "APP_BUILDER_F": "", "APP_BUILDER_G": "a,b c.d/e:f@g=h&x=1?y",
                                "APP_BUILDER_H": "#not-a-comment", "APP_BUILDER_I": "tab\there",
                                "APP_BUILDER_J": "indented"})
        self.assertEqual(warns, [])

    def test_ambiguous_unquoted_values_must_be_quoted(self):
        for text in ("APP_BUILDER_A=a #comment?\n", "APP_BUILDER_A= leading\n", "APP_BUILDER_A=a\tb\n",
                     "APP_BUILDER_A=~/x\n", "APP_BUILDER_A=a&b\n", "APP_BUILDER_A=a*\n", "APP_BUILDER_A=a>b\n",
                     "APP_BUILDER_A='it's'\n", 'APP_BUILDER_A="x"y"\n'):
            with self.subTest(text=text), self.assertRaises(envfile.EnvFileError):
                envfile.parse(text)

    SAMPLES = ["APP_BUILDER_A=plain-value_1.2/3:4,5@6%7+8=9\n", "APP_BUILDER_A='a b; c && d | e > f'\n",
               'APP_BUILDER_A="it\'s (fine) #really"\n', "APP_BUILDER_A='say \"hi\" & bye'\n",
               'APP_BUILDER_A="  lead and trail  "\n', "APP_BUILDER_A=\n", "APP_BUILDER_A=''\n",
               "APP_BUILDER_A='~/not-home *.glob ?'\n", 'APP_BUILDER_A="tab\there!"\n']

    def test_every_accepted_file_is_also_inert_under_sh(self):
        # The invariant behind the rules: whatever this parser accepts means the SAME thing to a shell and runs
        # nothing, so even a stray `. .env` somewhere could not execute code or read a different value.
        for i, text in enumerate(self.SAMPLES):
            for sh in ("sh", "bash"):
                with self.subTest(sample=text, shell=sh):
                    vals, _ = envfile.parse(text)
                    f = self.write(text, f"s{i}")
                    r = subprocess.run([sh, "-c", f'. "{f}"; printf %s "$APP_BUILDER_A"'], capture_output=True,
                                       text=True, cwd=self.t, timeout=10, env={"PATH": os.environ["PATH"]})
                    self.assertEqual((r.returncode, r.stderr), (0, ""))
                    self.assertEqual(r.stdout, vals["APP_BUILDER_A"])
                    self.assertEqual(sorted(p.name for p in self.t.iterdir()), sorted(f"s{j}" for j in range(i + 1)))

    def test_crlf_and_bom_refused(self):
        with self.assertRaises(envfile.EnvFileError):
            envfile.parse("APP_BUILDER_A=1\r\nAPP_BUILDER_B=2\r\n")
        with self.assertRaises(envfile.EnvFileError):
            envfile.parse("﻿APP_BUILDER_A=1\n")

    def test_unknown_names_warned_not_exported(self):
        f = self.write("APP_BUILDER_USER=boss\nSOMETHING_ELSE=1\n")
        r = envpy("run", str(f), "--all", "--", "env", env={"PATH": os.environ["PATH"]})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("APP_BUILDER_USER=boss", r.stdout)
        self.assertNotIn("SOMETHING_ELSE", r.stdout)
        self.assertIn("SOMETHING_ELSE is not a setting", r.stderr)

    def test_run_keys_only_sets_named_keys(self):
        f = self.write("APP_BUILDER_USER=boss\nANTHROPIC_API_KEY=sk-x\n")
        r = envpy("run", str(f), "--keys", "APP_BUILDER_USER", "--", "env", env={"PATH": os.environ["PATH"]})
        self.assertIn("APP_BUILDER_USER=boss", r.stdout)
        self.assertNotIn("ANTHROPIC_API_KEY", r.stdout)

    def test_get_with_default(self):
        f = self.write("APP_BUILDER_PORT=9001\nAPP_BUILDER_DOMAIN=\n")
        self.assertEqual(envpy("get", str(f), "APP_BUILDER_PORT", "8787").stdout.strip(), "9001")
        self.assertEqual(envpy("get", str(f), "APP_BUILDER_DOMAIN", "_").stdout.strip(), "_")
        self.assertEqual(envpy("get", str(f), "APP_BUILDER_HOST", "127.0.0.1").stdout.strip(), "127.0.0.1")
        self.assertEqual(envpy("get", str(f), "bad name").returncode, 2)

    def test_generated_templates_are_valid(self):
        # The two .env files ATTa writes itself (server bootstrap and local launcher) must pass their own check.
        boot = (DEP / "bootstrap-lib.sh").read_text()
        self.assertIn("atta_write_default_env", boot)
        r = subprocess.run(["bash", "-c", f'. "{DEP}/bootstrap-lib.sh"; atta_write_default_env "{self.t}/srv.env" '
                                          f'/srv/app-builder'], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        vals, warns = envfile.load(str(self.t / "srv.env"))
        self.assertEqual(warns, [])
        self.assertGreaterEqual(len(vals["APP_BUILDER_SESSION_SECRET"]), 48)
        self.assertEqual(oct((self.t / "srv.env").stat().st_mode & 0o777), "0o600")


class NothingExecutesEnvFiles(unittest.TestCase):
    """Static scan of every shell script in the bundle: no sourcing of an env file, no eval, no set -a, and no
    unquoted heredoc that would run a backtick or $( ) while WRITING a file (v114.2's fresh-install bug)."""
    SHELL_FILES = sorted({*BUNDLE.rglob("*.sh"), BUNDLE / "run"} - set(BUNDLE.glob("tests/**/*")))

    def _code_lines(self, f):
        for n, line in enumerate(f.read_text().splitlines(), 1):
            s = line.strip()
            if s.startswith("#"):
                continue
            yield n, line

    def test_shell_files_found(self):
        names = {f.name for f in self.SHELL_FILES}
        self.assertTrue({"run", "bootstrap.sh", "bootstrap-lib.sh"} <= names, names)

    def test_no_source_eval_or_set_a(self):
        bad = re.compile(r"(^|[;&|({\s])(source|\.)\s+\S*env"          # . "$ROOT/.env", source ./.env ...
                         r"|(^|[;&|({\s])eval\s"                          # eval anything
                         r"|(^|[;&|({\s])set\s+-[a-z]*a")                 # set -a (auto-export for sourcing)
        for f in self.SHELL_FILES:
            for n, line in self._code_lines(f):
                with self.subTest(file=f.name, line=n):
                    self.assertIsNone(bad.search(line), f"{f}:{n}: {line.strip()}")

    def test_unquoted_heredocs_contain_no_command_substitution(self):
        # In `cat <<EOF` (unquoted) bash runs `...` and $(...) inside the body. Bodies that need variables may use
        # $NAME / ${NAME}; anything that runs a command must use a quoted heredoc (<<'EOF') or printf.
        opener = re.compile(r"<<-?\s*([A-Za-z_][A-Za-z0-9_]*)\s*$|<<-?\s*([A-Za-z_][A-Za-z0-9_]*)\b")
        for f in self.SHELL_FILES:
            lines = f.read_text().splitlines()
            i = 0
            while i < len(lines):
                m = opener.search(lines[i]) if not lines[i].lstrip().startswith("#") else None
                if m and "<<<" not in lines[i]:
                    word = m.group(1) or m.group(2)
                    j = i + 1
                    while j < len(lines) and lines[j].strip() != word:
                        with self.subTest(file=f.name, line=j + 1):
                            self.assertIsNone(re.search(r"`|\$\(", lines[j]),
                                              f"{f}:{j + 1}: unquoted heredoc <<{word} runs a command: {lines[j]}")
                        j += 1
                    i = j
                i += 1

    def test_env_files_only_read_through_envfile(self):
        # Reading a single value with sed/grep would disagree with envfile on quotes and duplicates.
        for f in self.SHELL_FILES:
            for n, line in self._code_lines(f):
                with self.subTest(file=f.name, line=n):
                    self.assertIsNone(re.search(r"(sed|grep|awk|cut)\b[^|]*\.env\b", line), f"{f}:{n}: {line.strip()}")


if __name__ == "__main__":
    unittest.main()

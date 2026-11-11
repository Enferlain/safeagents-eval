"""Data-free, local fixtures for strict managed-run monitoring."""
import importlib.util
import os
import fcntl
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import patch


class RunFixture:
    """A held flock and synthetic /proc; never starts or inspects a real proxy."""
    def __init__(self, root, options=None, route="safeagents-glm"):
        self.root = Path(root)
        self.run = self.root / "run"
        self.run.mkdir(mode=0o700)
        self.cwd = self.root / "work"
        self.cwd.mkdir()
        self.proc = self.root / "proc"
        self.pid = 4242
        self.p = self.proc / str(self.pid)
        (self.p / "fd").mkdir(parents=True)
        (self.p / "fdinfo").mkdir()
        (self.p / "net").mkdir()
        (self.p / "ns").mkdir()
        (self.proc / "self/ns").mkdir(parents=True)
        for path in (self.p / "ns/net", self.proc / "self/ns/net"):
            path.symlink_to("net:[123]")
        self.p.joinpath("root").symlink_to("/")
        self.p.joinpath("cwd").symlink_to(self.cwd)
        self.venv = self.root / "venv"
        (self.venv / "bin").mkdir(parents=True)
        self.python = self.venv / "bin/python"
        self.python.symlink_to(sys.executable)
        self.p.joinpath("exe").symlink_to(Path(sys.executable).resolve())
        self.site = self.venv / "lib/python3.14/site-packages"
        self.package = self.site / "safeagents"
        self.package.mkdir(parents=True)
        installed_spec = importlib.util.find_spec("safeagents")
        assert installed_spec is not None and installed_spec.origin is not None
        installed = Path(installed_spec.origin).parent
        for source in sorted(installed.glob("*.py")):
            shutil.copyfile(source, self.package / source.name)
        dist = self.site / "safeagents-0.0.1.dist-info"
        dist.mkdir()
        (dist / "METADATA").write_text("Name: safeagents\nVersion: 0.0.1\n")
        self.started = int(time.time()) - 30
        self.boot = self.started - 10000
        self.proc.joinpath("stat").write_text(f"btime {self.boot}\n")
        self.start_ticks = 10000 * os.sysconf("SC_CLK_TCK")
        self.write_stat()
        self.p.joinpath("status").write_text(f"Uid:\t{os.getuid()}\t{os.getuid()}\t{os.getuid()}\t{os.getuid()}\n")
        self.options = options if options is not None else ["--log-only", "--judge-url", "http://127.0.0.1:18873", "--journal", "journal.jsonl"]
        self.listen = "127.0.0.1:" + ("8788" if route == "safeagents-glm" else "8789")
        self.upstream = "https://api.z.ai" if route == "safeagents-glm" else "http://localhost:8080"
        self.meta = {"id": 1, "pid": self.pid, "upstream": self.upstream, "listen": self.listen,
                     "options": self.options, "started": self.started + .1, "log": str(self.run / "1.log")}
        self.write_meta()
        self.write_cmdline()
        self.p.joinpath("environ").write_bytes(f"SAFEAGENTS_LOCK={self.run / '1.lock'}\0TZ=UTC\0HOME={self.root}\0".encode())
        self.lock = open(self.run / "1.lock", "w")
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.p.joinpath("fd/3").symlink_to(self.run / "1.lock")
        self.p.joinpath("fdinfo/3").write_text(f"lock:\t1: FLOCK  ADVISORY  WRITE {self.pid} 00:00:1 0 EOF\n")
        self.p.joinpath("fd/4").symlink_to("socket:[67890]")
        port = int(self.listen.rsplit(":", 1)[1])
        (self.p / "net/tcp").write_text(f"  sl local_address rem_address st tx_queue rx_queue tr tm->when retrnsmt uid timeout inode\n  0: 0100007F:{port:04X} 00000000:0000 0A 00000000:00000000 00:00000000 00000000 {os.getuid()} 0 67890 1\n")
        (self.p / "net/tcp6").write_text("header\n")
        self.log = self.run / "1.log"
        # Mirror cli.py's setup-string construction so plugin/no-scan/no-journal
        # options produce the banner the real proxy would emit.
        def value_of(name):
            for index, token in enumerate(self.options):
                if token == name and index + 1 < len(self.options):
                    return self.options[index + 1]
                if token.startswith(name + "="):
                    return token.split("=", 1)[1]
            return None
        plugins, index = [], 0
        while index < len(self.options):
            token = self.options[index]
            if token == "--plugin" and index + 1 < len(self.options):
                plugins.append(self.options[index + 1]); index += 2; continue
            if token.startswith("--plugin="):
                plugins.append(token.split("=", 1)[1])
            index += 1
        parts = ["log-only" if "--log-only" in self.options else "block"]
        if "--no-scan" not in self.options:
            parts.append("atif-scan" + "".join(f" + {plugin}" for plugin in plugins))
        if value_of("--judge-url"):
            parts.append(f"judge {value_of('--judge-url')}")
        if "--no-journal" not in self.options:
            parts.append(f"journal {value_of('--journal') or 'journal.jsonl'}")
        self.banner = f"[safeagents] listening on http://{self.listen} -> {self.upstream} (" + ", ".join(parts) + ")\n"
        self.log.write_text(self.banner)
        for number in (1, 2):
            self.p.joinpath(f"fd/{number}").symlink_to(self.log)
        self.journal = self.cwd / "journal.jsonl"

    def write_stat(self):
        self.p.joinpath("stat").write_text(f"{self.pid} (python worker) S " + " ".join(["0"] * 18 + [str(self.start_ticks)] + ["0"] * 5))

    def write_meta(self):
        self.run.joinpath("1.json").write_text(json.dumps(self.meta))

    def write_cmdline(self):
        self.p.joinpath("cmdline").write_bytes("\0".join([str(self.python), "-m", "safeagents.cli", self.upstream, self.listen, *self.options, ""]).encode())

    def call(self, verdict="PASS", call_id=None, what="fixture-only", why="", when=None, tool="terminal"):
        timestamp = time.strftime("%H:%M:%S", time.gmtime(when if when is not None else self.started + 1))
        ref = f"#{call_id}" if call_id else ""
        return f"{timestamp}  {ref:<5}  {verdict:<5}  {tool:<12}  {what}  {why}".rstrip() + "\n"

    def close(self):
        self.lock.close()

    def journal_call(self, verdict, call_id, judge=None, reason=None, signals=None, when=None, arguments=None):
        signals = signals if signals is not None else [{"id": "fixture.signal", "severity": "low", "title": "fake"}]
        arguments = arguments if arguments is not None else '{"command":"SECRET_FAKE_ARGUMENT"}'
        when = self.started + call_id if when is None else when
        entry = {"id": call_id, "time": when + .1, "verdict": verdict, "tool": "terminal",
                 "arguments": arguments, "reason": reason, "signals": signals, "judge": judge,
                 "model": "fixture", "task": "SECRET_FAKE_TASK"}
        with self.journal.open("a") as stream:
            stream.write(json.dumps(entry) + "\n")
        from safeagents.monitor import Monitor
        from rich.console import Console
        from unittest.mock import patch
        import io
        buffer = io.StringIO()
        with patch("safeagents.monitor.time.strftime", return_value=time.strftime("%H:%M:%S", time.gmtime(when))):
            Monitor(Console(file=buffer, force_terminal=False)).record("terminal", arguments, verdict, reason,
                [f"{s['id']} ({s['severity']})" for s in signals], judge, call_id)
        return buffer.getvalue()

MODULE = Path(__file__).parent / "security-plugin" / "managed_monitor.py"


def load_monitor():
    spec = importlib.util.spec_from_file_location("managed_monitor_test_target", MODULE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ManagedMonitorTests(unittest.TestCase):
    def setUp(self):
        home = tempfile.TemporaryDirectory(prefix="managed-monitor-home-", dir=os.environ.get("TMPDIR"))
        self.addCleanup(home.cleanup)
        environment = patch.dict(os.environ, {"HOME": home.name, "HERMES_HOME": str(Path(home.name) / ".hermes"), "HERMES_PROFILE": "default"})
        environment.start()
        self.addCleanup(environment.stop)

    def fixture(self, **kwargs):
        tmp = tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR"))
        self.addCleanup(tmp.cleanup)
        fixture = RunFixture(tmp.name, **kwargs)
        self.addCleanup(fixture.close)
        return fixture

    def test_verified_pass_has_run_scoped_totals_and_no_raw_content(self):
        f = self.fixture()
        f.log.write_text(f.banner + f.call(what="SECRET_FAKE_ARGUMENT"))
        result = load_monitor().ManagedMonitor(f.run, f.proc).snapshot("safeagents-glm")
        self.assertEqual(result["state"], "verified")
        self.assertEqual(result["guard"], {"reviewed": 1, "passed": 1, "warn": 0, "flagged": 0, "blocked": 0})
        self.assertEqual(result["run"]["id"], 1)
        self.assertEqual(result["run"]["listen"], f.listen)
        self.assertEqual(result["run"]["started_at"], f.meta["started"])
        self.assertTrue(result["run"]["generation"])
        self.assertEqual(result["policy"], {"mode": "log-only", "scanner_enabled": True,
            "judge_enabled": True, "journal_enabled": True, "block_severity": "high", "judge_failure": "fail-open"})
        self.assertEqual(result["judge"]["consulted"], 0)
        self.assertIsNone(result["judge"]["last_consultation"])
        self.assertEqual(result["records"], [{"call_id": None, "verdict": "passed", "judge_outcome": None, "time": f.started + 1}])
        self.assertEqual(result["journal_path"], str(f.journal))
        self.assertNotIn("SECRET", json.dumps(result))
        self.assertEqual((f.run / "1.json").stat().st_mode & 0o777, 0o644)

    def test_untrusted_files_and_directories_are_refused_without_writes(self):
        for target, change in (("run", "mode"), ("1.json", "mode"), ("1.lock", "mode"),
                               ("1.log", "mode"), ("1.json", "link"), ("1.lock", "link"),
                               ("1.log", "link"), ("run", "link")):
            with self.subTest(target=target, change=change):
                f = self.fixture()
                path = f.run if target == "run" else f.run / target
                if change == "mode":
                    path.chmod(0o777 if path.is_dir() else 0o666)
                else:
                    saved = path.with_name(path.name + "-saved")
                    path.rename(saved)
                    path.symlink_to(saved, target_is_directory=saved.is_dir())
                before = {str(p): (p.lstat().st_mode, p.lstat().st_size, p.lstat().st_mtime_ns)
                          for p in f.root.rglob("*") if not p.is_symlink()}
                result = load_monitor().ManagedMonitor(f.run, f.proc).snapshot("safeagents-glm")
                self.assertEqual(result["state"], "unverified")
                self.assertIsNone(result["guard"]["reviewed"])
                after = {str(p): (p.lstat().st_mode, p.lstat().st_size, p.lstat().st_mtime_ns)
                         for p in f.root.rglob("*") if not p.is_symlink()}
                self.assertEqual(before, after)

    def test_process_identity_requires_current_owner_lock_listener_and_start(self):
        changes = {
            "pid-reused": lambda f: (setattr(f, "start_ticks", f.start_ticks + 1000), f.write_stat()),
            "dead": lambda f: f.p.joinpath("stat").write_text(f.p.joinpath("stat").read_text().replace(") S ", ") Z ")),
            "uid": lambda f: f.p.joinpath("status").write_text("Uid:\t99999\t99999\t99999\t99999\n"),
            "no-listener": lambda f: f.p.joinpath("net/tcp").write_text("header\n"),
            "other-socket": lambda f: f.p.joinpath("fd/4").unlink(),
            "wrong-port": lambda f: f.p.joinpath("net/tcp").write_text(f.p.joinpath("net/tcp").read_text().replace(":2254", ":2255")),
            "lock-not-process": lambda f: f.p.joinpath("fd/3").unlink(),
            "wrong-lock-owner": lambda f: f.p.joinpath("fdinfo/3").write_text("lock: 1: FLOCK ADVISORY WRITE 99999 00:00:1 0 EOF\n"),
            "wrong-env": lambda f: f.p.joinpath("environ").write_bytes(b"SAFEAGENTS_LOCK=/not/ours\0"),
            "import-override": lambda f: (
                shutil.copytree(f.package, f.root / "shadow" / "safeagents"),
                (f.root / "shadow" / "safeagents" / "runs.py").write_text("# overridden\n"),
                f.p.joinpath("environ").write_bytes(f.p.joinpath("environ").read_bytes()
                                                    + b"PYTHONPATH=" + os.fsencode(f.root / "shadow") + b"\0")),
            "unsafe-cwd": lambda f: f.cwd.chmod(0o777),
            "wrong-exe": lambda f: (f.p.joinpath("exe").unlink(), f.p.joinpath("exe").symlink_to("/not/python")),
            "wrong-namespace": lambda f: (f.p.joinpath("ns/net").unlink(), f.p.joinpath("ns/net").symlink_to("net:[999]")),
        }
        for name, change in changes.items():
            with self.subTest(name=name):
                f = self.fixture()
                change(f)
                result = load_monitor().ManagedMonitor(f.run, f.proc).snapshot("safeagents-glm")
                self.assertEqual(result["state"], "unverified")
                self.assertIsNone(result["run"])

    def test_repeated_plugins_keep_order_and_accept_file_path_specs(self):
        f = self.fixture(options=["--log-only", "--plugin", "my_pack:checks",
                                  "--plugin", "./detectors/pack.py:factory", "--plugin=third:mk",
                                  "--judge-url", "http://127.0.0.1:18873", "--journal", "journal.jsonl"])
        f.log.write_text(f.banner + f.call())
        self.assertIn("atif-scan + my_pack:checks + ./detectors/pack.py:factory + third:mk", f.banner)
        result = load_monitor().ManagedMonitor(f.run, f.proc).snapshot("safeagents-glm")
        self.assertEqual(result["state"], "verified")
        self.assertTrue(result["policy"]["scanner_enabled"])

    def test_malformed_plugin_spec_never_verifies(self):
        for spec in ("noseparator", ":factory", "module:", "with space:factory"):
            with self.subTest(spec=spec):
                f = self.fixture(options=["--log-only", "--plugin", spec,
                                          "--judge-url", "http://127.0.0.1:18873", "--journal", "journal.jsonl"])
                f.log.write_text(f.banner + f.call())
                self.assertNotEqual(load_monitor().ManagedMonitor(f.run, f.proc).snapshot("safeagents-glm")["state"],
                                    "verified")

    def with_pythonpath(self, f, path):
        f.p.joinpath("environ").write_bytes(f.p.joinpath("environ").read_bytes()
                                            + b"PYTHONPATH=" + os.fsencode(path) + b"\0")

    def test_runtime_error_block_counts_records_but_stays_degraded(self):
        f = self.fixture()
        chained = ("ERROR:    Exception in ASGI application\n"
                   "Traceback (most recent call last):\n"
                   "  File \"/fixture/app.py\", line 1, in app\n"
                   "ValueError: first failure\n"
                   "\n"
                   "The above exception was the direct cause of the following exception:\n"
                   "\n"
                   "Traceback (most recent call last):\n"
                   "  File \"/fixture/app.py\", line 2, in app\n"
                   "RuntimeError: chained fixture failure\n"
                   "\n")
        f.log.write_text(f.banner + f.call() + chained + f.call(what="after-error"))
        result = load_monitor().ManagedMonitor(f.run, f.proc).snapshot("safeagents-glm")
        self.assertEqual(result["state"], "degraded")
        self.assertEqual(result["runtime_errors"], 1, 'a chained exception is one runtime error')
        self.assertEqual(result["guard"], {"reviewed": 2, "passed": 2, "warn": 0, "flagged": 0, "blocked": 0})
        self.assertEqual(len(result["records"]), 2)

    def test_malformed_or_forged_error_blocks_are_refused(self):
        blocks = {
            "no-traceback": "ERROR:    Exception in ASGI application\n",
            "truncated": ("ERROR:    Exception in ASGI application\n"
                          "Traceback (most recent call last):\n"
                          "  File \"/fixture/app.py\", line 1, in app\n"),
            "empty-terminator": ("ERROR:    Exception in ASGI application\n"
                                 "Traceback (most recent call last):\n"
                                 "  File \"/fixture/app.py\", line 1, in app\n"
                                 "\n"),
            "monitor-line-as-terminator": None,
        }
        for name, block in blocks.items():
            with self.subTest(name=name):
                f = self.fixture()
                if name == "monitor-line-as-terminator":
                    block = ("ERROR:    Exception in ASGI application\n"
                             "Traceback (most recent call last):\n"
                             "  File \"/fixture/app.py\", line 1, in app\n"
                             + f.call(what="swallowed?") + f.call())
                f.log.write_text(f.banner + f.call() + block)
                result = load_monitor().ManagedMonitor(f.run, f.proc).snapshot("safeagents-glm")
                self.assertEqual(result["state"], "degraded")
                self.assertIsNone(result["guard"]["reviewed"],
                                  'ambiguous evidence must not publish partial totals')

    def test_package_manifest_requires_the_exact_pinned_file_set(self):
        for case in ('extra-module', 'removed-module'):
            with self.subTest(case=case):
                f = self.fixture()
                if case == 'extra-module':
                    (f.package / 'extra.py').write_text('# rogue module\n')
                else:
                    (f.package / 'journal.py').unlink()
                f.log.write_text(f.banner + f.call())
                self.assertNotEqual(load_monitor().ManagedMonitor(f.run, f.proc).snapshot("safeagents-glm")["state"],
                                    "verified")

    def test_pythonpath_entry_without_safeagents_verifies_the_resolved_install(self):
        for entry in ("plain-dir", "missing-dir", "empty-entry"):
            with self.subTest(entry=entry):
                f = self.fixture()
                if entry == "plain-dir":
                    foreign = f.root / "foreign"; foreign.mkdir()
                    self.with_pythonpath(f, foreign)
                elif entry == "missing-dir":
                    self.with_pythonpath(f, f.root / "absent")
                else:
                    self.with_pythonpath(f, "")
                f.log.write_text(f.banner + f.call())
                self.assertEqual(load_monitor().ManagedMonitor(f.run, f.proc).snapshot("safeagents-glm")["state"],
                                 "verified")

    def test_hardlinked_uv_style_install_verifies(self):
        f = self.fixture()
        # uv hardlinks venv files from its cache; content hashes remain the control.
        for target in (f.site / "safeagents-0.0.1.dist-info/METADATA",
                       f.package / "guard.py", f.package / "monitor.py"):
            os.link(target, f.root / target.name)
        f.log.write_text(f.banner + f.call())
        self.assertEqual(load_monitor().ManagedMonitor(f.run, f.proc).snapshot("safeagents-glm")["state"],
                         "verified")

    def test_pythonpath_shadow_is_resolved_and_verified_by_content(self):
        for tamper in (False, True):
            with self.subTest(tamper=tamper):
                f = self.fixture()
                shadow = f.root / "shadow"
                shutil.copytree(f.package, shadow / "safeagents")
                shutil.copytree(f.site / "safeagents-0.0.1.dist-info", shadow / "safeagents-0.0.1.dist-info")
                if tamper:
                    (shadow / "safeagents" / "guard.py").write_text("# different source\n")
                self.with_pythonpath(f, shadow)
                f.log.write_text(f.banner + f.call())
                state = load_monitor().ManagedMonitor(f.run, f.proc).snapshot("safeagents-glm")["state"]
                self.assertEqual(state, "unverified" if tamper else "verified")

    def test_pythonpath_file_entry_is_refused(self):
        f = self.fixture()
        marker = f.root / "marker.txt"; marker.write_text("x")
        self.with_pythonpath(f, marker)
        f.log.write_text(f.banner + f.call())
        self.assertNotEqual(load_monitor().ManagedMonitor(f.run, f.proc).snapshot("safeagents-glm")["state"],
                            "verified")

    def test_pythonhome_is_still_refused(self):
        f = self.fixture()
        f.p.joinpath("environ").write_bytes(f.p.joinpath("environ").read_bytes() + b"PYTHONHOME=/elsewhere\0")
        f.log.write_text(f.banner + f.call())
        self.assertEqual(load_monitor().ManagedMonitor(f.run, f.proc).snapshot("safeagents-glm")["state"],
                         "unverified")

    def test_cwd_source_checkout_resolves_first_and_verifies_by_content(self):
        for tamper in (False, True):
            with self.subTest(tamper=tamper):
                f = self.fixture()
                shutil.copytree(f.package, f.cwd / "safeagents")
                shutil.copytree(f.site / "safeagents-0.0.1.dist-info", f.cwd / "safeagents-0.0.1.dist-info")
                if tamper:
                    (f.cwd / "safeagents" / "monitor.py").write_text("# different source\n")
                f.log.write_text(f.banner + f.call())
                state = load_monitor().ManagedMonitor(f.run, f.proc).snapshot("safeagents-glm")["state"]
                self.assertEqual(state, "unverified" if tamper else "verified")

    def test_unsupported_install_or_malformed_run_never_claims_verification(self):
        for case in ("version", "monitor-source", "guard-source", "bool-id", "bool-pid", "nan-start", "negative-start", "options-string", "null-record", "duplicate-field"):
            with self.subTest(case=case):
                f = self.fixture()
                if case == "version":
                    (f.site / "safeagents-0.0.1.dist-info/METADATA").write_text("Name: safeagents\nVersion: 9.9\n")
                elif case.endswith("source"):
                    (f.package / ("monitor.py" if case == "monitor-source" else "guard.py")).write_text("# incompatible parser\n")
                elif case == "null-record":
                    (f.run / "1.json").write_text("null")
                elif case == "duplicate-field":
                    (f.run / "1.json").write_text(json.dumps(f.meta)[:-1] + ', "id": 1}')
                else:
                    key, value = {"bool-id": ("id", True), "bool-pid": ("pid", True),
                                  "nan-start": ("started", float("nan")), "negative-start": ("started", -1),
                                  "options-string": ("options", "--log-only")}[case]
                    f.meta[key] = value
                    f.write_meta()
                result = load_monitor().ManagedMonitor(f.run, f.proc).snapshot("safeagents-glm")
                self.assertEqual(result["state"], "unverified")

    def test_real_monitor_outcomes_are_corroborated_not_journal_lifetime_totals(self):
        f = self.fixture()
        f.journal_call("flagged", 900, reason="historical", when=f.started - 100)
        lines = [f.call(when=f.started),
                 f.journal_call("suspicious", 901, judge="allow: expected", when=f.started + 1),
                 f.journal_call("flagged", 902, judge="block: unwanted", reason="fixture.signal (low); judge: unwanted", when=f.started + 2),
                 f.journal_call("suspicious", 903, judge="error: offline", when=f.started + 3),
                 f.journal_call("flagged", 904, reason="deterministic (judge allow: not an annotation)", signals=[], when=f.started + 4)]
        f.log.write_text(f.banner + "".join(lines))
        result = load_monitor().ManagedMonitor(f.run, f.proc).snapshot("safeagents-glm")
        self.assertEqual(result["state"], "verified")
        self.assertEqual(result["guard"], {"reviewed": 5, "passed": 1, "warn": 2, "flagged": 2, "blocked": 0})
        self.assertEqual(result["judge"], {"consulted": 3, "allow": 1, "block": 1, "errors": 1,
            "last_consultation": {"time": f.started + 3, "outcome": "error"},
            "last_success": {"time": f.started + 2, "outcome": "block"},
            "last_failure": {"time": f.started + 3, "outcome": "error"}})
        self.assertEqual([r["judge_outcome"] for r in result["records"]], [None, "allow", "block", "error", None])
        self.assertNotIn("SECRET", json.dumps(result))
        self.assertNotIn("historical", json.dumps(result))

    def test_partial_or_incompatible_logs_do_not_return_fake_totals(self):
        for case in ("partial-pass", "partial-banner", "banner-policy-mismatch", "duplicate-id", "unknown-line", "traceback", "oversize", "control-character"):
            with self.subTest(case=case):
                f = self.fixture()
                text = f.banner + f.call()
                if case == "partial-pass":
                    text = text.rstrip("\n")
                elif case == "partial-banner":
                    text = f.banner.rstrip("\n")
                elif case == "banner-policy-mismatch":
                    text = text.replace("(log-only,", "(block,")
                elif case == "duplicate-id":
                    line = f.journal_call("suspicious", 1, judge="allow: expected")
                    text = f.banner + line + line
                elif case == "unknown-line":
                    text += "unsupported mysterious output\n"
                elif case == "traceback":
                    # A traceback without the uvicorn ERROR header is not a
                    # recognizable runtime-error record: refuse it. (Well-formed
                    # ERROR blocks are covered by the runtime-error tests.)
                    text += "Traceback (most recent call last):\nPermissionError: journal append failed\n"
                elif case == "oversize":
                    text += "x" * (8 * 1024 * 1024)
                elif case == "control-character":
                    text = f.banner + f.call(what="forged\x1b[0m")
                f.log.write_text(text)
                result = load_monitor().ManagedMonitor(f.run, f.proc).snapshot("safeagents-glm")
                self.assertEqual(result["state"], "degraded")
                self.assertIsNone(result["guard"]["reviewed"])
                self.assertIsNone(result["judge"]["consulted"])
                self.assertEqual(result["records"], [])

    def test_restart_and_log_races_never_publish_a_mixed_snapshot(self):
        from unittest.mock import patch
        for case in ("metadata-rewrite", "pid-reuse", "truncate", "replace-inode", "release-lock"):
            with self.subTest(case=case):
                f = self.fixture()
                f.log.write_text(f.banner + f.call())
                module = load_monitor()
                monitor = module.ManagedMonitor(f.run, f.proc)
                original = monitor._parse
                def race(*args):
                    parsed = original(*args)
                    if case == "metadata-rewrite":
                        f.meta["started"] += 1
                        f.write_meta()
                    elif case == "pid-reuse":
                        f.start_ticks += 1000
                        f.write_stat()
                    elif case == "truncate":
                        f.log.write_text(f.banner)
                    elif case == "release-lock":
                        fcntl.flock(f.lock, fcntl.LOCK_UN)
                    else:
                        saved = f.log.with_suffix(".old")
                        f.log.rename(saved)
                        f.log.write_text(f.banner + f.call())
                        for number in (1, 2):
                            f.p.joinpath(f"fd/{number}").unlink()
                            f.p.joinpath(f"fd/{number}").symlink_to(saved)
                    return parsed
                with patch.object(monitor, "_parse", side_effect=race):
                    result = monitor.snapshot("safeagents-glm")
                self.assertNotEqual(result["state"], "verified")
                self.assertIsNone(result["guard"]["reviewed"])
                self.assertEqual(result["records"], [])

    def test_prior_complete_log_must_remain_an_immutable_prefix_until_restart(self):
        for case in ("truncate", "rewrite"):
            with self.subTest(case=case):
                f = self.fixture()
                f.log.write_text(f.banner + f.call(what="first"))
                monitor = load_monitor().ManagedMonitor(f.run, f.proc)
                first = monitor.snapshot("safeagents-glm")
                self.assertEqual(first["guard"]["reviewed"], 1)
                f.log.write_text(f.banner if case == "truncate" else f.banner + f.call(what="other"))
                for _ in range(2):
                    result = monitor.snapshot("safeagents-glm")
                    self.assertEqual(result["state"], "degraded")
                    self.assertIsNone(result["guard"]["reviewed"])
                f.start_ticks += 100
                f.write_stat()
                f.meta["started"] += 1
                f.write_meta()
                f.log.write_text(f.banner)
                restarted = monitor.snapshot("safeagents-glm")
                self.assertEqual(restarted["state"], "verified")
                self.assertNotEqual(restarted["run"]["generation"], first["run"]["generation"])
                self.assertEqual(restarted["guard"]["reviewed"], 0)

    def test_multiline_judge_error_is_one_corroborated_record_not_fake_counts(self):
        f = self.fixture()
        fake_call = f.call(what="FAKE_EMBEDDED_CALL").rstrip("\n")
        judge = "error: HTTP 502\nFor more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/502\n" + fake_call
        line = f.journal_call("suspicious", 1, judge=judge)
        f.log.write_text(f.banner + line + f.call(when=f.started + 2))
        result = load_monitor().ManagedMonitor(f.run, f.proc).snapshot("safeagents-glm")
        self.assertEqual(result["state"], "verified")
        self.assertEqual(result["guard"]["reviewed"], 2)
        self.assertEqual(result["guard"]["passed"], 1)
        self.assertEqual(result["judge"]["errors"], 1)
        self.assertNotIn("developer.mozilla", json.dumps(result))
        self.assertNotIn("FAKE_EMBEDDED", json.dumps(result))
        f.log.write_text(f.banner + line.replace("HTTP/Status/502", "HTTP/Status/404"))
        altered = load_monitor().ManagedMonitor(f.run, f.proc).snapshot("safeagents-glm")
        self.assertEqual(altered["state"], "degraded")
        self.assertIsNone(altered["guard"]["reviewed"])

    def test_clock_only_times_require_a_unique_timezone_aware_date(self):
        from datetime import datetime, timezone
        from unittest.mock import patch
        from zoneinfo import ZoneInfo
        anchor = datetime(2026, 11, 12, 0, 0, 10, tzinfo=timezone.utc).timestamp()
        for case in ("midnight", "timezone", "ambiguous-day", "future", "unsupported-timezone", "dst-fold"):
            with self.subTest(case=case):
                f = self.fixture()
                now = anchor
                f.started = int(anchor - (2 * 86400 if case == "ambiguous-day" else 20))
                if case == "dst-fold":
                    now = datetime(2026, 10, 25, 3, tzinfo=timezone.utc).timestamp()
                    f.started = int(now - 3 * 3600)
                f.boot = f.started - 10000
                f.proc.joinpath("stat").write_text(f"btime {f.boot}\n")
                f.meta["started"] = f.started + .1
                f.write_meta()
                stamps = [f.started + 1, now - 1] if case == "midnight" else [now - 1]
                zone = "UTC"
                if case in ("timezone", "dst-fold"):
                    zone = "Europe/Berlin"
                if case == "unsupported-timezone":
                    zone = "unsupported-zone"
                f.p.joinpath("environ").write_bytes(f"SAFEAGENTS_LOCK={f.run / '1.lock'}\0TZ={zone}\0".encode())
                if case == "future":
                    stamps = [now + 60]
                if case == "dst-fold":
                    stamps = [f.started + 1800]
                text = f.banner
                for stamp in stamps:
                    clock = datetime.fromtimestamp(stamp, ZoneInfo("UTC" if case == "unsupported-timezone" else zone)).strftime("%H:%M:%S")
                    text += clock + f.call(when=stamp)[8:]
                f.log.write_text(text)
                module = load_monitor()
                with patch.object(module.time, "time", return_value=now):
                    result = module.ManagedMonitor(f.run, f.proc).snapshot("safeagents-glm")
                if case in ("midnight", "timezone"):
                    self.assertEqual(result["state"], "verified")
                    self.assertEqual([r["time"] for r in result["records"]], stamps)
                else:
                    self.assertEqual(result["state"], "degraded")
                    self.assertIsNone(result["guard"]["reviewed"])

    def test_policy_options_follow_cli_equals_form_on_explicit_local_route(self):
        f = self.fixture(route="safeagents-local", options=["--log-only", "--no-scan", "--no-journal",
            "--block-severity=medium", "--judge-fail-closed"])
        f.log.write_text(f"[safeagents] listening on http://{f.listen} -> {f.upstream} (log-only)\n" + f.call())
        monitor = load_monitor().ManagedMonitor(f.run, f.proc)
        result = monitor.snapshot("safeagents-local")
        self.assertEqual(result["state"], "verified")
        self.assertEqual(result["policy"], {"mode": "log-only", "scanner_enabled": False,
            "judge_enabled": False, "journal_enabled": False, "block_severity": "medium", "judge_failure": "fail-closed"})
        self.assertIsNone(result["journal_path"])
        self.assertEqual(monitor.snapshot("safeagents-glm")["state"], "unverified")
        self.assertEqual(monitor.snapshot(None)["state"], "unverified")

    def test_journal_cannot_claim_impossible_judge_routing_or_reason(self):
        for case in ("severe-consult", "missing-consult", "forged-reason"):
            with self.subTest(case=case):
                f = self.fixture()
                if case == "severe-consult":
                    line = f.journal_call("suspicious", 1, judge="allow: expected", signals=[{"id": "fixture.high", "severity": "high"}])
                elif case == "missing-consult":
                    line = f.journal_call("suspicious", 1)
                else:
                    line = f.journal_call("flagged", 1, judge="block: unwanted", reason="not the Guard reason")
                f.log.write_text(f.banner + line)
                result = load_monitor().ManagedMonitor(f.run, f.proc).snapshot("safeagents-glm")
                self.assertEqual(result["state"], "degraded")
                self.assertIsNone(result["judge"]["consulted"])
                self.assertIsNone(result["guard"]["reviewed"])

    def test_journal_linkage_uses_target_home_and_refuses_unsafe_targets(self):
        for case in ("default-home", "symlink", "writable", "fifo", "ancestor-link"):
            with self.subTest(case=case):
                f = self.fixture(options=["--log-only"] if case == "default-home" else None)
                expected = str(f.root / ".safeagents/calls.jsonl")
                if case == "default-home":
                    f.p.joinpath("environ").write_bytes(f"SAFEAGENTS_LOCK={f.run / '1.lock'}\0TZ=UTC\0HOME={f.root}\0".encode())
                    f.log.write_text(f"[safeagents] listening on http://{f.listen} -> {f.upstream} (log-only, atif-scan, journal {expected})\n" + f.call())
                elif case == "symlink":
                    f.journal.symlink_to(f.root / "absent-private-target")
                elif case == "writable":
                    f.journal.write_text("")
                    f.journal.chmod(0o666)
                elif case == "fifo":
                    os.mkfifo(f.journal)
                else:
                    saved = f.cwd.with_name("saved-work")
                    f.cwd.rename(saved)
                    f.cwd.symlink_to(saved)
                result = load_monitor().ManagedMonitor(f.run, f.proc).snapshot("safeagents-glm")
                if case == "default-home":
                    self.assertEqual(result["state"], "verified")
                    self.assertEqual(result["journal_path"], expected)
                    self.assertFalse((f.root / ".safeagents").exists())
                else:
                    self.assertNotEqual(result["state"], "verified")
                    self.assertIsNone(result["journal_path"])

    def test_malformed_proc_and_nested_metadata_fail_closed_without_exceptions(self):
        for case in ("empty-stat", "wrong-stat-pid", "nested-json", "duplicate-environment"):
            with self.subTest(case=case):
                f = self.fixture()
                if case == "empty-stat":
                    f.p.joinpath("stat").write_text("")
                elif case == "wrong-stat-pid":
                    f.p.joinpath("stat").write_text(f.p.joinpath("stat").read_text().replace("4242 (", "99999 ("))
                elif case == "nested-json":
                    f.run.joinpath("1.json").write_text("[" * 2000 + "]" * 2000)
                else:
                    f.p.joinpath("environ").write_bytes(b"SAFEAGENTS_LOCK=/not/ours\0" + f.p.joinpath("environ").read_bytes())
                try:
                    result = load_monitor().ManagedMonitor(f.run, f.proc).snapshot("safeagents-glm")
                except Exception as exc:
                    self.fail(f"untrusted data escaped as {type(exc).__name__}")
                self.assertEqual(result["state"], "unverified")

    def test_severe_scanner_reason_and_fail_closed_block_match_guard(self):
        for case in ("enforcing-error", "log-only-error", "severe", "severe-bad-reason"):
            with self.subTest(case=case):
                f = self.fixture(options=["--judge-url", "http://127.0.0.1:18873", "--journal", "journal.jsonl", "--judge-fail-closed"]
                    + (["--log-only"] if case == "log-only-error" else []))
                mode = "log-only" if case == "log-only-error" else "block"
                f.banner = f"[safeagents] listening on http://{f.listen} -> {f.upstream} ({mode}, atif-scan, judge http://127.0.0.1:18873, journal journal.jsonl)\n"
                verdict = "flagged" if case == "log-only-error" else "blocked"
                if case.startswith("severe"):
                    line = f.journal_call(verdict, 1, reason="atif-scan: fixture.signal (high)" if case == "severe" else "wrong scanner reason",
                        signals=[{"id": "fixture.signal", "severity": "high"}])
                else:
                    line = f.journal_call(verdict, 1, judge="error: offline", reason="fixture.signal (low); judge unavailable")
                f.log.write_text(f.banner + line)
                result = load_monitor().ManagedMonitor(f.run, f.proc).snapshot("safeagents-glm")
                if case == "severe-bad-reason":
                    self.assertEqual(result["state"], "degraded")
                    self.assertIsNone(result["guard"]["reviewed"])
                else:
                    self.assertEqual(result["state"], "verified")
                    self.assertEqual(result["guard"][verdict], 1)
                    self.assertEqual(result["judge"]["errors"], 0 if case == "severe" else 1)

    def test_supported_padding_handles_long_tool_names_and_empty_summary(self):
        f = self.fixture()
        f.log.write_text(f.banner + f.call(what="", tool="tool_name_longer_than_twelve")
            + f.call(what="(judge block: only an argument summary)", when=f.started + 2))
        result = load_monitor().ManagedMonitor(f.run, f.proc).snapshot("safeagents-glm")
        self.assertEqual(result["state"], "verified")
        self.assertEqual(result["guard"]["passed"], 2)
        self.assertEqual(result["judge"]["consulted"], 0)
        self.assertNotIn("only an argument", json.dumps(result))

    def test_corroborating_journal_cannot_change_during_a_snapshot(self):
        from unittest.mock import patch
        f = self.fixture()
        f.log.write_text(f.banner + f.journal_call("suspicious", 1, judge="allow: expected"))
        module = load_monitor()
        original = module._corroborate
        def rotate_after_read(*args):
            result = original(*args)
            f.journal.write_text("")
            return result
        with patch.object(module, "_corroborate", side_effect=rotate_after_read):
            result = module.ManagedMonitor(f.run, f.proc).snapshot("safeagents-glm")
        self.assertNotEqual(result["state"], "verified")
        self.assertIsNone(result["guard"]["reviewed"])
        self.assertEqual(result["records"], [])

    def test_incomplete_read_still_remembers_size_to_detect_later_truncation(self):
        for case in ("oversize", "partial"):
            with self.subTest(case=case):
                f = self.fixture()
                incomplete = f.banner + f.call() + ("x" * (8 * 1024 * 1024) if case == "oversize" else "partial record")
                f.log.write_text(incomplete)
                monitor = load_monitor().ManagedMonitor(f.run, f.proc)
                self.assertEqual(monitor.snapshot("safeagents-glm")["state"], "degraded")
                f.log.write_text(f.banner + f.call())
                result = monitor.snapshot("safeagents-glm")
                self.assertEqual(result["state"], "degraded")
                self.assertIsNone(result["guard"]["reviewed"])

    def test_concurrent_pollers_do_not_mistake_an_older_read_for_truncation(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event, Lock
        f = self.fixture()
        f.log.write_text(f.banner + f.call())
        module = load_monitor()
        monitor = module.ManagedMonitor(f.run, f.proc)
        original = module._read
        entered, release = Event(), Event()
        counter_lock = Lock()
        count = 0
        def pause_first_log_read(path, *args, **kwargs):
            nonlocal count
            data = original(path, *args, **kwargs)
            if Path(path) == f.log:
                with counter_lock:
                    count += 1
                    first = count == 1
                if first:
                    entered.set()
                    if not release.wait(3):
                        raise AssertionError("fixture reader was not released")
            return data
        with patch.object(module, "_read", side_effect=pause_first_log_read), ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(monitor.snapshot, "safeagents-glm")
            self.assertTrue(entered.wait(3))
            with f.log.open("a") as stream:
                stream.write(f.call(when=f.started + 2))
            second = executor.submit(monitor.snapshot, "safeagents-glm")
            try:
                # If polls are not serialized, the newer read finishes first.
                try:
                    second.result(timeout=.2)
                except TimeoutError:
                    pass
            finally:
                release.set()
            first.result(timeout=3)
            second.result(timeout=3)
        result = monitor.snapshot("safeagents-glm")
        self.assertEqual(result["state"], "verified")
        self.assertEqual(result["guard"]["reviewed"], 2)

    def test_missing_route_never_guesses_or_creates_store(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as root:
            missing = Path(root) / "absent"
            if not MODULE.exists():
                self.fail("ManagedMonitor implementation missing")
            result = load_monitor().ManagedMonitor(missing, Path(root) / "proc").snapshot()
            self.assertEqual(result["state"], "unverified")
            self.assertEqual(result["scope"], "current_managed_run")
            self.assertIsNone(result["guard"]["reviewed"])
            self.assertIsNone(result["run"])
            self.assertIsNone(result["policy"])
            self.assertIsNone(result["judge"]["consulted"])
            self.assertEqual(result["records"], [])
            self.assertFalse(missing.exists())


if __name__ == "__main__":
    unittest.main()

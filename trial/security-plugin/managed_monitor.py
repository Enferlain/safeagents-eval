"""Strict, read-only metadata observation of an explicitly selected managed run.

No SafeAgents imports, subprocesses, network requests, writes, or raw log output.
The journal is only corroborating evidence; its lifetime length is never a total.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import pwd
import re
import stat
import threading
import time
from zoneinfo import ZoneInfo


@contextmanager
def _directory(path, private=False):
    """Walk from / with pinned, no-follow directory descriptors."""
    path = Path(path).absolute()
    _require(".." not in path.parts)
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in path.parts[1:]:
            nxt = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
            info = os.fstat(fd)
            _require(info.st_uid in (0, os.getuid()) and not info.st_mode & 0o022)
        info = os.fstat(fd)
        if private:
            _require(info.st_uid == os.getuid() and not info.st_mode & 0o077)
        yield fd
    finally:
        os.close(fd)


@contextmanager
def _file(path, private_parent=False):
    path = Path(path)
    with _directory(path.parent, private=private_parent) as directory:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        try:
            info = os.fstat(fd)
            _require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                     and not info.st_mode & 0o022 and info.st_nlink == 1)
            yield fd
        finally:
            os.close(fd)


def _read(path, limit=8 * 1024 * 1024, private_parent=False):
    with _file(path, private_parent) as fd:
        before = os.fstat(fd)
        _require(before.st_size <= limit)
        chunks = []
        remaining = limit + 1
        while remaining:
            chunk = os.read(fd, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        after = os.fstat(fd)
        _require(len(data) <= limit and _signature(before) == _signature(after))
        _require(_signature(after) == _signature(os.stat(path, follow_symlinks=False)))
        return data


def _signature(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _proc(path, limit=1024 * 1024):
    # /proc files report size zero; never use their stat size as a read bound.
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    _require(len(data) <= limit)
    return data


def _same_file(a, b):
    left, right = os.stat(a), os.stat(b)
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _process(p, proc_root, record, argv, lock):
    _require(Path(argv[0]).is_absolute())
    _require(Path(os.readlink(p / "exe")) == Path(argv[0]).resolve(strict=True))
    _require(os.readlink(p / "root") == "/")
    _require(os.readlink(p / "ns/net") == os.readlink(proc_root / "self/ns/net"))
    uid = re.search(rb"^Uid:\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)$", _proc(p / "status"), re.M)
    _require(uid is not None and all(int(n) == os.getuid() for n in uid.groups()))
    process_stat = _proc(p / "stat").decode()
    _require(process_stat.startswith(f"{record['pid']} ("))
    fields = process_stat.rsplit(") ", 1)[1].split()
    _require(len(fields) >= 20)
    _require(fields[0] not in {"Z", "X", "x"})
    ticks = int(fields[19])
    boot = re.search(rb"^btime (\d+)$", _proc(proc_root / "stat"), re.M)
    if boot is None:
        raise EvidenceError()
    started = int(boot[1]) + ticks / os.sysconf("SC_CLK_TCK")
    _require(-1 <= record["started"] - started <= 5 and record["started"] <= time.time())
    entries = _proc(p / "environ").split(b"\0")
    env = {}
    for item in entries:
        if b"=" in item:
            key, value = item.split(b"=", 1)
            _require(key not in env)
            env[key] = value
    _require(env.get(b"SAFEAGENTS_LOCK") == os.fsencode(lock))
    _require(not env.get(b"PYTHONPATH") and not env.get(b"PYTHONHOME"))
    cwd = Path(os.readlink(p / "cwd"))
    with _directory(cwd):
        pass
    fds = list((p / "fd").iterdir())
    _require(len(fds) <= 4096)
    owns_lock = False
    sockets = set()
    for fd in fds:
        target = os.readlink(fd)
        if target.startswith("socket:["):
            sockets.add(target)
        elif _same_file(fd, lock):
            info = _proc(p / "fdinfo" / fd.name).decode()
            if re.search(rf"^lock:\s+\d+: FLOCK\s+ADVISORY\s+WRITE\s+{record['pid']}\s+", info, re.M):
                owns_lock = True
    _require(owns_lock)
    port = int(record["listen"].rsplit(":", 1)[1])
    listeners = []
    for line in _proc(p / "net/tcp").decode().splitlines()[1:]:
        row = line.split()
        if len(row) >= 10 and row[1] == f"0100007F:{port:04X}" and row[3] == "0A":
            listeners.append(row)
    _require(len(listeners) == 1 and int(listeners[0][7]) == os.getuid()
             and f"socket:[{listeners[0][9]}]" in sockets)
    home = os.fsdecode(env[b"HOME"]) if b"HOME" in env else pwd.getpwuid(os.getuid()).pw_dir
    _require(Path(home).is_absolute())
    return cwd, started, env.get(b"TZ"), ticks, home


ROUTES = {"safeagents-glm": ("127.0.0.1:8788", "https://api.z.ai"),
          "safeagents-local": ("127.0.0.1:8789", "http://localhost:8080")}
GUARD_KEYS = ("reviewed", "passed", "warn", "flagged", "blocked")
JUDGE_KEYS = ("consulted", "allow", "block", "errors", "last_consultation", "last_success", "last_failure")
CALL = re.compile(r"^(\d{2}:\d{2}:\d{2})  (#[1-9]\d*|) *  (PASS|WARN|FLAG|BLOCK) {2,}([A-Za-z_][\w./:-]*)(?: {2,}(.*))?$")
VERDICTS = {"PASS": "passed", "WARN": "suspicious", "FLAG": "flagged", "BLOCK": "blocked"}
SUPPORTED_SOURCE = {
    "runs.py": "265ab004fcdaac1a73f88de2ceedb91e04541061e85c02a97f00ba4e07f0058b",
    "monitor.py": "7ec347c31a809834959b836824ebe924ce063f2017051903e3f9c68a7dcce2e6",
    "guard.py": "d460da9e905f13582d53cca53d22684b07bd0a72e89e58eb687aa1cb4aa01eb1",
    "cli.py": "1702dbf2aa5cc0f689104ffa6d3d501795b1093073ff20c5cf3748b07a66052e",
    "constants.py": "74cc83781fbbc7fb3ccbddd18a208faa6191e3290179a487c85d875fba880338",
    "utils.py": "9de94d82fa98ad95569ed6397b3332d74892c45ff691ee66a3546d1f9da94b70",
}


class EvidenceError(ValueError):
    """Deliberately carries no private source content."""


def _require(condition):
    if not condition:
        raise EvidenceError()


def _json(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            _require(key not in result)
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=unique)


def _installed(executable, cwd):
    """Only the inspected 0.0.1 piped format is supported; do not run the venv."""
    _require(not (cwd / "safeagents").exists() and not (cwd / "safeagents.py").exists())
    sites = list(Path(executable).parent.parent.glob("lib/python*/site-packages/safeagents"))
    _require(len(sites) == 1)
    package = sites[0]
    metadata = list(package.parent.glob("safeagents-*.dist-info/METADATA"))
    _require(len(metadata) == 1)
    _require(re.findall(rb"^Version: (.+)$", _read(metadata[0], 262144), re.M) == [b"0.0.1"])
    for name, digest in SUPPORTED_SOURCE.items():
        _require(hashlib.sha256(_read(package / name, 262144)).hexdigest() == digest)


def _empty():
    return {"state": "unverified", "scope": "current_managed_run",
            "detail": "Managed run could not be verified.", "run": None, "policy": None,
            "guard": dict.fromkeys(GUARD_KEYS), "judge": dict.fromkeys(JUDGE_KEYS),
            "records": [], "journal_path": None}


def _trusted_journal(path):
    """Allow a lazily absent journal, but not an unsafe existing ancestor/target."""
    path = Path(path)
    _require(path.is_absolute() and ".." not in path.parts)
    ancestor = path.parent
    while not ancestor.exists() and not ancestor.is_symlink():
        ancestor = ancestor.parent
    with _directory(ancestor):
        pass
    if path.exists() or path.is_symlink():
        with _file(path):
            pass


def _options(options, cwd, home):
    flags = {"--log-only", "--no-scan", "--no-journal", "--judge-fail-closed"}
    values = {"--judge-url", "--journal", "--block-severity", "--judge-model", "--judge-timeout", "--allow-host"}
    parsed = {}
    index = 0
    while index < len(options):
        name = options[index]
        if "=" in name and name.split("=", 1)[0] in values:
            name, value = name.split("=", 1)
            parsed[name] = value
            index += 1
        elif name in flags:
            parsed[name] = True
            index += 1
        elif name in values and index + 1 < len(options):
            parsed[name] = options[index + 1]
            index += 2
        else:
            raise EvidenceError()
    severity = parsed.get("--block-severity", "high")
    _require(severity in {"low", "medium", "high", "critical"})
    policy = {"mode": "log-only" if parsed.get("--log-only") else "enforcing",
              "scanner_enabled": not parsed.get("--no-scan", False),
              "judge_enabled": bool(parsed.get("--judge-url")),
              "journal_enabled": not parsed.get("--no-journal", False),
              "block_severity": severity,
              "judge_failure": "fail-closed" if parsed.get("--judge-fail-closed") else "fail-open"}
    display = Path(parsed.get("--journal", str(Path(home) / ".safeagents/calls.jsonl")))
    if str(display).startswith("~/"):
        display = Path(home) / str(display)[2:]
    _require(not str(display).startswith("~"))
    journal = display
    if not journal.is_absolute():
        journal = cwd / journal
    if policy["journal_enabled"]:
        _trusted_journal(journal)
    setup = ["log-only" if policy["mode"] == "log-only" else "block"]
    if policy["scanner_enabled"]:
        setup.append("atif-scan")
    if policy["judge_enabled"]:
        setup.append(f"judge {parsed['--judge-url']}")
    if policy["journal_enabled"]:
        setup.append(f"journal {display}")
    return policy, str(journal) if policy["journal_enabled"] else None, ", ".join(setup)


def _zone(value):
    if value is None:
        with open("/etc/localtime", "rb") as stream:
            return ZoneInfo.from_file(stream)
    name = value.decode().removeprefix(":")
    if name in ("UTC", "UTC0", "GMT", "GMT0"):
        return timezone.utc
    _require(name and not name.startswith("/") and ".." not in name.split("/"))
    return ZoneInfo(name)


def _clock(clock, started, now, zone):
    """A clock time is not a date. Refuse multiple days or DST-fold candidates."""
    first = datetime.fromtimestamp(started, zone).date()
    last = datetime.fromtimestamp(now, zone).date()
    days = (last - first).days
    _require(0 <= days <= 31)
    parsed = datetime.strptime(clock, "%H:%M:%S").time()
    candidates = set()
    for offset in range(days + 1):
        day = first + timedelta(days=offset)
        for fold in (0, 1):
            value = datetime.combine(day, parsed, zone).replace(fold=fold).timestamp()
            back = datetime.fromtimestamp(value, zone)
            if math.floor(started) <= value <= math.floor(now) and back.date() == day and back.strftime("%H:%M:%S") == clock:
                candidates.add(value)
    _require(len(candidates) == 1)
    return candidates.pop()


def _summary(arguments):
    try:
        parsed = json.loads(arguments)
    except ValueError:
        parsed = None
    text = arguments
    if isinstance(parsed, dict):
        for key in ("command", "cmd", "url", "query", "file_path", "path", "pattern", "code"):
            if isinstance(parsed.get(key), str) and parsed[key]:
                text = parsed[key]
                break
    text = " ".join(text.split())
    return text[:59] + "~" if len(text) > 60 else text


def _render(clock, ref, tag, tool, what, why=""):
    return f"{clock}  {ref:<5}  {tag:<5}  {tool:<12}  {what}  {why}".rstrip()


def _journal(path):
    text = _read(path).decode("utf-8")
    _require(not text or text.endswith("\n"))
    entries = {}
    for line in text.splitlines():
        entry = _json(line)
        _require(isinstance(entry, dict) and type(entry.get("id")) is int and entry["id"] > 0)
        _require(entry["id"] not in entries)
        entries[entry["id"]] = entry
    return entries


def _corroborate(entry, clock, stamp, ref, tag, tool, policy, started):
    _require(entry.get("verdict") == VERDICTS[tag] and entry.get("tool") == tool)
    when = entry.get("time")
    _require(type(when) in (int, float) and math.isfinite(when)
             and started - 1 <= when and stamp - 1 <= when < stamp + 1)
    arguments, reason, signals, judge = (entry.get(k) for k in ("arguments", "reason", "signals", "judge"))
    _require(isinstance(arguments, str) and (reason is None or isinstance(reason, str)) and isinstance(signals, list))
    findings = []
    for signal in signals:
        _require(isinstance(signal, dict) and isinstance(signal.get("id"), str)
                 and signal.get("severity") in {"info", "low", "medium", "high", "critical"})
        findings.append(f"{signal['id']} ({signal['severity']})")
    expected = "blocked" if reason is not None else "suspicious" if signals else "passed"
    if expected == "blocked" and policy["mode"] == "log-only":
        expected = "flagged"
    _require(expected == VERDICTS[tag])
    why = reason or ", ".join(findings)
    outcome = None
    severities = ("info", "low", "medium", "high", "critical")
    severe = any(severities.index(s["severity"]) >= severities.index(policy["block_severity"]) for s in signals)
    _require(not signals or policy["scanner_enabled"])
    if severe:
        severe_findings = [finding for finding, signal in zip(findings, signals)
                           if severities.index(signal["severity"]) >= severities.index(policy["block_severity"])]
        _require(reason == "atif-scan: " + ", ".join(severe_findings))
    requires_judge = bool(signals) and not severe and policy["judge_enabled"]
    _require((judge is not None) == requires_judge)
    if judge is not None:
        _require(isinstance(judge, str) and policy["judge_enabled"] and policy["scanner_enabled"] and bool(signals))
        match = re.fullmatch(r"(allow|block|error): ([\s\S]*)", judge)
        if match is None:
            raise EvidenceError()
        outcome = match[1]
        _require((outcome == "allow" and reason is None)
                 or (outcome == "block" and reason is not None)
                 or (outcome == "error" and (reason is not None) == (policy["judge_failure"] == "fail-closed")))
        if outcome == "block":
            _require(reason == f"{', '.join(findings)}; judge: {match[2]}")
        elif outcome == "error" and policy["judge_failure"] == "fail-closed":
            _require(reason == f"{', '.join(findings)}; judge unavailable")
        why += f" (judge {judge})"
    return outcome, _render(clock, ref, tag, tool, _summary(arguments), why)


class ManagedMonitor:
    def __init__(self, run_dir=None, proc_root=None):
        self.run_dir = Path(run_dir) if run_dir is not None else Path.home() / ".safeagents/run"
        self.proc_root = Path(proc_root) if proc_root is not None else Path("/proc")
        self._history = {}  # Lengths and hashes only, never private log contents.
        self._observed_sizes = {}
        self._mutex = threading.Lock()

    def _observe_size(self, route, generation, size):
        previous = self._observed_sizes.get(route)
        if previous and previous[0] == generation:
            _, maximum, invalid = previous
            invalid = invalid or size < maximum
            maximum = max(size, maximum)
        else:
            maximum, invalid = size, False
        self._observed_sizes[route] = (generation, maximum, invalid)
        _require(not invalid)

    def _identity(self, path, route):
        raw = _read(path, 65536, private_parent=True)
        record = _json(raw)
        _require(isinstance(record, dict))
        _require(type(record.get("id")) is int and record["id"] > 0)
        _require(type(record.get("pid")) is int and record["pid"] > 0)
        _require(type(record.get("started")) in (int, float) and math.isfinite(record["started"]) and record["started"] > 0)
        _require(isinstance(record.get("options"), list) and all(isinstance(v, str) and "\0" not in v for v in record["options"]))
        _require((record["listen"], record["upstream"]) == ROUTES[route])
        _require(str(record["id"]) == path.stem)
        p = self.proc_root / str(record["pid"])
        argv = _proc(p / "cmdline").decode().rstrip("\0").split("\0")
        _require(argv[1:] == ["-m", "safeagents.cli", record["upstream"], record["listen"], *record["options"]])
        lock = self.run_dir / f"{record['id']}.lock"
        with _file(lock, private_parent=True) as held:
            try:
                fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                pass
            else:
                raise EvidenceError()
        cwd, started, tz, ticks, home = _process(p, self.proc_root, record, argv, lock)
        _installed(argv[0], cwd)
        policy, journal, setup = _options(record["options"], cwd, home)
        log = self.run_dir / f"{record['id']}.log"
        _require(record["log"] == str(log))
        with _file(log, private_parent=True):
            pass
        for n in (1, 2):
            _require(_same_file(p / f"fd/{n}", log))
        identity = (ticks, started, _signature(path.stat()),
                    (log.stat().st_dev, log.stat().st_ino),
                    (lock.stat().st_dev, lock.stat().st_ino))
        generation = hashlib.sha256(raw + repr(identity).encode()).hexdigest()
        record["_process_started"] = started
        record["_tz"] = tz
        record["_setup"] = setup
        try:
            record["_journal_signature"] = _signature(os.stat(journal, follow_symlinks=False)) if journal is not None else None
        except FileNotFoundError:
            record["_journal_signature"] = None
        return record, policy, journal, log, generation

    def _parse(self, text, record, policy, journal):
        _require(text.endswith("\n") and all(c == "\n" or c.isprintable() for c in text))
        lines = text[:-1].split("\n")
        _require(lines and lines[0] == f"[safeagents] listening on http://{record['listen']} -> {record['upstream']} ({record['_setup']})")
        records = []
        entries = None
        seen = set()
        zone, now = _zone(record["_tz"]), time.time()
        index = 1
        while index < len(lines):
            line = lines[index]
            m = CALL.fullmatch(line)
            if m is None:
                raise EvidenceError()
            clock, ref, tag, tool, body = m.groups()
            body = body or ""
            stamp = _clock(clock, record["_process_started"], now, zone)
            _require(not records or stamp >= records[-1]["time"])
            call_id = int(ref[1:]) if ref else None
            if call_id is not None:
                _require(call_id not in seen)
                seen.add(call_id)
            if tag == "PASS":
                _require(not ref and len(body) <= 60 and _render(clock, ref, tag, tool, body) == line)
                outcome = None
                consumed = 1
            else:
                _require(call_id is not None and journal is not None)
                if entries is None:
                    entries = _journal(journal)
                _require(call_id in entries)
                outcome, expected = _corroborate(entries[call_id], clock, stamp, ref, tag, tool, policy, record["_process_started"])
                logical = expected.split("\n")
                _require(lines[index:index + len(logical)] == logical)
                consumed = len(logical)
            records.append({"call_id": call_id, "verdict": VERDICTS[tag], "judge_outcome": outcome, "time": stamp})
            index += consumed
        return records

    def snapshot(self, route=None):
        # HTTP polling can overlap; an older reader must not poison watermarks.
        with self._mutex:
            return self._snapshot(route)

    def _snapshot(self, route):
        result = _empty()
        if route not in ROUTES:
            return result
        candidates = []
        try:
            for path in self.run_dir.glob("*.json"):
                try:
                    candidates.append(self._identity(path, route))
                except (OSError, ValueError, KeyError, TypeError, IndexError, OverflowError, RecursionError):
                    continue
            _require(len(candidates) == 1)
            record, policy, journal, log, generation = candidates[0]
            result.update(run={"id": record["id"], "generation": generation,
                               "started_at": record["started"], "listen": record["listen"]},
                          policy=policy, journal_path=journal)
            result["state"] = "degraded"
            result["detail"] = "Managed run verified; monitoring evidence is incomplete."
            log_signature = _signature(log.stat())
            self._observe_size(route, generation, log_signature[2])
            data = _read(log, private_parent=True)
            self._observe_size(route, generation, len(data))
            previous = self._history.get(route)
            if previous and previous[0] == generation:
                _, size, digest, invalid = previous
                if invalid or len(data) < size or hashlib.sha256(data[:size]).hexdigest() != digest:
                    self._history[route] = (generation, size, digest, True)
                    raise EvidenceError()
            records = self._parse(data.decode("utf-8"), record, policy, journal)
            try:
                after = self._identity(self.run_dir / f"{record['id']}.json", route)
                _require(after == candidates[0])
            except (OSError, ValueError, KeyError, TypeError, IndexError, OverflowError, RecursionError):
                return _empty()
            _require(log_signature == _signature(log.stat()))
            self._history[route] = (generation, len(data), hashlib.sha256(data).hexdigest(), False)
            result["records"] = records
            result["guard"] = dict.fromkeys(GUARD_KEYS, 0)
            result["guard"]["reviewed"] = len(records)
            for row in records:
                key = "warn" if row["verdict"] == "suspicious" else row["verdict"]
                result["guard"][key] += 1
            result["judge"] = {**dict.fromkeys(JUDGE_KEYS[:4], 0), **dict.fromkeys(JUDGE_KEYS[4:])}
            for row in records:
                outcome = row["judge_outcome"]
                if outcome is not None:
                    result["judge"]["consulted"] += 1
                    result["judge"]["errors" if outcome == "error" else outcome] += 1
                    last = {"time": row["time"], "outcome": outcome}
                    result["judge"]["last_consultation"] = last
                    result["judge"]["last_failure" if outcome == "error" else "last_success"] = last
            result.update(state="verified", detail="Current managed run monitoring verified.")
        except (OSError, ValueError, KeyError, TypeError, IndexError, OverflowError, RecursionError):
            pass
        return result

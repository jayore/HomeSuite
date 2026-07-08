#!/usr/bin/env python3

import os
import json
import time
import uuid
import fcntl
import threading
import logging

import subprocess

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "scheduled_jobs.json")
POLL_INTERVAL = 1.0

# Cross-process singleton lock. Without this, both homesuite.service and
# piphone-scheduler.service (or any other process that imports this module
# and calls start_scheduler) would each run their own poll loop against the
# same scheduled_jobs.json and execute every job twice.
SINGLETON_LOCK_PATH = os.path.join(BASE_DIR, ".scheduler.lock")
_singleton_lock_fp = None

_lock = threading.RLock()
_started = False
_executor = None

# Generic periodic tasks: callables invoked once per poll iteration inside the
# (single-instance) scheduler loop. Each callable MUST self-throttle/window-gate
# (the loop ticks every POLL_INTERVAL). Used e.g. by the YouTube reel refresh.
_periodic_tasks = []


def register_periodic(fn):
    """Register a callable to run every scheduler poll tick (must self-throttle)."""
    with _lock:
        if fn not in _periodic_tasks:
            _periodic_tasks.append(fn)


def _load():
    if not os.path.exists(DB_PATH):
        return []
    try:
        with open(DB_PATH, "r") as f:
            return json.load(f)
    except Exception:
        logging.exception("SCHED_LOAD_FAIL")
        return []


def _save(rows):
    tmp = DB_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(rows, f, indent=2)
    os.replace(tmp, DB_PATH)


def set_executor(fn):
    """
    Register an in-process executor for due scheduled commands.

    main.py uses this so scheduled jobs can run through the already-loaded
    command brain instead of spawning a separate Python interpreter.
    """
    global _executor
    _executor = fn if callable(fn) else None


def list_jobs():
    with _lock:
        return _load()


def schedule_command(command: str, run_at_epoch: float, metadata=None):
    job = {
        "id": str(uuid.uuid4())[:8],
        "command": command.strip(),
        "run_at": float(run_at_epoch),
        "status": "pending",
        "created_at": time.time(),
    }

    if isinstance(metadata, dict) and metadata:
        job["metadata"] = metadata

    with _lock:
        rows = _load()
        rows.append(job)
        _save(rows)

    logging.info("SCHED_ADD id=%s when=%s cmd=%r",
                 job["id"], run_at_epoch, command)
    return job


def cancel_job(job_id: str):
    with _lock:
        rows = _load()
        new_rows = [r for r in rows if r["id"] != job_id]
        changed = len(rows) != len(new_rows)
        if changed:
            _save(new_rows)
        return changed


def cancel_all():
    with _lock:
        _save([])


def _run_due():
    now = time.time()

    with _lock:
        rows = _load()
        changed = False

        for row in rows:
            if row["status"] != "pending":
                continue

            if row["run_at"] > now:
                continue

            jid = row["id"]
            cmd = row["command"]

            try:
                logging.info("SCHED_EXEC_BEGIN id=%s cmd=%r", jid, cmd)

                executor = _executor
                if callable(executor):
                    result = executor(cmd)
                    logging.info("SCHED_EXEC_INPROC_RESULT id=%s result=%r", jid, result)
                else:
                    subprocess.run(
                        [sys.executable, "command_runtime.py", "--live", cmd],
                        cwd=BASE_DIR,
                        check=True,
                    )

                row["status"] = "done"
                logging.info("SCHED_EXEC_OK id=%s", jid)
            except Exception as e:
                row["status"] = "error"
                row["error"] = str(e)
                logging.exception("SCHED_EXEC_FAIL id=%s", jid)

            changed = True

        if changed:
            _save(rows)


def _loop():
    logging.info("SCHED_DAEMON_STARTED")
    while True:
        try:
            _run_due()
        except Exception:
            logging.exception("SCHED_LOOP_FAIL")
        for fn in list(_periodic_tasks):
            try:
                fn()
            except Exception:
                logging.exception("SCHED_PERIODIC_FAIL")
        time.sleep(POLL_INTERVAL)


def _acquire_singleton_lock() -> bool:
    """Acquire a cross-process exclusive lock on SINGLETON_LOCK_PATH using
    fcntl.flock. The held file descriptor is retained in module state so the
    OS keeps the lock as long as this process lives.

    Returns True if this process now owns the scheduler loop, False if
    another process already does (in which case the caller should skip
    starting its own loop)."""
    global _singleton_lock_fp
    if _singleton_lock_fp is not None:
        return True
    try:
        fp = open(SINGLETON_LOCK_PATH, "w")
    except OSError:
        logging.exception("SCHED_LOCK_OPEN_FAIL path=%s", SINGLETON_LOCK_PATH)
        return False
    try:
        fcntl.flock(fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError):
        try:
            fp.close()
        except Exception:
            pass
        return False
    try:
        fp.truncate(0)
        fp.write(f"{os.getpid()}\n")
        fp.flush()
    except Exception:
        pass
    _singleton_lock_fp = fp
    return True


def start_scheduler():
    global _started

    if _started:
        return

    if not _acquire_singleton_lock():
        logging.warning(
            "SCHED_SKIP_NOT_OWNER another scheduler process already holds the lock at %s",
            SINGLETON_LOCK_PATH,
        )
        return

    _started = True

    t = threading.Thread(
        target=_loop,
        daemon=True,
        name="scheduler"
    )
    t.start()


if __name__ == "__main__":
    import sys

    # scheduler.py is a LIBRARY + CLI, not a daemon.
    #
    # The scheduler poll loop runs inside homesuite.service via
    # start_scheduler() — that process is the canonical executor and has
    # access to the full command brain (audio, GPIO, HA cache, etc.).
    # There deliberately is no standalone-daemon mode here anymore.
    #
    # Historically a separate piphone-scheduler.service ran this file with
    # no args and entered an infinite loop, but that meant TWO scheduler
    # processes were polling the same scheduled_jobs.json and every job
    # fired twice. That service has been retired; running this file
    # without a CLI subcommand below now exits with a clear error.

    _USAGE = (
        "scheduler.py is a library + CLI; it is not meant to be run as a "
        "daemon. The scheduler poll loop runs inside homesuite.service.\n"
        "\n"
        "Usage:\n"
        "  python scheduler.py list                  # show all jobs as JSON\n"
        "  python scheduler.py in <secs> <command>   # schedule a one-shot\n"
        "  python scheduler.py cancel <job_id>       # remove one job\n"
        "  python scheduler.py clear                 # remove all jobs\n"
    )

    if len(sys.argv) >= 2 and sys.argv[1] == "list":
        print(json.dumps(list_jobs(), indent=2))
        raise SystemExit(0)

    if len(sys.argv) >= 4 and sys.argv[1] == "in":
        secs = int(sys.argv[2])
        cmd = " ".join(sys.argv[3:])
        job = schedule_command(cmd, time.time() + secs)
        print(json.dumps(job, indent=2))
        raise SystemExit(0)

    if len(sys.argv) == 3 and sys.argv[1] == "cancel":
        print(cancel_job(sys.argv[2]))
        raise SystemExit(0)

    if len(sys.argv) == 2 and sys.argv[1] == "clear":
        cancel_all()
        print("cleared")
        raise SystemExit(0)

    sys.stderr.write(_USAGE)
    raise SystemExit(2)

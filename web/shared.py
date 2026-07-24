"""Shared mutable state: job registry + scheduler queue."""
import threading

_jobs: dict = {}
_jobs_lock  = threading.Lock()

# Ordered list of pending job IDs (supports reordering)
_job_queue: list = []
_queue_cv   = threading.Condition()   # independent lock, NOT _jobs_lock

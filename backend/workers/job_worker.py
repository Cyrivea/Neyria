import logging
import signal
import socket
import sqlite3
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from core import config
from repositories import job_repo
from services import billing_service, workflow_service
from workers.dispatcher import JOB_HANDLERS, JobCancelled

logger = logging.getLogger("job_worker")


class JobWorker:
    def __init__(self, worker_id: str | None = None):
        self.worker_id = worker_id or f"{socket.gethostname()}-{id(self)}"
        self.stop_requested = False

    def stop(self, *_args: Any) -> None:
        self.stop_requested = True

    def recover(self) -> int:
        now = datetime.now(UTC)
        retry_at = (now + timedelta(seconds=1)).isoformat()
        recovered = job_repo.recover_expired(now.isoformat(), retry_at)
        if recovered:
            logger.warning(
                "恢复过期任务",
                extra={"evt": "jobs_recovered", "count": recovered},
            )
        return recovered

    def _lease_heartbeat(self, job_id: str, stop_event: threading.Event) -> None:
        interval = max(1.0, config.JOB_LEASE_SECONDS / 3)
        while not stop_event.wait(interval):
            if not job_repo.renew_lease(job_id, self.worker_id, config.JOB_LEASE_SECONDS):
                logger.warning(
                    "任务租约续期失败",
                    extra={"evt": "job_lease_lost", "job_id": job_id},
                )
                return

    def run_once(self) -> bool:
        now = datetime.now(UTC).isoformat()
        job = job_repo.claim_next(self.worker_id, config.JOB_LEASE_SECONDS, now)
        if job is None:
            return False

        logger.info(
            "领取后台任务",
            extra={"evt": "job_claimed", "job_id": job["id"], "job_type": job["type"]},
        )
        try:
            if job["type"] not in JOB_HANDLERS:
                raise ValueError(f"未注册的任务类型: {job['type']}")

            heartbeat_stop = threading.Event()
            heartbeat = threading.Thread(
                target=self._lease_heartbeat,
                args=(job["id"], heartbeat_stop),
                name=f"job-lease-{job['id'][:8]}",
                daemon=True,
            )
            heartbeat.start()
            try:
                handler = JOB_HANDLERS[job["type"]]
                result = handler(
                    job,
                    lambda progress, message: job_repo.update_progress(
                        job["id"], self.worker_id, progress, message
                    ),
                    lambda: job_repo.is_cancel_requested(job["id"], self.worker_id),
                )
            finally:
                heartbeat_stop.set()
                heartbeat.join(timeout=1)

            if job_repo.is_cancel_requested(job["id"], self.worker_id):
                job_repo.mark_cancelled(job["id"], self.worker_id, datetime.now(UTC).isoformat())
            else:
                job_repo.mark_succeeded(job["id"], self.worker_id, result, datetime.now(UTC).isoformat())
        except JobCancelled:
            job_repo.mark_cancelled(job["id"], self.worker_id, datetime.now(UTC).isoformat())
        except Exception as exc:
            logger.exception(
                "后台任务执行失败",
                extra={
                    "evt": "job_failed",
                    "job_id": job["id"],
                    "job_type": job["type"],
                    "error_type": type(exc).__name__,
                },
            )
            retry_at = None
            if job["attempts"] < job["max_attempts"]:
                delay = min(60, 5 * (2 ** max(0, job["attempts"] - 1)))
                retry_at = (datetime.now(UTC) + timedelta(seconds=delay)).isoformat()
            job_repo.mark_failed_or_retry(
                job["id"],
                self.worker_id,
                str(exc),
                retry_at,
                datetime.now(UTC).isoformat(),
            )
        return True

    def run_forever(self) -> None:
        try:
            self.recover()
        except sqlite3.OperationalError as exc:
            # 允许 Worker 与 API 同时启动：迁移尚未完成时等待下一轮，而不是永久退出。
            logger.warning(
                "Worker 启动时数据库尚未就绪",
                extra={"evt": "worker_db_not_ready", "error_type": type(exc).__name__},
            )
        logger.info("后台 Worker 启动", extra={"evt": "worker_started", "worker_id": self.worker_id})
        last_recovery = time.monotonic()
        # I11：计费预占是冻结的真金白银，清扫不能等「正好有人看余额」——进程崩溃后超时预占必须自动回收。
        last_billing_sweep = 0.0  # 启动即先扫一次，吞掉上次崩溃留下的尾巴
        # WF1-③-v2：档案馆自适应周期清扫，未保留 + 超期终态运行连带产物文件清理
        last_archive_sweep = time.monotonic()  # 首次等等扫一次，启动即扫拖慢热启动
        while not self.stop_requested:
            if time.monotonic() - last_recovery >= config.JOB_LEASE_SECONDS:
                try:
                    self.recover()
                except sqlite3.OperationalError:
                    logger.warning(
                        "Worker 恢复任务时数据库暂不可用", extra={"evt": "worker_recovery_db_error"}
                    )
                last_recovery = time.monotonic()
            if time.monotonic() - last_billing_sweep >= config.BILLING_SWEEP_INTERVAL_SECONDS:
                try:
                    swept = billing_service.sweep_stale_reservations(
                        config.BILLING_RESERVATION_STALE_SECONDS
                    )
                    if swept:
                        logger.warning(
                            "回收超时计费预占",
                            extra={"evt": "billing_sweep_reclaimed", "count": swept},
                        )
                except sqlite3.OperationalError:
                    # 与 API 同时启动时迁移可能没完，等下一轮；预占不会丢，只是晚回收一个周期。
                    logger.warning(
                        "Worker 清扫计费预占时数据库暂不可用", extra={"evt": "billing_sweep_db_error"}
                    )
                last_billing_sweep = time.monotonic()
            if (
                config.ARCHIVE_RETENTION_DAYS > 0
                and time.monotonic() - last_archive_sweep >= config.ARCHIVE_SWEEP_INTERVAL_SECONDS
            ):
                try:
                    outcome = workflow_service.sweep_run_archives(config.ARCHIVE_RETENTION_DAYS)
                    if outcome["removed"]:
                        logger.info(
                            "档案馆清扫完成",
                            extra={
                                "evt": "archive_sweep_completed",
                                "removed": outcome["removed"],
                                "files_removed": outcome["files_removed"],
                            },
                        )
                except sqlite3.OperationalError:
                    logger.warning(
                        "Worker 清扫档案馆时数据库暂不可用", extra={"evt": "archive_sweep_db_error"}
                    )
                last_archive_sweep = time.monotonic()
            try:
                has_job = self.run_once()
            except sqlite3.OperationalError:
                logger.warning("Worker 领取任务时数据库暂不可用", extra={"evt": "worker_claim_db_error"})
                time.sleep(config.JOB_POLL_INTERVAL_SECONDS)
                continue
            if not has_job:
                time.sleep(config.JOB_POLL_INTERVAL_SECONDS)
        logger.info("后台 Worker 停止", extra={"evt": "worker_stopped", "worker_id": self.worker_id})


def main() -> None:
    worker = JobWorker()
    signal.signal(signal.SIGINT, worker.stop)
    signal.signal(signal.SIGTERM, worker.stop)
    worker.run_forever()


if __name__ == "__main__":
    main()

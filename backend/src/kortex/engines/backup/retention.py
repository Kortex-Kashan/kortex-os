"""KORTEX Backup Engine retention policy manager.

Phase 7 — Production Hardening — Backup Engine.
Enforces deterministic count, age, and size pruning policies while
guaranteeing the inviolable safety invariant:
NEVER DELETE THE LAST VALID BACKUP.
"""

from __future__ import annotations

import datetime
import logging
from typing import Final

from kortex.engines.backup.constants import BackupState, RetentionPolicyType
from kortex.engines.backup.interfaces import IBackupRepository, IRetentionPolicyEngine
from kortex.engines.backup.models import (
    DeleteBackupRequest,
    ListBackupsRequest,
    RetentionPolicy,
)

logger = logging.getLogger("kortex.engines.backup.retention")

_ISO_FORMAT: Final[str] = "%Y-%m-%dT%H:%M:%SZ"


class RetentionEngine(IRetentionPolicyEngine):
    """Evaluates retention criteria and safely executes artifact pruning."""

    def evaluate_and_prune(
        self,
        repository: IBackupRepository,
        policy: RetentionPolicy,
        active_backup_id: str | None = None,
    ) -> list[str]:
        """Apply retention policies to prune older backups.

        Enforces:
        1. Never delete the active (in-progress or newly created) backup.
        2. Inviolable Safety Invariant: Never delete the last valid backup.
           If count(valid_backups) <= 1, pruning is aborted immediately.

        Returns:
            List of backup IDs successfully deleted.
        """
        response = repository.list_backups(ListBackupsRequest(limit=500))
        all_backups = response.backups

        # Filter to valid backups
        valid_backups = [b for b in all_backups if b.state == BackupState.VALID]

        # Inviolable safety check
        if len(valid_backups) <= 1:
            logger.info(
                "Retention check: Only %d valid backup(s) exist. Safety invariant enforced; pruning aborted.",
                len(valid_backups),
            )
            return []

        # Candidate pool: valid backups sorted from newest to oldest
        candidates_newest_first = sorted(valid_backups, key=lambda b: b.created_at, reverse=True)

        to_prune: set[str] = set()

        # 1. Count policy
        if (
            policy.policy_type in (RetentionPolicyType.COUNT, RetentionPolicyType.COMPOSITE)
            and len(candidates_newest_first) > policy.max_count
        ):
            excess = candidates_newest_first[policy.max_count :]
            for b in excess:
                if b.backup_id != active_backup_id:
                    to_prune.add(b.backup_id)

        # 2. Age policy
        if policy.policy_type in (RetentionPolicyType.AGE, RetentionPolicyType.COMPOSITE):
            now = datetime.datetime.now(datetime.UTC)
            max_age_delta = datetime.timedelta(days=policy.max_age_days)
            for b in candidates_newest_first:
                try:
                    # Parse timestamp (handle both Z and +00:00)
                    ts_str = b.created_at.replace("Z", "+00:00")
                    created_dt = datetime.datetime.fromisoformat(ts_str)
                    if created_dt.tzinfo is None:
                        created_dt = created_dt.replace(tzinfo=datetime.UTC)
                    if now - created_dt > max_age_delta and b.backup_id != active_backup_id:
                        to_prune.add(b.backup_id)
                except Exception as exc:
                    logger.warning("Could not parse created_at timestamp '%s': %s", b.created_at, exc)

        # 3. Size policy
        if policy.policy_type in (RetentionPolicyType.SIZE, RetentionPolicyType.COMPOSITE):
            cumulative_size = 0
            # Iterate newest first, accumulate size
            for b in candidates_newest_first:
                cumulative_size += b.file_size_bytes
                if cumulative_size > policy.max_size_bytes and b.backup_id != active_backup_id:
                    to_prune.add(b.backup_id)

        # Final safety check: ensure we never prune ALL valid backups
        remaining_valid_count = len(valid_backups) - len(to_prune)
        if remaining_valid_count < 1:
            logger.warning(
                "Retention candidate set would eliminate all valid backups! "
                "Retaining the single newest valid backup '%s'.",
                candidates_newest_first[0].backup_id,
            )
            to_prune.discard(candidates_newest_first[0].backup_id)

        pruned_ids: list[str] = []
        for backup_id in to_prune:
            try:
                repository.delete_backup(DeleteBackupRequest(backup_id=backup_id))
                pruned_ids.append(backup_id)
                logger.info("Retention pruned expired backup: %s", backup_id)
            except Exception as exc:
                logger.error("Failed to prune backup '%s': %s", backup_id, exc)

        return pruned_ids

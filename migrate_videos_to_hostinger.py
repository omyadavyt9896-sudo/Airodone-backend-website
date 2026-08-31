"""
Migration Utility: Migrate Local Video Files to Hostinger Persistent Storage.

Usage:
    python migrate_videos_to_hostinger.py --dry-run
    python migrate_videos_to_hostinger.py --migrate

Safety Guarantees:
    - Never deletes database records.
    - Preserves local source files until remote transfer is verified.
    - Transactionally updates MySQL course_videos.video_file only after successful transfer.
    - Idempotent: Skips videos that are already migrated to remote storage.
"""

import argparse
import logging
import os
import sys

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from app import get_db_connection, get_db_cursor, VIDEO_UPLOAD_FOLDER
import storage
from storage.hostinger import HostingerStorageBackend

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("video_migration")


def audit_existing_videos():
    """Audits and prints summary of all course videos in the database."""
    conn = get_db_connection()
    cur = get_db_cursor(conn)
    cur.execute(
        """
        SELECT v.id, v.title, v.video_file, v.module_id, m.course_id, c.title AS course_title
        FROM course_videos v
        JOIN modules m ON v.module_id = m.id
        JOIN courses c ON m.course_id = c.id
        ORDER BY c.id, m.id, v.sequence
        """
    )
    videos = cur.fetchall()
    cur.close()
    conn.close()

    total = len(videos)
    with_file = 0
    without_file = 0
    local_exists = 0
    local_missing = 0
    already_remote = 0

    for v in videos:
        v_file = v.get("video_file")
        if not v_file:
            without_file += 1
            continue

        with_file += 1
        clean_path = v_file.replace("\\", "/")

        if clean_path.startswith("videos/course_"):
            already_remote += 1
        else:
            # Check local file
            full_path = os.path.join(VIDEO_UPLOAD_FOLDER, v_file)
            if os.path.exists(full_path):
                local_exists += 1
            else:
                local_missing += 1

    logger.info("=" * 60)
    logger.info("COURSE VIDEOS STORAGE AUDIT REPORT")
    logger.info("=" * 60)
    logger.info(f"Total Video Records in DB       : {total}")
    logger.info(f"Videos with file reference      : {with_file}")
    logger.info(f"Videos without file (Coming Soon): {without_file}")
    logger.info(f"Already in structured storage   : {already_remote}")
    logger.info(f"Local files present on disk     : {local_exists}")
    logger.info(f"Local files missing on disk     : {local_missing}")
    logger.info("=" * 60)

    return videos


def migrate_to_hostinger(dry_run: bool = True):
    """Transfers local video files to Hostinger remote storage."""
    hostinger_backend = HostingerStorageBackend()
    if not hostinger_backend.is_configured():
        logger.error(
            "Hostinger storage credentials are NOT configured. "
            "Please set HOSTINGER_SFTP_HOST, HOSTINGER_SFTP_USERNAME, HOSTINGER_SFTP_PASSWORD in environment."
        )
        return False

    videos = audit_existing_videos()

    if dry_run:
        logger.info("[DRY RUN MODE] No files will be transferred and no database rows will be modified.")
        logger.info("Run with --migrate to perform the actual transfer.")
        return True

    conn = get_db_connection()
    cur = get_db_cursor(conn)

    migrated_count = 0
    skipped_count = 0
    failed_count = 0

    for v in videos:
        vid_id = v["id"]
        v_file = v.get("video_file")
        course_id = v["course_id"]
        module_id = v["module_id"]
        title = v["title"]

        if not v_file:
            logger.info(f"Video ID {vid_id} ('{title}'): No file attached (Coming Soon). Skipping.")
            skipped_count += 1
            continue

        clean_path = v_file.replace("\\", "/")

        # Resolve local source path
        if os.path.isabs(v_file):
            src_path = v_file
        else:
            src_path = os.path.join(VIDEO_UPLOAD_FOLDER, v_file)

        if not os.path.exists(src_path):
            if hostinger_backend.video_exists(clean_path):
                logger.info(f"Video ID {vid_id} ('{title}'): Already exists on Hostinger at {clean_path}. Skipping.")
                skipped_count += 1
                continue
            else:
                logger.warning(f"Video ID {vid_id} ('{title}'): Local file missing at {src_path}. Cannot migrate.")
                failed_count += 1
                continue

        # Upload file to Hostinger
        try:
            with open(src_path, "rb") as f_in:
                orig_filename = os.path.basename(src_path)
                success, remote_rel_path, err = hostinger_backend.save_video(
                    file_obj=f_in,
                    course_id=course_id,
                    module_id=module_id,
                    original_filename=orig_filename,
                )

            if not success:
                logger.error(f"Failed to transfer video ID {vid_id} ('{title}'): {err}")
                failed_count += 1
                continue

            # Verify remote file exists
            if not hostinger_backend.video_exists(remote_rel_path):
                logger.error(f"Verification failed: {remote_rel_path} not found on Hostinger after upload.")
                failed_count += 1
                continue

            # Update database record
            cur.execute(
                "UPDATE course_videos SET video_file = %s WHERE id = %s",
                (remote_rel_path, vid_id),
            )
            conn.commit()

            logger.info(f"✓ Successfully migrated Video ID {vid_id} ('{title}') -> {remote_rel_path}")
            migrated_count += 1

        except Exception as e:
            logger.error(f"Exception during migration of Video ID {vid_id}: {e}", exc_info=True)
            failed_count += 1

    cur.close()
    conn.close()

    logger.info("=" * 60)
    logger.info("MIGRATION COMPLETED")
    logger.info(f"Successfully Migrated : {migrated_count}")
    logger.info(f"Skipped / Unchanged   : {skipped_count}")
    logger.info(f"Failed / Missing      : {failed_count}")
    logger.info("=" * 60)
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate LMS video files to Hostinger storage.")
    parser.add_argument("--dry-run", action="store_true", default=False, help="Perform dry run audit without transferring")
    parser.add_argument("--migrate", action="store_true", default=False, help="Execute migration to Hostinger storage")
    args = parser.parse_args()

    if args.migrate:
        migrate_to_hostinger(dry_run=False)
    else:
        audit_existing_videos()
        if not args.dry_run:
            print("\nTo perform a dry run: python migrate_videos_to_hostinger.py --dry-run")
            print("To execute migration: python migrate_videos_to_hostinger.py --migrate\n")


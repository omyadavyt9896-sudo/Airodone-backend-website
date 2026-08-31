"""
Hostinger persistent storage backend for production deployments.
Transfers and manages video files on Hostinger filesystem storage via SFTP or FTPS/FTP.
"""

import ftplib
import logging
import os
import shutil
import tempfile
from typing import Generator, Optional, Tuple

from storage.base import (
    BaseStorageBackend,
    build_video_storage_path,
    sanitize_storage_filename,
)

logger = logging.getLogger("storage.hostinger")


class HostingerStorageBackend(BaseStorageBackend):
    """
    Hostinger remote filesystem storage backend.
    """

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        base_path: Optional[str] = None,
        protocol: Optional[str] = None,
        max_size_mb: int = 500,
    ):
        self.host = host or os.environ.get("HOSTINGER_SFTP_HOST") or os.environ.get("HOSTINGER_FTP_HOST") or ""
        self.username = username or os.environ.get("HOSTINGER_SFTP_USERNAME") or os.environ.get("HOSTINGER_FTP_USERNAME") or ""
        self.password = password or os.environ.get("HOSTINGER_SFTP_PASSWORD") or os.environ.get("HOSTINGER_FTP_PASSWORD") or ""
        self.base_path = base_path or os.environ.get("HOSTINGER_SFTP_BASE_PATH") or os.environ.get("HOSTINGER_STORAGE_BASE_PATH") or "storage"
        self.protocol = (protocol or os.environ.get("HOSTINGER_TRANSFER_PROTOCOL", "sftp")).lower()
        
        default_port = 22 if self.protocol == "sftp" else 21
        env_port = os.environ.get("HOSTINGER_SFTP_PORT") or os.environ.get("HOSTINGER_FTP_PORT")
        self.port = int(port or (int(env_port) if env_port else default_port))
        self.max_size_bytes = int(os.environ.get("HOSTINGER_MAX_VIDEO_SIZE_MB", max_size_mb)) * 1024 * 1024

    def is_configured(self) -> bool:
        """Returns True if the necessary connection credentials are provided."""
        return bool(self.host and self.username and self.password)

    def _get_remote_full_path(self, storage_path: str) -> str:
        """
        Combines base_path and relative storage_path securely.
        """
        clean_rel = storage_path.replace("\\", "/").lstrip("/")
        clean_base = self.base_path.replace("\\", "/").rstrip("/")
        if clean_base:
            return f"{clean_base}/{clean_rel}"
        return clean_rel

    # -------------------------------------------------------------------------
    # SFTP Helper Methods (paramiko)
    # -------------------------------------------------------------------------
    def _get_sftp_client(self):
        """Initializes and returns an active (SSHClient, SFTPClient) tuple."""
        try:
            import paramiko
        except ImportError:
            raise RuntimeError(
                "The 'paramiko' library is required for SFTP transfer to Hostinger. "
                "Please ensure paramiko is listed in requirements.txt or use protocol='ftps'."
            )

        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(
            hostname=self.host,
            port=self.port,
            username=self.username,
            password=self.password,
            timeout=30,
            banner_timeout=30,
            auth_timeout=30,
        )
        sftp = ssh.open_sftp()
        return ssh, sftp

    def _sftp_makedirs(self, sftp, remote_dir: str):
        """Recursively creates remote directories over SFTP."""
        dirs = []
        path = remote_dir.replace("\\", "/")
        while len(path) > 1:
            dirs.append(path)
            path, _ = os.path.split(path)

        dirs.reverse()
        for d in dirs:
            try:
                sftp.stat(d)
            except IOError:
                try:
                    sftp.mkdir(d)
                except IOError:
                    pass

    # -------------------------------------------------------------------------
    # FTP / FTPS Helper Methods (Standard Library ftplib)
    # -------------------------------------------------------------------------
    def _get_ftp_client(self):
        """Initializes and returns an active FTP/FTPS connection."""
        if self.protocol == "ftps":
            ftp = ftplib.FTP_TLS(timeout=30)
        else:
            ftp = ftplib.FTP(timeout=30)

        ftp.connect(self.host, self.port)
        ftp.login(self.username, self.password)
        if isinstance(ftp, ftplib.FTP_TLS):
            ftp.prot_p()  # Secure data connection
        return ftp

    def _ftp_makedirs(self, ftp, remote_dir: str):
        """Recursively creates remote directories over FTP."""
        parts = remote_dir.replace("\\", "/").strip("/").split("/")
        current = ""
        for p in parts:
            current += "/" + p
            try:
                ftp.cwd(current)
            except ftplib.error_perm:
                try:
                    ftp.mkd(current)
                except ftplib.error_perm:
                    pass

    # -------------------------------------------------------------------------
    # Core BaseStorageBackend Implementation
    # -------------------------------------------------------------------------
    def save_video(
        self,
        file_obj,
        course_id: int,
        module_id: int,
        original_filename: str,
    ) -> Tuple[bool, str, Optional[str]]:
        if not self.is_configured():
            err_msg = "Hostinger storage credentials not configured. Please set HOSTINGER_SFTP_HOST, HOSTINGER_SFTP_USERNAME, HOSTINGER_SFTP_PASSWORD."
            logger.error(err_msg)
            return False, "", err_msg

        safe_name = sanitize_storage_filename(original_filename, prefix=f"vid_{course_id}_{module_id}")
        rel_storage_path = build_video_storage_path(course_id, module_id, safe_name)
        remote_full_path = self._get_remote_full_path(rel_storage_path)
        remote_dir = os.path.dirname(remote_full_path)

        # Buffer incoming upload stream to a temporary local file first
        tmp_file = None
        try:
            fd, tmp_file = tempfile.mkstemp(prefix="hostinger_upload_", suffix=".tmp")
            os.close(fd)

            if hasattr(file_obj, "save"):
                file_obj.save(tmp_file)
            elif hasattr(file_obj, "read"):
                file_obj.seek(0)
                with open(tmp_file, "wb") as f_out:
                    shutil.copyfileobj(file_obj, f_out)
            else:
                return False, "", "Invalid file object provided for upload."

            upload_size = os.path.getsize(tmp_file)
            if upload_size > self.max_size_bytes:
                max_mb = self.max_size_bytes // (1024 * 1024)
                return False, "", f"File exceeds maximum allowed size ({max_mb} MB)."

            logger.info(f"Transferring video to Hostinger: {rel_storage_path} ({upload_size} bytes)")

            # Execute remote transfer
            if self.protocol == "sftp":
                ssh, sftp = self._get_sftp_client()
                try:
                    self._sftp_makedirs(sftp, remote_dir)
                    sftp.put(tmp_file, remote_full_path)
                    # Verify remote file exists and size matches
                    stat = sftp.stat(remote_full_path)
                    if stat.st_size != upload_size:
                        raise IOError("Uploaded remote file size mismatch.")
                finally:
                    sftp.close()
                    ssh.close()
            else:
                ftp = self._get_ftp_client()
                try:
                    self._ftp_makedirs(ftp, remote_dir)
                    with open(tmp_file, "rb") as f_read:
                        ftp.storbinary(f"STOR {remote_full_path}", f_read)
                finally:
                    ftp.quit()

            logger.info(f"Successfully transferred video to Hostinger: {rel_storage_path}")
            return True, rel_storage_path, None

        except Exception as e:
            logger.error(f"Hostinger storage transfer failed for {rel_storage_path}: {e}", exc_info=True)
            # Try cleaning up remote partial file on error
            try:
                self.delete_video(rel_storage_path)
            except Exception:
                pass
            return False, "", f"Remote storage transfer failed: {str(e)}"
        finally:
            if tmp_file and os.path.exists(tmp_file):
                try:
                    os.remove(tmp_file)
                except Exception:
                    pass

    def delete_video(self, storage_path: str) -> bool:
        if not storage_path or not self.is_configured():
            return True

        remote_full_path = self._get_remote_full_path(storage_path)
        try:
            if self.protocol == "sftp":
                ssh, sftp = self._get_sftp_client()
                try:
                    sftp.remove(remote_full_path)
                    logger.info(f"Deleted remote video on Hostinger: {storage_path}")
                except IOError:
                    pass  # Already absent
                finally:
                    sftp.close()
                    ssh.close()
            else:
                ftp = self._get_ftp_client()
                try:
                    ftp.delete(remote_full_path)
                    logger.info(f"Deleted remote video on Hostinger: {storage_path}")
                except ftplib.error_perm:
                    pass  # Already absent
                finally:
                    ftp.quit()
            return True
        except Exception as e:
            logger.error(f"Error deleting Hostinger video {storage_path}: {e}")
            return False

    def video_exists(self, storage_path: str) -> bool:
        if not storage_path or not self.is_configured():
            return False

        remote_full_path = self._get_remote_full_path(storage_path)
        try:
            if self.protocol == "sftp":
                ssh, sftp = self._get_sftp_client()
                try:
                    sftp.stat(remote_full_path)
                    return True
                except IOError:
                    return False
                finally:
                    sftp.close()
                    ssh.close()
            else:
                ftp = self._get_ftp_client()
                try:
                    size = ftp.size(remote_full_path)
                    return size is not None and size > 0
                except Exception:
                    return False
                finally:
                    ftp.quit()
        except Exception as e:
            logger.warning(f"Error checking remote video existence on Hostinger for {storage_path}: {e}")
            return False

    def get_video_size(self, storage_path: str) -> Optional[int]:
        if not storage_path or not self.is_configured():
            return None

        remote_full_path = self._get_remote_full_path(storage_path)
        try:
            if self.protocol == "sftp":
                ssh, sftp = self._get_sftp_client()
                try:
                    stat = sftp.stat(remote_full_path)
                    return stat.st_size
                except IOError:
                    return None
                finally:
                    sftp.close()
                    ssh.close()
            else:
                ftp = self._get_ftp_client()
                try:
                    return ftp.size(remote_full_path)
                except Exception:
                    return None
                finally:
                    ftp.quit()
        except Exception as e:
            logger.warning(f"Error retrieving Hostinger video size for {storage_path}: {e}")
            return None

    def open_video_stream(
        self,
        storage_path: str,
        start_byte: int = 0,
        length: Optional[int] = None,
        chunk_size: int = 65536,
    ) -> Generator[bytes, None, None]:
        """
        Streams remote video chunks over SFTP/FTP without buffering full file into RAM.
        """
        if not storage_path or not self.is_configured():
            return

        remote_full_path = self._get_remote_full_path(storage_path)

        if self.protocol == "sftp":
            ssh, sftp = self._get_sftp_client()
            try:
                remote_file = sftp.open(remote_full_path, "rb")
                try:
                    total_size = sftp.stat(remote_full_path).st_size
                    if start_byte >= total_size:
                        return

                    bytes_remaining = total_size - start_byte
                    if length is not None:
                        bytes_remaining = min(bytes_remaining, length)

                    remote_file.seek(start_byte)
                    while bytes_remaining > 0:
                        read_len = min(chunk_size, bytes_remaining)
                        data = remote_file.read(read_len)
                        if not data:
                            break
                        bytes_remaining -= len(data)
                        yield data
                finally:
                    remote_file.close()
            finally:
                sftp.close()
                ssh.close()
        else:
            # FTP range streaming with REST offset
            ftp = self._get_ftp_client()
            try:
                total_size = ftp.size(remote_full_path) or 0
                if start_byte >= total_size:
                    return

                bytes_remaining = total_size - start_byte
                if length is not None:
                    bytes_remaining = min(bytes_remaining, length)

                conn = ftp.transfercmd(f"RETR {remote_full_path}", rest=start_byte)
                try:
                    while bytes_remaining > 0:
                        read_len = min(chunk_size, bytes_remaining)
                        data = conn.recv(read_len)
                        if not data:
                            break
                        bytes_remaining -= len(data)
                        yield data
                finally:
                    conn.close()
            finally:
                ftp.quit()

    def get_video_url(self, storage_path: str) -> Optional[str]:
        # Protected streaming through LMS authorization
        return None


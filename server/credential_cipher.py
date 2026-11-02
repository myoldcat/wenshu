"""Authenticated API-key storage on POSIX, scoped to one persistent data directory.

The master key never leaves the service. A database backup without this key cannot
decrypt credentials; backups must preserve .secrets and its private permissions.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import secrets
import stat


class CredentialError(ValueError):
    """Only fixed, safe-to-display messages; never include key material."""


class FernetFileCipher:
    PREFIX = 'fernet:v1:'

    def __init__(self, data_root, *, has_ciphertext=lambda: False):
        self.directory = Path(data_root) / '.secrets'
        self.has_ciphertext = has_ciphertext

    @staticmethod
    def _fernet():
        try:
            from cryptography.fernet import Fernet
            return Fernet
        except ImportError:
            raise CredentialError('服务缺少密钥加密组件，请更新后端依赖并重启服务') from None

    @staticmethod
    def _private(info, *, directory=False):
        correct_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
        if not correct_type or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise CredentialError('服务端密钥存储权限不安全，请检查私有目录 0700、密钥文件 0600 及服务账户所有权')

    @contextmanager
    def _directory(self, create):
        if os.name != 'posix':
            raise CredentialError('当前系统不支持此密钥存储格式，请在原服务环境中读取')
        if create:
            try:
                self.directory.mkdir(mode=0o700)
            except FileExistsError:
                pass
        try:
            fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except FileNotFoundError:
            raise CredentialError('服务端主密钥缺失，请恢复原 .secrets/model.key，勿重新生成或覆盖') from None
        except OSError:
            raise CredentialError('无法安全访问服务端密钥目录，请检查目录类型、权限和服务账户所有权') from None
        try:
            self._private(os.fstat(fd), directory=True)
            yield fd
        finally:
            os.close(fd)

    def _read_key(self, directory_fd):
        # NONBLOCK also keeps a substituted FIFO/device from hanging the service.
        fd = os.open('model.key', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
        try:
            self._private(os.fstat(fd))
            key = os.read(fd, 128)
            if len(key) != 44:
                raise CredentialError('服务端主密钥格式损坏，请恢复原 .secrets/model.key，勿覆盖')
            return key
        finally:
            os.close(fd)

    @staticmethod
    def _publish_key(directory_fd, key):
        # Publish a fully written key without overwriting another process's key.
        temporary = '.model-key-' + secrets.token_hex(16)
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=directory_fd)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(key)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, 'model.key', src_dir_fd=directory_fd,
                        dst_dir_fd=directory_fd, follow_symlinks=False)
            except FileExistsError:
                pass
        finally:
            os.unlink(temporary, dir_fd=directory_fd)
            os.fsync(directory_fd)

    def _load(self, *, create):
        fernet = self._fernet()
        try:
            with self._directory(create) as directory_fd:
                try:
                    key = self._read_key(directory_fd)
                except FileNotFoundError:
                    if not create or self.has_ciphertext():
                        raise CredentialError('服务端主密钥缺失，请恢复原 .secrets/model.key，勿重新生成或覆盖') from None
                    self._publish_key(directory_fd, fernet.generate_key())
                    key = self._read_key(directory_fd)
                try:
                    return fernet(key)
                except (ValueError, TypeError):
                    raise CredentialError('服务端主密钥格式损坏，请恢复原 .secrets/model.key，勿覆盖') from None
        except CredentialError:
            raise
        except OSError:
            raise CredentialError('无法安全访问服务端主密钥，请检查文件类型、权限和服务账户所有权') from None

    def encrypt(self, text):
        try:
            return self.PREFIX + self._load(create=True).encrypt(text.encode('utf-8')).decode('ascii')
        except CredentialError:
            raise
        except Exception:
            raise CredentialError('服务端密钥加密失败，配置未保存，请检查密钥存储') from None

    def decrypt(self, text):
        if isinstance(text, str) and text.startswith('dpapi:v1:'):
            raise CredentialError('此密钥受原 Windows 账户保护，请在当前服务重新填写 API Key 后保存')
        if not isinstance(text, str) or not text.startswith(self.PREFIX):
            raise CredentialError('模型密钥存储格式无效，请重新填写 API Key 后保存')
        cipher = self._load(create=False)
        try:
            return cipher.decrypt(text[len(self.PREFIX):].encode('ascii')).decode('utf-8')
        except Exception:
            raise CredentialError('模型密钥无法解密或已损坏，请恢复原主密钥或重新填写 API Key 后保存') from None

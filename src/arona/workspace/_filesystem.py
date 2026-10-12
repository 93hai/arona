"""Pinned directory I/O used only by the Candidate filesystem boundary."""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import os
from pathlib import Path
import stat


def reject_links(info) -> None:
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError("Candidate links and reparse points are prohibited")
    if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
        raise ValueError("Candidate hardlinks are prohibited")


@contextmanager
def _windows_pin(path: Path, *, directory: bool):
    import ctypes
    from ctypes import wintypes

    class FileInformation(ctypes.Structure):
        _fields_ = [
            ("attributes", wintypes.DWORD), ("created", wintypes.FILETIME),
            ("accessed", wintypes.FILETIME), ("modified", wintypes.FILETIME),
            ("volume", wintypes.DWORD), ("size_high", wintypes.DWORD),
            ("size_low", wintypes.DWORD), ("links", wintypes.DWORD),
            ("index_high", wintypes.DWORD), ("index_low", wintypes.DWORD),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(FileInformation)]
    kernel.GetFileInformationByHandle.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    # READ_ATTRIBUTES; READ/WRITE sharing, deliberately no DELETE sharing.
    # OPEN_REPARSE_POINT inspects the entry itself; BACKUP_SEMANTICS opens directories.
    # Do not share delete: the pinned directory must not be renamed or replaced
    # while path-based Windows operations are using it.
    handle = kernel.CreateFileW(str(path), 0x80, 0, None, 3, 0x02200000, None)
    if handle == wintypes.HANDLE(-1).value:
        raise OSError("Candidate path cannot be pinned")
    try:
        info = FileInformation()
        if not kernel.GetFileInformationByHandle(handle, ctypes.byref(info)):
            raise OSError("Candidate path cannot be inspected")
        if info.attributes & 0x400 or bool(info.attributes & 0x10) != directory:
            raise ValueError("Candidate path type or reparse point is prohibited")
        if not directory and info.links != 1:
            raise ValueError("Candidate hardlinks are prohibited")
        yield
    finally:
        kernel.CloseHandle(handle)


class Directory:
    def __init__(self, path: Path, fd: int | None, identity: tuple[int, int] | None = None):
        self.path, self.fd, self.identity = path, fd, identity

    def _assert_identity(self) -> None:
        if self.identity is None or self.fd is not None:
            return
        info = self.path.stat()
        if (info.st_dev, info.st_ino) == self.identity:
            return
        parent = self.path.parent
        for entry in parent.iterdir():
            try:
                candidate = entry.stat()
            except OSError:
                continue
            if (candidate.st_dev, candidate.st_ino) == self.identity and stat.S_ISDIR(candidate.st_mode):
                self.path = entry
                return
        raise ValueError("Candidate directory was replaced")

    def names(self):
        return os.listdir(self.path if self.fd is None else self.fd)

    def stat(self, name: str):
        info = (self.path / name).lstat() if self.fd is None else os.stat(name, dir_fd=self.fd, follow_symlinks=False)
        reject_links(info)
        return info

    @contextmanager
    def read_file(self, name: str):
        with ExitStack() as stack:
            if self.fd is None:
                self._assert_identity()
                stack.enter_context(_windows_pin(self.path / name, directory=False))
                fd = os.open(self.path / name, os.O_RDONLY | os.O_BINARY)
            else:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self.fd)
            stack.callback(os.close, fd)
            info = os.fstat(fd)
            reject_links(info)
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("Candidate requires a regular file")
            yield fd

    def create_file(self, name: str) -> int:
        self._assert_identity()
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        return (os.open(self.path / name, flags, 0o600) if self.fd is None else
                os.open(name, flags | os.O_NOFOLLOW, 0o600, dir_fd=self.fd))

    def replace(self, source: str, destination: str) -> None:
        self._assert_identity()
        if self.fd is None:
            os.replace(self.path / source, self.path / destination)
        else:
            os.replace(source, destination, src_dir_fd=self.fd, dst_dir_fd=self.fd)

    def unlink(self, name: str) -> None:
        self._assert_identity()
        if self.fd is None:
            (self.path / name).unlink(missing_ok=True)
        else:
            try:
                os.unlink(name, dir_fd=self.fd)
            except FileNotFoundError:
                pass


@contextmanager
def pinned_directory(root: Path, parts: tuple[str, ...], identity: tuple[int, int], *, create: bool = False):
    with ExitStack() as stack:
        if os.name == "nt":
            # Pin every ancestor before resolving the next component. A rename or
            # junction swap then fails until this operation closes its handles.
            for path in (*reversed(root.parents), root):
                stack.enter_context(_windows_pin(path, directory=True))
            info = root.stat()
            if (info.st_dev, info.st_ino) != identity:
                raise ValueError("Candidate root was replaced")
            path = root
            for part in parts:
                path = path / part
                if create:
                    path.mkdir(mode=0o700, exist_ok=True)
                stack.enter_context(_windows_pin(path, directory=True))
            yield Directory(path, None, (path.stat().st_dev, path.stat().st_ino))
        else:
            if not hasattr(os, "O_NOFOLLOW") or os.open not in os.supports_dir_fd:
                raise OSError("Candidate requires no-follow directory I/O")
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            fd = os.open(root.anchor, flags)
            stack.callback(os.close, fd)
            for part in root.parts[1:]:
                fd = os.open(part, flags, dir_fd=fd)
                stack.callback(os.close, fd)
            info = os.fstat(fd)
            if (info.st_dev, info.st_ino) != identity:
                raise ValueError("Candidate root was replaced")
            for part in parts:
                if create:
                    try:
                        os.mkdir(part, mode=0o700, dir_fd=fd)
                    except FileExistsError:
                        pass
                fd = os.open(part, flags, dir_fd=fd)
                stack.callback(os.close, fd)
            yield Directory(root.joinpath(*parts), fd)

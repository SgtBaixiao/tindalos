"""Exclusive local process lease. All stores must live on a local filesystem."""
from pathlib import Path


def acquire_lease(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    lease = path.open('a+b')
    try:
        import os
        if os.name == 'nt':
            import msvcrt
            if path.stat().st_size == 0:
                lease.write(b'0'); lease.flush()
            lease.seek(0)
            msvcrt.locking(lease.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lease.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        lease.close()
        raise RuntimeError('only one Tindalos runtime or backup may own this database') from exc
    return lease

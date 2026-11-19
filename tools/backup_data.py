"""Create a consistent local data backup while the application is stopped."""
from __future__ import annotations
import argparse
import shutil
import sqlite3
import os
from pathlib import Path


def main():
    p=argparse.ArgumentParser();p.add_argument('--data',type=Path,default=Path(os.environ.get('TINDALOS_DATA_DIR','data')));p.add_argument('--site-db',type=Path);p.add_argument('--out',type=Path,required=True);args=p.parse_args()
    from tindalos.lease import acquire_lease
    args.data.mkdir(parents=True,exist_ok=True)
    site_db=args.site_db or Path(os.environ.get('TINDALOS_SITE_DB',str(args.data/'site.db')))
    if not site_db.resolve().is_relative_to(args.data.resolve()):
        raise SystemExit('Keep the site database inside --data for a complete backup.')
    try:lease=acquire_lease(Path(str(site_db)+'.runtime.lock'))
    except RuntimeError:raise SystemExit('Stop Tindalos before backing up all stores together.')
    if args.out.exists():raise SystemExit('Backup destination already exists; choose a new directory.')
    if args.out.resolve().is_relative_to(args.data.resolve()):raise SystemExit('Backup must be outside the data directory.')
    args.out.mkdir(parents=True)
    for source in args.data.rglob('*'):
        if not source.is_file() or source.name.endswith(('-wal','-shm','.lock')):continue
        target=args.out/source.relative_to(args.data);target.parent.mkdir(parents=True,exist_ok=True)
        if source.suffix in ('.sqlite','.sqlite3','.db'):
            with sqlite3.connect(source) as db,sqlite3.connect(target) as backup:db.backup(backup)
        else:shutil.copy2(source,target)
    lease.close();print(args.out.resolve())
if __name__=='__main__':main()

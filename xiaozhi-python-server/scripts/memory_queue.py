"""Inspect queue metadata or retry a blocked ORIGINAL Commit after stopping Python."""
import argparse
import os
import sqlite3
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['status', 'retry-blocked'])
    parser.add_argument('--id', type=int)
    parser.add_argument('--db', default=os.environ.get('XIAOZHI_MEMORY_OUTBOX', str(Path(__file__).resolve().parents[1] / 'data/memory-outbox.sqlite3')))
    args = parser.parse_args()
    if not Path(args.db).is_file():
        parser.error('Queue file does not exist')
    # Reuse the same process lock for repair. Never race a running worker.
    if args.action == 'retry-blocked':
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from core.providers.memory.memory_v2.background import MemoryQueue
        if args.id is None:
            parser.error('--id is required')
        queue = MemoryQueue(args.db)
        try:
            with queue.db:
                result = queue.db.execute("UPDATE jobs SET state='pending',attempts=0,result=NULL WHERE id=? AND state='blocked' AND proposal IS NOT NULL", (args.id,))
            if result.rowcount != 1:
                parser.error('Only a blocked original Commit can be retried')
            print('Original Commit retained. Start Python and connect the corresponding role to resume.')
        finally:
            queue.close()
    else:
        with sqlite3.connect(Path(args.db).resolve().as_uri() + '?mode=ro', uri=True) as db:
            for row in db.execute("SELECT id,substr(scope,1,12),state,attempts,proposal IS NOT NULL FROM jobs WHERE state IN ('pending','blocked','failed') ORDER BY id"):
                print('id=%s scope=%s state=%s attempts=%s has_proposal=%s' % row)


if __name__ == '__main__':
    main()

import hashlib
import mimetypes
from pathlib import Path
import uuid
from .db import ROOT


def inventory(run_id, attempt, folder):
    folder=Path(folder).resolve()
    if not folder.is_relative_to(ROOT.resolve()):raise ValueError('Artifact root outside storage')
    result=[]
    for path in sorted(folder.rglob('*')):
        if not path.is_file() or path.is_symlink() or 'mlruns' in path.relative_to(folder).parts:continue
        if len(result)>=200:raise ValueError('Too many indexed artifacts')
        digest=hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
        result.append((uuid.uuid4(),run_id,attempt,str(path.relative_to(folder)),str(path.relative_to(ROOT)),
                       digest.hexdigest(),path.stat().st_size,mimetypes.guess_type(path.name)[0] or 'application/octet-stream'))
    return result


def insert(conn, items):
    for item in items:
        conn.execute('''INSERT INTO artifacts(id,run_id,attempt,name,path,sha256,size,media_type)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(run_id,attempt,name) DO NOTHING''',item)

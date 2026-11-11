"""Owner-only local observation records. Never execute captured input."""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import threading
import time
import urllib.request
import uuid


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()


class Store:
    def __init__(self,path):
        self.path=Path(path).absolute()
        if self.path.is_symlink():raise OSError('private store must not be a symlink')
        self.path.mkdir(mode=0o700,parents=True,exist_ok=True)
        if self.path.stat().st_uid!=os.getuid():raise PermissionError('private store owner mismatch')
        self.path.chmod(0o700)
        self.lock=threading.Lock()

    def _path(self,name):
        if name != '.gitignore' and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*',name):raise ValueError('invalid store filename')
        return self.path/name

    def append(self,name,record):
        body=(json.dumps(record,ensure_ascii=False,allow_nan=False)+'\n').encode()
        with self.lock:
            fd=os.open(self._path(name),os.O_WRONLY|os.O_CREAT|os.O_APPEND|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'ab') as handle:
                if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):raise OSError('store target must be regular file')
                os.fchmod(handle.fileno(),0o600)
                handle.write(body);handle.flush();os.fsync(handle.fileno())

    def read(self,name):
        try:fd=os.open(self._path(name),os.O_RDONLY|os.O_NOFOLLOW)
        except FileNotFoundError:return []
        with os.fdopen(fd,'r',encoding='utf-8') as handle:
            info=os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077:
                raise PermissionError('private record permissions/owner invalid')
            return [json.loads(line) for line in handle if line.strip()]

    def write_text(self,name,text):
        path=self._path(name)
        # NOFOLLOW prevents following an existing malicious symlink. This writes
        # derived reports only; immutable raw records use append().
        with self.lock:
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'w',encoding='utf-8') as handle:
                if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):raise OSError('store target must be regular file')
                os.fchmod(handle.fileno(),0o600)
                handle.write(text);handle.flush();os.fsync(handle.fileno())


def new_capture(payload,p,threshold,model,error=None,origin='traffic'):
    if p is not None and (isinstance(p,bool) or not isinstance(p,(int,float)) or not math.isfinite(p) or not 0<=p<=1):
        raise ValueError('invalid probability')
    if error is not None and not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',error):
        raise ValueError('error must be a type name, never exception text')
    if p is None and error is None:raise ValueError('missing result must have error type')
    if error is not None and p is not None:raise ValueError('error must not have a score')
    if not math.isfinite(threshold) or not 0<=threshold<=1:raise ValueError('invalid threshold')
    if origin not in {'traffic','verification'}:raise ValueError('invalid origin')
    # Roundtrip freezes exactly the JSON values sent, independent of later mutation.
    frozen=json.loads(json.dumps(payload,ensure_ascii=False,allow_nan=False))
    return {'schema':1,'id':str(uuid.uuid4()),'time':time.time(),'origin':origin,
            'payload':frozen,'input_sha256':canonical_hash(frozen) if frozen is not None else None,
            'policy_sha256':canonical_hash(frozen['questions']) if frozen and 'questions' in frozen else None,
            'score':p,'threshold':threshold,'would_block':p>=threshold if p is not None else None,
            'model':model,'error':error}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        raise ValueError('local inference redirect refused')


def model_info(base_url):
    """Fetch local backend provenance, hash its actual known decision weights.

    Backend aliases alone are not model identity. No credentials or external
    network are used; this function never follows redirects or environment proxies.
    """
    if base_url not in {'http://127.0.0.1:18872','http://127.0.0.1:18874'}:
        raise ValueError('only explicit local decision backends permitted')
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    responses=[]
    for suffix in ['/v1/models','/props']:
        with opener.open(base_url+suffix,timeout=10) as handle:responses.append(json.load(handle))
    models,props=responses
    native_path=props['model_path'].replace('\\','/')
    match=re.fullmatch(r'D:/textgen/models/decision/(vllm-d3-lite-Q8_0\.gguf|qev-0\.8b-text-Q8_0\.gguf)',native_path)
    if match is None:raise ValueError('unexpected local model path; provenance not established')
    local_path=Path('/mnt/d/textgen/models/decision')/match.group(1)
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    from run import metadata
    gguf=metadata(local_path)
    with local_path.open('rb') as handle:digest=hashlib.file_digest(handle,'sha256').hexdigest()
    native=gguf['metadata']
    template=native.get('tokenizer.chat_template.systemone')
    if not template:raise ValueError('native System One template absent')
    return {'base_url':base_url,'model_path':native_path,'model_id':models['data'][0]['id'],
            'model_file_sha256':digest,'bytes':gguf['bytes'],'server_build':props.get('build_info'),
            'model_meta':models['data'][0].get('meta'),
            'native_template_sha256':hashlib.sha256(template.encode()).hexdigest(),
            'decision_metadata':{k:v for k,v in native.items() if '.decision.' in k}}

"""Runtime readers may generate concurrently; package mutations are exclusive."""
import threading,time

class ProfileGate:
    def __init__(self):
        self.cv=threading.Condition();self.writer=None;self.depth=0;self.readers={}
    def acquire(self,blocking=True,timeout=-1):
        ident=threading.get_ident()
        with self.cv:
            ok=self.cv.wait_for(lambda: self.writer==ident or (self.writer is None and not self.readers),timeout=None if blocking and timeout<0 else max(0,timeout) if blocking else 0)
            if ok:self.writer=ident;self.depth+=1
            return ok
    def release(self):
        with self.cv:
            if self.writer!=threading.get_ident():raise RuntimeError('Runtime write lock not owned')
            self.depth-=1
            if not self.depth:self.writer=None;self.cv.notify_all()
    def __enter__(self):self.acquire();return self
    def __exit__(self,*args):self.release()
    def reader(self):return Reader(self)

class Reader:
    def __init__(self,gate):self.gate=gate
    def acquire(self,blocking=True,timeout=-1):
        g=self.gate;ident=threading.get_ident()
        with g.cv:
            ok=g.cv.wait_for(lambda:g.writer is None or g.writer==ident,timeout=None if blocking and timeout<0 else max(0,timeout) if blocking else 0)
            if ok:g.readers[ident]=g.readers.get(ident,0)+1
            return ok
    def release(self):
        g=self.gate;ident=threading.get_ident()
        with g.cv:
            if ident not in g.readers:raise RuntimeError('Runtime read lock not owned')
            g.readers[ident]-=1
            if not g.readers[ident]:del g.readers[ident];g.cv.notify_all()

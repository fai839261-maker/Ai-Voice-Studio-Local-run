"""Adapt known Kokoro input arrays to the ONNX graph's declared types."""
class TypedSession:
    def __init__(self, session):
        self.session = session
    def __getattr__(self, name):
        return getattr(self.session, name)
    def run(self, outputs, feeds, *args, **kwargs):
        import numpy as np
        types = {'tensor(float)':np.float32,'tensor(float16)':np.float16,'tensor(double)':np.float64,'tensor(int32)':np.int32,'tensor(int64)':np.int64}
        expected = {x.name:x.type for x in self.session.get_inputs()}
        feeds = dict(feeds)
        # Older Kokoro exports use tokens, newer ones input_ids.
        for old,new in [('tokens','input_ids'),('input_ids','tokens')]:
            if old in feeds and old not in expected and new in expected:
                feeds[new] = feeds.pop(old)
        for name in ('speed','style','tokens','input_ids'):
            if name in feeds and expected.get(name) in types:
                feeds[name] = np.asarray(feeds[name],dtype=types[expected[name]])
        return self.session.run(outputs,feeds,*args,**kwargs)

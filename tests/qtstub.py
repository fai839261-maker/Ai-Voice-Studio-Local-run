"""Test-only: stub QtMultimedia (sandbox has no audio device)."""
import sys,types
from PySide6.QtCore import QObject,Signal
class QAudioOutput(QObject):
    def __init__(self,p=None):super().__init__(p);self.v=1
    def setVolume(self,v):self.v=v
    def volume(self):return self.v
class QMediaPlayer(QObject):
    durationChanged=Signal('qint64');positionChanged=Signal('qint64');errorOccurred=Signal(object,str)
    playbackStateChanged=Signal(object)
    def __init__(self,p=None):super().__init__(p);self._pos=0;self._src=None;self.state='stopped'
    def setAudioOutput(self,a):pass
    def setSource(self,u):self._src=u
    def source(self):return self._src
    def play(self):self.state='playing'
    def pause(self):self.state='paused'
    def stop(self):self.state='stopped'
    def position(self):return self._pos
    def duration(self):return 60000
    def setPosition(self,p):self._pos=p
mod=types.ModuleType('PySide6.QtMultimedia');mod.QMediaPlayer=QMediaPlayer;mod.QAudioOutput=QAudioOutput
sys.modules['PySide6.QtMultimedia']=mod
class _PS:PlayingState='playing';PausedState='paused';StoppedState='stopped'
QMediaPlayer.PlaybackState=_PS
QMediaPlayer.playbackState=lambda self:self.state

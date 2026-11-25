"""Qt editor for a selected reference interval; original audio is never modified."""
from pathlib import Path
import threading,uuid
from PySide6.QtCore import QUrl,Qt,Signal,QObject,QRectF
from PySide6.QtMultimedia import QMediaPlayer,QAudioOutput
from PySide6.QtGui import QPainter,QColor,QPen,QKeySequence,QShortcut
from PySide6.QtWidgets import (QWidget,QSizePolicy,QDialog,QVBoxLayout,QFormLayout,QDoubleSpinBox,QLabel,QPushButton,QDialogButtonBox,QHBoxLayout,QMessageBox,QProgressDialog,QSlider)
from .core import subdir
from .reference_audio import edit_reference,export_audio

class Result(QObject):
    done=Signal(object,object)

class BusyDialog(QProgressDialog):
    def reject(self):
        # Do not allow Escape to discard an active background write.
        pass
    def closeEvent(self,event):
        event.ignore()

def run_dialog(parent,title,fn):
    # Modal progress dialog keeps GUI event loop responsive and prevents closing/moving mid-write.
    dialog=BusyDialog(title,'',0,0,parent);dialog.setCancelButton(None);dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
    dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowType.WindowCloseButtonHint);dialog.setMinimumDuration(0)
    signal=Result();result=[]
    def finish(value,error):result.append((value,error));dialog.accept()
    signal.done.connect(finish,Qt.ConnectionType.QueuedConnection)
    def work():
        try:signal.done.emit(fn(),None)
        except Exception as e:signal.done.emit(None,str(e))
    threading.Thread(target=work,daemon=True).start();dialog.exec()
    if not result:raise RuntimeError('Audio operation interrupted.')
    value,error=result[0]
    if error:raise RuntimeError(error)
    return value

PPS = 200            # one peak every 5 ms: the sound picture stays sharp even when zoomed in a lot
MIN_VIEW = 0.25      # the closest zoom shows a quarter of a second
_STEPS = (0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200)


def clock(seconds, tenths=False):
    seconds = max(0.0, float(seconds))
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    if tenths:
        text = f"{int(m):02d}:{s:04.1f}"
    else:
        text = f"{int(m):02d}:{int(s):02d}"
    return f"{int(h)}:{text}" if h >= 1 else text


class PeakLoader(QObject):
    """Reads the whole sound in the background and makes the sound picture (one peak every 5 ms). The window never waits for it."""
    ready = Signal(object, float)
    progress = Signal(float)

    def __init__(self, path):
        super().__init__()
        self.path = path
        self.stop = False

    def start(self):
        threading.Thread(target=self._run, daemon=True, name="avs-sound-picture").start()

    def _run(self):
        import array, sys, wave
        peaks = array.array('f')
        duration = 0.0
        try:
            with wave.open(str(self.path), 'rb') as w:
                if w.getsampwidth() != 2:
                    self.ready.emit(peaks, 0.0)
                    return
                rate, channels, total = w.getframerate(), w.getnchannels(), w.getnframes()
                duration = total / rate if rate else 0.0
                per = max(1, rate // PPS)
                block = per * PPS * 4                      # about 4 seconds per read
                done, last = 0, 0.0
                while not self.stop:
                    raw = w.readframes(block)
                    if not raw:
                        break
                    samples = array.array('h')
                    samples.frombytes(raw[:len(raw) // (2 * channels) * 2 * channels])
                    if sys.byteorder == 'big':
                        samples.byteswap()
                    if channels > 1:
                        samples = samples[::channels]
                    for i in range(0, len(samples), per):
                        seg = samples[i:i + per]
                        if len(seg):
                            peaks.append(max(max(seg), -min(seg)) / 32768.0)
                    done += len(raw) // (2 * channels)
                    if total and done / total - last >= 0.03:
                        last = done / total
                        self.progress.emit(last)
        except Exception:
            pass
        if not self.stop:
            self.ready.emit(peaks, duration)


class WavePick(QWidget):
    """The sound as a picture that can be zoomed. Mouse wheel = zoom, Shift + wheel or right-drag = move, drag = choose the part to keep,
    drag the two edges to fine-tune, click = hear from there. Quiet gaps are shaded so you can see where to start and where to stop."""
    changed = Signal(float, float)
    seek = Signal(float)
    viewChanged = Signal(float, float)
    loaded = Signal()

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(170)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(190)
        self.setMouseTracking(True)
        self.peaks = []
        self.duration = 0.0
        self.start = 0.0
        self.end = 0.0
        self.position = 0.0
        self.view_start = 0.0
        self.view_len = 60.0
        self.loading = 0.0
        self.scale = 1.0
        self.drag = None
        self.anchor = 0.0
        self.pan_from = None
        self._cols = None
        self._moved = False
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.loader = PeakLoader(path)
        self.loader.ready.connect(self._loaded, Qt.ConnectionType.QueuedConnection)
        self.loader.progress.connect(self._progress, Qt.ConnectionType.QueuedConnection)
        self.loader.start()

    # ---- loading
    def _progress(self, fraction):
        self.loading = fraction
        self.update()

    def _loaded(self, peaks, duration):
        self.peaks = peaks
        self.duration = duration
        self.loading = 1.0
        top = max(peaks) if len(peaks) else 0.0
        self.scale = min(8.0, 0.9 / top) if top > 0.01 else 1.0      # quiet recordings are drawn taller so speech is easy to see
        self.start, self.end = 0.0, min(duration, 15.0)
        self.view_start, self.view_len = 0.0, max(MIN_VIEW, min(duration, 60.0))
        self._cols = None
        self.update()
        self.viewChanged.emit(self.view_start, self.view_len)
        self.changed.emit(self.start, self.end)
        self.loaded.emit()

    def stop_loading(self):
        self.loader.stop = True

    # ---- view
    def _clamp_view(self):
        self.view_len = max(MIN_VIEW, min(self.view_len, self.duration or self.view_len))
        self.view_start = max(0.0, min(self.view_start, max(0.0, (self.duration or 0.0) - self.view_len)))

    def set_view(self, start, length):
        self.view_start, self.view_len = start, length
        self._clamp_view()
        self._cols = None
        self.update()
        self.viewChanged.emit(self.view_start, self.view_len)

    def zoom_by(self, factor, anchor=None):
        if not self.duration:
            return
        anchor = self.view_start + self.view_len / 2 if anchor is None else anchor
        ratio = (anchor - self.view_start) / self.view_len if self.view_len else 0.5
        length = max(MIN_VIEW, min(self.duration, self.view_len * factor))
        self.set_view(anchor - ratio * length, length)

    def fit_all(self):
        self.set_view(0.0, self.duration or 60.0)

    def zoom_selection(self):
        length = max(MIN_VIEW * 4, (self.end - self.start) * 1.4)
        middle = (self.start + self.end) / 2
        self.set_view(middle - length / 2, length)

    def move_view(self, fraction):
        self.set_view(self.view_start + self.view_len * fraction, self.view_len)

    def center_on(self, seconds):
        self.set_view(seconds - self.view_len / 2, self.view_len)

    def follow(self, seconds):
        """While playing, keep the playhead inside the picture."""
        if seconds < self.view_start or seconds > self.view_start + self.view_len:
            self.set_view(seconds - self.view_len * 0.15, self.view_len)

    def set_range(self, start, end):
        self.start, self.end = max(0.0, start), min(self.duration or end, max(start, end))
        self.update()

    def set_position(self, seconds):
        self.position = seconds
        self.update()

    # ---- coordinates
    def _w(self):
        return max(1, self.width() - 16)

    def _x(self, seconds):
        return 8 + self._w() * ((seconds - self.view_start) / self.view_len if self.view_len else 0)

    def _t(self, x):
        return self.view_start + (x - 8) / self._w() * self.view_len

    def _ruler_height(self):
        return 22

    # ---- drawing
    def _columns(self):
        width = self._w()
        key = (round(self.view_start, 4), round(self.view_len, 4), width, len(self.peaks))
        if self._cols and self._cols[0] == key:
            return self._cols[1]
        cols = []
        n = len(self.peaks)
        per_px = self.view_len / width
        for px in range(width):
            i0 = int((self.view_start + px * per_px) * PPS)
            i1 = max(i0 + 1, int((self.view_start + (px + 1) * per_px) * PPS))
            if i0 >= n or i0 < 0:
                cols.append(0.0)
            else:
                cols.append(max(self.peaks[i0:min(i1, n)]))
        self._cols = (key, cols)
        return cols

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        pal = self.palette()
        back = pal.color(self.backgroundRole()).darker(115)
        accent = pal.color(pal.ColorRole.Highlight)
        text = pal.color(pal.ColorRole.Text)
        p.fillRect(self.rect(), back)
        top = self._ruler_height()
        if not len(self.peaks):
            p.setPen(text)
            message = f"Drawing the sound picture… {int(self.loading * 100)}%" if self.loading < 1.0 else "The sound picture is not available for this file. Use the Start and End boxes."
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, message)
            return
        height = self.height()
        mid = top + (height - top) / 2
        half = (height - top) / 2 - 4
        cols = self._columns()
        # quiet gaps: a soft band so you can see where nobody speaks
        quiet = 0.04 / max(0.0001, self.scale) if self.scale else 0.04
        run = None
        for px, pk in enumerate(cols + [1.0]):
            is_quiet = pk < quiet
            if is_quiet and run is None:
                run = px
            elif not is_quiet and run is not None:
                if px - run >= 3:
                    p.fillRect(QRectF(8 + run, top, px - run, height - top), QColor(120, 140, 170, 38))
                run = None
        sel0, sel1 = self._x(self.start), self._x(self.end)
        p.fillRect(QRectF(max(8, sel0), top, max(1.0, min(sel1, 8 + self._w()) - max(8, sel0)), height - top), QColor(accent.red(), accent.green(), accent.blue(), 55))
        for px, pk in enumerate(cols):
            x = 8 + px
            h = max(1.0, min(1.0, pk * self.scale) * half)
            inside = sel0 <= x <= sel1
            p.setPen(QColor(accent) if inside else QColor(130, 150, 175))
            p.drawLine(x, int(mid - h), x, int(mid + h))
        # ruler
        step = next((s for s in _STEPS if s / self.view_len * self._w() >= 80), _STEPS[-1])
        first = int(self.view_start / step) * step
        p.setPen(QPen(QColor(text.red(), text.green(), text.blue(), 150), 1))
        t = first
        while t <= self.view_start + self.view_len + step:
            x = self._x(t)
            if 8 <= x <= 8 + self._w():
                p.drawLine(int(x), 0, int(x), top - 4)
                p.drawText(int(x) + 4, top - 7, clock(t, tenths=step < 1))
                p.setPen(QPen(QColor(text.red(), text.green(), text.blue(), 40), 1))
                p.drawLine(int(x), top, int(x), height)
                p.setPen(QPen(QColor(text.red(), text.green(), text.blue(), 150), 1))
            t += step
        # the two edges of the chosen part
        for x, label in ((sel0, "START"), (sel1, "END")):
            if 8 <= x <= 8 + self._w():
                p.setPen(QPen(accent, 3))
                p.drawLine(int(x), top, int(x), height)
                p.setPen(accent)
                p.drawText(int(x) + (4 if label == "START" else -34), top + 12, label)
        px = self._x(self.position)
        if 8 <= px <= 8 + self._w():
            p.setPen(QPen(QColor(255, 255, 255, 220), 1))
            p.drawLine(int(px), top, int(px), height)

    # ---- mouse
    def wheelEvent(self, e):
        if not self.duration:
            return
        steps = e.angleDelta().y() / 120.0
        if not steps:
            return
        if e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.move_view(-steps * 0.15)
        else:
            self.zoom_by(0.8 ** steps, self._t(e.position().x()))
        e.accept()

    def mousePressEvent(self, e):
        if not self.duration:
            return
        x = e.position().x()
        if e.button() in (Qt.MouseButton.RightButton, Qt.MouseButton.MiddleButton):
            self.pan_from = (x, self.view_start)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        s0, s1 = self._x(self.start), self._x(self.end)
        self._moved = False
        if abs(x - s0) <= 9:
            self.drag = 'start'
        elif abs(x - s1) <= 9:
            self.drag = 'end'
        else:
            self.drag = 'new'
            self.anchor = max(0.0, min(self.duration, self._t(x)))
            self.press_x = x
        self.update()

    def mouseMoveEvent(self, e):
        x = e.position().x()
        if self.pan_from is not None:
            self.set_view(self.pan_from[1] - (x - self.pan_from[0]) / self._w() * self.view_len, self.view_len)
            return
        if not self.drag:
            near = self.duration and (abs(x - self._x(self.start)) <= 9 or abs(x - self._x(self.end)) <= 9)
            self.setCursor(Qt.CursorShape.SizeHorCursor if near else Qt.CursorShape.CrossCursor)
            return
        if self.drag == 'new' and abs(x - self.press_x) < 4 and not self._moved:
            return
        self._moved = True
        t = max(0.0, min(self.duration, self._t(x)))
        # dragging past the edge of the picture moves the picture along
        if x < 8:
            self.set_view(self.view_start - self.view_len * 0.03, self.view_len)
        elif x > 8 + self._w():
            self.set_view(self.view_start + self.view_len * 0.03, self.view_len)
        if self.drag == 'start':
            self.start = min(t, self.end)
        elif self.drag == 'end':
            self.end = max(t, self.start)
        else:
            self.start, self.end = min(self.anchor, t), max(self.anchor, t)
        self.update()
        self.changed.emit(self.start, self.end)

    def mouseReleaseEvent(self, e):
        if self.pan_from is not None:
            self.pan_from = None
            self.setCursor(Qt.CursorShape.CrossCursor)
            return
        if self.drag == 'new' and not self._moved:
            self.seek.emit(max(0.0, min(self.duration, self._t(e.position().x()))))     # a click only moves the playhead; the chosen part stays
        self.drag = None
        self.update()


class Overview(QWidget):
    """The whole file in one thin strip. The lit window is what the big picture shows; click or drag to move it."""
    moved = Signal(float)

    def __init__(self, wave, parent=None):
        super().__init__(parent)
        self.wave = wave
        self.setFixedHeight(48)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cache = None
        wave.viewChanged.connect(lambda *_: self.update())
        wave.changed.connect(lambda *_: self.update())
        wave.loaded.connect(self._reset)

    def _reset(self):
        self._cache = None
        self.update()

    def _bars(self, width):
        if self._cache and self._cache[0] == width:
            return self._cache[1]
        peaks = self.wave.peaks
        n = len(peaks)
        bars = []
        for px in range(width):
            i0, i1 = n * px // width, max(n * px // width + 1, n * (px + 1) // width)
            bars.append(max(peaks[i0:i1]) if n and i0 < n else 0.0)
        self._cache = (width, bars)
        return bars

    def paintEvent(self, _e):
        p = QPainter(self)
        pal = self.palette()
        accent = pal.color(pal.ColorRole.Highlight)
        p.fillRect(self.rect(), pal.color(self.backgroundRole()).darker(125))
        w = self.wave
        if not len(w.peaks) or not w.duration:
            return
        width = max(1, self.width() - 16)
        mid = self.height() / 2
        p.setPen(QColor(130, 150, 175))
        for px, pk in enumerate(self._bars(width)):
            h = max(1.0, min(1.0, pk * w.scale) * (mid - 4))
            p.drawLine(8 + px, int(mid - h), 8 + px, int(mid + h))
        x0 = 8 + width * w.view_start / w.duration
        x1 = 8 + width * (w.view_start + w.view_len) / w.duration
        p.fillRect(QRectF(x0, 0, max(3.0, x1 - x0), self.height()), QColor(accent.red(), accent.green(), accent.blue(), 70))
        p.setPen(QPen(accent, 2))
        p.drawRect(QRectF(x0, 1, max(3.0, x1 - x0), self.height() - 2))
        a, b = 8 + width * w.start / w.duration, 8 + width * w.end / w.duration
        p.setPen(QPen(QColor(255, 255, 255, 230), 2))
        p.drawLine(int(a), 0, int(a), 8)
        p.drawLine(int(b), 0, int(b), 8)

    def _go(self, e):
        if self.wave.duration:
            width = max(1, self.width() - 16)
            self.moved.emit(max(0.0, min(self.wave.duration, (e.position().x() - 8) / width * self.wave.duration)))

    def mousePressEvent(self, e):
        self._go(e)

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MouseButton.LeftButton:
            self._go(e)


class ReferenceDialog(QDialog):
    def __init__(self, parent, source):
        super().__init__(parent)
        self.source = Path(source)
        self.result_file = None
        self.metadata = {}
        self.previewing = False
        self.setWindowTitle('Choose the part to use')
        self.resize(1040, 700)
        self.setSizeGripEnabled(True)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(self.source.name))
        hint = QLabel('Zoom in with the mouse wheel (Shift + wheel or right-drag to move). Drag over the picture to choose the part (5 to 15 seconds of clear speech is best). '
                      'Shaded bands are quiet gaps. Click to listen from that spot, then use “Set start here” and “Set end here”.')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.wave = WavePick(self.source)
        self.overview = Overview(self.wave)
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.audio.setVolume(.8)
        # zoom row
        zoom = QHBoxLayout()
        for name, tip, fn in (('−  Zoom out', 'Show more of the sound', lambda: self.wave.zoom_by(1.6)),
                              ('+  Zoom in', 'Look closer', lambda: self.wave.zoom_by(0.625)),
                              ('◀', 'Move left', lambda: self.wave.move_view(-0.5)),
                              ('▶', 'Move right', lambda: self.wave.move_view(0.5)),
                              ('Show all', 'The whole file', self.wave.fit_all),
                              ('Zoom to my part', 'Fill the picture with the chosen part', self.wave.zoom_selection),
                              ('Go to playhead', 'Move the picture to where the sound is playing', lambda: self.wave.center_on(self.player.position() / 1000))):
            b = QPushButton(name)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            zoom.addWidget(b)
        zoom.addStretch(1)
        self.view_note = QLabel('')
        zoom.addWidget(self.view_note)
        layout.addLayout(zoom)
        layout.addWidget(self.wave)
        layout.addWidget(QLabel('Whole file (click to move the picture):'))
        layout.addWidget(self.overview)
        # times
        self.start = QDoubleSpinBox()
        self.end = QDoubleSpinBox()
        self.gain = QDoubleSpinBox()
        for field in (self.start, self.end):
            field.setRange(0, 86400)
            field.setDecimals(2)
            field.setSuffix(' s')
            field.setSingleStep(.1)
        self.end.setValue(10)
        self.gain.setRange(-24, 24)
        self.gain.setSuffix(' dB')
        self.gain.setSingleStep(1)
        times = QHBoxLayout()
        for text, box in (('Start', self.start), ('End', self.end), ('Gain', self.gain)):
            times.addWidget(QLabel(text))
            times.addWidget(box)
        times.addStretch(1)
        layout.addLayout(times)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 0)
        layout.addWidget(self.slider)
        self.player.durationChanged.connect(self.duration)
        self.player.positionChanged.connect(self.position)
        self.slider.sliderMoved.connect(self.player.setPosition)
        row = QHBoxLayout()
        for name, fn in [('Play selection', self.play), ('Pause', self.player.pause), ('Play from here', self.play_here),
                         ('Set start here', lambda: self.start.setValue(self.player.position() / 1000)),
                         ('Set end here', lambda: self.end.setValue(self.player.position() / 1000))]:
            b = QPushButton(name)
            b.clicked.connect(fn)
            row.addWidget(b)
        layout.addLayout(row)
        self.info = QLabel('')
        self.info.setWordWrap(True)
        layout.addWidget(self.info)
        self.wave.changed.connect(self._from_wave)
        self.wave.seek.connect(self._seek_play)
        self.wave.viewChanged.connect(self._view_note)
        self.overview.moved.connect(self.wave.center_on)
        self.start.valueChanged.connect(self._from_spin)
        self.end.valueChanged.connect(self._from_spin)
        self._from_spin()
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Save).setText('Use this part')
        bb.accepted.connect(self.save)
        bb.rejected.connect(self.reject)
        layout.addWidget(bb)
        for key, fn in (('Space', self.toggle), ('S', lambda: self.start.setValue(self.player.position() / 1000)),
                        ('E', lambda: self.end.setValue(self.player.position() / 1000)),
                        ('+', lambda: self.wave.zoom_by(0.625)), ('=', lambda: self.wave.zoom_by(0.625)), ('-', lambda: self.wave.zoom_by(1.6))):
            QShortcut(QKeySequence(key), self, activated=fn)
        self.player.setSource(QUrl.fromLocalFile(str(self.source)))

    def toggle(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.previewing = False
            self.player.play()

    def _view_note(self, start, length):
        self.view_note.setText(f'Showing {clock(start)} – {clock(start + length)}  ({length:.1f} s wide)')

    def _seek_play(self, t):
        self.previewing = False
        self.player.setPosition(round(t * 1000))
        self.player.play()

    def play_here(self):
        self.previewing = False
        self.player.play()

    def _from_wave(self, a, b):
        for box, v in ((self.start, a), (self.end, b)):
            box.blockSignals(True)
            box.setValue(v)
            box.blockSignals(False)
        self._note()

    def _from_spin(self, *_):
        self.wave.set_range(self.start.value(), self.end.value())
        self.overview.update()
        self._note()

    def _note(self):
        length = max(0.0, self.end.value() - self.start.value())
        if length < 0.5:
            text = 'Choose at least half a second.'
        elif length > 30:
            text = f'Selected {length:.1f} s ({clock(self.start.value(), True)} – {clock(self.end.value(), True)}). Longer than 30 seconds: the voice may match less exactly. 5 to 15 seconds is best.'
        else:
            text = f'Selected {length:.1f} s ({clock(self.start.value(), True)} – {clock(self.end.value(), True)}) ✔'
        self.info.setText(text)

    def duration(self, n):
        if n > 0:
            self.slider.setRange(0, n)
            self.start.setMaximum(n / 1000)
            self.end.setMaximum(n / 1000)
            if not len(self.wave.peaks):                  # the picture is still being drawn: keep the first 15 seconds as the start choice
                self.end.setValue(min(n / 1000, 15))
            self._from_spin()

    def position(self, n):
        if not self.slider.isSliderDown():
            self.slider.setValue(n)
        self.wave.set_position(n / 1000)
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.wave.follow(n / 1000)
        if self.previewing and n >= self.end.value() * 1000:
            self.player.pause()
            self.previewing = False

    def play(self):
        self.previewing = True
        self.player.setPosition(round(self.start.value() * 1000))
        self.player.play()
        self.wave.follow(self.start.value())

    def save(self):
        self.player.stop()
        dest = subdir('clones') / (uuid.uuid4().hex + '-edited.wav')
        try:
            start, end, gain = self.start.value(), self.end.value(), self.gain.value()
            self.metadata = run_dialog(self, 'Preparing reference…', lambda: edit_reference(self.source, dest, start, end, gain))
            self.result_file = dest
            self.accept()
        except Exception as e:
            dest.unlink(missing_ok=True)
            QMessageBox.warning(self, 'Reference audio', str(e))

    def done(self, result):
        self.wave.stop_loading()
        self.player.stop()
        self.player.setSource(QUrl())
        super().done(result)


EXPORT_FILTER='WAV audio (*.wav);;MP3 audio (*.mp3);;MPEG Layer II audio (*.mp2 *.mpeg)'

def export_dialog(parent,source):
    from PySide6.QtWidgets import QFileDialog
    path,kind=QFileDialog.getSaveFileName(parent,'Export audio',str(Path.home()/Path(source).name),EXPORT_FILTER)
    if not path:return None
    ext='.mp3' if kind.startswith('MP3') else '.mp2' if kind.startswith('MPEG') else '.wav'
    p=Path(path)
    if not p.suffix:p=p.with_suffix(ext)
    return run_dialog(parent,'Exporting audio…',lambda:export_audio(source,p))

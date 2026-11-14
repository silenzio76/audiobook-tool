"""Qt editor composition and application lifecycle."""
from ui_language import set_language, tr
from version_info import PUBLIC_NAME, __build_version__
import queue
from PySide6 import QtCore, QtWidgets as Qt


from editor_view import EditorViewMixin
from editor_preferences import EditorPreferencesMixin
from editor_document import EditorDocumentMixin
from editor_playback import EditorPlaybackMixin


class Editor(EditorViewMixin, EditorPreferencesMixin, EditorDocumentMixin, EditorPlaybackMixin, Qt.QMainWindow):
    def __init__(self, path, audio, auto_read=False, *, settings=None):
        super().__init__()
        self.audio = audio
        self.settings = settings if settings is not None else QtCore.QSettings('Di Spiriti e Scisma', 'Audiolibro')
        self.path = None
        self.plan = {'segments': []}
        self.events = queue.Queue()
        self.pipeline = None
        self.closing = self.stopped = self.exporting = False
        self.language_switch_requested = False
        self.resume_after_language = None
        self.resume_request = None
        self.playing_index = self.replaying_index = None
        self.follow_playback = True
        self.export_requested = False
        self.generate_only = False
        self.junction_work = None
        self.last_audio = self.generation_started = self.transport = None
        self.generated = self.converted = self.played = self.position = self.total = 0
        self.conversion_started = None
        self.voice_statistics = {'generated': 0, 'cached': 0, 'source': 0}
        self.direct_statistics = {'generated':0,'cached':0}
        self.edit_controls = []
        self.icons = {}
        self.setWindowTitle(PUBLIC_NAME)
        self.build()
        self.restore_preferences()
        self.voice_mode.currentIndexChanged.connect(self.refresh_narrator_label)
        self.voice_design.toggled.connect(self.refresh_narrator_label)
        self.refresh_narrator_label()
        area = self.screen().availableGeometry()
        self.resize(min(1220, area.width() - 40), min(840, area.height() - 40))
        self.poll_timer = QtCore.QTimer(self, interval=100)
        self.poll_timer.timeout.connect(self.poll)
        self.poll_timer.start()
        self.auto_timer = QtCore.QTimer(self, interval=200, singleShot=True)
        self.auto_timer.timeout.connect(lambda: self.start(continuous=True, from_start=True))
        if path is not None:
            self.load_document(path)
        if auto_read and self.path is not None:
            self.auto_timer.start()
        for control in self.edit_controls:
            control.setEnabled(self.path is not None)
        self.update_transport()

    @property
    def busy(self):
        return self.pipeline is not None


def run(path, audio, auto_read=False):
    app = Qt.QApplication.instance()
    if app is None:
        app = Qt.QApplication([])
        if 'windows11' in Qt.QStyleFactory.keys():
            app.setStyle('windows11')
    app.setApplicationDisplayName(PUBLIC_NAME)
    settings = QtCore.QSettings('Di Spiriti e Scisma', 'Audiolibro')
    resume = None
    while True:
        set_language(settings.value('interface_language', 'it'))
        window = Editor(path, audio, auto_read, settings=settings)
        if resume is not None:
            index, export, continuous, from_start, duration, quick, generate_only, regenerate, junction = resume
            window.select_segment(index)
            window.reading_duration.setText(duration)
            QtCore.QTimer.singleShot(0, lambda: window.start(
                export=export, continuous=continuous, from_start=from_start, quick=quick,
                generate_only=generate_only, regenerate=regenerate, junction=junction))
        window.show()
        if app.exec() != 42 or not window.language_switch_requested:
            break
        path, auto_read = window.path, False
        resume = window.resume_after_language
        window.close()

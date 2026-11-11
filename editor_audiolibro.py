"""Qt window for manuscript review and playback."""
from ui_language import current_language, display_tone, set_language, tr
import json
import os
from pathlib import Path
import queue
import re
import tempfile
import time
import wave

from PySide6 import QtCore, QtGui, QtSvg, QtWidgets as Qt

from gestione_voci import (assignment, change_document_language, change_pause, change_spoken_text, import_voice_reference, profile_dialog,
                           set_narrator_reference, split_segment)
from equalizzatore_voci import BANDS, FLAT, GAIN_LIMIT, SOFT
from lingue_audio import DOCUMENT_LANGUAGES, LANGUAGES, segment_language, speech_parts
from pannelli_voce import TonePanel, gender_dialog, pronunciation_dialog
from pipeline_audio import AudioPipeline
from profili_vocali import load as load_profiles, tone_label


Pixmap = Qt.QStyle.StandardPixmap
ICON_PATHS = {
    Pixmap.SP_MediaPlay: 'M8 5l11 7-11 7z',
    Pixmap.SP_MediaSkipForward: 'M4 5l11 7-11 7z M19 5v14',
    Pixmap.SP_MediaPause: 'M9 5v14 M15 5v14',
    Pixmap.SP_MediaStop: 'M6 6h12v12H6z',
    Pixmap.SP_MediaSeekBackward: 'M11 6l-8 6 8 6z M21 6l-8 6 8 6z',
    Pixmap.SP_MediaSeekForward: 'M3 6l8 6-8 6z M13 6l8 6-8 6z',
    Pixmap.SP_DialogApplyButton: 'M4 12l5 5 11-11',
    Pixmap.SP_ArrowForward: 'M4 12h15 M13 6l6 6-6 6',
    Pixmap.SP_FileIcon: 'M5 3h9l5 5v13H5z M14 3v5h5 M9 14h6 M12 11v6',
    Pixmap.SP_FileDialogInfoView: 'M12 11v6 M12 7h.01 M22 12a10 10 0 1 1-20 0 10 10 0 1 1 20 0',
    Pixmap.SP_FileDialogDetailedView: 'M3 5v14h18V5z M6 12h2l2-4 3 8 2-4h3',
    Pixmap.SP_DialogSaveButton: 'M12 3v12 M7 10l5 5 5-5 M4 16v5h16v-5',
    Pixmap.SP_DialogOpenButton: 'M3 19V5h6l2 3h10l-2 11z M3 19l3-7h14',
    'generate': 'M13 2L5 13h6l-1 9 9-12h-6z',
    'regenerate': 'M20 10a8 8 0 1 0 0 5 M20 4v6h-6',
    'voice': 'M9 5a3 3 0 0 1 6 0v6a3 3 0 0 1-6 0z M6 11a6 6 0 0 0 12 0 M12 17v4 M9 21h6',
    'edit': 'M4 20l1-5L16 4a2 2 0 0 1 4 4L9 19z M14 6l4 4',
    'delete': 'M4 7h16 M9 7V4h6v3 M6 7l1 13h10l1-13 M10 10v7 M14 10v7',
    'eq-soft': 'M3 7h18 M3 17h18 M8 4v6 M16 14v6',
    'eq-flat': 'M4 6h16 M4 12h16 M4 18h16',
    'junction': 'M3 6v12 M21 6v12 M6 12h12 M9 9l-3 3 3 3 M15 9l3 3-3 3',
}

ICON_ACCENTS = {
    'green': ('#08744b', '#75e0a9'),
    'blue': ('#1759ae', '#8ec3ff'),
    'violet': ('#7242ad', '#d3b2ff'),
    'amber': ('#8c5007', '#ffc076'),
    'red': ('#b52d38', '#ff9ba4'),
    'teal': ('#086b70', '#78dce0'),
}


class Editor(Qt.QMainWindow):
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
        self.export_requested = False
        self.generate_only = False
        self.junction_work = None
        self.last_audio = self.generation_started = self.transport = None
        self.generated = self.played = self.position = self.total = 0
        self.edit_controls = []
        self.icons = {}
        self.setWindowTitle(tr('Audiolibro di revisione'))
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

    def button(self, layout, title, action, icon=None, *, edit=True, accent=None):
        control = Qt.QToolButton()
        control.setAccessibleName(title.replace('&', ''))
        control.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        control.setMinimumHeight(32)
        if accent is not None:
            control.setToolTip(title)
            control.setMinimumWidth(36)
            control.setIcon(self.command_icon(icon, accent))
            control.setIconSize(QtCore.QSize(20, 20))
            control.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonIconOnly)
        else:
            control.setText(title)
        if icon is not None and accent is None:
            control.setIcon(self.command_icon(icon))
            control.setIconSize(QtCore.QSize(22, 22) if edit else QtCore.QSize(16, 16))
            control.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextUnderIcon if edit
                                       else QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        control.clicked.connect(lambda: action())
        layout.addWidget(control)
        if edit:
            self.edit_controls.append(control)
        return control

    def command_icon(self, name, accent=None):
        key = (name, accent)
        if key not in self.icons:
            result = QtGui.QIcon()
            dark = self.palette().color(QtGui.QPalette.ColorRole.Button).lightness() < 128
            for size in (48, 64):
                for mode, group in [(QtGui.QIcon.Mode.Normal, QtGui.QPalette.ColorGroup.Active),
                                    (QtGui.QIcon.Mode.Disabled, QtGui.QPalette.ColorGroup.Disabled)]:
                    color = self.palette().color(group, QtGui.QPalette.ColorRole.ButtonText).name()
                    if accent and mode == QtGui.QIcon.Mode.Normal:
                        color = ICON_ACCENTS[accent][dark]
                    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
                           f'<path d="{ICON_PATHS[name]}" fill="none" stroke="{color}" '
                           'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>')
                    renderer = QtSvg.QSvgRenderer(QtCore.QByteArray(svg.encode('ascii')))
                    pixmap = QtGui.QPixmap(size, size)
                    pixmap.fill(QtCore.Qt.GlobalColor.transparent)
                    painter = QtGui.QPainter(pixmap)
                    renderer.render(painter)
                    painter.end()
                    result.addPixmap(pixmap, mode)
            self.icons[key] = result
        return self.icons[key]

    def ribbon(self, name):
        page = Qt.QWidget()
        layout = Qt.QHBoxLayout(page)
        self.ribbon_tabs.addTab(page, name)
        return layout

    @staticmethod
    def group(layout, title):
        box = Qt.QGroupBox(title)
        layout.addWidget(box)
        return Qt.QHBoxLayout(box)

    def build(self):
        icon = Qt.QStyle.StandardPixmap
        central = Qt.QWidget()
        self.setCentralWidget(central)
        outer = Qt.QVBoxLayout(central)
        outer.setContentsMargins(16, 12, 16, 8)
        outer.setSpacing(10)
        self.ribbon_tabs = Qt.QTabWidget()
        outer.addWidget(self.ribbon_tabs)
        file_corner = Qt.QWidget()
        file_layout = Qt.QHBoxLayout(file_corner)
        file_layout.setContentsMargins(0, 0, 4, 2)
        self.open_markdown_button = self.button(file_layout, tr('Apri Markdown…'), self.open_markdown,
                                                icon.SP_DialogOpenButton, edit=False)
        self.open_markdown_button.setShortcut(QtGui.QKeySequence.StandardKey.Open)
        self.open_markdown_button.setToolTip(tr('Apri un file Markdown (Ctrl+O)'))
        self.ribbon_tabs.setCornerWidget(file_corner)
        listen = self.ribbon(tr('&Ascolto'))
        starts = self.group(listen, tr('Avvia lettura'))
        self.button(starts, tr('Ascolta segmento'), self.start, icon.SP_MediaPlay)
        self.button(starts, tr('Leggi da qui'), lambda: self.start(continuous=True), icon.SP_MediaSkipForward)
        duration = Qt.QVBoxLayout()
        starts.addLayout(duration)
        duration_label = Qt.QLabel(tr('&Durata (mm:ss)'))
        self.reading_duration = Qt.QLineEdit('00:00')
        self.reading_duration.setMaximumWidth(95)
        self.reading_duration.setToolTip(tr('Solo per Leggi da qui. 00:00 legge fino alla fine.'))
        duration_label.setBuddy(self.reading_duration)
        duration.addWidget(duration_label)
        duration.addWidget(self.reading_duration)
        self.edit_controls.append(self.reading_duration)
        self.button(starts, tr('Leggi tutto il file'), lambda: self.start(continuous=True, from_start=True), icon.SP_MediaPlay)
        self.preload = Qt.QCheckBox(tr('Prepara tutto prima dell’ascolto'))
        self.preload.setChecked(True)
        preparation = self.group(listen, tr('Preparazione'))
        options = Qt.QVBoxLayout()
        preparation.addLayout(options)
        options.addWidget(self.preload)
        self.fractional = Qt.QCheckBox(tr('Invia tronconi alla vocalizzazione'))
        self.fractional.setToolTip(tr('Consegna gruppi di quattro segmenti mentre l’analisi continua.'))
        options.addWidget(self.fractional)
        self.edit_controls.extend([self.preload, self.fractional])
        listen.addStretch()

        voices = self.ribbon(tr('&Voci'))
        assign_group = self.group(voices, tr('Attribuzione'))
        self.button(assign_group, tr('Assegna'), self.assign, icon.SP_DialogApplyButton)
        self.button(assign_group, tr('Prossima da assegnare'), self.next_pending, icon.SP_ArrowForward)
        casting = self.group(voices, tr('Interpretazione'))
        mode_label = Qt.QLabel(tr('Modalità &voci'))
        self.voice_mode = Qt.QComboBox()
        self.voice_mode.addItem(tr('Narratore unico'), self.audio.NARRATOR_MODE)
        self.voice_mode.addItem(tr('Cast separato'), self.audio.CAST_MODE)
        self.voice_mode.setToolTip(tr('Usa la voce del Narratore per tutti i ruoli oppure le voci dei singoli profili.'))
        mode_label.setBuddy(self.voice_mode)
        casting.addWidget(mode_label)
        casting.addWidget(self.voice_mode)
        self.voice_design = Qt.QCheckBox(tr('VoiceDesign diretto'))
        self.voice_design.setToolTip(tr('Con Narratore unico riusa il campione sintetico. Con Cast separato genera dalla descrizione del profilo Qwen.'))
        casting.addWidget(self.voice_design)
        self.edit_controls.extend([self.voice_mode, self.voice_design])
        profiles = self.group(voices, tr('Profili e dizione'))
        self.button(profiles, tr('Nuovo profilo…'), lambda: profile_dialog(self, self.profile_created), icon.SP_FileIcon)
        self.button(profiles, tr('Genere…'), lambda: gender_dialog(self, self.role.currentText(), self.show_language), icon.SP_FileDialogInfoView)
        self.button(profiles, tr('Pronunce…'), lambda: pronunciation_dialog(self), icon.SP_FileDialogDetailedView)
        references = self.group(voices, tr('Riferimenti vocali'))
        self.button(references, tr('Importa WAV…'), self.import_reference)
        self.button(references, tr('Imposta come Narratore…'), self.choose_narrator)
        voices.addStretch()

        output = self.ribbon(tr('&Esportazione'))
        files = self.group(output, tr('File audio'))
        self.export_button = self.button(files, tr('Esporta capitolo'), self.request_export, icon.SP_DialogSaveButton)
        self.open_button = self.button(files, tr('Apri audio nel lettore Windows'), self.open_audio, icon.SP_DialogOpenButton)
        output.addStretch()

        directing = self.ribbon(tr('&Regia'))
        rhythm = self.group(directing, tr('Ritmo di lettura'))
        pause_label = Qt.QLabel(tr('&Pausa dopo il segmento'))
        self.segment_pause = Qt.QSpinBox(minimum=-1, maximum=self.audio.MAX_PAUSE_MS, singleStep=10, suffix=' ms')
        self.segment_pause.setSpecialValueText(tr('Automatica'))
        self.segment_pause.setKeyboardTracking(False)
        self.segment_pause.setAccessibleName(tr('Pausa dopo il segmento'))
        self.segment_pause.setToolTip(tr('Automatica oppure da 0 a 3000 ms. Conferma con Invio.'))
        pause_label.setBuddy(self.segment_pause)
        rhythm.addWidget(pause_label)
        rhythm.addWidget(self.segment_pause)
        self.segment_pause.editingFinished.connect(self.set_segment_pause)
        speed_label = Qt.QLabel(tr('V&elocità della voce'))
        self.reading_speed = Qt.QComboBox()
        for label, level in [(tr('Normale'), 0), (tr('Rapida'), 1), (tr('Molto rapida'), 2)]:
            self.reading_speed.addItem(label, level)
        speed_label.setBuddy(self.reading_speed)
        rhythm.addWidget(speed_label)
        rhythm.addWidget(self.reading_speed)
        self.edit_controls.extend([self.segment_pause, self.reading_speed])
        split_button = self.button(rhythm, tr('Dividi qui per la lettura'), self.split_here)
        split_button.setToolTip(tr('Posiziona il cursore tra due parole nel testo del segmento.'))
        directing.addStretch()

        settings = self.ribbon(tr('&Lingua'))
        languages = self.group(settings, tr('Lingue'))
        form_languages = Qt.QFormLayout()
        languages.addLayout(form_languages)
        self.interface_language = Qt.QComboBox()
        self.interface_language.addItem('Italiano', 'it')
        self.interface_language.addItem('English', 'en')
        self.interface_language.setCurrentIndex(self.interface_language.findData(current_language()))
        self.interface_language.activated.connect(self.change_interface_language)
        form_languages.addRow(tr('Lingua &interfaccia'), self.interface_language)
        self.document_language = Qt.QComboBox()
        for code in DOCUMENT_LANGUAGES:
            self.document_language.addItem(tr(LANGUAGES[code]), code)
        self.document_language.activated.connect(self.set_document_language)
        form_languages.addRow(tr('Lingua &documento'), self.document_language)
        self.edit_controls.append(self.document_language)
        settings.addStretch()

        header = Qt.QHBoxLayout()
        self.document_title = Qt.QLabel(tr('Nessun documento aperto'))
        self.document_title.setObjectName('documentTitle')
        self.document_title.setWordWrap(True)
        self.summary = Qt.QLabel()
        header.addWidget(self.document_title, 1)
        header.addWidget(self.summary)
        outer.addLayout(header)

        split = Qt.QSplitter()
        outer.addWidget(split, 1)
        navigator = Qt.QTabWidget()
        sidebar = Qt.QWidget()
        sidebar_layout = Qt.QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.addWidget(navigator, 1)
        sidebar_layout.addWidget(self.equalizer_panel())
        split.addWidget(sidebar)
        self.listing = Qt.QListWidget()
        self.listing.setAccessibleName(tr('Segmenti del manoscritto'))
        self.listing.setAlternatingRowColors(True)
        navigator.addTab(self.listing, tr('&Segmenti'))
        register = Qt.QWidget()
        register_layout = Qt.QVBoxLayout(register)
        self.table = Qt.QTreeWidget()
        self.table.setAccessibleName(tr('Registro delle assegnazioni'))
        self.table.setHeaderLabels([tr('N.'), tr('Riga'), tr('Profilo'), tr('Tono')])
        self.table.setRootIsDecorated(False)
        self.table.setAlternatingRowColors(True)
        for index in range(3):
            self.table.header().setSectionResizeMode(index, Qt.QHeaderView.ResizeMode.ResizeToContents)
        register_layout.addWidget(self.table)
        row = Qt.QHBoxLayout()
        register_layout.addLayout(row)
        self.button(row, tr('Modifica selezionata'), self.edit_assignment, 'edit', accent='blue')
        self.button(row, tr('Elimina assegnazione'), self.delete_assignment, 'delete', accent='red')
        row.addStretch()
        navigator.addTab(register, tr('&Registro'))
        self.navigator = navigator
        self.table.itemDoubleClicked.connect(self.edit_assignment)
        for key, action in [('F2', self.edit_assignment), ('Delete', self.delete_assignment)]:
            shortcut = QtGui.QShortcut(QtGui.QKeySequence(key), self.table)
            shortcut.setContext(QtCore.Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(action)

        detail = Qt.QWidget()
        split.addWidget(detail)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 2)
        split.setSizes([350, 710])
        body = Qt.QVBoxLayout(detail)
        body.setContentsMargins(8, 0, 0, 0)
        self.segment_label = Qt.QLabel(tr('Testo del segmento'))
        self.segment_label.setObjectName('segmentTitle')
        body.addWidget(self.segment_label)
        self.content = Qt.QPlainTextEdit(readOnly=True)
        self.content.setPlaceholderText(tr('Apri un file Markdown per visualizzarne i segmenti.'))
        self.content.setAccessibleName(tr('Testo del segmento selezionato'))
        self.content.setFont(QtGui.QFont('Georgia', 13))
        self.content.document().setDocumentMargin(14)
        body.addWidget(self.content, 1)
        actions = Qt.QHBoxLayout()
        body.addLayout(actions)
        self.segment_play = self.button(actions, tr('Ascolta'), lambda: self.start(quick=True), icon.SP_MediaPlay, accent='green')
        self.segment_generate = self.button(actions, tr('Genera'), lambda: self.start(quick=True, generate_only=True),
                                            'generate', accent='blue')
        self.segment_regenerate = self.button(actions, tr('Rigenera'), lambda: self.start(
            quick=True, generate_only=True, regenerate=True), 'regenerate', accent='amber')
        self.segment_voice = self.button(actions, tr('Cambia voce'), self.choose_segment_voice, 'voice', accent='violet')
        self.segment_pronunciation = self.button(actions, tr('Pronuncia…'), self.edit_spoken_text,
                                                 icon.SP_FileDialogDetailedView, accent='teal')
        self.segment_junction = self.button(actions, tr('Ascolta raccordo successivo'), lambda: self.start(
            quick=True, junction=True), 'junction', accent='green')
        self.segment_review = self.button(actions, tr('Punti da verificare…'), self.show_audio_review,
                                          icon.SP_FileDialogInfoView, accent='amber')
        actions.addStretch()
        self.segment_audio_status = Qt.QLabel(wordWrap=True)
        body.addWidget(self.segment_audio_status)
        self.segment_play.setToolTip(tr('Ascolta') + '\n' + tr('Ascolta o metti in pausa con Spazio nella lista dei segmenti.'))
        self.play_shortcut = QtGui.QShortcut(QtGui.QKeySequence('Space'), self)
        self.play_shortcut.activated.connect(self.toggle_segment_playback)
        Qt.QApplication.instance().focusChanged.connect(self.update_play_shortcut)
        for key, action in [('Ctrl+Up', lambda: self.step_segment(-1)),
                            ('Ctrl+Down', lambda: self.step_segment(1))]:
            shortcut = QtGui.QShortcut(QtGui.QKeySequence(key), self.listing)
            shortcut.setContext(QtCore.Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(action)
        QtGui.QShortcut(QtGui.QKeySequence('Escape'), self).activated.connect(
            lambda: self.stop() if self.stop_button.isEnabled() else None)
        form = Qt.QFormLayout()
        body.addLayout(form)
        self.role = Qt.QComboBox()
        self.role.addItems(list(load_profiles()['characters']))
        self.language = Qt.QComboBox()
        for code, label in LANGUAGES.items():
            self.language.addItem(tr('Lingua del documento') if code == 'auto' else tr(label), code)
        form.addRow(tr('&Profilo vocale'), self.role)
        form.addRow(tr('&Lingua effettiva'), self.language)
        self.language_hint = Qt.QLabel(wordWrap=True)
        form.addRow(self.language_hint)
        self.tones = TonePanel(detail, icon=self.command_icon(icon.SP_FileDialogDetailedView, 'violet'))
        body.addWidget(self.tones)
        self.edit_controls.extend([self.listing, self.table, self.role, self.language, self.tones])

        transport = Qt.QGroupBox(tr('Riproduzione'))
        outer.addWidget(transport)
        transport_layout = Qt.QVBoxLayout(transport)
        self.narrator_label = Qt.QLabel(wordWrap=True)
        self.narrator_label.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        transport_layout.addWidget(self.narrator_label)
        controls = Qt.QHBoxLayout()
        transport_layout.addLayout(controls)
        self.pause_button = self.button(controls, tr('Pausa'), lambda: self.send_transport('toggle'),
                                        icon.SP_MediaPause, edit=False, accent='amber')
        self.stop_button = self.button(controls, tr('Ferma lettura'), self.stop, icon.SP_MediaStop,
                                       edit=False, accent='red')
        self.back_button = self.button(controls, tr('Indietro'), lambda: self.send_transport('jump', -1),
                                       icon.SP_MediaSeekBackward, edit=False, accent='blue')
        self.forward_button = self.button(controls, tr('Avanti'), lambda: self.send_transport('jump', 1),
                                          icon.SP_MediaSeekForward, edit=False, accent='blue')
        self.jump = Qt.QSpinBox(minimum=1, maximum=3600, value=10, suffix=' s')
        jump_label = Qt.QLabel(tr('Salto nel segmento'))
        jump_label.setBuddy(self.jump)
        controls.addWidget(jump_label)
        controls.addWidget(self.jump)
        controls.addStretch()
        progress = Qt.QGridLayout()
        transport_layout.addLayout(progress)
        self.gen_label, self.gen_bar = self.progress_row(progress, 0, tr('Elaborazione'))
        self.read_label, self.read_bar = self.progress_row(progress, 1, tr('Lettura'))
        self.preparation_eta = Qt.QLabel()
        self.reading_eta = Qt.QLabel()
        progress.addWidget(self.preparation_eta, 2, 0, 1, 2)
        progress.addWidget(self.reading_eta, 3, 0, 1, 2)
        self.status = Qt.QLabel(tr('Scegli Apri Markdown… per iniziare.'), wordWrap=True)
        outer.addWidget(self.status)
        self.setStyleSheet('''
            QLabel#documentTitle { font-size: 22px; font-weight: 600; }
            QLabel#segmentTitle { font-weight: 600; }
            QToolButton { padding: 3px 7px; }
            QToolButton:focus { border: 2px solid palette(highlight); border-radius: 4px; }
            QListWidget, QTreeWidget { selection-background-color: #155689; selection-color: white; }
        ''')
        self.listing.currentRowChanged.connect(self.pick)
        self.role.activated.connect(self.assign)
        self.language.currentIndexChanged.connect(self.show_language)
        self.tones.changed.connect(self.assign)
        self.voice_mode.currentIndexChanged.connect(self.refresh_segment_audio)
        self.voice_design.toggled.connect(self.refresh_segment_audio)
        self.reading_speed.currentIndexChanged.connect(self.refresh_segment_audio)
        self.refresh_segment_audio()
        self.open_button.setEnabled(False)

    def restore_preferences(self):
        def restore(key, default, allowed):
            value = self.settings.value(key, default)
            if type(default) is bool:
                value = {'true': True, 'false': False}.get(str(value).lower(), default)
            elif type(default) is int:
                try:
                    value = int(str(value))
                except ValueError:
                    value = default
            return value if value in allowed else default

        def bind_choice(control, key, default, allowed):
            value = restore(key, default, allowed)
            control.setCurrentIndex(control.findData(value))
            control.currentIndexChanged.connect(
                lambda _: self.save_preference(key, control.currentData()))

        bind_choice(self.voice_mode, 'voice_mode', self.audio.NARRATOR_MODE, self.audio.VOICE_MODES)
        bind_choice(self.reading_speed, 'speed_level', 0, self.audio.SPEED_LEVELS)
        for control, key, default in (
            (self.preload, 'preload_all', True),
            (self.fractional, 'fractional_synthesis', False),
            (self.voice_design, 'voice_design', False),
        ):
            control.setChecked(restore(key, default, (False, True)))
            control.toggled.connect(lambda value, key=key: self.save_preference(key, value))
        self.bind_numeric_preference(self.jump, 'seek_seconds', restore)
        for control, (_, frequency) in zip(self.eq_controls, BANDS):
            self.bind_numeric_preference(control, f'equalizer/{frequency}', restore)

    def bind_numeric_preference(self, control, key, restore):
        control.setValue(restore(key, control.value(), range(control.minimum(), control.maximum() + 1)))
        control.valueChanged.connect(lambda value: self.save_preference(key, value))

    def save_preference(self, key, value):
        self.settings.setValue(key, value)
        self.settings.sync()
        if self.settings.status() != QtCore.QSettings.Status.NoError:
            self.status.setText(tr('Preferenze non salvate.'))

    def equalizer_panel(self):
        panel = Qt.QGroupBox(tr('Equalizzatore personaggi'))
        layout = Qt.QFormLayout(panel)
        self.eq_controls = []
        for name, frequency in BANDS:
            gain = Qt.QSpinBox(minimum=-GAIN_LIMIT, maximum=GAIN_LIMIT, suffix=' dB')
            gain.setToolTip(f'{name} · {frequency} Hz')
            layout.addRow('&' + name, gain)
            self.eq_controls.append(gain)
        presets = Qt.QHBoxLayout()
        self.button(presets, tr('Meno metallico'), lambda: self.set_equalizer(SOFT), 'eq-soft', accent='teal')
        self.button(presets, display_tone('Neutro'), lambda: self.set_equalizer(FLAT), 'eq-flat', accent='blue')
        presets.addStretch()
        layout.addRow(presets)
        hint = Qt.QLabel(tr('Si applica al prossimo ascolto o WAV.'), wordWrap=True)
        layout.addRow(hint)
        self.edit_controls.append(panel)
        return panel

    def set_equalizer(self, gains):
        for control, gain in zip(self.eq_controls, gains):
            control.setValue(gain)

    def open_markdown(self):
        if self.busy:
            return
        self.auto_timer.stop()
        self.update_transport()
        directory = str(Path(self.plan['source']).parent) if self.path is not None else ''
        selected, _ = Qt.QFileDialog.getOpenFileName(
            self, tr('Apri Markdown'), directory, tr('File Markdown (*.md *.markdown)'))
        if selected:
            try:
                self.load_document(selected)
            except (OSError, ValueError) as error:
                Qt.QMessageBox.critical(self, tr('Markdown non aperto'), str(error))

    def load_document(self, source):
        path = self.audio.reading_plan(source)
        plan = self.audio.read_plan(path)
        self.path, self.plan = path, plan
        self.document_language.setCurrentIndex(self.document_language.findData(plan.get('language', 'auto')))
        self.last_audio = None
        self.preparation_eta.clear()
        self.reading_eta.clear()
        title = Path(plan['source']).stem
        self.setWindowTitle(tr('Audiolibro di revisione — ') + title)
        self.document_title.setText(title.replace('_', ' '))
        self.rebuild_segments(0)
        self.navigator.setCurrentIndex(0)
        self.ribbon_tabs.setCurrentIndex(0)
        for label, bar, name in [(self.gen_label, self.gen_bar, tr('Elaborazione')),
                                 (self.read_label, self.read_bar, tr('Lettura'))]:
            label.setText(name + tr(': in attesa'))
            bar.setValue(0)
        for control in self.edit_controls:
            control.setEnabled(True)
        self.refresh_narrator_label()
        self.status.setText(tr('Scegli come avviare la lettura dalla scheda Ascolto.'))
        self.update_transport()

    def change_interface_language(self, choice):
        selected = self.interface_language.itemData(choice)
        if selected == current_language():
            return
        self.save_preference('interface_language', selected)
        self.language_switch_requested = True
        if self.busy:
            if not self.pipeline.stop_event.is_set():
                self.resume_after_language = self.active_request
            self.interface_language.setEnabled(False)
            self.pipeline.stop()
            self.status.setText(tr('Cambio lingua in corso…'))
        else:
            Qt.QApplication.instance().exit(42)

    def set_document_language(self, choice):
        if self.busy or self.path is None:
            return
        selected = self.document_language.itemData(choice)
        try:
            self.plan = change_document_language(self.path, self.plan, selected)
        except (OSError, ValueError) as error:
            self.document_language.setCurrentIndex(self.document_language.findData(self.plan.get('language', 'auto')))
            Qt.QMessageBox.warning(self, tr('Lingua non salvata'), str(error))
            return
        self.show_language()
        self.refresh_segment_audio()
        self.status.setText(tr('Lingua del documento salvata.'))

    def rebuild_segments(self, index):
        self.listing.clear()
        for segment in self.plan['segments']:
            item = Qt.QListWidgetItem()
            self.format_item(item, segment)
            self.listing.addItem(item)
        self.select_segment(index)
        self.refresh_assignments()

    def set_segment_pause(self):
        index = self.listing.currentRow()
        if self.busy or index < 0:
            return
        value = self.segment_pause.value()
        milliseconds = None if value == -1 else value
        if milliseconds == self.plan['segments'][index].get('pause_ms'):
            return
        try:
            self.plan = change_pause(self.path, self.plan, index, milliseconds)
        except (OSError, ValueError) as error:
            self.pick(index)
            Qt.QMessageBox.critical(self, tr('Pausa non salvata'), str(error))
            return
        self.refresh_segment_audio()
        self.status.setText(tr('Pausa di {} ms salvata per il segmento {}.').format(
            self.audio.plan_pause_ms(self.plan, self.plan['segments'][index]), index + 1))

    def edit_spoken_text(self):
        index = self.listing.currentRow()
        segment = self.plan['segments'][index]
        spoken, confirmed = Qt.QInputDialog.getMultiLineText(
            self, tr('Pronuncia del segmento'),
            tr('Indica gli accenti nel testo letto. Svuota il campo per ripristinare il testo originale.'),
            segment.get('spoken_text', segment['text']),
        )
        if confirmed:
            try:
                self.plan = change_spoken_text(self.path, self.plan, index, spoken)
            except (OSError, ValueError) as error:
                Qt.QMessageBox.critical(self, tr('Pronuncia non salvata'), str(error))
                return
            self.show_language()
            self.refresh_segment_audio()

    def choose_segment_voice(self):
        self.role.setFocus()
        self.role.showPopup()

    def step_segment(self, direction):
        index = self.listing.currentRow() + direction
        if not self.busy and 0 <= index < self.listing.count():
            self.select_segment(index)

    def toggle_segment_playback(self):
        if self.busy:
            self.send_transport('toggle')
        else:
            self.start(quick=True)

    def update_play_shortcut(self, *_):
        focus = self.focusWidget()
        in_list = focus is self.listing or (focus is not None and self.listing.isAncestorOf(focus))
        editing = isinstance(focus, (Qt.QLineEdit, Qt.QPlainTextEdit, Qt.QTextEdit, Qt.QComboBox,
                                     Qt.QAbstractSpinBox, Qt.QAbstractButton))
        self.play_shortcut.setEnabled(in_list or (self.busy and not editing))

    def refresh_segment_audio(self):
        index = self.listing.currentRow()
        available = self.path is not None and index >= 0 and not self.busy
        ready = False
        if available:
            try:
                ready = self.audio.raw_preview(
                    self.path, index, cache_only=True, voice_mode=self.voice_mode.currentData(),
                    speed_level=self.reading_speed.currentData(), voice_design=self.voice_design.isChecked(),
                ) is not None
            except (OSError, ValueError):
                pass
        for control in (self.segment_play, self.segment_voice, self.segment_pronunciation):
            control.setEnabled(available)
        self.segment_generate.setEnabled(available and not ready)
        self.segment_regenerate.setEnabled(available and ready)
        self.segment_junction.setEnabled(available and index + 1 < self.listing.count())
        self.segment_review.setEnabled(available)
        if available:
            segment = self.plan['segments'][index]
            pause = self.audio.plan_pause_ms(self.plan, segment)
            state = tr('Audio pronto') if ready else tr('Audio da generare')
            self.segment_audio_status.setText(tr('{} · Pausa dopo: {} ms').format(state, pause))
            try:
                spoken = ''.join(part['text'] for part in self.audio.segment_speech_parts(self.plan, segment))
                tooltip = tr('Pronuncia…')
                if spoken != segment['text']:
                    tooltip += '\n' + tr('Testo inviato alla sintesi: {}').format(spoken)
                self.segment_pronunciation.setToolTip(tooltip)
            except ValueError as error:
                self.segment_pronunciation.setToolTip(tr('Pronuncia…') + '\n' + str(error))
        else:
            self.segment_audio_status.clear()

    def show_audio_review(self):
        labels = {
            'peak': tr('Picco elevato'), 'long_clip': tr('Clip lungo'), 'long_edge': tr('Margine lungo'),
            'weak_or_silent': tr('Audio debole o silenzioso'), 'unassigned': tr('Voce da assegnare'),
            'check_language_voice': tr('Verifica la voce della lingua'),
        }
        entries = {}
        for index, segment in enumerate(self.plan['segments']):
            target = self.audio.preview_target(self.path, index, self.voice_mode.currentData(),
                                               self.reading_speed.currentData(), self.voice_design.isChecked())
            flags = ['unassigned'] if segment['role'] == 'Da assegnare' else []
            try:
                data = json.loads(target.with_suffix('.indice.json').read_text(encoding='utf-8'))
                timing = data['segments'][0]
                if timing.get('audio_key') == self.audio.segment_audio_key(self.plan, segment):
                    flags.extend(timing.get('review', []))
            except (OSError, ValueError, KeyError, IndexError):
                pass
            if flags:
                summary = ', '.join(labels.get(flag, flag) for flag in dict.fromkeys(flags))
                entries[f"{segment['id']:03} · {summary} · {segment['text'][:55]}"] = index
        if entries:
            label, confirmed = Qt.QInputDialog.getItem(self, tr('Punti da verificare…'),
                                                      tr('Scegli un segmento'), list(entries), editable=False)
            if confirmed:
                self.select_segment(entries[label])
        else:
            self.status.setText(tr('Nessun punto segnalato nelle anteprime disponibili.'))

    def split_here(self):
        index = self.listing.currentRow()
        if self.busy or index < 0:
            return
        cursor = self.content.textCursor()
        if cursor.hasSelection():
            self.status.setText(tr('Posiziona il cursore tra due parole senza selezionare testo.'))
            return
        # Qt counts UTF-16 units, while the plan uses Python string indices.
        text = self.content.toPlainText()
        try:
            offset = len(text.encode('utf-16-le')[:cursor.position() * 2].decode('utf-16-le'))
            self.plan = split_segment(self.path, self.plan, index, offset)
        except (OSError, ValueError) as error:
            Qt.QMessageBox.warning(self, tr('Segmento non diviso'), str(error))
            return
        self.rebuild_segments(index)
        self.status.setText(tr('Segmento {} diviso.').format(index + 1))

    @staticmethod
    def progress_row(layout, row, name):
        label = Qt.QLabel(name + tr(': in attesa'))
        bar = Qt.QProgressBar()
        bar.setAccessibleName(name)
        bar.setTextVisible(False)
        bar.setRange(0, 100)
        bar.setValue(0)
        bar.setFixedHeight(12)
        layout.addWidget(label, row, 0)
        layout.addWidget(bar, row, 1)
        layout.setColumnStretch(1, 1)
        return label, bar

    @staticmethod
    def format_item(item, segment):
        excerpt = ' '.join(segment['text'].split())[:120]
        label = tr('{:03}  ·  {}  ·  riga {}').format(segment['id'], segment['role'], segment['line'])
        item.setText(label + '\n' + excerpt)
        item.setToolTip(excerpt)

    def pick(self, index):
        if index < 0:
            self.refresh_segment_audio()
            return
        segment = self.plan['segments'][index]
        self.segment_label.setText(tr('Segmento {:03}  ·  Riga {}').format(segment['id'], segment['line']))
        self.content.setPlainText(segment['text'])
        self.segment_pause.setValue(segment.get('pause_ms', -1))
        self.refresh_segment_audio()
        self.role.setCurrentText(segment['role'])
        self.tones.set_value(segment.get('tone', 'Neutro'))
        self.language.setCurrentIndex(self.language.findData(segment.get('language', 'auto')))
        self.show_language()

    def show_language(self):
        index = self.listing.currentRow()
        if index < 0:
            return
        profile = load_profiles()['characters'].get(self.role.currentText(), {})
        gender = profile.get('gender', 'non_specificato').replace('_', ' ')
        try:
            segment = self.plan['segments'][index]
            selected = segment_language(self.plan, {'language': self.language.currentData()})
            parts = speech_parts(segment.get('spoken_text', segment['text']), selected)
            languages = ', '.join(dict.fromkeys(tr(LANGUAGES[part['language']]) for part in parts))
            self.language_hint.setText(tr('Genere profilo: {}  ·  Lingua del testo: {}').format(gender, languages))
        except ValueError as error:
            self.language_hint.setText(str(error))

    def import_reference(self):
        wav, _ = Qt.QFileDialog.getOpenFileName(self, tr('Importa riferimento vocale mono'), '', 'WAV (*.wav)')
        if not wav:
            return
        name, ok = Qt.QInputDialog.getText(self, tr('Riferimento vocale'), tr('Nome del profilo'))
        if not ok:
            return
        provenance, ok = Qt.QInputDialog.getText(self, tr('Riferimento vocale'), tr('Provenienza del campione'))
        if not ok:
            return
        transcript, ok = Qt.QInputDialog.getMultiLineText(
            self, tr('Riferimento vocale'), tr('Trascrizione esatta (lascia vuoto per un provino dal solo audio)'))
        if not ok:
            return
        try:
            self.profile_created(import_voice_reference(wav, name, provenance, transcript))
        except (OSError, ValueError, EOFError, wave.Error) as error:
            Qt.QMessageBox.warning(self, tr('Campione non importato'), str(error))
            return
        self.status.setText(tr('Profilo {} pronto per la prova con Cast separato.').format(name.strip()))

    def refresh_narrator_label(self):
        try:
            narrator = load_profiles()['characters']['Narratore']
            if narrator.get('engine') == 'qwen3':
                reference = Path(narrator['reference'])
                metadata = json.loads(reference.read_text(encoding='utf-8'))
                name = metadata.get('character') or reference.stem
            else:
                name = narrator['voice']
        except (OSError, ValueError, KeyError):
            name = tr('Riferimento vocale non disponibile')
        label = tr('Voce narrante: {} · {}').format(name, self.voice_mode.currentText())
        if self.voice_design.isChecked():
            mode = 'VoiceDesign · voce fissa' if self.voice_mode.currentData() == self.audio.NARRATOR_MODE else 'VoiceDesign diretto'
            label += ' · ' + tr(mode)
        self.narrator_label.setText(label)

    def choose_narrator(self):
        choices = [name for name, profile in load_profiles()['characters'].items()
                   if name != 'Narratore' and profile.get('engine') == 'qwen3'
                   and profile.get('kind') != 'gruppo']
        if not choices:
            self.status.setText(tr('Importa prima un riferimento Qwen.'))
            return
        name, ok = Qt.QInputDialog.getItem(
            self, tr('Imposta come Narratore'), tr('Profilo da usare come voce base'), choices, editable=False)
        if not ok:
            return
        try:
            set_narrator_reference(name)
        except (OSError, ValueError) as error:
            Qt.QMessageBox.warning(self, tr('Narratore non modificato'), str(error))
            return
        if self.role.findText('Narratore precedente') < 0 and 'Narratore precedente' in load_profiles()['characters']:
            self.role.addItem('Narratore precedente')
        self.refresh_narrator_label()
        self.status.setText(tr('Il Narratore usa il riferimento di {}.').format(name))

    def profile_created(self, name):
        self.role.addItem(name)
        self.role.setCurrentText(name)
        self.show_language()
        self.status.setText(tr('Profilo creato. Usa Voci → Assegna per applicarlo.'))

    def refresh_assignments(self):
        selected = self.table.currentItem()
        selected_id = selected.data(0, QtCore.Qt.ItemDataRole.UserRole) if selected else None
        self.table.clear()
        for index, segment in enumerate(self.plan['segments']):
            if segment['role'] in ('Narratore', 'Da assegnare') and 'tone' not in segment:
                continue
            tones = tone_label(segment.get('tone', 'Neutro')).split(' + ')
            tone_display = ' + '.join(display_tone(tone) for tone in tones)
            item = Qt.QTreeWidgetItem([str(segment['id']), str(segment['line']), segment['role'],
                                       tone_display])
            item.setData(0, QtCore.Qt.ItemDataRole.UserRole, index)
            self.table.addTopLevelItem(item)
            if index == selected_id:
                self.table.setCurrentItem(item)
        self.summary.setText(tr('{} segmenti  ·  {} da assegnare').format(len(self.plan['segments']), self.audio.pending(self.plan)))

    def select_segment(self, index):
        self.listing.setCurrentRow(index)
        self.listing.scrollToItem(self.listing.item(index))

    def edit_assignment(self):
        item = self.table.currentItem()
        if self.busy or item is None:
            return
        self.select_segment(item.data(0, QtCore.Qt.ItemDataRole.UserRole))
        self.navigator.setCurrentIndex(0)
        self.ribbon_tabs.setCurrentIndex(1)
        self.role.setFocus()

    def delete_assignment(self):
        item = self.table.currentItem()
        if self.busy or item is None:
            return
        index = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
        try:
            self.plan = assignment(self.path, self.plan, index, delete=True)
        except Exception as error:
            Qt.QMessageBox.critical(self, tr('Assegnazione non eliminata'), str(error))
            return
        self.format_item(self.listing.item(index), self.plan['segments'][index])
        self.select_segment(index)
        self.pick(index)
        self.refresh_assignments()

    def assign(self):
        index = self.listing.currentRow()
        if self.busy or index < 0:
            return False
        try:
            self.plan = assignment(self.path, self.plan, index, self.role.currentText(),
                                   self.tones.value(), language=self.language.currentData())
        except Exception as error:
            Qt.QMessageBox.critical(self, tr('Attribuzione non salvata'), str(error))
            return False
        self.format_item(self.listing.item(index), self.plan['segments'][index])
        self.refresh_assignments()
        self.show_language()
        self.refresh_segment_audio()
        self.status.setText(tr('Assegnazione salvata per il segmento {}.').format(index + 1))
        return True

    def next_pending(self):
        segments = self.plan['segments']
        current = self.listing.currentRow()
        for offset in range(1, len(segments) + 1):
            index = (current + offset) % len(segments)
            if segments[index]['role'] == 'Da assegnare':
                self.navigator.setCurrentIndex(0)
                self.ribbon_tabs.setCurrentIndex(1)
                self.select_segment(index)
                self.role.setFocus()
                return
        self.status.setText(tr('Tutte le battute sono attribuite.'))

    def request_export(self):
        if self.busy:
            if not self.can_export_prepared():
                return
            self.export_requested = True
            self.pipeline.stop()
            self.status.setText(tr('Preparazione del file audio dai segmenti disponibili…'))
            self.update_transport()
            return
        self.start(export=True)

    def can_export_prepared(self):
        return (self.busy and not self.exporting and self.junction_work is None
                and not self.generate_only and not self.pipeline.stop_event.is_set()
                and self.generated == self.total and not self.audio.pending(self.plan))

    def choose_audio_file(self, export):
        source = Path(self.plan['source'])
        name = source.stem if export else source.stem + ' - ' + tr('lettura')
        dialog = Qt.QFileDialog(self, tr('Scegli il file audio'))
        dialog.setAcceptMode(Qt.QFileDialog.AcceptMode.AcceptSave)
        mp4_filter = tr('MP4 solo audio (*.mp4)')
        dialog.setNameFilters(['WAV (*.wav)', mp4_filter])
        dialog.setDefaultSuffix('wav')
        dialog.filterSelected.connect(lambda selected: dialog.setDefaultSuffix(
            'mp4' if selected == mp4_filter else 'wav'))
        dialog.setDirectory(str(source.parent))
        dialog.selectFile(name)
        if dialog.exec():
            target = Path(dialog.selectedFiles()[0])
            if target.suffix.lower() == '.mp4':
                from converti_audio import conversion_tools
                try:
                    conversion_tools()
                except RuntimeError:
                    self.status.setText(tr('Per salvare in MP4, FFmpeg e ffprobe devono essere nel PATH.'))
                    return None
            elif target.suffix.lower() != '.wav':
                self.status.setText(tr('Scegli un nome con estensione .wav o .mp4.'))
                return None
            return target
        return None

    def start(self, *, export=False, continuous=False, from_start=False, quick=False,
              generate_only=False, regenerate=False, junction=False):
        self.auto_timer.stop()
        index = self.listing.currentRow()
        if self.busy or index < 0:
            return
        if junction and index + 1 >= len(self.plan['segments']):
            return
        segment = self.plan['segments'][index]
        if (self.role.currentText() != segment['role']
                or self.tones.value() != tone_label(segment.get('tone', 'Neutro'))
                or self.language.currentData() != segment.get('language', 'auto')):
            if not self.assign():
                return
        self.refresh_narrator_label()
        pending = self.audio.pending(self.plan)
        if export and pending:
            self.next_pending()
            self.status.setText(tr('Esportazione sospesa. Assegna le {} battute rimanenti.').format(pending))
            return
        selected = 0 if export or from_start else index
        indices = range(selected, len(self.plan['segments'])) if export or continuous else [selected]
        checked = [index, index + 1] if junction else indices
        issues = []
        for row in checked:
            item = self.plan['segments'][row]
            try:
                self.audio.segment_speech_parts(self.plan, item)
            except (OSError, ValueError) as error:
                issues.append((row, tr('Segmento {:03} · riga {}\n{}\n{}').format(
                    item['id'], item['line'], item['text'][:180], error)))
        if issues:
            self.select_segment(issues[0][0])
            dialog = Qt.QMessageBox(Qt.QMessageBox.Icon.Warning, tr('Pronunce da verificare'),
                                    tr('{} segmenti richiedono una correzione prima della sintesi.').format(len(issues)),
                                    Qt.QMessageBox.StandardButton.Ok, self)
            dialog.setTextFormat(QtCore.Qt.TextFormat.PlainText)
            dialog.setInformativeText(issues[0][1])
            if len(issues) > 1:
                dialog.setDetailedText('\n\n'.join(message for _, message in issues))
            dialog.exec()
            return
        limit_ms = None
        if continuous and not from_start and not export:
            value = self.reading_duration.text().strip()
            if not re.fullmatch(r'[0-9]{1,6}:[0-5][0-9]', value):
                self.status.setText(tr('Inserisci una durata in formato mm:ss.'))
                self.reading_duration.setFocus()
                return
            minutes, seconds = map(int, value.split(':'))
            limit_ms = ((minutes * 60 + seconds) * 1000) or None
        target = None if quick else self.choose_audio_file(export)
        if target is None and not quick:
            return
        self.active_request = (index, export, continuous, from_start, self.reading_duration.text(),
                               quick, generate_only, regenerate, junction)
        segments = self.plan['segments']
        source_indices = tuple(indices)
        speed = 1 + .10 * self.reading_speed.currentData()

        def analyze(index):
            segment = segments[index]
            try:
                parts = self.audio.segment_speech_parts(self.plan, segment)
            except ValueError as error:
                location = f"Segmento {segment['id']:03} · riga {segment['line']}"
                raise ValueError(f'{location}\n{segment["text"][:180]}\n\n{error}') from error
            words = sum(len(part['text'].split()) for part in parts)
            pause = self.audio.plan_pause_ms(self.plan, segment)
            return max(.3, words / (2.5 * speed)) + pause / 1000

        self.exporting = export
        self.generate_only = generate_only
        self.stopped = False
        self.generated = self.played = 0
        self.generation_started = self.transport = None
        gains = tuple(control.value() for control in self.eq_controls)
        audio_options = {'voice_mode': self.voice_mode.currentData()}
        if self.voice_design.isChecked():
            audio_options['voice_design'] = True
        if self.reading_speed.currentData():
            audio_options['speed_level'] = self.reading_speed.currentData()
        if gains != FLAT:
            audio_options['voice_eq'] = gains
        if export:
            indices = [0]

            def generate(index, cancel):
                for segment_index in source_indices:
                    if cancel.is_set():
                        return None
                    analyze(segment_index)
                return self.audio.render(self.path, target, cancel_event=cancel,
                                         reuse_previews=True, **audio_options)
        elif junction:
            self.junction_work = tempfile.TemporaryDirectory()

            def generate(index, cancel):
                clips = self.audio.preview_batch(self.path, [index, index + 1], cancel, **audio_options)
                target = Path(self.junction_work.name) / 'raccordo.wav'
                return self.audio.write_junction(target, clips, cancel)
        else:
            def generate(index, cancel):
                options = dict(audio_options)
                if regenerate:
                    options['regenerate'] = True
                return self.audio.preview(self.path, index, cancel_event=cancel, **options)

        def generate_batch(batch, cancel):
            return self.audio.preview_batch(self.path, batch, cancel, **audio_options)

        sections = []
        played_ms = 0.0

        def play_segment(audio):
            nonlocal played_ms
            options = {}
            if limit_ms is not None:
                options['limit_ms'] = max(1, round(limit_ms - played_ms))
            self.audio.play_audio(audio, stop_event=self.pipeline.stop_event, events=self.events, **options)
            sections.append(audio)
            if limit_ms is not None and not self.pipeline.stop_event.is_set():
                with wave.open(str(audio), 'rb') as wav:
                    played_ms += wav.getnframes() * 1000 / wav.getframerate()
                return played_ms < limit_ms

        def save_playback():
            if sections:
                return self.audio.save_reading(target, sections, self.pipeline.stop_event, limit_ms)

        self.total = len(indices)
        self.reading_indices = tuple(indices)
        self.reading_limit = limit_ms / 1000 if limit_ms is not None else None
        self.estimated_durations = {}
        self.actual_durations = {}
        self.synthesis_times = []
        self.session_started = time.monotonic()
        self.preparation_eta.clear()
        self.reading_eta.clear()
        for bar in (self.gen_bar, self.read_bar):
            bar.setRange(0, self.total * 100)
            bar.setValue(0)
        self.gen_label.setText(tr('Elaborazione: 0/{}').format(self.total))
        if generate_only:
            self.read_label.setText(tr('Lettura: nessuna riproduzione'))
        else:
            self.read_label.setText(tr('Lettura: non prevista per esportazione') if export else tr('Lettura: attendo audio'))
        message = tr('Esportazione in corso.') if export else tr('Vocalizzazione e lettura in parallelo.')
        preload = self.preload.isChecked() and limit_ms is None
        if not export and preload:
            message = tr('Preparazione dei segmenti prima dell’ascolto.')
        self.status.setText(message)
        if generate_only:
            self.status.setText(tr('Generazione del segmento in corso.'))
        fractional = not export and not quick and self.fractional.isChecked()
        self.pipeline = AudioPipeline(indices, generate, None if export or generate_only else play_segment, self.events,
                                      buffer_size=4, startup_items=1 if limit_ms is not None else 3,
                                      preload_all=preload,
                                      analyze=None if export else analyze,
                                      batch_size=4 if fractional else None,
                                      generate_batch=generate_batch if fractional else None,
                                      on_playback_complete=save_playback if target is not None and not export else None)
        for control in self.edit_controls:
            if control is not self.listing or not quick:
                control.setEnabled(False)
        self.pipeline.start()
        self.refresh_segment_audio()
        self.update_transport()

    def poll(self):
        if not self.busy:
            return
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            kind = event['kind']
            if kind == 'analyzed':
                self.estimated_durations.update(event['estimates'])
            elif kind == 'generating':
                self.generation_started = event['started']
            elif kind == 'generated':
                self.generated = event['position']
                self.synthesis_times.append(event['synthesis_seconds'])
                if event['duration'] is not None:
                    self.actual_durations[event['index']] = event['duration']
                self.gen_bar.setValue(self.generated * 100)
                self.generation_started = None
                if self.exporting or self.generate_only:
                    self.last_audio = event['path']
            elif kind == 'playing':
                self.position = event['position']
                if self.junction_work is None:
                    self.last_audio = event['path']
                self.select_segment(event['index'])
                self.status.setText(tr('Lettura in corso.') if self.pipeline.producer_done.is_set()
                                    else tr('Lettura e vocalizzazione in parallelo.'))
            elif kind == 'saved':
                self.last_audio = event['path']
            elif kind == 'played':
                self.played = event['position']
                self.read_bar.setValue(self.played * 100)
                self.transport = None
            elif kind == 'transport':
                self.transport = event
            elif kind == 'generation_finished':
                self.generation_started = None
            elif kind == 'buffering':
                self.status.setText(event['message'])
            elif kind == 'error':
                self.status.setText(tr('Errore: arresto dei lavori in corso…'))

        detail = ''
        if self.generation_started is not None:
            detail = tr('  ·  {} s sul segmento corrente').format(int(time.monotonic() - self.generation_started))
        self.gen_label.setText(tr('Elaborazione: {}/{} pronti').format(self.generated, self.total) + detail)
        if self.pipeline.stop_event.is_set():
            self.read_label.setText(tr('Lettura arrestata'))
        elif self.transport:
            elapsed, duration = self.transport['elapsed'], self.transport['duration']
            self.read_bar.setValue(self.played * 100 + min(99, int(100 * elapsed / duration)))
            state = tr('In pausa') if self.transport['paused'] else tr('In riproduzione')
            self.read_label.setText(f'{state}  ·  {self.position}/{self.total}  ·  {self.clock(elapsed)} / {self.clock(duration)}')
        elif not self.exporting:
            suffix = tr('  ·  Attendo audio') if self.played < self.total else ''
            self.read_label.setText(tr('Lettura: {}/{} completati').format(self.played, self.total) + suffix)
        self.update_estimates()
        if self.pipeline.finished and self.events.empty():
            self.finish()
        self.update_transport()

    def update_estimates(self):
        if self.exporting:
            return
        if self.pipeline.stop_event.is_set():
            self.preparation_eta.clear()
            self.reading_eta.clear()
            return
        generation_remaining = None
        average = current_remaining = 0
        if self.generated == self.total:
            generation_remaining = 0
        elif self.synthesis_times:
            recent = self.synthesis_times[-5:]
            average = sum(recent) / len(recent)
            elapsed = time.monotonic() - self.generation_started if self.generation_started else 0
            pending = self.total - self.generated
            current_remaining = max(.1, average - elapsed)
            generation_remaining = average * (pending - 1) + current_remaining

        if self.pipeline.preload_all:
            if self.generated == self.total:
                preparation = tr('audio pronto')
            elif generation_remaining is None:
                preparation = tr('in analisi o in attesa del primo campione')
            else:
                preparation = tr('circa {} rimanenti').format(self.clock(generation_remaining + 0.999))
            self.preparation_eta.setText(tr('Prima dell’ascolto: ') + preparation)

        if self.transport and self.transport['paused']:
            self.reading_eta.setText(tr('Fine lettura: stima sospesa durante la pausa'))
            return
        durations = self.estimated_durations | self.actual_durations
        remaining = self.reading_indices[self.played:]
        if self.reading_limit is not None:
            elapsed = sum(durations.get(index, 0) for index in self.reading_indices[:self.played])
            if self.transport:
                elapsed += self.transport['elapsed']
            available = sum(durations[index] for index in remaining) if all(index in durations for index in remaining) else self.reading_limit
            if self.transport:
                available -= self.transport['elapsed']
            audio = max(0, min(available, self.reading_limit - elapsed))
            self.reading_eta.setText(tr('Audio restante: circa {}').format(self.clock(audio + 0.999)))
            return
        if any(index not in durations for index in remaining):
            self.reading_eta.setText(tr('Fine lettura: analisi del testo in corso'))
            return
        audio = sum(durations[index] for index in remaining)
        if self.transport:
            audio = max(0, audio - self.transport['elapsed'])
        self.reading_eta.setText(tr('Audio restante: circa {}').format(self.clock(audio + 0.999)))
        if generation_remaining is not None:
            if self.pipeline.preload_all:
                finish = audio + generation_remaining
            else:
                ready_count = max(0, self.generated - self.played)
                finish = sum(durations[index] for index in remaining[:ready_count])
                if self.transport:
                    finish = max(0, finish - self.transport['elapsed'])
                for offset, index in enumerate(remaining[ready_count:]):
                    ready_at = current_remaining + offset * average
                    finish = max(finish, ready_at) + durations[index]
            self.reading_eta.setText(self.reading_eta.text() + tr('  ·  Fine stimata fra {}').format(self.clock(finish + 0.999)))

    @staticmethod
    def clock(seconds):
        minutes, seconds = divmod(int(seconds), 60)
        return f'{minutes:02d}:{seconds:02d}'

    def finish(self):
        error = self.pipeline.error
        self.pipeline = self.transport = self.generation_started = None
        junction = self.junction_work is not None
        if junction:
            self.junction_work.cleanup()
            self.junction_work = None
        if self.closing:
            self.close()
            return
        if self.language_switch_requested:
            Qt.QApplication.instance().exit(42)
            return
        for control in self.edit_controls:
            control.setEnabled(True)
        self.refresh_segment_audio()
        if self.export_requested and not error:
            self.export_requested = False
            self.start(export=True)
            return
        self.export_requested = False
        if error:
            self.status.setText(tr('Errore: lettura e vocalizzazione arrestate.'))
            Qt.QMessageBox.critical(self, tr('Lettura non riuscita'), error)
        elif self.stopped:
            self.status.setText(tr('Lettura interrotta. Puoi riprendere dal segmento selezionato.'))
        else:
            if not self.exporting and not self.generate_only:
                self.read_bar.setValue(self.read_bar.maximum())
                self.reading_eta.setText(tr('Lettura terminata in {}').format(self.clock(time.monotonic() - self.session_started)))
            self.status.setText(tr('Raccordo ascoltato.') if junction
                                else tr('Completato. Audio: {}').format(self.last_audio))

    def update_transport(self):
        self.update_play_shortcut()
        stopping = self.busy and self.pipeline.stop_event.is_set()
        active = self.busy and self.transport is not None and not stopping
        paused = active and self.transport['paused']
        icon = Qt.QStyle.StandardPixmap
        label = tr('Riprendi') if paused else tr('Pausa')
        self.pause_button.setToolTip(label)
        self.pause_button.setAccessibleName(label)
        self.pause_button.setIcon(self.command_icon(icon.SP_MediaPlay, 'green') if paused
                                  else self.command_icon(icon.SP_MediaPause, 'amber'))
        for control in (self.pause_button, self.back_button, self.forward_button):
            control.setEnabled(active)
        self.stop_button.setEnabled((self.busy or self.auto_timer.isActive()) and not stopping)
        self.open_markdown_button.setEnabled(not self.busy)
        self.open_button.setEnabled(not self.busy and self.last_audio is not None)
        self.export_button.setEnabled(self.can_export_prepared() if self.busy else self.path is not None)

    def send_transport(self, action, direction=0):
        if self.busy and self.transport is not None and not self.pipeline.stop_event.is_set():
            self.transport['commands'].put((action, direction * self.jump.value()))

    def stop(self):
        self.stopped = True
        self.auto_timer.stop()
        if self.busy:
            self.pipeline.stop()
            self.status.setText(tr('Arresto di vocalizzazione e lettura in corso…'))
        else:
            self.status.setText(tr('Avvio automatico annullato.'))
        self.update_transport()

    def open_audio(self):
        try:
            os.startfile(str(self.last_audio))
        except OSError as error:
            Qt.QMessageBox.warning(self, tr('Audio non aperto'), str(error))

    def closeEvent(self, event):
        if self.busy:
            self.closing = True
            self.stop()
            event.ignore()
        else:
            self.poll_timer.stop()
            self.auto_timer.stop()
            event.accept()


def run(path, audio, auto_read=False):
    app = Qt.QApplication.instance()
    if app is None:
        app = Qt.QApplication([])
        if 'windows11' in Qt.QStyleFactory.keys():
            app.setStyle('windows11')
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

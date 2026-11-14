"""Editor view."""
from ui_language import current_language, display_tone, tr
from PySide6 import QtCore, QtGui, QtSvg, QtWidgets as Qt
from gestione_voci import profile_dialog
from equalizzatore_voci import BANDS, FLAT, GAIN_LIMIT, SOFT
from voice_conversion import MODELS
from version_info import PUBLIC_NAME, __build_version__
from pannelli_voce import TonePanel, gender_dialog, pronunciation_dialog
from profili_vocali import load as load_profiles


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


class EditorViewMixin:
    """Editor view responsibilities of the Qt editor."""
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
        layout.setContentsMargins(10,8,10,8)
        scroll = Qt.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(Qt.QFrame.Shape.NoFrame)
        scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(page)
        self.ribbon_tabs.addTab(scroll, name)
        return layout

    @staticmethod
    def group(layout, title):
        box = Qt.QGroupBox(title)
        layout.addWidget(box)
        return Qt.QHBoxLayout(box)

    def build(self):
        palette=self.palette()
        dark=palette.color(QtGui.QPalette.ColorRole.Window).lightness()<128
        foreground='#edf2f5' if dark else '#17212b'
        for role in (QtGui.QPalette.ColorRole.Text,QtGui.QPalette.ColorRole.WindowText,QtGui.QPalette.ColorRole.ButtonText):
            for group in (QtGui.QPalette.ColorGroup.Active,QtGui.QPalette.ColorGroup.Inactive):
                palette.setColor(group,role,QtGui.QColor(foreground))
            palette.setColor(QtGui.QPalette.ColorGroup.Disabled,role,QtGui.QColor('#87939e' if dark else '#697581'))
        palette.setColor(QtGui.QPalette.ColorRole.Base,QtGui.QColor('#172027' if dark else '#ffffff'))
        palette.setColor(QtGui.QPalette.ColorRole.Highlight,QtGui.QColor('#287d93' if dark else '#15637a'))
        palette.setColor(QtGui.QPalette.ColorRole.HighlightedText,QtGui.QColor('#ffffff'))
        self.setPalette(palette)
        icon = Qt.QStyle.StandardPixmap
        central = Qt.QWidget()
        self.setCentralWidget(central)
        outer = Qt.QVBoxLayout(central)
        outer.setContentsMargins(16, 12, 16, 8)
        outer.setSpacing(10)
        brand = Qt.QHBoxLayout()
        name = Qt.QLabel(PUBLIC_NAME)
        name.setObjectName('brandTitle')
        brand.addWidget(name)
        brand.addWidget(Qt.QLabel(tr('Studio di lettura e voci')))
        brand.addStretch()
        brand.addWidget(Qt.QLabel(__build_version__))
        outer.addLayout(brand)
        self.ribbon_tabs = Qt.QTabWidget()
        self.ribbon_tabs.setDocumentMode(True)
        self.ribbon_tabs.setMaximumHeight(152)
        outer.addWidget(self.ribbon_tabs)
        file_corner = Qt.QWidget()
        file_layout = Qt.QHBoxLayout(file_corner)
        file_layout.setContentsMargins(0, 0, 4, 2)
        self.open_markdown_button = self.button(file_layout, tr('Apri Markdown…'), self.open_markdown,
                                                icon.SP_DialogOpenButton, edit=False)
        self.open_markdown_button.setShortcut(QtGui.QKeySequence.StandardKey.Open)
        self.open_markdown_button.setToolTip(tr('Apri un file Markdown (Ctrl+O)'))
        self.ribbon_tabs.setCornerWidget(file_corner)
        file_page = self.ribbon(tr('&File'))
        self.files_tab_index = self.ribbon_tabs.count()-1
        manage=self.group(file_page,tr('Documenti'))
        management=Qt.QVBoxLayout()
        manage.addLayout(management)
        self.file_controls=[self.button(management,tr('Apri documento…'),self.open_markdown,icon.SP_DialogOpenButton,edit=False),
                            self.button(management,tr('Scegli cartella…'),self.choose_library_folder,edit=False)]
        self.button(management,tr('Apri cartella'),self.open_document_folder,edit=False)
        library=Qt.QVBoxLayout()
        file_page.addLayout(library,1)
        self.library_path=Qt.QLineEdit(readOnly=True)
        self.library_path.setPlaceholderText(tr('Scegli una cartella per i documenti.'))
        self.library_path.setAccessibleName(tr('Cartella dei documenti'))
        library.addWidget(self.library_path)
        self.file_listing=Qt.QTreeWidget()
        self.file_listing.setHeaderLabels([tr('Documento'),tr('Formato')])
        self.file_listing.setRootIsDecorated(False)
        self.file_listing.setMinimumWidth(250)
        self.file_listing.setAccessibleName(tr('File nella cartella'))
        self.file_listing.itemDoubleClicked.connect(self.open_library_document)
        self.file_listing.header().setSectionResizeMode(0,Qt.QHeaderView.ResizeMode.Stretch)
        library.addWidget(self.file_listing)
        self.file_listing.setToolTip(tr('Doppio clic o Invio per aprire il documento.'))
        file_open=QtGui.QShortcut(QtGui.QKeySequence('Return'),self.file_listing)
        file_open.setContext(QtCore.Qt.ShortcutContext.WidgetWithChildrenShortcut)
        file_open.activated.connect(self.open_library_document)
        self.library_folder=None
        listen = self.ribbon(tr('&Ascolto'))
        self.listen_tab_index = self.ribbon_tabs.count()-1
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
        self.voices_tab_index = self.ribbon_tabs.count()-1
        assign_group = self.group(voices, tr('Attribuzione'))
        self.button(assign_group, tr('Assegna'), self.assign, icon.SP_DialogApplyButton)
        self.button(assign_group, tr('Prossima da assegnare'), self.next_pending, icon.SP_ArrowForward)
        casting = self.group(voices, tr('Interpretazione'))
        cast_stack=Qt.QVBoxLayout()
        casting.addLayout(cast_stack)
        cast_row=Qt.QHBoxLayout()
        cast_stack.addLayout(cast_row)
        mode_label = Qt.QLabel(tr('Modalità &voci'))
        self.voice_mode = Qt.QComboBox()
        self.voice_mode.addItem(tr('Narratore unico'), self.audio.NARRATOR_MODE)
        self.voice_mode.addItem(tr('Cast separato'), self.audio.CAST_MODE)
        self.voice_mode.setToolTip(tr('Usa la voce del Narratore per tutti i ruoli oppure le voci dei singoli profili.'))
        mode_label.setBuddy(self.voice_mode)
        cast_row.addWidget(mode_label)
        cast_row.addWidget(self.voice_mode)
        self.voice_design = Qt.QCheckBox(tr('VoiceDesign diretto'))
        self.voice_design.setToolTip(tr('Con Narratore unico riusa il campione sintetico. Con Cast separato genera dalla descrizione del profilo Qwen.'))
        cast_stack.addWidget(self.voice_design)
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
        directing.addWidget(self.equalizer_panel())
        directing.addStretch()

        settings = self.ribbon(tr('&Impostazioni'))
        languages = self.group(settings, tr('Interfaccia'))
        form_languages = Qt.QFormLayout()
        languages.addLayout(form_languages)
        self.interface_language = Qt.QComboBox()
        self.interface_language.addItem('Italiano', 'it')
        self.interface_language.addItem('English', 'en')
        self.interface_language.setCurrentIndex(self.interface_language.findData(current_language()))
        self.interface_language.activated.connect(self.change_interface_language)
        form_languages.addRow(tr('Lingua &interfaccia'), self.interface_language)
        settings.addStretch()

        header = Qt.QHBoxLayout()
        self.document_title = Qt.QLabel(tr('Nessun documento aperto'))
        self.document_title.setObjectName('documentTitle')
        self.document_title.setWordWrap(True)
        self.summary = Qt.QLabel()
        header.addWidget(self.document_title, 1)
        header.addWidget(self.summary)
        outer.addLayout(header)
        model_row=Qt.QHBoxLayout()
        model_label=Qt.QLabel(tr('Modello &voce del documento'))
        self.document_model=Qt.QComboBox()
        for code,label in MODELS.items(): self.document_model.addItem(tr(label),code)
        self.document_model.setAccessibleName(tr('Modello voce del documento'))
        model_label.setBuddy(self.document_model)
        self.document_model.activated.connect(self.set_document_model)
        model_row.addWidget(model_label)
        model_row.addWidget(self.document_model)
        self.model_status=Qt.QLabel(wordWrap=True)
        model_row.addWidget(self.model_status,1)
        outer.addLayout(model_row)
        self.edit_controls.append(self.document_model)

        split = Qt.QSplitter()
        outer.addWidget(split, 1)
        navigator = Qt.QTabWidget()
        sidebar = Qt.QWidget()
        sidebar_layout = Qt.QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.addWidget(navigator, 1)
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
        self.segment_play = self.button(actions, tr('Ascolta'), self.listen_selected_segment, icon.SP_MediaPlay, accent='green')
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
        self.play_shortcut.activated.connect(self.activate_selected_segment)
        self.listing.itemDoubleClicked.connect(lambda *_: self.listen_selected_segment())
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
        self.segment_model = Qt.QComboBox()
        self.segment_model.addItem(tr('Modello del documento'),'inherit')
        for code,label in MODELS.items(): self.segment_model.addItem(tr(label),code)
        form.addRow(tr('&Profilo vocale'), self.role)
        form.addRow(tr('&Modello voce'), self.segment_model)
        self.language_hint = Qt.QLabel(wordWrap=True)
        form.addRow(self.language_hint)
        self.tones = TonePanel(detail, icon=self.command_icon(icon.SP_FileDialogDetailedView, 'violet'))
        self.tones.setSizePolicy(Qt.QSizePolicy.Policy.Preferred,Qt.QSizePolicy.Policy.Maximum)
        actions.addWidget(self.tones)
        self.edit_controls.extend([self.listing, self.table, self.role, self.segment_model, self.tones])

        transport = Qt.QGroupBox(tr('Riproduzione'))
        outer.addWidget(transport)
        transport_layout = Qt.QVBoxLayout(transport)
        transport_layout.setSpacing(4)
        self.narrator_label = Qt.QLabel(wordWrap=True)
        self.narrator_label.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        transport_layout.addWidget(self.narrator_label)
        controls = Qt.QHBoxLayout()
        transport_layout.addLayout(controls)
        self.pause_button = self.button(controls, tr('Pausa'), self.toggle_segment_playback,
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
        self.gen_label, self.gen_bar = self.progress_row(progress, 0, tr('Pronuncia'))
        self.voice_label, self.voice_bar = self.progress_row(progress, 1, tr('Voce'))
        self.read_label, self.read_bar = self.progress_row(progress, 2, tr('Ascolto'))
        self.preparation_eta = Qt.QLabel()
        self.reading_eta = Qt.QLabel()
        self.preparation_eta.setVisible(False)
        self.reading_eta.setVisible(False)
        progress.addWidget(self.preparation_eta, 3, 0, 1, 2)
        progress.addWidget(self.reading_eta, 4, 0, 1, 2)
        self.status = Qt.QLabel(tr('Scegli Apri Markdown… per iniziare.'), wordWrap=True)
        outer.addWidget(self.status)
        self.setStyleSheet('QWidget { color: '+foreground+'; } '
                           'QWidget:disabled { color: '+('#87939e' if dark else '#697581')+'; } '+'''
            QLabel#brandTitle { font-size: 24px; font-weight: 650; }
            QLabel#documentTitle { font-size: 18px; font-weight: 600; }
            QLabel#segmentTitle { font-weight: 600; }
            QToolButton { padding: 3px 7px; }
            QToolButton:focus { border: 2px solid palette(highlight); border-radius: 4px; }
            QProgressBar { border: 1px solid palette(mid); border-radius: 3px; background: palette(base); }
            QProgressBar::chunk { background: palette(highlight); border-radius: 2px; }
            QTabBar::tab { padding: 7px 16px; }
        ''')
        self.listing.currentRowChanged.connect(self.pick)
        self.role.activated.connect(self.assign)
        self.segment_model.activated.connect(self.assign)
        self.tones.changed.connect(self.assign)
        self.voice_mode.currentIndexChanged.connect(self.refresh_segment_audio)
        self.voice_design.toggled.connect(self.refresh_segment_audio)
        self.reading_speed.currentIndexChanged.connect(self.refresh_segment_audio)
        self.refresh_segment_audio()
        self.open_button.setEnabled(False)
        if self.path is None:
            self.ribbon_tabs.setCurrentIndex(self.files_tab_index)

    def equalizer_panel(self):
        panel = Qt.QGroupBox(tr('Equalizzatore personaggi'))
        layout = Qt.QVBoxLayout(panel)
        bands=Qt.QHBoxLayout()
        layout.addLayout(bands)
        self.eq_controls = []
        for name, frequency in BANDS:
            gain = Qt.QSpinBox(minimum=-GAIN_LIMIT, maximum=GAIN_LIMIT, suffix=' dB')
            gain.setToolTip(f'{name} · {frequency} Hz')
            gain.setAccessibleName(name)
            field=Qt.QVBoxLayout()
            field.addWidget(Qt.QLabel(name)); field.addWidget(gain)
            bands.addLayout(field)
            self.eq_controls.append(gain)
        presets = Qt.QHBoxLayout()
        self.button(presets, tr('Meno metallico'), lambda: self.set_equalizer(SOFT), 'eq-soft', accent='teal')
        self.button(presets, display_tone('Neutro'), lambda: self.set_equalizer(FLAT), 'eq-flat', accent='blue')
        presets.addStretch()
        layout.addLayout(presets)
        hint = Qt.QLabel(tr('Si applica al prossimo ascolto o WAV.'), wordWrap=True)
        layout.addWidget(hint)
        self.edit_controls.append(panel)
        return panel

    def set_equalizer(self, gains):
        for control, gain in zip(self.eq_controls, gains):
            control.setValue(gain)

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

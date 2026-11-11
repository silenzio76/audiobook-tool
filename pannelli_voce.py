"""Qt panels for delivery tones, gender and pronunciation."""
from ui_language import display_tone, tr
from PySide6 import QtCore, QtWidgets as Qt

from profili_vocali import TONES, tone_names, tone_label


class TonePanel(Qt.QWidget):
    changed = QtCore.Signal()

    def __init__(self, parent=None, *, icon=None):
        super().__init__(parent)
        self.menu = Qt.QMenu(self)
        self.button = Qt.QPushButton()
        if icon is not None:
            self.button.setIcon(icon)
            self.button.setIconSize(QtCore.QSize(20, 20))
            self.button.setFixedSize(44, 32)
        self.button.setMenu(self.menu)
        self.checks = {}
        for name in TONES:
            action = self.menu.addAction(display_tone(name))
            action.setCheckable(True)
            action.triggered.connect(lambda checked, name=name: self.choose(name, checked))
            self.checks[name] = action
        layout = Qt.QFormLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addRow(tr('&Toni vocali'), self.button)
        self.set_value('Neutro')

    def value(self):
        return self.selected_tones

    def set_value(self, value):
        selected = tone_names(value)
        for name, action in self.checks.items():
            action.setChecked(name in selected)
        self.selected_tones = tone_label(selected)
        display = ' + '.join(display_tone(name) for name in selected)
        description = tr('Toni vocali: ') + display
        self.button.setToolTip(description)
        self.button.setAccessibleName(description)
        if self.button.icon().isNull():
            self.button.setText(display if len(selected) <= 2 else tr('{} toni selezionati').format(len(selected)))

    def choose(self, name, checked):
        if name == 'Neutro' and checked:
            selected = ['Neutro']
        else:
            selected = [key for key, action in self.checks.items() if key != 'Neutro' and action.isChecked()]
        self.set_value(selected or ['Neutro'])
        self.changed.emit()


def form_dialog(parent, title, accept_text):
    window = Qt.QDialog(parent)
    window.setWindowTitle(title)
    window.setMinimumWidth(420)
    form = Qt.QFormLayout(window)
    actions = Qt.QDialogButtonBox(Qt.QDialogButtonBox.StandardButton.Cancel)
    actions.button(Qt.QDialogButtonBox.StandardButton.Cancel).setText(tr('Annulla'))
    actions.addButton(accept_text, Qt.QDialogButtonBox.ButtonRole.AcceptRole)
    actions.rejected.connect(window.reject)
    form.addRow(actions)
    return window, form, actions


def gender_combo(value='non_specificato'):
    combo = Qt.QComboBox()
    for code, label in [('maschio', tr('Maschio')), ('femmina', tr('Femmina')),
                        ('misto', tr('Misto (gruppi)')), ('non_specificato', tr('Non specificato'))]:
        combo.addItem(label, code)
    combo.setCurrentIndex(combo.findData(value))
    return combo


def gender_dialog(app, name, updated):
    from audiolibro import save
    from profili_vocali import CONFIG, load, validate

    window, form, actions = form_dialog(app, tr('Genere · ') + name, tr('Salva'))
    value = gender_combo(load()['characters'][name].get('gender', 'non_specificato'))
    form.insertRow(0, tr('&Genere'), value)
    form.insertRow(1, Qt.QLabel(tr('Il genere non rigenera il timbro Qwen esistente.'), wordWrap=True))

    def commit():
        try:
            data = load()
            data['characters'][name]['gender'] = value.currentData()
            validate(data)
            save(CONFIG, data)
        except Exception as error:
            Qt.QMessageBox.critical(window, tr('Genere non salvato'), str(error))
            return
        updated()
        window.accept()

    actions.accepted.connect(commit)
    window.exec()


def pronunciation_dialog(app):
    from audiolibro import save
    from lingue_audio import LANGUAGES, LEXICON, lexicon, speech_parts

    window, form, actions = form_dialog(app, tr('Lessico pronunce'), tr('Salva pronuncia'))
    original, native, audio = Qt.QLineEdit(), Qt.QLineEdit(), Qt.QLineEdit()
    for row, (label, control) in enumerate([(tr('&Espressione nel testo'), original),
                                          (tr('&Grafia originale verificata'), native),
                                          (tr('&WAV verificato (alternativo)'), audio)]):
        form.insertRow(row, label, control)
    browse = Qt.QPushButton(tr('Scegli WAV…'))

    def choose_audio():
        path, _ = Qt.QFileDialog.getOpenFileName(window, tr('Scegli WAV'), '', tr('Audio WAV (*.wav)'))
        if path:
            audio.setText(path)

    browse.clicked.connect(choose_audio)
    form.insertRow(3, '', browse)
    language = Qt.QComboBox()
    for code, label in LANGUAGES.items():
        if code != 'auto':
            language.addItem(tr(label), code)
    language.setCurrentIndex(language.findData('el'))
    form.insertRow(4, tr('&Lingua'), language)
    form.insertRow(5, Qt.QLabel(tr('Per IPA e varianti storiche usa un WAV verificato.'), wordWrap=True))

    def commit():
        try:
            expression = original.text().strip()
            if not expression or not (native.text().strip() or audio.text().strip()):
                raise ValueError(tr('Inserisci l’espressione e la grafia originale oppure un WAV.'))
            data = lexicon()
            data[expression] = {'language': language.currentData(), 'text': native.text(), 'audio': audio.text()}
            speech_parts(expression, entries=data)
            save(LEXICON, data)
        except Exception as error:
            Qt.QMessageBox.critical(window, tr('Pronuncia non salvata'), str(error))
            return
        window.accept()

    actions.accepted.connect(commit)
    window.exec()

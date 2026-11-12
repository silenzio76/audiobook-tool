"""Editor preferences."""
from ui_language import current_language, tr
from PySide6 import QtCore, QtWidgets as Qt
from equalizzatore_voci import BANDS


class EditorPreferencesMixin:
    """Editor preferences responsibilities of the Qt editor."""
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

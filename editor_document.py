"""Editor document."""
from ui_language import display_tone, tr
import json
from pathlib import Path
import wave
from PySide6 import QtCore, QtWidgets as Qt
from gestione_voci import assignment, change_document_language, change_pause, change_spoken_text, import_voice_reference, set_narrator_reference, split_segment
from lingue_audio import LANGUAGES, segment_language, speech_parts
from profili_vocali import load as load_profiles, tone_label


class EditorDocumentMixin:
    """Editor document responsibilities of the Qt editor."""
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
        self.resume_request = None
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
        with QtCore.QSignalBlocker(self.listing):
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

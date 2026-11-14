"""Editor document."""
from ui_language import display_tone, tr
import json
from pathlib import Path
import os
import wave
from PySide6 import QtCore, QtWidgets as Qt
from gestione_voci import assignment, change_document_voice_model, change_pause, change_spoken_text, import_voice_reference, narrator_reference_choices, set_narrator_reference, split_segment
from lingue_audio import LANGUAGES, segment_language, speech_parts
from profili_vocali import load as load_profiles, tone_label
from voice_conversion import MODELS, document_model, segment_model, installed
from version_info import PUBLIC_NAME


class EditorDocumentMixin:
    """Editor document responsibilities of the Qt editor."""
    def open_markdown(self):
        if self.busy:
            return
        self.auto_timer.stop()
        self.update_transport()
        directory = str(Path(self.plan['source']).parent) if self.path is not None else ''
        selected, _ = Qt.QFileDialog.getOpenFileName(
            self, tr('Apri documento'), directory, tr('Documenti (*.md *.markdown *.json)'))
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
        self.document_model.setCurrentIndex(self.document_model.findData(document_model(plan)))
        self.last_audio = None
        self.preparation_eta.clear()
        self.reading_eta.clear()
        title = Path(plan['source']).stem
        self.setWindowTitle(PUBLIC_NAME+' — '+title)
        self.document_title.setText(title.replace('_', ' '))
        self.rebuild_segments(0)
        self.navigator.setCurrentIndex(0)
        self.ribbon_tabs.setCurrentIndex(self.listen_tab_index)
        self.refresh_library(Path(plan['source']).parent)
        self.refresh_model_status()
        for label, bar, name in [(self.gen_label, self.gen_bar, tr('Pronuncia')),
                                 (self.voice_label,self.voice_bar,tr('Voce')),
                                 (self.read_label, self.read_bar, tr('Ascolto'))]:
            label.setText(name + tr(': in attesa'))
            bar.setValue(0)
        for control in self.edit_controls:
            control.setEnabled(True)
        self.refresh_narrator_label()
        self.status.setText(tr('Scegli come avviare la lettura dalla scheda Ascolto.'))
        self.update_transport()

    def set_document_model(self, choice):
        if self.busy or self.path is None:
            return
        selected = self.document_model.itemData(choice)
        try:
            self.plan = change_document_voice_model(self.path, self.plan, selected)
        except (OSError, ValueError) as error:
            self.document_model.setCurrentIndex(self.document_model.findData(document_model(self.plan)))
            Qt.QMessageBox.warning(self, tr('Modello non salvato'), str(error))
            return
        self.show_language()
        self.refresh_segment_audio()
        self.refresh_model_status()
        self.status.setText(tr('Modello voce salvato.'))

    def refresh_model_status(self):
        selected=document_model(self.plan)
        self.model_status.setText(tr('Seed-VC locale · qualità in valutazione') if selected=='seed-vc' and installed()
                                 else tr('Seed-VC non installato') if selected=='seed-vc'
                                 else tr('Qwen diretto · preset originale · dizione da verificare') if selected=='qwen-direct'
                                 else tr('Sintesi diretta · formato del testo rilevato automaticamente'))
        if selected in ('seed-vc','qwen-direct'):
            self.voice_design.setChecked(False)
        self.voice_design.setEnabled(not self.busy and selected=='source')

    def choose_library_folder(self):
        if self.busy: return
        folder=Qt.QFileDialog.getExistingDirectory(self,tr('Cartella dei documenti'),
                                                   str(self.library_folder or Path.cwd()))
        if folder: self.refresh_library(Path(folder))

    def refresh_library(self, folder):
        self.library_folder=Path(folder)
        self.library_path.setText(str(self.library_folder))
        self.library_path.setToolTip(str(self.library_folder))
        self.file_listing.clear()
        for path in sorted(self.library_folder.iterdir(),key=lambda p:p.name.casefold()):
            if path.is_file() and path.suffix.lower() in ('.md','.markdown','.json'):
                item=Qt.QTreeWidgetItem([path.name,path.suffix[1:].upper()])
                item.setData(0,QtCore.Qt.ItemDataRole.UserRole,str(path))
                item.setToolTip(0,str(path))
                self.file_listing.addTopLevelItem(item)

    def open_library_document(self, *_):
        if self.busy: return
        item=self.file_listing.currentItem()
        if item is None: return
        try:
            self.load_document(item.data(0,QtCore.Qt.ItemDataRole.UserRole))
        except (OSError,ValueError) as error:
            Qt.QMessageBox.warning(self,tr('Documento non aperto'),str(error))

    def open_document_folder(self):
        folder=self.library_folder
        if folder is None and self.path is not None:
            folder=Path(self.plan['source']).parent
        if folder is not None:
            os.startfile(str(folder))

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
        if (not self.busy or (not self.exporting and not self.generate_only)) and 0 <= index < self.listing.count():
            self.select_segment(index)

    def refresh_segment_audio(self):
        index = self.listing.currentRow()
        available = self.path is not None and index >= 0 and not self.busy
        ready = False
        if available:
            try:
                ready = self.audio.preview(
                    self.path, index, cache_only=True, voice_mode=self.voice_mode.currentData(),
                    speed_level=self.reading_speed.currentData(), voice_design=self.voice_design.isChecked(),
                ) is not None
            except (OSError, ValueError, RuntimeError):
                pass
        for control in (self.segment_play, self.segment_voice, self.segment_pronunciation):
            control.setEnabled(available)
        if self.busy and not self.exporting and not self.generate_only:
            self.segment_play.setEnabled(index >= 0 and not self.pipeline.stop_event.is_set())
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
        if self.busy and index != self.playing_index:
            self.follow_playback = False
        self.segment_label.setText(tr('Segmento {:03}  ·  Riga {}').format(segment['id'], segment['line']))
        self.content.setPlainText(segment['text'])
        self.segment_pause.setValue(segment.get('pause_ms', -1))
        self.refresh_segment_audio()
        self.role.setCurrentText(segment['role'])
        self.tones.set_value(segment.get('tone', 'Neutro'))
        with QtCore.QSignalBlocker(self.segment_model):
            self.segment_model.setCurrentIndex(self.segment_model.findData(segment.get('voice_model','inherit')))
        self.show_language()

    def show_language(self):
        index = self.listing.currentRow()
        if index < 0:
            return
        profile = load_profiles()['characters'].get(self.role.currentText(), {})
        gender = profile.get('gender', 'non_specificato').replace('_', ' ')
        try:
            segment = self.plan['segments'][index]
            selected = segment_language(self.plan,segment)
            model=segment_model(self.plan,dict(segment,voice_model=self.segment_model.currentData() or 'inherit'))
            self.audio.segment_speech_parts(self.plan,dict(segment,voice_model=self.segment_model.currentData() or 'inherit'))
            self.language_hint.setText(tr('Genere profilo: {} · Modello: {}').format(gender,tr(MODELS[model])))
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
        details = ''
        try:
            narrator = load_profiles()['characters']['Narratore']
            if narrator.get('engine') == 'qwen3':
                reference = Path(narrator['reference'])
                metadata = json.loads(reference.read_text(encoding='utf-8'))
                name = metadata.get('character') or reference.stem
                from qwen_backend import reference as verified_reference
                _, wav = verified_reference(reference)
                details = str(wav) + '\nSHA-256: ' + metadata['audio_sha256']
                name += ' · ' + wav.name
            else:
                name = narrator['voice']
        except (OSError, ValueError, KeyError):
            name = tr('Riferimento vocale non disponibile')
        label = tr('Voce narrante: {} · {}').format(name, self.voice_mode.currentText())
        if self.voice_design.isChecked():
            mode = 'VoiceDesign · voce fissa' if self.voice_mode.currentData() == self.audio.NARRATOR_MODE else 'VoiceDesign diretto'
            label += ' · ' + tr(mode)
        self.narrator_label.setText(label)
        self.narrator_label.setToolTip(details)

    def choose_narrator(self):
        try:
            choices = narrator_reference_choices()
        except (OSError, ValueError, KeyError) as error:
            Qt.QMessageBox.warning(self, tr('Riferimento vocale non disponibile'), str(error))
            return
        if not choices:
            self.status.setText(tr('Importa prima un riferimento vocale WAV.'))
            return
        labels = [choice['label'] for choice in choices]
        current = next((i for i, choice in enumerate(choices) if choice['current']), 0)
        selected, ok = Qt.QInputDialog.getItem(
            self, tr('Imposta come Narratore'), tr('Riferimento WAV da usare come voce base'),
            labels, current, False)
        if not ok:
            return
        choice = choices[labels.index(selected)]
        try:
            set_narrator_reference(choice['profile'])
        except (OSError, ValueError) as error:
            Qt.QMessageBox.warning(self, tr('Narratore non modificato'), str(error))
            return
        if self.role.findText('Narratore precedente') < 0 and 'Narratore precedente' in load_profiles()['characters']:
            self.role.addItem('Narratore precedente')
        self.refresh_narrator_label()
        self.last_audio = None
        self.refresh_segment_audio()
        self.update_transport()
        self.status.setText(tr('Riferimento del Narratore: {}').format(choice['label']))

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
        self.ribbon_tabs.setCurrentIndex(self.voices_tab_index)
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
                                   self.tones.value(), voice_model=self.segment_model.currentData())
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
                self.ribbon_tabs.setCurrentIndex(self.voices_tab_index)
                self.select_segment(index)
                self.role.setFocus()
                return
        self.status.setText(tr('Tutte le battute sono attribuite.'))

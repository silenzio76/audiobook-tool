"""Editor playback."""
from ui_language import tr
import os
from pathlib import Path
import queue
import re
import tempfile
import time
import wave
from PySide6 import QtCore, QtWidgets as Qt
from equalizzatore_voci import FLAT
from pipeline_audio import AudioPipeline
from profili_vocali import tone_label
from voice_conversion import VoiceStage, export_prepared, segment_model


class EditorPlaybackMixin:
    """Editor playback responsibilities of the Qt editor."""
    def regenerate_file(self):
        self.start(quick=True, continuous=True, from_start=True,
                   generate_only=True, regenerate=True)

    def activate_selected_segment(self):
        if self.busy and self.listing.currentRow() != self.playing_index:
            self.listen_selected_segment()
        else:
            self.toggle_segment_playback()

    def listen_selected_segment(self):
        index = self.listing.currentRow()
        if index < 0:
            return
        if not self.busy:
            self.resume_request = None
            self.start(quick=True)
            return
        if self.exporting or self.generate_only or self.pipeline.stop_event.is_set():
            return
        if (self.transport and self.transport['paused'] and
                (self.playing_index is None or index == self.playing_index) and self.replaying_index is None):
            self.send_transport('toggle')
        elif self.pipeline.request_replay(index):
            self.status.setText(tr('Riascolto del segmento selezionato; poi riprendo la lettura interrotta.'))
        else:
            self.status.setText(tr('Audio del segmento non ancora pronto. La preparazione continua.'))

    def toggle_segment_playback(self):
        if self.busy:
            self.send_transport('toggle')
        elif self.resume_request is not None:
            request = self.resume_request
            self.select_segment(request['index'])
            self.reading_duration.setText(request['duration'])
            self.start(quick=True, continuous=request['continuous'])
        else:
            self.start(quick=True)

    def update_play_shortcut(self, *_):
        focus = self.focusWidget()
        in_list = focus is self.listing or (focus is not None and self.listing.isAncestorOf(focus))
        editing = isinstance(focus, (Qt.QLineEdit, Qt.QPlainTextEdit, Qt.QTextEdit, Qt.QComboBox,
                                     Qt.QAbstractSpinBox, Qt.QAbstractButton))
        self.play_shortcut.setEnabled(in_list or (self.busy and not editing))

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
                and self.converted == self.total and not self.audio.pending(self.plan))

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
                or self.segment_model.currentData() != segment.get('voice_model', 'inherit')):
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
        voice_stage=VoiceStage(self.plan,self.voice_mode.currentData(),regenerate=regenerate)
        self.voice_statistics = voice_stage.statistics
        self.direct_statistics = {'generated':0,'cached':0}
        try:
            voice_stage.preflight(checked)
        except (OSError,ValueError,RuntimeError) as error:
            Qt.QMessageBox.warning(self,tr('Modello voce non pronto'),str(error))
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
        self.resume_request = None
        self.playing_index = self.replaying_index = None
        self.follow_playback = True
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
        self.generated = self.converted = self.played = 0
        self.conversion_started = None
        self.generation_started = self.transport = None
        gains = tuple(control.value() for control in self.eq_controls)
        audio_options = {'voice_mode': self.voice_mode.currentData(), 'apply_voice_model':False}
        if self.voice_design.isChecked():
            audio_options['voice_design'] = True
        if self.reading_speed.currentData():
            audio_options['speed_level'] = self.reading_speed.currentData()
        if junction:
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
                direct=segment_model(self.plan,self.plan['segments'][index])=='qwen-direct'
                cached=None
                if direct and not regenerate:
                    cached=self.audio.preview(self.path,index,cancel_event=cancel,cache_only=True,**audio_options)
                if cached is not None:
                    self.direct_statistics['cached']+=1
                    return cached
                result=self.audio.preview(self.path, index, cancel_event=cancel, **options)
                if direct: self.direct_statistics['generated']+=1
                return result

        def generate_batch(batch, cancel):
            if any(segment_model(self.plan,self.plan['segments'][i])=='qwen-direct' for i in batch):
                return [generate(i,cancel) for i in batch]
            return self.audio.preview_batch(self.path, batch, cancel, **audio_options)

        def convert_voice(index,source,cancel):
            if junction:
                originals=self.audio.preview_batch(self.path,[index,index+1],cancel,**audio_options)
                converted=[self.audio.filter_preview(self.path,i,voice_stage.convert(i,p,cancel),gains,cancel)
                           for i,p in zip([index,index+1],originals)]
                return self.audio.write_junction(Path(self.junction_work.name)/'raccordo-voce.wav',converted,cancel)
            final=voice_stage.convert(index,source,cancel)
            return self.audio.filter_preview(self.path,index,final,gains,cancel)

        sections = {}
        played_durations = {}
        played_ms = 0.0

        def play_segment(audio):
            nonlocal played_ms
            options = {}
            if limit_ms is not None and not self.pipeline.replaying:
                options['limit_ms'] = max(1, round(limit_ms - played_ms))
            self.audio.play_audio(audio, stop_event=self.pipeline.playback_stop_event, events=self.events, **options)
            if (self.pipeline.replaying or self.pipeline.playback_stop_event.is_set()
                    or self.pipeline.stop_event.is_set()):
                return
            sections[self.pipeline.current_index] = audio
            if limit_ms is not None:
                with wave.open(str(audio), 'rb') as wav:
                    played_durations[self.pipeline.current_index] = wav.getnframes() * 1000 / wav.getframerate()
                    played_ms = sum(played_durations.values())
                return played_ms < limit_ms

        def save_playback():
            if sections:
                return self.audio.save_reading(target, list(sections.values()), self.pipeline.stop_event, limit_ms)

        export_clips=[]
        def collect_export(clip):
            export_clips.append(clip)

        def assemble_export():
            return export_prepared(self.path,export_clips,target,self.pipeline.stop_event)

        self.total = len(indices)
        self.reading_indices = tuple(indices)
        self.reading_limit = limit_ms / 1000 if limit_ms is not None else None
        self.estimated_durations = {}
        self.actual_durations = {}
        self.synthesis_times = []
        self.session_started = time.monotonic()
        self.preparation_eta.clear()
        self.reading_eta.clear()
        for bar in (self.gen_bar, self.voice_bar, self.read_bar):
            bar.setRange(0, self.total * 100)
            bar.setValue(0)
        self.gen_label.setText(tr('Pronuncia: 0/{}').format(self.total))
        self.voice_label.setText(tr('Voce: 0/{}').format(self.total))
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
        if regenerate:
            self.status.setText(tr('Rigenerazione di {} segmenti: cache ignorata.').format(self.total))
        fractional = not export and not quick and self.fractional.isChecked()
        self.pipeline = AudioPipeline(indices, generate, None if generate_only else collect_export if export else play_segment, self.events,
                                      buffer_size=4, startup_items=1 if limit_ms is not None else 3,
                                      preload_all=preload,
                                      analyze=analyze,
                                      batch_size=4 if fractional else None,
                                      generate_batch=generate_batch if fractional else None,
                                      on_playback_complete=assemble_export if export else save_playback if target is not None else None,
                                      convert=convert_voice,on_conversion_finished=voice_stage.close)
        for control in self.edit_controls:
            if control is not self.listing or export or generate_only:
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
                self.gen_bar.setValue(self.generated * 100)
                self.generation_started = None
            elif kind == 'converting':
                self.conversion_started=event['started']
                self.status.setText(tr('Voce: preparazione del modello o conversione del segmento {}.').format(event['index']+1))
            elif kind == 'converted':
                self.converted=event['position']
                self.voice_bar.setValue(self.converted*100)
                self.conversion_started=None
                if event['duration'] is not None:
                    self.actual_durations[event['index']]=event['duration']
                if self.exporting or self.generate_only:
                    self.last_audio=event['path']
            elif kind == 'conversion_finished':
                self.conversion_started=None
            elif kind == 'playing':
                self.playing_index = event['index']
                self.replaying_index = None
                self.transport = None
                self.position = event['position']
                if self.junction_work is None:
                    self.last_audio = event['path']
                if self.follow_playback:
                    self.select_segment(event['index'])
                self.status.setText(tr('Assemblaggio del file audio…') if self.exporting else tr('Lettura in corso.') if self.pipeline.producer_done.is_set()
                                    else tr('Lettura e vocalizzazione in parallelo.'))
            elif kind == 'saved':
                self.last_audio = event['path']
            elif kind == 'replaying':
                self.replaying_index = event['index']
                self.transport = None
                self.status.setText(tr('Riascolto segmento {}').format(event['index'] + 1))
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
        self.gen_label.setText(tr('Pronuncia: {}/{} pronti').format(self.generated, self.total) + detail)
        voice_detail=''
        if self.conversion_started is not None:
            voice_detail=tr('  ·  {} s sul segmento corrente').format(int(time.monotonic()-self.conversion_started))
        stats = self.voice_statistics
        self.voice_label.setText(tr('Voce: {}/{} · nuove {} · cache {} · base {}').format(
            self.converted,self.total,stats['generated'],stats['cached'],stats['source'])+voice_detail)
        direct=self.direct_statistics
        if direct['generated'] or direct['cached']:
            self.voice_label.setText(tr('Voce diretta: {} nuove · {} da cache').format(
                direct['generated'],direct['cached'])+voice_detail)
        if self.pipeline.stop_event.is_set():
            self.read_label.setText(tr('Lettura arrestata'))
        elif self.transport:
            elapsed, duration = self.transport['elapsed'], self.transport['duration']
            if self.replaying_index is None:
                self.read_bar.setValue(self.played * 100 + min(99, int(100 * elapsed / duration)))
            state = tr('In pausa') if self.transport['paused'] else tr('In riproduzione')
            self.read_label.setText(f'{state}  ·  {self.position}/{self.total}  ·  {self.clock(elapsed)} / {self.clock(duration)}')
            if self.replaying_index is not None:
                self.read_label.setText(tr('Riascolto segmento {}').format(self.replaying_index + 1) +
                                        f'  ·  {self.clock(elapsed)} / {self.clock(duration)}')
        elif not self.exporting:
            suffix = tr('  ·  Attendo audio') if self.played < self.total else ''
            self.read_label.setText(tr('Lettura: {}/{} completati').format(self.played, self.total) + suffix)
        if self.exporting:
            self.read_label.setText(tr('Ascolto: non previsto · preparazione del file audio'))
        self.update_estimates()
        self.preparation_eta.setVisible(bool(self.preparation_eta.text()))
        self.reading_eta.setVisible(bool(self.reading_eta.text()))
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
                preparation = tr('pronuncia pronta · attendo la voce') if self.converted<self.total else tr('audio pronto')
            elif generation_remaining is None:
                preparation = tr('in analisi o in attesa del primo campione')
            else:
                preparation = tr('circa {} rimanenti').format(self.clock(generation_remaining + 0.999))
            self.preparation_eta.setText(tr('Prima dell’ascolto: ') + preparation)

        if self.replaying_index is not None:
            self.reading_eta.setText(tr('Fine lettura: stima sospesa durante il riascolto'))
            return
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
        if self.converted<self.total:
            self.reading_eta.setText(self.reading_eta.text()+tr(' · stima da confermare dopo la conversione'))
            return
        if generation_remaining is not None:
            if self.pipeline.preload_all:
                finish = audio + generation_remaining
            else:
                ready_count = max(0, self.converted - self.played)
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
        self.playing_index = self.replaying_index = None
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
        self.refresh_model_status()
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
            stats = self.voice_statistics
            self.status.setText(self.status.text() + '\n' + tr(
                'Conversioni vocali: {} nuove · {} da cache · {} senza conversione.').format(
                    stats['generated'], stats['cached'], stats['source']))
            direct=self.direct_statistics
            if direct['generated'] or direct['cached']:
                self.status.setText(tr('Voce diretta: {} nuove · {} da cache').format(
                    direct['generated'],direct['cached'])+'\n'+tr('Completato. Audio: {}').format(self.last_audio))

    def update_transport(self):
        self.update_play_shortcut()
        stopping = self.busy and self.pipeline.stop_event.is_set()
        active = self.busy and self.transport is not None and not stopping
        paused = active and self.transport['paused']
        resumable = not self.busy and self.resume_request is not None
        icon = Qt.QStyle.StandardPixmap
        label = tr('Riprendi') if paused or resumable else tr('Pausa')
        self.pause_button.setText(label)
        self.pause_button.setToolTip(label)
        self.pause_button.setAccessibleName(label)
        self.pause_button.setIcon(self.command_icon(icon.SP_MediaPlay, 'green') if paused or resumable
                                  else self.command_icon(icon.SP_MediaPause, 'amber'))
        self.pause_button.setEnabled(active or resumable)
        self.segment_play.setEnabled((self.busy and not self.exporting and not self.generate_only and not stopping)
                                     or (not self.busy and self.path is not None))
        for control in (self.back_button, self.forward_button):
            control.setEnabled(active)
        self.stop_button.setEnabled((self.busy or self.auto_timer.isActive()) and not stopping)
        self.open_markdown_button.setEnabled(not self.busy)
        self.file_listing.setEnabled(not self.busy)
        for control in self.file_controls:
            control.setEnabled(not self.busy)
        self.open_button.setEnabled(not self.busy and self.last_audio is not None)
        self.export_button.setEnabled(self.can_export_prepared() if self.busy else self.path is not None)

    def send_transport(self, action, direction=0):
        if self.busy and self.transport is not None and not self.pipeline.stop_event.is_set():
            self.transport['commands'].put((action, direction * self.jump.value()))

    def stop(self):
        self.stopped = True
        self.auto_timer.stop()
        if self.busy:
            if not self.exporting and not self.generate_only and self.junction_work is None:
                self.resume_request = dict(index=self.listing.currentRow(),
                                           continuous=self.active_request[2],
                                           duration=self.reading_duration.text())
                if self.replaying_index is not None or self.playing_index is not None:
                    self.resume_request['index'] = self.replaying_index if self.replaying_index is not None else self.playing_index
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

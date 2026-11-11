"""Audiolibro locale per revisione: Python standard + voci Windows OneCore."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import tempfile
import wave
from version_info import __version__
from profili_vocali import load as load_profiles, resolve as resolve_voice, TONES
from qwen_backend import reference as qwen_reference
from numeri_italiani import VERSION as SPEECH_VERSION, spoken_it
from pipeline_audio import AudioPipeline, ReadingCancelled
from gestione_voci import mix_group
from equalizzatore_voci import FLAT, VERSION as EQ_VERSION, applies_to, equalize_pcm, validate_gains
from profili_vocali import tone_names, tone_label
from lingue_audio import (DOCUMENT_LANGUAGES, LANGUAGES, QWEN, VERSION as LANGUAGE_VERSION,
                          lexicon, segment_language, speech_parts)
from montaggio_audio import VERSION as MOUNT_VERSION, flag_level_jump, mount_pcm


def language_policy():
    from lingue_audio import LEXICON
    entries=lexicon()
    hashes={}
    for key,item in entries.items():
        if item.get('audio'):
            p=Path(item['audio']); p=p if p.is_absolute() else LEXICON.parent/p
            hashes[key]=hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else 'missing'
    return LANGUAGE_VERSION + ':' + hashlib.sha256(json.dumps([entries,hashes],ensure_ascii=False,sort_keys=True).encode()).hexdigest()

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
ROLES = {
    'Narratore': ('CosimoM', -1, 0),
    'Da assegnare': ('CosimoM', -1, 0),
    'Yusuf': ('CosimoM', -1, -2),
    'Baldo': ('CosimoM', -2, -4),
    'Aureliano': ('CosimoM', 0, -3),
    'Anselmo': ('CosimoM', -2, -1),
    'Nicasio': ('CosimoM', 0, 3),
    'Sālim': ('CosimoM', 0, 1),
    'Alasia': ('ElsaM', 0, 1),
    'Eudocia': ('ElsaM', -1, -2),
    'Miriam': ('ElsaM', -1, 0),
    'Altro': ('ElsaM', 0, 0),
}


def atomic(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False, suffix='.tmp') as f:
        staged = Path(f.name)
        f.write(data)
    try:
        assert staged.read_bytes() == data
        if path.exists():
            previous = path.read_bytes()
            copy = path.with_name(path.name + '.' + hashlib.sha256(previous).hexdigest()[:12] + '.bak')
            if not copy.exists():
                copy.write_bytes(previous)
        os.replace(staged, path)
    finally:
        staged.unlink(missing_ok=True)


def save(path, data):
    payload = json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8')
    assert json.loads(payload) == data
    atomic(path, payload)


def prepare(source, output):
    if Path(output).exists():
        raise ValueError('Il piano esiste già: scegliere un nuovo nome per conservare le attribuzioni.')
    source = source.resolve()
    raw = source.read_bytes()
    text = raw.decode('utf-8-sig')
    depth = 0
    for char in text:
        if char == '«': depth += 1
        if char == '»': depth -= 1
        if depth not in (0, 1):
            raise ValueError('Caporali annidati o chiusi senza apertura: verificare il sorgente.')
    if depth:
        raise ValueError('Caporale aperto senza chiusura: verificare il sorgente.')
    segments = []
    # Every paragraph keeps its source line; quotations within prose stay intact.
    for match in re.finditer(r'\S[^\n]*(?:\n(?!\s*\n)[^\n]+)*', text):
        original = match.group().strip()
        if original.startswith('#') or re.fullmatch(r'[\s*_-]+', original):
            continue
        if original.startswith('*') and 'POV' in original and '«' not in original:
            continue
        clean = re.sub(r'\*\*\[([^\]]+)\]\*\*\s*', '', original)
        clean = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', clean)
        clean = clean.replace('*', '').replace('\n', ' ')
        for piece in re.split(r'(«[^»]*»)', clean):
            if not piece.strip():
                continue
            segments.append({'id': len(segments) + 1,
                             'line': text.count('\n', 0, match.start()) + 1,
                             'role': 'Da assegnare' if piece.startswith('«') else 'Narratore',
                             'text': piece.strip()})
    if not segments:
        raise ValueError('Nessun paragrafo narrativo trovato')
    plan = {'version': 1, 'source': str(source), 'sha256': hashlib.sha256(raw).hexdigest(),
            'note': 'Battute non attribuite: voce narrante, segnalazione esplicita. Non è canonizzazione.',
            'segments': segments}
    save(output, plan)
    return plan


DEFAULT_PAUSE_MS = 600
MAX_PAUSE_MS = 3000


def segment_pause_ms(segment, following=None):
    if 'pause_ms' in segment:
        return segment['pause_ms']
    text = segment['text'].rstrip(' »”"\'')
    if text.endswith(('…', '...')):
        return 480
    if following is not None and segment['line'] == following['line']:
        # Keep an attribution attached to its quotation, but give questions
        # and exclamations enough space before the narration resumes.
        if text.endswith(('?', '!')):
            return 240
        if text.endswith((',', ';', ':')):
            return 90
        return 120
    if text.endswith(('.', '!', '?')):
        return 320
    return 150


def segment_speech_parts(plan, segment):
    return speech_parts(segment.get('spoken_text', segment['text']), segment_language(plan, segment))


def segment_audio_key(plan, segment, *, legacy=False):
    fields = {key: segment.get(key) for key in ('id', 'text', 'spoken_text', 'role', 'tone')}
    fields['language'] = segment_language(plan, segment)
    if legacy:
        fields['pause_ms'] = segment_pause_ms(segment)
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


def plan_pause_ms(plan, segment):
    index = segment['id']
    following = plan['segments'][index] if index < len(plan['segments']) else None
    return segment_pause_ms(segment, following)


def legacy_preview_pause(plan, segment, audio_key):
    for candidate in (segment, {key: value for key, value in segment.items() if key != 'pause_ms'}):
        if audio_key == segment_audio_key(plan, candidate, legacy=True):
            return segment_pause_ms(candidate)
    return None
NARRATOR_PITCH_ST = 0.0
SPEED_LEVELS = (0, 1, 2)


def read_plan(path):
    plan = json.loads(Path(path).read_text(encoding='utf-8'))
    if plan.get('version') != 1 or not isinstance(plan.get('segments'), list) or not plan['segments']:
        raise ValueError('Piano non valido o vuoto: importare un capitolo narrativo.')
    if plan.get('language', 'auto') not in DOCUMENT_LANGUAGES:
        raise ValueError('Lingua del documento non valida.')
    raw = Path(plan['source']).read_bytes()
    if hashlib.sha256(raw).hexdigest() != plan['sha256']:
        raise ValueError('Il manoscritto è cambiato: preparare un nuovo piano, senza sovrascrivere le attribuzioni precedenti.')
    profiles = load_profiles()
    for expected, segment in enumerate(plan['segments'], 1):
        if segment.get('id') != expected or not isinstance(segment.get('line'), int) or segment['line'] < 1:
            raise ValueError('Identificativi o righe non validi nel piano.')
        if not isinstance(segment.get('text'), str) or not segment['text'].strip():
            raise ValueError('Segmento di testo vuoto o non valido.')
        if segment['role'] not in profiles['characters']:
            raise ValueError('Voce sconosciuta: ' + segment['role'])
        tone_names(segment.get('tone', 'Neutro'))
        if segment.get('language', 'auto') not in LANGUAGES:
            raise ValueError('Lingua del segmento non valida.')
        if 'spoken_text' in segment and (not isinstance(segment['spoken_text'], str)
                                         or not segment['spoken_text'].strip()):
            raise ValueError('Testo per la pronuncia non valido')
        pause = segment.get('pause_ms', DEFAULT_PAUSE_MS)
        if type(pause) is not int or not 0 <= pause <= MAX_PAUSE_MS:
            raise ValueError('Pausa non valida nel piano.')
    return plan


def pending(plan):
    return sum(s['role'] == 'Da assegnare' for s in plan['segments'])


def reference_hashes(profiles):
    result = {}
    for item in profiles['characters'].values():
        if item.get('engine') == 'qwen3':
            path = Path(item['reference'])
            data, audio = qwen_reference(path)
            result[str(path)] = {'metadata': hashlib.sha256(path.read_bytes()).hexdigest(),
                                 'audio': data['audio_sha256']}
    return result


def run_backend(command, work, engine, cancel_event=None):
    options = dict(text=True, encoding='utf-8', errors='replace',
                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if cancel_event is None:
        return subprocess.run(command, check=False, capture_output=True, **options)
    if cancel_event.is_set(): raise ReadingCancelled('Sintesi interrotta')
    stdout = work / (engine + '.stdout.log')
    stderr = work / (engine + '.stderr.log')
    with stdout.open('w', encoding='utf-8') as out, stderr.open('w', encoding='utf-8') as err:
        process = subprocess.Popen(command, stdout=out, stderr=err,
                                   creationflags=options['creationflags'])
        try:
            while process.poll() is None:
                if cancel_event.wait(.1):
                    if os.name == 'nt':
                        # Solo l'albero del processo appena avviato dal lettore.
                        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                                       capture_output=True, creationflags=options['creationflags'])
                    else:
                        process.terminate()
                    try: process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill(); process.wait()
                    raise ReadingCancelled('Sintesi interrotta')
            if cancel_event.is_set(): raise ReadingCancelled('Sintesi interrotta')
        finally:
            if process.poll() is None:
                process.kill(); process.wait()
    return subprocess.CompletedProcess(command, process.returncode,
        stdout.read_text(encoding='utf-8', errors='replace'),
        stderr.read_text(encoding='utf-8', errors='replace'))


NARRATOR_MODE = "narratore"
CAST_MODE = "cast"
VOICE_MODES = (NARRATOR_MODE, CAST_MODE)


def render(plan_path, output, selected=None, allow_unassigned=False, cancel_event=None, *, voice_eq=FLAT, voice_mode=NARRATOR_MODE, speed_level=0, reuse_previews=False, voice_design=False):
    if type(speed_level) is not int or speed_level not in SPEED_LEVELS:
        raise ValueError('Velocità di lettura sconosciuta')
    if voice_mode not in VOICE_MODES:
        raise ValueError('Modalità voci sconosciuta')
    voice_eq = validate_gains(voice_eq)
    policy=language_policy()
    plan = read_plan(plan_path)
    plan_hash = hashlib.sha256(Path(plan_path).read_bytes()).hexdigest()
    profiles = load_profiles()
    references = reference_hashes(profiles)
    profiles_hash = hashlib.sha256(json.dumps(profiles, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()
    if selected is None and pending(plan) and not allow_unassigned:
        raise ValueError(f"Restano {pending(plan)} battute da assegnare. Completarle oppure usare --allow-unassigned per una bozza narrata.")
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    indices = range(len(plan['segments'])) if selected is None else (
        (selected,) if isinstance(selected, int) else selected)
    segments = [plan['segments'][index] for index in indices]
    with tempfile.TemporaryDirectory(dir=output.parent) as work:
        work = Path(work)
        characters = profiles['characters']
        narrator = characters['Narratore']
        fixed_design_voice = voice_design and voice_mode == NARRATOR_MODE
        narrator_pitch = NARRATOR_PITCH_ST
        if not voice_design and voice_mode == NARRATOR_MODE and narrator.get('engine') == 'qwen3':
            metadata = json.loads(Path(narrator['reference']).read_text(encoding='utf-8'))
            if metadata.get('origin') == 'recorded-reference':
                narrator_pitch = 0

        design_instructions = {}

        def voice_job(role, tone, parts, destination):
            base = narrator if voice_mode == NARRATOR_MODE else characters[role]
            delivery_role = 'Narratore' if voice_mode == NARRATOR_MODE and role == 'Da assegnare' else role
            _, rate, pitch = resolve_voice(profiles, delivery_role, tone)
            engine = base.get('engine', 'windows')
            if voice_design and engine != 'qwen3':
                raise ValueError('VoiceDesign diretto richiede un profilo sintetico Qwen: ' + role)
            if engine == 'windows':
                if any(part['language'] != 'it' for part in parts):
                    raise ValueError('La lettura multilingue richiede una voce base Qwen: ' + role)
                rate = min(10, rate + speed_level)
            job = {'token': 'MSTTS_V110_itIT_' + base['voice'], 'rate': rate, 'pitch': pitch,
                   'text': ''.join(part['text'] for part in parts), 'parts': parts,
                   'gender': base.get('gender', 'non_specificato'), 'output': str(destination),
                   'engine': engine, 'tone': tone}
            if engine == 'qwen3':
                job['reference'] = base['reference']
                if speed_level:
                    job['reading_speed'] = 1 + .10 * speed_level
                if voice_design:
                    reference = base['reference']
                    if reference not in design_instructions:
                        metadata = json.loads(Path(reference).read_text(encoding='utf-8'))
                        description = metadata.get('description')
                        if (metadata.get('origin') != 'synthetic-voice-design'
                                or not isinstance(description, str) or not description.strip()):
                            raise ValueError('VoiceDesign diretto richiede una descrizione sintetica salvata: ' + role)
                        design_instructions[reference] = description
                    if fixed_design_voice:
                        job['speaker_embedding_only'] = True
                    else:
                        job['design_instruct'] = design_instructions[reference]
                elif voice_mode == NARRATOR_MODE:
                    profile = characters[delivery_role]
                    job['character_speed'] = 1 + .025 * (profile['rate'] - narrator['rate'])
                    job['character_pitch'] = narrator_pitch + .25 * (profile['pitch'] - narrator['pitch'])
            return job

        parts_by_id = {
            seg['id']: segment_speech_parts(plan, seg) for seg in segments
        }
        jobs, synthesis_jobs, groups = [], [], []
        cached_pauses = {}
        for n, seg in enumerate(segments):
            role = seg['role']
            tone = seg.get('tone', 'Neutro')
            parts = parts_by_id[seg['id']]
            destination = work / f'{n:05}.wav'
            if reuse_previews:
                preview_path = raw_preview(plan_path, seg['id'] - 1, voice_mode=voice_mode,
                                           speed_level=speed_level, cache_only=True, voice_design=voice_design)
                if preview_path is not None:
                    jobs.append(preview_path)
                    info = json.loads(preview_path.with_suffix('.indice.json').read_text(encoding='utf-8'))
                    timing = info['segments'][0]
                    cached_pauses[seg['id']] = (timing.get('pause_frames'),
                                                 legacy_preview_pause(plan, seg, timing.get('audio_key')))
                    continue
            jobs.append(destination)
            profile = characters[role]
            if fixed_design_voice or profile.get('kind') != 'gruppo':
                synthesis_jobs.append(voice_job(role, tone, parts, destination))
                continue
            members = []
            for i, name in enumerate(profile['members']):
                member_output = destination.with_stem(f'{destination.stem}_voce{i}')
                synthesis_jobs.append(voice_job(name, tone, parts, member_output))
                members.append(str(member_output))
            groups.append((members, str(destination)))
        backend_reports = {}
        for engine in ('windows', 'qwen3'):
            subset = [item for item in synthesis_jobs if item['engine'] == engine]
            if not subset:
                continue
            job = work / (engine + '.json')
            job.write_text(json.dumps(subset, ensure_ascii=False), encoding='utf-8-sig')
            if engine == 'windows':
                command = ['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File',
                           str(HERE / 'voce_windows.ps1'), '-Job', str(job)]
            else:
                python = HERE / '.runtime' / 'qwen' / 'Scripts' / 'python.exe'
                if not python.exists():
                    raise RuntimeError('Qwen non installato: eseguire installa_qwen.py')
                command = [str(python), '-X', 'utf8', str(HERE / 'qwen_backend.py'), 'sintesi', str(job)]
            result = run_backend(command, work, engine, cancel_event)
            if result.returncode:
                raise RuntimeError('Sintesi ' + engine + ' non riuscita.\n' + (result.stderr or result.stdout).strip())
            report = job.with_suffix('.backend.json')
            if report.exists():
                backend_reports[engine] = json.loads(report.read_text(encoding='utf-8'))
        for members, destination in groups: mix_group(members, destination)
        combined = work / 'complete.wav'
        timings = []
        elapsed = 0.0
        with wave.open(str(combined), 'wb') as dest:
            fmt = None
            for seg, destination in zip(segments, jobs):
                with wave.open(str(destination), 'rb') as src:
                    current = (src.getnchannels(), src.getsampwidth(), src.getframerate())
                    if fmt is None:
                        fmt = current
                        dest.setnchannels(fmt[0]); dest.setsampwidth(fmt[1]); dest.setframerate(fmt[2])
                    if current != fmt:
                        raise ValueError('Formati vocali incompatibili')
                    padding, legacy_pause = cached_pauses.get(seg['id'], (0, None))
                    if padding is None:
                        padding_ms = segment_pause_ms(seg) if legacy_pause is None else legacy_pause
                        padding = round(fmt[2] * padding_ms / 1000)
                    speech_frames = src.getnframes() - padding
                    if speech_frames <= 0:
                        raise ValueError('Segmento audio senza parlato')
                    frames = src.readframes(speech_frames)
                    if voice_eq != FLAT and applies_to(seg['role']):
                        if fmt[1] != 2:
                            raise ValueError('L’equalizzatore richiede audio PCM a 16 bit.')
                        frames = equalize_pcm(frames, fmt[2], fmt[0], voice_eq)
                    pause_ms = plan_pause_ms(plan, seg)
                    frames, mounting = mount_pcm(frames, *fmt, pause_ms, following=seg['id'] < len(plan['segments']))
                    if seg['role'] == 'Da assegnare':
                        mounting.setdefault('review', []).append('unassigned')
                    parts = parts_by_id[seg['id']]
                    languages = {part['language'] for part in parts}
                    if len(languages) == 1 and any(part.get('audio') or part['language'] not in QWEN for part in parts):
                        mounting.setdefault('review', []).append('check_language_voice')
                    pause = mounting['pause_frames']
                    count = len(frames) // (fmt[0] * fmt[1]) + pause
                    timings.append({'id': seg['id'], 'line': seg['line'], 'role': seg['role'],
                                    'tone': seg.get('tone', 'Neutro'), 'seconds': round(elapsed, 3),
                                    'frames': count, 'audio_key': segment_audio_key(plan, seg),
                                    'pause_ms': pause_ms, **mounting})
                    if len(timings) > 1:
                        flag_level_jump(timings[-2], timings[-1])
                    dest.writeframes(frames)
                    dest.writeframes(b'\x00' * pause * fmt[0] * fmt[1])
                    elapsed += count / fmt[2]
        with wave.open(str(combined), 'rb') as check:
            assert check.getnframes() > 0
        read_plan(plan_path)
        if language_policy()!=policy: raise ValueError('Lessico pronunce modificato durante la sintesi')
        if cancel_event is not None and cancel_event.is_set():
            raise ReadingCancelled('Sintesi interrotta')
        if hashlib.sha256(Path(plan_path).read_bytes()).hexdigest() != plan_hash:
            raise ValueError('Attribuzioni cambiate durante la sintesi: rigenerare l’audio.')
        if load_profiles() != profiles:
            raise ValueError('Profili vocali cambiati durante la sintesi: rigenerare l’audio.')
        if reference_hashes(profiles) != references:
            raise ValueError('Campioni vocali cambiati durante la sintesi: rigenerare l’audio.')
        if output.suffix.lower() == '.mp4':
            from converti_audio import convert, digest
            convert(combined, output, cancel_event=cancel_event)
            audio_hash = digest(output)
            index_path = output.with_suffix('.mp4.indice.json')
        else:
            audio_bytes = combined.read_bytes()
            atomic(output, audio_bytes)
            audio_hash = hashlib.sha256(audio_bytes).hexdigest()
            index_path = output.with_suffix('.indice.json')
        save(index_path, {'source_sha256': plan['sha256'],
             'plan_sha256': plan_hash, 'audio_sha256': audio_hash,
             'profiles_sha256': profiles_hash, 'profiles': profiles,
             'voice_references': references, 'backends': backend_reports, 'voice_mode': voice_mode, 'speed_level': speed_level,
             'voice_eq': {'version': EQ_VERSION, 'gains_db': list(voice_eq)},
             'speech_normalization': SPEECH_VERSION,
             'mounting_version': MOUNT_VERSION,
             'default_pause_ms': DEFAULT_PAUSE_MS, 'narrator_pitch_st': 0 if voice_design else NARRATOR_PITCH_ST,
             'voice_design': voice_design,
             'language_policy': language_policy(),
             'language_parts': [{'id':s['id'],'parts':parts_by_id[s['id']]} for s in segments],
             'spoken_forms': [{'id': s['id'], 'source_text': s['text'], 'spoken_text': ''.join(p['text'] for p in parts_by_id[s['id']])}
                              for s in segments if ''.join(p['text'] for p in parts_by_id[s['id']]) != s['text']],
             'unassigned': sum(s['role'] == 'Da assegnare' for s in segments),
             'duration': elapsed, 'segments': timings})
    return output


def preview_target(path, index, voice_mode, speed_level, voice_design=False):
    target = path.parent / '.ascolto' / path.stem / voice_mode
    if voice_design:
        target /= 'voicedesign-timbro-v2' if voice_mode == NARRATOR_MODE else 'voicedesign'
    if speed_level:
        target /= f'velocita-{speed_level}'
    return target / f'{index + 1:05d}.wav'


def cached_profiles_match(data):
    previous = data['profiles']
    current = load_profiles()
    if any(current.get(key) != value for key, value in previous.items() if key != 'characters'):
        return False
    if any(current['characters'].get(name) != profile for name, profile in previous['characters'].items()):
        return False
    return data.get('voice_references', {}) == reference_hashes(previous)


def cached_pronunciation_matches(data, plan, segment):
    previous = data.get('language_policy', '')
    if previous == language_policy():
        return True
    if previous.split(':', 1)[0] != LANGUAGE_VERSION:
        return False
    parts = segment_speech_parts(plan, segment)
    return (not any(part.get('audio') for part in parts)
            and data.get('language_parts') == [{'id': segment['id'], 'parts': parts}])


def raw_preview(path, index, cancel_event=None, *, voice_mode=NARRATOR_MODE, speed_level=0,
                cache_only=False, voice_design=False, regenerate=False):
    if type(speed_level) is not int or speed_level not in SPEED_LEVELS:
        raise ValueError('Velocità di lettura sconosciuta')
    if voice_mode not in VOICE_MODES:
        raise ValueError('Modalità voci sconosciuta')
    path = Path(path).resolve()
    plan = read_plan(path)
    target = preview_target(path, index, voice_mode, speed_level, voice_design)
    metadata = target.with_suffix('.indice.json')
    if not regenerate and target.exists() and metadata.exists():
        try:
            data = json.loads(metadata.read_text(encoding='utf-8'))
            segment = plan['segments'][index]
            timing = data['segments'][0]
            audio_key = timing.get('audio_key')
            if not audio_key:
                same_segment = data['plan_sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
            elif data.get('mounting_version'):
                same_segment = audio_key == segment_audio_key(plan, segment)
            else:
                same_segment = legacy_preview_pause(plan, segment, audio_key) is not None
            if (same_segment
                    and data.get('voice_mode') == voice_mode
                    and data.get('speed_level', 0) == speed_level
                    and data.get('default_pause_ms') == DEFAULT_PAUSE_MS
                    and data.get('voice_design', False) == voice_design
                    and data.get('narrator_pitch_st') == (0 if voice_design else NARRATOR_PITCH_ST)
                    and cached_pronunciation_matches(data, plan, segment)
                    and (spoken_it(plan['segments'][index]['text']) == plan['segments'][index]['text']
                         or data.get('speech_normalization') == SPEECH_VERSION)
                    and cached_profiles_match(data)
                    and data.get('voice_eq', {}).get('gains_db', list(FLAT)) == list(FLAT)
                    and [x['id'] for x in data['segments']] == [plan['segments'][index]['id']]
                    and data['audio_sha256'] == hashlib.sha256(target.read_bytes()).hexdigest()):
                if not cache_only and audio_key and (data.get('mounting_version') != MOUNT_VERSION
                                                       or timing.get('pause_ms') != plan_pause_ms(plan, segment)):
                    return render(path, target, selected=index, cancel_event=cancel_event,
                                  voice_mode=voice_mode, speed_level=speed_level,
                                  voice_design=voice_design, reuse_previews=True)
                return target
        except (OSError, ValueError, KeyError, IndexError):
            pass
    if cache_only:
        return None
    options = {'speed_level': speed_level} if speed_level else {}
    if voice_design:
        options['voice_design'] = True
    return render(path, target, selected=index, cancel_event=cancel_event, voice_mode=voice_mode, **options)


def preview(path, index, cancel_event=None, *, voice_eq=FLAT, voice_mode=NARRATOR_MODE,
            speed_level=0, voice_design=False, regenerate=False):
    target = raw_preview(path, index, cancel_event, voice_mode=voice_mode, speed_level=speed_level,
                         voice_design=voice_design, regenerate=regenerate)
    return filter_preview(path, index, target, voice_eq, cancel_event)


def preview_batch(path, indices, cancel_event, *, voice_eq=FLAT, voice_mode=NARRATOR_MODE,
                  speed_level=0, voice_design=False):
    path = Path(path).resolve()
    ready = {index: raw_preview(path, index, cancel_event, voice_mode=voice_mode,
                                speed_level=speed_level, cache_only=True, voice_design=voice_design) for index in indices}
    missing = [index for index, target in ready.items() if target is None]
    if missing:
        with tempfile.TemporaryDirectory(dir=path.parent) as folder:
            combined = Path(folder) / 'batch.wav'
            render(path, combined, selected=missing, cancel_event=cancel_event,
                   voice_mode=voice_mode, speed_level=speed_level, voice_design=voice_design)
            metadata = json.loads(combined.with_suffix('.indice.json').read_text(encoding='utf-8'))
            with wave.open(str(combined), 'rb') as src:
                for index, timing in zip(missing, metadata['segments'], strict=True):
                    if cancel_event.is_set():
                        raise ReadingCancelled('Sintesi interrotta')
                    target = preview_target(path, index, voice_mode, speed_level, voice_design)
                    buffer = io.BytesIO()
                    with wave.open(buffer, 'wb') as dest:
                        dest.setparams(src.getparams())
                        dest.writeframes(src.readframes(timing['frames']))
                    payload = buffer.getvalue()
                    segment_id = timing['id']
                    segment_metadata = dict(metadata, segments=[dict(timing, seconds=0)],
                                            duration=timing['frames'] / src.getframerate(),
                                            audio_sha256=hashlib.sha256(payload).hexdigest())
                    for key in ('language_parts', 'spoken_forms'):
                        segment_metadata[key] = [item for item in metadata[key] if item['id'] == segment_id]
                    segment_metadata['unassigned'] = int(timing['role'] == 'Da assegnare')
                    atomic(target, payload)
                    save(target.with_suffix('.indice.json'), segment_metadata)
                    ready[index] = target
    for index in set(ready) - set(missing):
        ready[index] = raw_preview(path, index, cancel_event, voice_mode=voice_mode,
                                   speed_level=speed_level, voice_design=voice_design)
    return [filter_preview(path, index, ready[index], voice_eq, cancel_event) for index in indices]


def filter_preview(path, index, target, voice_eq, cancel_event):
    voice_eq = validate_gains(voice_eq)
    if voice_eq == FLAT or not applies_to(read_plan(path)['segments'][index]['role']):
        return target
    payload = target.read_bytes()
    key = hashlib.sha256(repr((EQ_VERSION, voice_eq)).encode() + payload).hexdigest()[:16]
    filtered = target.with_stem(target.stem + '-eq-' + key)
    if filtered.is_file():
        return filtered
    output = io.BytesIO()
    with wave.open(io.BytesIO(payload), 'rb') as src, wave.open(output, 'wb') as dest:
        if src.getsampwidth() != 2:
            raise ValueError('L’equalizzatore richiede audio PCM a 16 bit.')
        dest.setparams(src.getparams())
        frames = src.readframes(src.getnframes())
        dest.writeframes(equalize_pcm(frames, src.getframerate(), src.getnchannels(), voice_eq))
    if cancel_event is not None and cancel_event.is_set():
        raise ReadingCancelled('Sintesi interrotta')
    atomic(filtered, output.getvalue())
    return filtered


def write_reading(staged, paths, stop_event, limit_ms):
    fmt = None
    remaining = None
    offset = 0
    timings = []
    with wave.open(str(staged), 'wb') as dest:
        for path in paths:
            if stop_event.is_set():
                return
            with wave.open(str(path), 'rb') as source:
                current = (source.getnchannels(), source.getsampwidth(), source.getframerate())
                if fmt is None:
                    fmt = current
                    dest.setnchannels(fmt[0])
                    dest.setsampwidth(fmt[1])
                    dest.setframerate(fmt[2])
                    if limit_ms is not None:
                        remaining = (limit_ms * fmt[2] + 500) // 1000
                if current != fmt:
                    raise ValueError('Formati vocali incompatibili')
                frames = source.getnframes()
                if remaining is not None:
                    frames = min(frames, remaining)
                    remaining -= frames
                if not frames:
                    break
                metadata = Path(path).with_suffix('.indice.json')
                if not metadata.exists() and '-eq-' in Path(path).stem:
                    metadata = Path(path).with_name(Path(path).stem.rsplit('-eq-', 1)[0] + '.indice.json')
                info = {}
                try:
                    data = json.loads(metadata.read_text(encoding='utf-8'))
                    info = dict(data['segments'][0])
                except (OSError, ValueError, KeyError, IndexError):
                    pass
                timings.append(dict(info, clip=Path(path).name, seconds=round(offset / fmt[2], 3),
                                    frames=frames))
                if len(timings) > 1:
                    flag_level_jump(timings[-2], timings[-1])
                offset += frames
                while frames and not stop_event.is_set():
                    count = min(frames, fmt[2] * 10)
                    chunk = source.readframes(count)
                    if len(chunk) != count * fmt[0] * fmt[1]:
                        raise ValueError('Clip WAV incompleto')
                    dest.writeframesraw(chunk)
                    frames -= count
    if stop_event.is_set():
        return
    with wave.open(str(staged), 'rb') as check:
        if not offset or check.getnframes() != offset:
            raise ValueError('Lettura WAV vuota o incompleta')
    return timings, offset / fmt[2]


def write_junction(output, paths, stop_event):
    """Play the end and start of two clips in one stream."""
    with wave.open(str(output), 'wb') as dest:
        fmt = None
        for index, path in enumerate(paths):
            if stop_event.is_set():
                raise ReadingCancelled('Ascolto del raccordo interrotto')
            with wave.open(str(path), 'rb') as source:
                current = source.getparams()[:3]
                if fmt is None:
                    fmt = current
                    dest.setparams(source.getparams())
                if fmt != current:
                    raise ValueError('Formati vocali incompatibili')
                count = min(source.getnframes(), fmt[2] * 2)
                if index == 0:
                    source.setpos(source.getnframes() - count)
                dest.writeframes(source.readframes(count))
    return output


def save_reading(output, paths, stop_event, limit_ms=None):
    """Stream a reading to a validated temporary master before publishing."""
    from converti_audio import convert, digest
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=output.parent, suffix='.wav', delete=False) as file:
        staged = Path(file.name)
    try:
        result = write_reading(staged, paths, stop_event, limit_ms)
        if result is None or stop_event.is_set():
            return
        timings, duration = result
        if output.suffix.lower() == '.mp4':
            convert(staged, output, cancel_event=stop_event)
            index = output.with_suffix('.mp4.indice.json')
        else:
            if output.exists():
                backup = output.with_name(output.name + '.' + digest(output)[:12] + '.bak')
                if not backup.exists():
                    shutil.copyfile(output, backup)
            if stop_event.is_set():
                return
            os.replace(staged, output)
            index = output.with_suffix('.indice.json')
        save(index, {'audio_sha256': digest(output), 'duration': duration, 'segments': timings,
                     'mounting_version': MOUNT_VERSION})
        return output
    finally:
        staged.unlink(missing_ok=True)


def play_audio(path, *, stop_event, events, limit_ms=None):
    """Play a local WAV, trimming only the final segment of a timed reading."""
    if stop_event.is_set():
        return
    path = Path(path).resolve()
    if limit_ms is not None:
        with wave.open(str(path), 'rb') as wav:
            needs_trim = wav.getnframes() * 1000 > limit_ms * wav.getframerate()
        if needs_trim:
            with tempfile.TemporaryDirectory() as directory:
                clip = save_reading(Path(directory) / 'clip.wav', [path], stop_event, limit_ms)
                if clip is not None:
                    _play_wav(clip, stop_event, events)
            return
    _play_wav(path, stop_event, events)


def _play_wav(path, stop_event, events):
    from PySide6.QtCore import QEventLoop, QTimer, QUrl
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

    if stop_event.is_set():
        return
    commands = queue.Queue()
    loop = QEventLoop()
    player = QMediaPlayer()
    output = QAudioOutput()
    player.setAudioOutput(output)
    timer = QTimer(interval=40)

    def update():
        if stop_event.is_set() or player.error() != QMediaPlayer.Error.NoError:
            loop.quit()
            return
        if player.mediaStatus() == QMediaPlayer.MediaStatus.EndOfMedia:
            loop.quit()
            return
        duration = player.duration()
        if duration <= 0:
            return
        paused = player.playbackState() == QMediaPlayer.PlaybackState.PausedState
        position = player.position()
        try:
            action, seconds = commands.get_nowait()
        except queue.Empty:
            pass
        else:
            if action == 'toggle':
                if paused:
                    player.play()
                else:
                    player.pause()
            else:
                position = max(0, min(duration, position + seconds * 1000))
                if position >= duration and not paused:
                    loop.quit()
                    return
                player.setPosition(position)
        events.put(dict(kind='transport', commands=commands,
                        paused=player.playbackState() == QMediaPlayer.PlaybackState.PausedState,
                        elapsed=position / 1000, duration=duration / 1000))

    timer.timeout.connect(update)
    try:
        player.setSource(QUrl.fromLocalFile(str(path)))
        player.play()
        timer.start()
        loop.exec()
        if not stop_event.is_set() and player.error() != QMediaPlayer.Error.NoError:
            raise OSError(f'Riproduzione WAV: {player.errorString()}')
    finally:
        timer.stop()
        player.stop()
        player.setSource(QUrl())


def reading_plan(source):
    source = Path(source).resolve()
    if source.suffix.lower() == '.json':
        read_plan(source)
        return source
    if source.suffix.lower() not in ('.md', '.markdown'):
        raise ValueError('Selezionare un piano JSON o un file narrativo Markdown (.md, .markdown).')
    fingerprint = hashlib.sha256(source.read_bytes()).hexdigest()[:12]
    plan = source.parent / '.audiolibro' / (source.stem + '_' + fingerprint + '.json')
    if not plan.exists():
        prepare(source, plan)
    data = read_plan(plan)
    if Path(data['source']).resolve() != source:
        raise ValueError('Il piano appartiene a un altro sorgente.')
    return plan


def editor(path=None, auto_read=False):
    import sys
    try:
        from editor_audiolibro import run
    except ModuleNotFoundError as error:
        if error.name != 'PySide6':
            raise
        raise SystemExit('Installa la GUI con: python -m pip install -r '
                         + str(HERE / 'requirements-editor.txt')) from error
    run(path, sys.modules[__name__], auto_read)


if __name__ == '__main__':
    def existing_file(value):
        path = Path(value).resolve()
        if not path.is_file():
            raise argparse.ArgumentTypeError(
                f'File di ingresso non trovato: {path}. Indica il percorso di un file esistente.'
            )
        return path

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', action='version', version=__version__)
    sub = parser.add_subparsers(dest='action', required=True)
    audit = sub.add_parser('analizza', help='Analizza WAV senza modificarli: volume, pause e indice')
    audit.add_argument('directory', type=Path)
    audit.add_argument('report', type=Path, help='Rapporto JSON da salvare')
    prep = sub.add_parser('prepara'); prep.add_argument('source', type=existing_file); prep.add_argument('plan', type=Path)
    ui = sub.add_parser('editor', help='Apre l’editor con un piano JSON o un Markdown facoltativo')
    ui.add_argument('file', nargs='?', type=existing_file)
    reading = sub.add_parser('leggi', help='Apre il lettore e legge il file dall’inizio alla fine')
    reading.add_argument('file', type=existing_file, help='Piano JSON esistente oppure sorgente narrativo Markdown')
    audio = sub.add_parser('esporta'); audio.add_argument('plan', type=existing_file); audio.add_argument('output', type=Path)
    audio.add_argument('--allow-unassigned', action='store_true', help='Consente una bozza con narratore sulle battute non assegnate')
    audio.add_argument('--voice-mode', choices=VOICE_MODES, default=NARRATOR_MODE,
                       help='Narratore unico (narratore) o voci dei profili (cast)')
    audio.add_argument('--voice-design', action='store_true', help='Legge direttamente con VoiceDesign')
    args = parser.parse_args()
    if args.action == 'analizza':
        from analisi_audio import analyze_directory
        save(args.report, analyze_directory(args.directory))
        print(args.report)
    elif args.action == 'prepara':
        result = prepare(args.source, args.plan)
        print(f"Preparati {len(result['segments'])} paragrafi: {args.plan}")
    elif args.action == 'editor': editor(args.file)
    elif args.action == 'leggi': editor(args.file, auto_read=True)
    else: print(render(args.plan, args.output, allow_unassigned=args.allow_unassigned, voice_mode=args.voice_mode,
                       voice_design=args.voice_design))

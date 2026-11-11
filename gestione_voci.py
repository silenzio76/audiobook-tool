"""Editor actions for assignments, profiles and overlapping voices."""
from ui_language import tr
import copy
from pathlib import Path
import wave
from array import array


def checked_plan_copy(path, original):
    from audiolibro import read_plan
    if read_plan(path) != original:
        raise ValueError('Il piano è cambiato in un’altra finestra. Riaprirlo prima di salvare.')
    return copy.deepcopy(original)


def assignment(path, original, index, role=None, tone=None, delete=False, language=None):
    from audiolibro import save
    from profili_vocali import load, tone_label
    result = checked_plan_copy(path, original)
    segment = result['segments'][index]
    if delete:
        segment['role'] = 'Da assegnare' if segment['text'].startswith('«') else 'Narratore'
        segment.pop('tone', None)
        segment.pop('language', None)
    else:
        if role not in load()['characters']: raise ValueError('Profilo non valido')
        segment.update(role=role, tone=tone_label(tone))
        if language is not None:
            from lingue_audio import LANGUAGES
            if language not in LANGUAGES: raise ValueError('Lingua non valida')
            segment['language']=language
    segment.pop('voice_review', None)
    save(path, result)
    return result


def change_document_language(path, original, language):
    from audiolibro import save
    from lingue_audio import DOCUMENT_LANGUAGES

    if language not in DOCUMENT_LANGUAGES:
        raise ValueError('Lingua del documento non valida.')
    result = checked_plan_copy(path, original)
    if language == 'auto':
        result.pop('language', None)
    else:
        result['language'] = language
    save(path, result)
    return result


def change_pause(path, original, index, milliseconds):
    from audiolibro import MAX_PAUSE_MS, save
    if milliseconds is not None and (type(milliseconds) is not int or not 0 <= milliseconds <= MAX_PAUSE_MS):
        raise ValueError('Pausa non valida.')
    result = checked_plan_copy(path, original)
    segment = result['segments'][index]
    if milliseconds is None:
        segment.pop('pause_ms', None)
    else:
        segment['pause_ms'] = milliseconds
    save(path, result)
    return result


def change_spoken_text(path, original, index, text):
    from audiolibro import save, segment_speech_parts
    result = checked_plan_copy(path, original)
    segment = result['segments'][index]
    if text.strip() and text != segment['text']:
        segment['spoken_text'] = text
    else:
        segment.pop('spoken_text', None)
    segment_speech_parts(result, segment)
    save(path, result)
    return result


def split_segment(path, original, index, offset):
    from audiolibro import save
    from lingue_audio import lexicon
    result = checked_plan_copy(path, original)
    segments = result['segments']
    segment = segments[index]
    if 'spoken_text' in segment:
        raise ValueError('Ripristina la pronuncia originale prima di dividere il segmento.')
    text = segment['text']
    if not 0 < offset < len(text) or not (text[offset - 1].isspace() or text[offset].isspace()):
        raise ValueError('Posiziona il cursore nello spazio fra due parole.')
    for term in lexicon():
        start = text.find(term, max(0, offset - len(term) + 1))
        if 0 <= start < offset < start + len(term):
            raise ValueError('Scegli un punto fuori dalla locuzione registrata nel lessico.')
    left, right = text[:offset].rstrip(), text[offset:].lstrip()
    if not all(any(char.isalnum() for char in part) for part in (left, right)):
        raise ValueError('Servono parole su entrambi i lati del punto di divisione.')
    if text.startswith('«') and text.endswith('»'):
        left += '»'
        right = '«' + right
    segment.pop('voice_review', None)
    following = dict(segment, text=right)
    segment.update(text=left)
    segment.pop('pause_ms', None)
    segments.insert(index + 1, following)
    for row, item in enumerate(segments[index:], index + 1):
        item['id'] = row
    save(path, result)
    return result


def add_profile(name, base, kind, members=(), gender='non_specificato'):
    from profili_vocali import load, validate, CONFIG
    from audiolibro import save
    data = load()
    name = name.strip()
    if not name or name in data['characters']: raise ValueError('Inserire un nome nuovo e non vuoto')
    if base not in data['characters'] or data['characters'][base].get('kind') == 'gruppo': raise ValueError('Voce base non valida')
    item = copy.deepcopy(data['characters'][base])
    item.pop('editorial_card', None)
    item.update(kind=kind, gender=gender, status='provvisorio', description='Profilo creato nell’editor',
                evidence='Voce base riutilizzata da ' + base + '; non nuovo timbro o tratto canonico.')
    if kind == 'gruppo': item['members'] = list(members)
    else: item.pop('members', None)
    data['characters'][name] = item
    validate(data); save(CONFIG, data)
    return name


def import_voice_reference(wav_path, name, provenance, transcript=''):
    import hashlib
    import io
    import json
    from audiolibro import atomic, save
    from profili_vocali import CONFIG, load, validate
    from qwen_backend import reference

    name, provenance = name.strip(), provenance.strip()
    if not name or not provenance:
        raise ValueError('Servono un nome per il profilo e la provenienza del campione.')
    data = load()
    profiles = data['characters']
    if name in profiles:
        raise ValueError('Esiste già un profilo con questo nome.')

    payload = Path(wav_path).read_bytes()
    with wave.open(io.BytesIO(payload), 'rb') as audio:
        if not 2 <= audio.getnframes() / audio.getframerate() <= 60:
            raise ValueError('Usa un estratto WAV PCM di durata compresa fra 2 e 60 secondi.')
        if audio.getnchannels() != 1:
            raise ValueError('Usa un WAV PCM mono.')
        if not any(audio.readframes(audio.getnframes())):
            raise ValueError('Il campione è vuoto o silenzioso.')

    digest = hashlib.sha256(payload).hexdigest()
    metadata = {'version': 1, 'origin': 'recorded-reference', 'text': transcript.strip(),
                'character': name, 'provenance': provenance, 'status': 'provvisorio',
                'audio': f'importato-{digest[:16]}.wav', 'audio_sha256': digest}
    folder = CONFIG.parent / 'voci_qwen'
    key = hashlib.sha256(json.dumps(metadata, sort_keys=True).encode('utf-8')).hexdigest()[:16]
    manifest = folder / f'importato-{key}.json'
    atomic(folder / metadata['audio'], payload)
    save(manifest, metadata)
    reference(manifest)
    narrator = profiles['Narratore']
    profiles[name] = {
        'voice': narrator['voice'], 'rate': narrator['rate'], 'pitch': narrator['pitch'],
        'engine': 'qwen3', 'reference': str(manifest.resolve()), 'status': 'provvisorio',
        'description': 'Campione vocale importato per il provino.',
        'evidence': provenance,
    }
    save(CONFIG, validate(data))
    return name


def set_narrator_reference(name):
    from audiolibro import save
    from profili_vocali import CONFIG, load, validate
    from qwen_backend import reference

    data = load()
    profiles = data['characters']
    source = profiles.get(name, {})
    if source.get('engine') != 'qwen3' or source.get('kind') == 'gruppo':
        raise ValueError('Scegli un singolo profilo con un riferimento Qwen.')
    reference(source['reference'])
    narrator = profiles['Narratore']
    if source['reference'] == narrator.get('reference'):
        return
    if 'Narratore precedente' not in profiles:
        profiles['Narratore precedente'] = dict(narrator)
    narrator.update(engine='qwen3', reference=source['reference'],
                    description=source['description'], evidence=source['evidence'],
                    status=source['status'], gender=source.get('gender', 'non_specificato'))
    save(CONFIG, validate(data))


def mix_group(paths, output):
    """Synthetic spoken chorus with overlapping voices, not harmonized singing."""
    tracks, fmt = [], None
    for path in paths:
        with wave.open(str(path), 'rb') as wav:
            current = (wav.getnchannels(), wav.getsampwidth(), wav.getframerate())
            if current[:2] != (1, 2) or (fmt is not None and current != fmt): raise ValueError('Formato del gruppo non compatibile')
            fmt = current
            samples = array('h'); samples.frombytes(wav.readframes(wav.getnframes())); tracks.append(samples)
    offsets = [int(i * .035 * fmt[2]) for i in range(len(tracks))]
    result = array('h', [0]) * max(len(t) + o for t, o in zip(tracks, offsets))
    for track, offset in zip(tracks, offsets):
        for i, value in enumerate(track): result[i + offset] += int(value / len(tracks))
    with wave.open(str(output), 'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(fmt[2]); wav.writeframes(result.tobytes())


def profile_dialog(app, on_created):
    from PySide6 import QtWidgets as Qt
    from pannelli_voce import form_dialog, gender_combo
    from profili_vocali import load

    window, form, actions = form_dialog(app, tr('Nuovo profilo vocale'), tr('Crea profilo'))
    names = [name for name, profile in load()['characters'].items() if profile.get('kind') != 'gruppo']
    name = Qt.QLineEdit()
    kind = Qt.QComboBox()
    for code, label in [('secondario', tr('Secondario')), ('fuori_campo', tr('Fuori campo')), ('gruppo', tr('Gruppo'))]:
        kind.addItem(label, code)
    gender = gender_combo()
    base = Qt.QComboBox()
    base.addItems(names)
    base.setCurrentText('Narratore')
    members = Qt.QListWidget()
    members.addItems(names)
    members.setSelectionMode(Qt.QAbstractItemView.SelectionMode.MultiSelection)
    for row, (label, control) in enumerate([(tr('&Nome'), name), (tr('&Tipo'), kind), (tr('&Genere'), gender),
                                             (tr('&Voce base'), base), (tr('&Componenti (2–6)'), members)]):
        form.insertRow(row, label, control)
    form.insertRow(5, Qt.QLabel(tr('La voce base riutilizza un timbro esistente.'), wordWrap=True))

    def update_kind():
        members.setEnabled(kind.currentData() == 'gruppo')

    def create():
        try:
            created = add_profile(name.text(), base.currentText(), kind.currentData(),
                                  [item.text() for item in members.selectedItems()], gender.currentData())
        except Exception as error:
            Qt.QMessageBox.critical(window, tr('Profilo non creato'), str(error))
            return
        on_created(created)
        window.accept()

    kind.currentIndexChanged.connect(update_kind)
    update_kind()
    actions.accepted.connect(create)
    window.exec()

"""Local Qwen engine run exclusively in its isolated Python environment."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
from numeri_italiani import spoken_it, VERSION as SPEECH_VERSION
from profili_vocali import delivery
from lingue_audio import speech_parts,QWEN

HERE = Path(__file__).resolve().parent
os.environ.setdefault('HF_HOME', str(HERE / '.runtime' / 'huggingface'))
os.environ.setdefault('HF_HUB_DISABLE_TELEMETRY', '1')
os.environ.setdefault('DO_NOT_TRACK', '1')
os.environ.setdefault('HF_HUB_DISABLE_XET', '1')
DESIGN = 'Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign'
BASE = 'Qwen/Qwen3-TTS-12Hz-0.6B-Base'
SAMPLE_TEXT = 'La porta è ancora aperta. Aspetta un momento: voglio capire che cosa è accaduto prima di prendere una decisione.'
VOICES = HERE / 'voci_qwen'
DESCRIPTIONS = {
    'Baldo': 'An adult male voice, deep bass, dry and slightly grainy, deliberate articulation, restrained delivery. Speak natural clear Italian, without theatrical effects.',
    'Alasia': 'An adult female voice, warm contralto with a firm resonant chest tone, direct delivery and clear Italian diction, not breathy.',
    'Yusuf': 'An adult male voice, soft rounded baritone, smooth and measured, precise consonants, calm clear Italian diction. No stereotyped accent.',
    'Aureliano': 'An adult male voice, dark metallic baritone, compact clipped delivery, slightly rough resonance, natural clear Italian diction.',
    'Anselmo': 'An adult male voice, light mellow tenor, slightly airy and gently textured, unhurried clear Italian diction.',
    'Nicasio': 'An adult male voice, bright forward tenor, lively rhythm and resonant nasal edge, clear natural Italian diction.',
    'Sālim': 'An adult male voice, middle register with a dry reedy texture and agile precise articulation, natural Italian diction. No stereotyped accent.',
    'Eudocia': 'An adult female voice, cool dark mezzo-soprano, controlled even delivery with crisp consonants and polished clear Italian diction.',
    'Miriam': 'An adult female voice, bright clear mezzo-soprano with a fine silvery resonance, careful lively articulation, natural Italian diction.',
    'Narratore': 'An adult male narrator, neutral mid-low register, clean smooth resonance, balanced conversational rhythm, clear standard Italian diction.',
}


def reference(path):
    path = Path(path).resolve()
    data = json.loads(path.read_text(encoding='utf-8'))
    wav = path.parent / data['audio']
    if hashlib.sha256(wav.read_bytes()).hexdigest() != data['audio_sha256']:
        raise ValueError('Campione vocale alterato: ' + str(wav))
    origin = data.get('origin')
    if origin == 'recorded-reference':
        if not data.get('provenance') or not isinstance(data.get('text'), str):
            raise ValueError('Riferimento registrato privo di provenienza o campo trascrizione')
    elif origin != 'synthetic-voice-design' or not data.get('text'):
        raise ValueError('Riferimento vocale privo di provenienza o trascrizione')
    return data, wav


def model(repo, download=False):
    if not download:
        os.environ['HF_HUB_OFFLINE'] = '1'
    import torch
    from huggingface_hub import snapshot_download
    from qwen_tts import Qwen3TTSModel
    if not torch.cuda.is_available():
        raise RuntimeError('CUDA non disponibile: il motore richiede la GPU verificata.')
    location = snapshot_download(repo, local_files_only=not download)
    print('Caricamento ' + repo, flush=True)
    loaded = Qwen3TTSModel.from_pretrained(location, device_map='cuda:0',
        dtype=torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16,
        attn_implementation='sdpa')
    return loaded, Path(location).name


def write_audio(path, audio, sr):
    import numpy as np
    import soundfile as sf
    import tempfile
    from audiolibro import atomic
    samples = np.asarray(audio, dtype=np.float32)
    if samples.ndim != 1 or samples.size == 0 or not np.isfinite(samples).all():
        raise ValueError('Audio sintetizzato non valido')
    if np.max(np.abs(samples)) < 0.00001:
        raise ValueError('Audio sintetizzato silenzioso')
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=path.parent) as tmp:
        candidate = Path(tmp) / 'candidate.wav'
        sf.write(candidate, samples, sr, subtype='PCM_16')
        info = sf.info(candidate)
        if info.frames <= 0 or info.channels != 1 or info.samplerate != sr:
            raise ValueError('Formato del campione non valido')
        atomic(path, candidate.read_bytes())


def design(names, download):
    import torch
    from audiolibro import save
    engine, revision = model(DESIGN, download)
    for name in names:
        if name not in DESCRIPTIONS:
            raise ValueError('Personaggio non configurato: ' + name)
        description = DESCRIPTIONS[name]
        identity = hashlib.sha256((name + description + revision).encode()).hexdigest()[:12]
        destination = VOICES / (re.sub(r'[^\w-]', '_', name) + '_' + identity + '.json')
        if destination.exists():
            reference(destination)
            print('Già pronto: ' + str(destination), flush=True)
            continue
        torch.manual_seed(int(identity[:8], 16))
        print('Progetto vocale provvisorio: ' + name, flush=True)
        with torch.inference_mode():
            wavs, sr = engine.generate_voice_design(text=SAMPLE_TEXT, language='Italian',
                instruct=description, max_new_tokens=1024, do_sample=True)
        audio = destination.with_suffix('.wav')
        if len(wavs[0]) / sr > max(15, len(SAMPLE_TEXT) / 4):
            raise ValueError('Campione eccessivamente lungo: possibile ripetizione generativa')
        write_audio(audio, wavs[0], sr)
        save(destination, {'version': 1, 'character': name, 'status': 'provvisorio',
            'origin': 'synthetic-voice-design', 'model': DESIGN, 'revision': revision,
            'description': description, 'text': SAMPLE_TEXT, 'audio': audio.name,
            'audio_sha256': hashlib.sha256(audio.read_bytes()).hexdigest()})
        print('Creato: ' + str(destination), flush=True)


def boundary_rank(text, boundary):
    prefix = text[:boundary.start()].rstrip('»”"')
    if prefix.endswith(('.', '!', '?', '…')):
        return 2
    if prefix.endswith((';', ':', ',')):
        return 1
    return 0


def recovery_boundary(text):
    boundaries = list(re.finditer(r'\s+', text))
    if not boundaries:
        return None
    central = [match for match in boundaries if len(text) * .3 <= match.start() <= len(text) * .7]
    return max(central or boundaries,
               key=lambda match: (boundary_rank(text, match), -abs(match.start() - len(text) / 2))
               if central else (0, -abs(match.start() - len(text) / 2)))


def clone_chunks(engine, prompt, path, text, language, splits_left=3, *, embedding_only=False):
    import torch

    seed = int(hashlib.sha256((path + text).encode()).hexdigest()[:8], 16)
    torch.manual_seed(seed)
    limit = max(5, len(text) / 6 + 2) if embedding_only else max(15, len(text) / 4)
    options = dict(text=text, language=language, voice_clone_prompt=prompt,
                   max_new_tokens=2048)
    audio, sr = engine.generate_voice_clone(**options, do_sample=False)
    if len(audio[0]) / sr <= limit:
        yield audio[0], sr
        return
    if embedding_only:
        torch.manual_seed(seed)
        audio, sr = engine.generate_voice_clone(**options, do_sample=True)
        if len(audio[0]) / sr <= limit:
            yield audio[0], sr
            return

    cut = recovery_boundary(text) if splits_left else None
    if cut is not None:
        for fragment in (text[:cut.start()], text[cut.end():]):
            yield from clone_chunks(engine, prompt, path, fragment, language, splits_left - 1,
                                    embedding_only=embedding_only)
        return

    if not embedding_only:
        torch.manual_seed(seed)
        audio, sr = engine.generate_voice_clone(**options, do_sample=True)
    duration = len(audio[0]) / sr
    if duration > limit:
        raise ValueError(f'Qwen ha superato il limite anche dopo il recupero '
                         f'({duration:.1f} s, limite {limit:.1f} s, {len(text)} caratteri)')
    yield audio[0], sr


def voice_design_chunks(engine, instruction, text, language, splits_left=3):
    import torch

    seed = int(hashlib.sha256(instruction.encode()).hexdigest()[:8], 16)
    limit = max(15, len(text) / 4)
    options = dict(text=text, instruct=instruction, language=language, max_new_tokens=2048)
    torch.manual_seed(seed)
    audio, sr = engine.generate_voice_design(**options, do_sample=True)
    if len(audio[0]) / sr <= limit:
        yield audio[0], sr
        return

    cut = recovery_boundary(text) if splits_left else None
    if cut is not None:
        for fragment in (text[:cut.start()], text[cut.end():]):
            yield from voice_design_chunks(engine, instruction, fragment, language, splits_left - 1)
        return

    torch.manual_seed(seed + 1)
    audio, sr = engine.generate_voice_design(**options, do_sample=True)
    duration = len(audio[0]) / sr
    if duration > limit:
        raise ValueError(f'VoiceDesign ha superato il limite anche dopo il recupero '
                         f'({duration:.1f} s, limite {limit:.1f} s): {text[:80]}')
    yield audio[0], sr


def preserve_formant_pitch(samples, sr, semitones):
    """Transpose speech with Rubber Band while retaining its spectral envelope."""
    import shutil
    import subprocess
    import numpy as np

    executable = shutil.which('ffmpeg')
    if not executable:
        raise RuntimeError('La trasposizione con formanti preservate richiede FFmpeg con Rubber Band.')
    source = np.asarray(samples, dtype='<f4')
    if source.ndim != 1 or not source.size or not np.isfinite(source).all():
        raise ValueError('Audio non valido per la trasposizione.')
    command = [executable, '-nostdin', '-v', 'error', '-f', 'f32le', '-ar', str(sr), '-ac', '1',
               '-i', 'pipe:0', '-af', f'rubberband=pitch={2 ** (semitones / 12):.12f}:'
               'formant=preserved:pitchq=quality:transients=smooth',
               '-f', 'f32le', '-ar', str(sr), '-ac', '1', 'pipe:1']
    result = subprocess.run(command, input=source.tobytes(), capture_output=True, timeout=90,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise RuntimeError('Trasposizione vocale non riuscita: ' + result.stderr.decode('utf-8', errors='replace')[-1500:])
    shifted = np.frombuffer(result.stdout, dtype='<f4').copy()
    if (not shifted.size or not np.isfinite(shifted).all()
            or abs(shifted.size - source.size) > sr * .1):
        raise ValueError('Audio trasposto vuoto, non valido o di durata inattesa.')
    return np.pad(shifted, (0, max(0, source.size - shifted.size)))[:source.size]


def synthesize(job_file):
    os.environ['HF_HUB_OFFLINE'] = '1'
    import numpy as np
    import torch
    from scipy.signal import resample_poly
    jobs = json.loads(Path(job_file).read_text(encoding='utf-8-sig'))
    if not jobs:
        raise ValueError('Nessun segmento Qwen')
    engine, revision = None, 'espeak-ng'
    direct = bool(jobs[0].get('design_instruct'))
    if any(bool(job.get('design_instruct')) != direct for job in jobs):
        raise ValueError('Il lavoro Qwen deve usare un solo modello.')
    repo = DESIGN if direct else BASE
    prompts = {}
    for job in jobs:
        path = job.get('reference','')
        instruction = job.get('design_instruct')
        with torch.inference_mode():
            chunks = []
            parts = job.get('parts') or speech_parts(job['text'], job.get('language', 'auto'))
            if len({part['language'] for part in parts}) > 1:
                # Keep code-switches in the same synthesis context and speaker.
                text = ''.join(part['text'] for part in parts)
                parts = [{'text': text, 'language': 'auto'}]
            for part in parts:
                language=part['language']
                for text in split_text(part['text']):
                    if part.get('audio'):
                        import soundfile as sf
                        samples,sr=sf.read(part['audio'],dtype='float32')
                        if samples.ndim!=1: raise ValueError('Il WAV di pronuncia deve essere mono')
                    elif language != 'auto' and language not in QWEN:
                        from sintesi_lingue import synthesize_native
                        samples,sr=synthesize_native(text,language,job.get('gender','non_specificato'))
                    else:
                        if engine is None:
                            engine, revision = model(repo)
                        model_language = QWEN.get(language, 'Auto')
                        if direct:
                            generated = voice_design_chunks(engine, instruction, text, model_language)
                        else:
                            embedding_only = job.get('speaker_embedding_only', False)
                            prompt_key = (path, embedding_only)
                            if prompt_key not in prompts:
                                data,wav=reference(path)
                                # Keep the voice identity without conditioning on the sample's words.
                                transcript = '' if embedding_only else data['text'].strip()
                                prompts[prompt_key] = engine.create_voice_clone_prompt(
                                    ref_audio=str(wav), ref_text=transcript or None,
                                    x_vector_only_mode=not transcript)
                            generated = clone_chunks(engine, prompts[prompt_key], path, text, model_language,
                                                     embedding_only=embedding_only)
                        for samples, sr in generated:
                            divisor = math.gcd(sr, 22050)
                            chunks.append(resample_poly(samples, 22050 // divisor, sr // divisor))
                        continue
                    divisor=math.gcd(sr,22050)
                    chunks.append(resample_poly(samples,22050//divisor,sr//divisor))
                    if part.get('audio'): break
            result = np.concatenate(chunks)
            sr=22050
        tone = job.get('tone', 'Neutro')
        # Delivery tones are explicit technical modulations, preserving base timbre.
        speed,gain=delivery(tone)
        speed = max(.75, min(1.25, speed * job.get('character_speed', 1.0)))
        speed *= job.get('reading_speed', 1.0)
        pitch = job.get('character_pitch', 0)
        if pitch:
            if job.get('preserve_formants', False):
                result = preserve_formant_pitch(result, sr, pitch)
            else:
                import librosa
                result = librosa.effects.pitch_shift(result, sr=sr, n_steps=pitch)
        if speed != 1.0:
            import librosa
            result = librosa.effects.time_stretch(result, rate=speed)
        result=result*gain
        peak=np.max(np.abs(result))
        if peak>.98: result=result*(.98/peak)
        divisor = math.gcd(sr, 22050)
        result = resample_poly(result, 22050 // divisor, sr // divisor)
        write_audio(job['output'], result, 22050)
        print('Sintetizzato segmento Qwen', flush=True)
    from audiolibro import save
    save(Path(job_file).with_suffix('.backend.json'), {'model': repo if engine is not None else 'eSpeak NG', 'revision': revision,
         'language': 'per-fragment', 'mode': 'local-offline-voice-design' if direct else 'local-offline-synthetic-reference',
         'speech_normalization': SPEECH_VERSION})


def split_text(text, limit=350):
    """Use short chunks to bound memory and generative truncation risk."""
    text = text.strip()
    while len(text) > limit:
        choices = list(re.finditer(r'\s+', text[:limit + 1]))
        if not choices:
            raise ValueError('Parola troppo lunga per la sintesi')
        near_limit = [match for match in choices if match.start() >= limit * .5]
        boundary = max(near_limit, key=lambda match: (boundary_rank(text, match), match.start())) if near_limit else choices[-1]
        cut = boundary.end()
        yield text[:cut].strip()
        text = text[cut:].strip()
    if text:
        yield text


def activate(names):
    from audiolibro import save
    from profili_vocali import CONFIG, load, validate
    data = load()
    for name in names:
        candidates = []
        for path in VOICES.glob('*.json'):
            info, _ = reference(path)
            if info.get('character') == name:
                candidates.append(path)
        if len(candidates) != 1:
            raise ValueError('Attivazione ambigua o campione mancante per ' + name + ': usare profili_vocali.py --reference')
        path = candidates[0]
        info, _ = reference(path)
        profile = data['characters'].setdefault(name, {'voice': 'CosimoM', 'rate': 0, 'pitch': 0,
            'description': '', 'evidence': '', 'status': 'provvisorio'})
        profile.update(engine='qwen3', reference=str(path.resolve()), status='provvisorio',
            evidence='Campione Qwen sintetico; proposta tecnica da ascoltare, non nuovo canone narrativo.')
        if not profile['description']:
            profile['description'] = info['description']
    if 'Narratore' in names:
        for name in ('Da assegnare', 'Altro'):
            # Explicit technical fallback, not automatic dialogue attribution.
            data['characters'][name].update(engine='qwen3', reference=data['characters']['Narratore']['reference'])
    validate(data)
    save(CONFIG, data)
    print('Profili attivati: ' + ', '.join(names), flush=True)


def main():
    # Use native Windows certificates while keeping TLS verification enabled.
    try:
        import truststore
        truststore.inject_into_ssl()
    except ImportError:
        pass
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['design', 'sintesi', 'download', 'attiva'])
    parser.add_argument('items', nargs='*')
    parser.add_argument('--download', action='store_true')
    parser.add_argument('--description', help='Descrizione acustica di un nuovo personaggio, senza imitare persone reali')
    args = parser.parse_args()
    if args.command == 'design':
        if args.description:
            if len(args.items) != 1:
                parser.error('--description richiede un solo nome di personaggio')
            DESCRIPTIONS[args.items[0]] = args.description
        design(args.items or list(DESCRIPTIONS), args.download)
    elif args.command == 'download':
        from huggingface_hub import snapshot_download
        for repo in (DESIGN, BASE):
            print(snapshot_download(repo), flush=True)
    elif args.command == 'attiva':
        activate(args.items or list(DESCRIPTIONS))
    else:
        if len(args.items) != 1:
            parser.error('Specificare un solo file job JSON')
        synthesize(args.items[0])


if __name__ == '__main__':
    main()

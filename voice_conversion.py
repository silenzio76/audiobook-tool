"""Offline, cached voice conversion; pronunciation and converted audio stay distinct."""
import hashlib
import io
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
import wave

from pipeline_audio import ReadingCancelled

HERE = Path(__file__).resolve().parent
MODELS = {'seed-vc': 'Seed-VC · conversione vocale', 'source': 'Qwen / eSpeak · voce di base'}
CONVERSION_VERSION = 'seed-vc-stage-1'
# Prefer the documented offline quality range over the faster 25-step baseline.
# This refines conversion; it does not certify natural prosody or IPA fidelity.
SETTINGS = {'diffusion_steps': 50, 'length_adjust': 1.0, 'inference_cfg_rate': .7, 'fp16': True}


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def installed(root=HERE):
    home = Path(root) / '.runtime/seed-vc'
    return all(p.is_file() for p in (home/'ready.json', home/'models-manifest.json',
                                    home/'venv/Scripts/python.exe', home/'source/inference.py'))


def document_model(plan, root=HERE):
    return plan.get('voice_model', 'seed-vc' if plan.get('language') == 'ipa-it' and installed(root) else 'source')


def segment_model(plan, segment, root=HERE):
    selected = segment.get('voice_model', 'inherit')
    return document_model(plan, root) if selected == 'inherit' else selected


def validate_models(plan):
    if not isinstance(plan.get('voice_model','source'),str) or plan.get('voice_model', 'source') not in MODELS:
        raise ValueError('Modello voce del documento non valido.')
    if any(s.get('voice_model', 'inherit') not in (*MODELS, 'inherit') for s in plan['segments']):
        raise ValueError('Modello voce del segmento non valido.')


def installation_identity(root=HERE):
    home = Path(root)/'.runtime/seed-vc'
    if not installed(root):
        raise RuntimeError('Seed-VC non disponibile. Seleziona la voce di base o completa l’installazione locale.')
    ready = json.loads((home/'ready.json').read_text(encoding='utf-8'))
    manifest = json.loads((home/'models-manifest.json').read_text(encoding='utf-8'))
    commit = subprocess.run(['git', '-C', str(home/'source'), 'rev-parse', 'HEAD'], check=True,
                            capture_output=True, text=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).stdout.strip()
    if not ready.get('installation_ready') or commit != ready['source_revision']:
        raise RuntimeError('Installazione Seed-VC modificata: ripetere la verifica locale.')
    dirty=subprocess.run(['git','-C',str(home/'source'),'status','--porcelain','--untracked-files=no'],
                         check=True,capture_output=True,text=True,
                         creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)).stdout.strip()
    if dirty:
        raise RuntimeError('Sorgenti Seed-VC modificati: verificare la versione prima della conversione.')
    return {'version': CONVERSION_VERSION, 'source_revision': commit,
            'models': digest(home/'models-manifest.json'), 'settings': SETTINGS,
            'worker':digest(HERE/'seed_vc_worker.py'),
            'environment':digest(home/'requirements-resolved.txt'),
            'files': manifest}


def reference_for(plan, index, voice_mode):
    from profili_vocali import load
    from qwen_backend import reference
    role = 'Narratore' if voice_mode == 'narratore' else plan['segments'][index]['role']
    profile = load()['characters'][role]
    if profile.get('kind') == 'gruppo' or not profile.get('reference'):
        raise ValueError(f'Seed-VC: il profilo {role} richiede un riferimento WAV individuale. Scegli la voce di base per questo segmento.')
    _, path = reference(profile['reference'])
    return path


class SeedWorker:
    """One isolated GPU process per reading session; cancellation terminates it."""
    def __init__(self, root=HERE):
        self.root = Path(root)
        self.process = None
        self.messages = queue.Queue()
        self.errors = []
        self.model_loads = 0

    def start(self):
        if self.process is not None:
            return
        home = self.root/'.runtime/seed-vc'
        self.messages=queue.Queue()
        self.errors=[]
        self.model_loads=0
        env = dict(os.environ, HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                   HF_HOME=str(home/'huggingface'), HF_HUB_CACHE=str(home/'source/checkpoints/hf_cache'),
                   HF_HUB_DISABLE_TELEMETRY='1', TORCH_HOME=str(home/'torch-cache'), MPLBACKEND='Agg')
        env['PYTHONNOUSERSITE']='1'
        env.pop('PYTHONPATH',None)
        self.process = subprocess.Popen([str(home/'venv/Scripts/python.exe'), '-X', 'utf8',
                                         str(HERE/'seed_vc_worker.py'), str(home)],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        text=True, encoding='utf-8', errors='replace', env=env,
                                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        process=self.process
        def read_output():
            for line in process.stdout:
                try:
                    self.messages.put(json.loads(line))
                except ValueError:
                    self.errors.append(line[-1000:])
        def read_errors():
            for line in process.stderr:
                self.errors.append(line[-1000:])
                del self.errors[:-40]
        self.readers = [threading.Thread(target=read_output, daemon=True),
                        threading.Thread(target=read_errors, daemon=True)]
        for reader in self.readers:
            reader.start()

    def convert(self, source, reference, output, cancel=None):
        self.start()
        self.process.stdin.write(json.dumps({'source': str(source), 'reference': str(reference),
                                             'output': str(output), 'settings': SETTINGS})+'\n')
        self.process.stdin.flush()
        deadline = time.monotonic()+600
        while True:
            if cancel is not None and cancel.is_set():
                self.close(force=True)
                raise ReadingCancelled('Conversione vocale interrotta.')
            try:
                message = self.messages.get(timeout=.1)
            except queue.Empty:
                if self.process.poll() is not None:
                    raise RuntimeError('Seed-VC arrestato.\n'+''.join(self.errors)[-3000:])
                if time.monotonic()>deadline:
                    self.close(force=True)
                    raise RuntimeError('Seed-VC: tempo massimo di conversione superato.')
                continue
            if message.get('kind') == 'error':
                raise RuntimeError(message['message'])
            if message.get('kind') == 'loading':
                self.model_loads+=1
            if message.get('kind') == 'complete':
                return message

    def close(self, force=False):
        process = self.process
        if process is None:
            return
        try:
            if process.poll() is None:
                if not force:
                    try:
                        process.stdin.write('{"quit":true}\n')
                        process.stdin.flush()
                        process.wait(timeout=3)
                    except (OSError, subprocess.TimeoutExpired):
                        force = True
                if force:
                    process.kill()
                    process.wait(timeout=3)
        finally:
            for reader in self.readers:
                reader.join(timeout=1)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()
            self.process = None


class VoiceStage:
    """Resolve per-segment models, preserve pauses, and publish matching cache pairs."""
    def __init__(self, plan, voice_mode='narratore', *, root=HERE, worker=None, regenerate=False):
        self.plan, self.voice_mode, self.root = plan, voice_mode, Path(root)
        self.worker = worker or SeedWorker(root)
        self.identity = None
        self.regenerate = regenerate

    def preflight(self, indices):
        for index in indices:
            if segment_model(self.plan, self.plan['segments'][index], self.root) == 'seed-vc':
                if self.identity is None:
                    self.identity = installation_identity(self.root)
                reference_for(self.plan, index, self.voice_mode)

    def convert(self, index, source, cancel=None, *, padding=0, cache_only=False):
        from audio_publication import publish_audio, recover_audio
        from audiolibro import plan_pause_ms
        source = Path(source)
        segment = self.plan['segments'][index]
        if segment_model(self.plan, segment, self.root) == 'source':
            return source
        self.preflight([index])
        reference = reference_for(self.plan, index, self.voice_mode)
        source_hash, reference_hash = digest(source), digest(reference)
        data = {}
        if source.with_suffix('.indice.json').exists():
            data = json.loads(source.with_suffix('.indice.json').read_text(encoding='utf-8'))
            if data.get('audio_sha256') != source_hash:
                raise ValueError('Audio sorgente e indice non corrispondono.')
            padding = data['segments'][0].get('pause_frames', 0)
        key_data = {'source': source_hash, 'reference': reference_hash, 'identity': self.identity,
                    'padding': padding, 'segment': segment['id']}
        key = hashlib.sha256(json.dumps(key_data, sort_keys=True).encode()).hexdigest()
        destination = source.parent/'seed-vc'/(f"{segment['id']:05}-"+key[:20]+'.wav')
        destination.parent.mkdir(parents=True, exist_ok=True)
        recover_audio(destination)
        metadata = destination.with_suffix('.indice.json')
        if not self.regenerate and destination.exists() and metadata.exists():
            try:
                cached = json.loads(metadata.read_text(encoding='utf-8'))
                if cached.get('voice_conversion', {}).get('key') == key and cached.get('audio_sha256') == digest(destination):
                    return destination
            except (OSError,ValueError):
                pass
        if cache_only:
            return None
        with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
            temporary = Path(temporary)
            speech, result, assembled = (temporary/n for n in ('speech.wav','converted.wav','complete.wav'))
            with wave.open(str(source), 'rb') as src:
                if (src.getnchannels(), src.getsampwidth(), src.getframerate()) != (1,2,22050):
                    raise ValueError('Seed-VC richiede PCM mono a 22.050 Hz.')
                count = src.getnframes()-padding
                if count <= 0:
                    raise ValueError('Sorgente senza parlato per Seed-VC.')
                with wave.open(str(speech), 'wb') as dst:
                    dst.setparams(src.getparams())
                    dst.writeframes(src.readframes(count))
            self.worker.convert(speech, reference, result, cancel)
            if cancel is not None and cancel.is_set():
                raise ReadingCancelled('Conversione vocale interrotta.')
            with wave.open(str(result), 'rb') as src, wave.open(str(assembled), 'wb') as dst:
                frames = src.getnframes()
                if (src.getnchannels(),src.getsampwidth(),src.getframerate()) != (1,2,22050) or frames <= 0:
                    raise ValueError('Formato della conversione Seed-VC non valido.')
                if abs(frames-count)>max(11025, count*.05):
                    raise ValueError('Durata della conversione Seed-VC inattesa.')
                dst.setparams(src.getparams())
                dst.writeframes(src.readframes(frames))
                dst.writeframes(b'\0'*(padding*2))
            if digest(source) != source_hash or digest(reference) != reference_hash:
                raise ValueError('Sorgente o riferimento cambiato durante la conversione.')
            timing = dict(data.get('segments', [{}])[0], id=segment['id'], line=segment['line'],
                          role=segment['role'], frames=frames+padding, pause_frames=padding,
                          seconds=0, pause_ms=plan_pause_ms(self.plan,segment))
            record = {'key': key, 'source_audio_sha256': source_hash, 'reference_sha256': reference_hash,
                      'version': CONVERSION_VERSION, 'settings': SETTINGS,
                      'model_manifest': self.identity['models'], 'source_revision': self.identity['source_revision']}
            publish_audio(assembled, destination, dict(data, duration=(frames+padding)/22050,
                                                       segments=[timing], voice_conversion=record), cancel_event=cancel)
        return destination

    def close(self):
        self.worker.close()


def export_prepared(path, clips, output, cancel=None, *, indices=None):
    """Assemble already-final clips; never launch synthesis or conversion here."""
    import audiolibro as audio
    from audio_publication import publish_audio, recover_audio
    plan = audio.read_plan(path)
    plan_hash = digest(path)
    profiles = audio.load_profiles()
    references = audio.reference_hashes(profiles)
    segments=plan['segments'] if indices is None else [plan['segments'][i] for i in indices]
    if len(clips) != len(segments):
        raise ValueError('Esportazione incompleta: mancano segmenti preparati.')
    output = Path(output)
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as temporary:
        temporary = Path(temporary)
        combined = temporary/'complete.wav'
        timings, conversions, parts, spoken = [], [], [], []
        elapsed = 0
        base = None
        with wave.open(str(combined),'wb') as dst:
            dst.setnchannels(1); dst.setsampwidth(2); dst.setframerate(22050)
            for segment, clip in zip(segments,clips,strict=True):
                if cancel is not None and cancel.is_set():
                    raise ReadingCancelled('Esportazione interrotta.')
                clip=Path(clip)
                recover_audio(clip)
                data=json.loads(clip.with_suffix('.indice.json').read_text(encoding='utf-8'))
                if data['audio_sha256']!=digest(clip) or data['source_sha256']!=plan['sha256']:
                    raise ValueError('Anteprima non valida per l’esportazione.')
                if data['profiles']!=profiles or data['voice_references']!=references:
                    raise ValueError('Profili o riferimenti cambiati: rigenerare prima di esportare.')
                if not audio.cached_pronunciation_matches(data,plan,segment):
                    raise ValueError('Pronuncia cambiata: rigenerare prima di esportare.')
                if segment_model(plan,segment)=='seed-vc' and not data.get('voice_conversion'):
                    raise ValueError('Conversione vocale mancante: esportazione arrestata.')
                if segment_model(plan,segment)=='source' and data.get('voice_conversion'):
                    raise ValueError('Modello voce cambiato: rigenerare prima di esportare.')
                timing=dict(data['segments'][0])
                if timing['id']!=segment['id'] or timing['audio_key']!=audio.segment_audio_key(plan,segment):
                    raise ValueError('Segmenti non corrispondenti: esportazione arrestata.')
                if timing.get('pause_ms')!=audio.plan_pause_ms(plan,segment):
                    raise ValueError('Pausa cambiata: rigenerare prima di esportare.')
                timing['voice_eq']=data.get('voice_eq')
                with wave.open(str(clip),'rb') as src:
                    if (src.getnchannels(),src.getsampwidth(),src.getframerate())!=(1,2,22050):
                        raise ValueError('Formato delle anteprime non compatibile.')
                    count=src.getnframes()
                    while block:=src.readframes(65536):
                        if cancel is not None and cancel.is_set():
                            raise ReadingCancelled('Esportazione interrotta.')
                        dst.writeframes(block)
                timing.update(seconds=elapsed/22050,frames=count)
                timings.append(timing); elapsed+=count
                conversions.append(data.get('voice_conversion'))
                parts.extend(data.get('language_parts',[])); spoken.extend(data.get('spoken_forms',[]))
                base=data
        if digest(path)!=plan_hash or audio.load_profiles()!=profiles or audio.reference_hashes(profiles)!=references:
            raise ValueError('Dati cambiati durante l’esportazione.')
        result=combined
        if output.suffix.lower()=='.mp4':
            from converti_audio import convert
            result=temporary/'complete.mp4'
            convert(combined,result,cancel_event=cancel)
        metadata=dict(base,plan_sha256=plan_hash,segments=timings,duration=elapsed/22050,
                      voice_conversions=conversions,language_parts=parts,spoken_forms=spoken,
                      unassigned=sum(s['role']=='Da assegnare' for s in segments))
        metadata.pop('voice_conversion',None)
        publish_audio(result,output,metadata,cancel_event=cancel)
    return output
